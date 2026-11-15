"""Typed LangGraph for autonomous, evidence-linked scientific experiments."""
from __future__ import annotations

import hashlib
import asyncio
import csv
import json
from pathlib import Path
from typing import Any

from langgraph.graph import END, StateGraph

from . import storage
from .config import settings
from .executor import ALLOWED_IMPORT_ROOTS, execute_bundle, experiment_root, validate_bundle
from .experiment_models import CodeBundle, ExperimentPlan, ResearchState
from .experiment_templates import bundle_for_plan, deterministic_plan
from .models import get_model, model_available
from .papers import search_papers, search_web


STAGE_PROGRESS = {
    'researching': 8,
    'planning': 20,
    'generating': 35,
    'validating': 48,
    'running': 62,
    'debugging': 58,
    'evaluating': 86,
    'reporting': 94,
    'completed': 100,
}


class ExperimentCancelled(RuntimeError):
    pass


def _job(state: ResearchState):
    job = storage.get_experiment_job(state['experiment_id'])
    if not job:
        raise RuntimeError('Experiment job no longer exists.')
    if job['cancel_requested']:
        raise ExperimentCancelled('Experiment cancelled by a collaborator.')
    return job


def _stage(state: ResearchState, stage: str, message: str, **changes):
    job = _job(state)
    progress = STAGE_PROGRESS[stage]
    storage.update_experiment_job(
        state['experiment_id'], status=stage, stage=stage, progress=progress,
        heartbeat_at=storage.utcnow(), **changes,
    )
    storage.add_experiment_event(state['experiment_id'], job['room_id'], 'experiment.stage_changed', {
        'experiment_id': state['experiment_id'], 'status': stage, 'stage': stage,
        'progress': progress, 'message': message,
    })


def _checkpoint(state: ResearchState, node: str, updates: dict[str, Any]):
    merged = {**state, **updates}
    storage.save_experiment_checkpoint(state['experiment_id'], node, merged)
    return updates


def _persist_bundle_version(state: ResearchState,label: str,bundle: CodeBundle):
    """Persist generated and repaired code even when it never passes validation."""
    root=experiment_root(state['experiment_id']);version=root/'code_versions'/label
    version.mkdir(parents=True,exist_ok=True)
    files={
        'experiment.py':bundle.experiment_py,
        'config.json':json.dumps(bundle.config,ensure_ascii=False,indent=2),
        'requirements.txt':'\n'.join(bundle.requirements)+'\n',
        'README.md':bundle.readme,
    }
    job=_job(state)
    for filename,content in files.items():
        path=version/filename;path.write_text(content,encoding='utf-8');data=path.read_bytes()
        artifact_id=storage.add_experiment_artifact(
            state['experiment_id'],None,'code_version',f'{label}-{filename}',str(path.relative_to(root)),
            'application/json' if filename.endswith('.json') else ('text/x-python' if filename.endswith('.py') else 'text/plain'),
            len(data),hashlib.sha256(data).hexdigest())
        storage.add_experiment_event(state['experiment_id'],job['room_id'],'experiment.artifact_created',{
            'experiment_id':state['experiment_id'],'artifact_id':artifact_id,'name':f'{label}-{filename}',
            'mime_type':'text/plain','size_bytes':len(data),'kind':'code_version'})


def _source_summary(sources):
    return '\n'.join(
        f"- {item.get('title', 'Untitled')} ({item.get('year', 'n.d.')}): "
        f"{item.get('summary', '')[:500]} URL={item.get('url', '')}"
        for item in sources[:10]
    )


async def research_node(state: ResearchState):
    _stage(state, 'researching', 'Searching the scientific literature and recording provenance.')
    (papers, source_name),(web_results,web_source)=await asyncio.gather(
        search_papers(state['user_request'], limit=10),
        search_web(state['user_request']+' experimental protocol dataset benchmark',limit=5),
    )
    sources=papers+web_results
    for source in sources:
        storage.add_experiment_source(state['experiment_id'], source)
    job = storage.get_experiment_job(state['experiment_id'])
    storage.add_experiment_event(state['experiment_id'], job['room_id'], 'experiment.research_completed', {
        'experiment_id': state['experiment_id'], 'source': source_name,
        'paper_count': len(papers),'web_result_count':len(web_results),'web_source':web_source,
        'message': (f'Research pass found {len(papers)} traceable paper records via {source_name} '
                    f'and {len(web_results)} general-web records via {web_source}.'),
    })
    return _checkpoint(state, 'research', {'research_sources': sources})


async def planning_node(state: ResearchState):
    _stage(state, 'planning', 'Converting the request into a bounded, executable protocol.')
    parent_plan = None
    if state.get('parent_experiment_id'):
        parent = storage.get_experiment_job(state['parent_experiment_id'])
        parent_plan = parent.get('plan') if parent else None
    plan = deterministic_plan(state['user_request'], parent_plan=parent_plan)
    if plan is None:
        if not model_available(getattr(settings, 'planning_model_provider', 'auto')):
            raise RuntimeError(
                'This experiment needs model-generated planning, but no planning model is configured. '
                'Configure OPENAI_API_KEY, OPENROUTER_API_KEY, NVIDIA_API_KEY, GROQ_API_KEY, or Ollama. '
                'The verified no-model path currently supports classification comparisons.'
            )
        model = get_model('planning').with_structured_output(ExperimentPlan)
        plan = await model.ainvoke(
            'You are ResearchForge\'s scientific planner. Produce a small, executable local-CPU '
            'experiment, not a promise. Use only network-free runtime data: scikit-learn bundled '
            'datasets or synthetic data generated in code. Maximum 300 seconds, 2048 MB RAM, 2 CPU. '
            'Never claim a faithful reproduction unless the evidence supports it. For paper requests, '
            'choose reduced_scale or adapted and state exact limitations. Every source URL must come '
            'from the supplied evidence.\n\nUSER REQUEST:\n' + state['user_request'] +
            '\n\nRETRIEVED EVIDENCE:\n' + _source_summary(state.get('research_sources', []))
        )
    job = _job(state)
    plan.resource_budget.runtime_seconds = min(plan.resource_budget.runtime_seconds, job['runtime_limit'])
    plan.resource_budget.memory_mb = min(plan.resource_budget.memory_mb, job['memory_mb'])
    plan.resource_budget.cpu_cores = min(plan.resource_budget.cpu_cores, job['cpu_cores'])
    plan.resource_budget.network_access = False
    value = plan.model_dump()
    storage.update_experiment_job(state['experiment_id'], plan=value)
    return _checkpoint(state, 'planning', {'experiment_plan': value})


async def generation_node(state: ResearchState):
    _stage(state, 'generating', 'Generating a versioned experiment bundle with a fixed runtime interface.')
    plan = ExperimentPlan.model_validate(state['experiment_plan'])
    bundle = bundle_for_plan(plan)
    if bundle is None:
        if not model_available(getattr(settings, 'coding_model_provider', 'auto')):
            raise RuntimeError('No verified code template matches this plan and no coding model is configured.')
        model = get_model('coding').with_structured_output(CodeBundle)
        allowed_imports = ', '.join(sorted(ALLOWED_IMPORT_ROOTS))
        bundle = await model.ainvoke(
            'Generate a complete deterministic Python experiment for the validated plan.\n'
            'The Docker executor runs: python /experiment/experiment.py '
            '--config /experiment/config.json --output-dir /outputs\n'
            'MANDATORY CONTRACT (implement all items literally):\n'
            '1. import argparse, json and from pathlib import Path. Define main() and call it '
            'under if __name__ == "__main__".\n'
            '2. In main(), use argparse.ArgumentParser(), '
            'parser.add_argument("--config", required=True), and '
            'parser.add_argument("--output-dir", required=True).\n'
            '3. Parse arguments, load config using json.loads(Path(args.config).read_text()), '
            'and create output = Path(args.output_dir); output.mkdir(parents=True, exist_ok=True).\n'
            '4. Run the actual experiment. Write a JSON OBJECT with a measured, nonempty '
            'verdict to (output / "metrics.json"). Use json.dumps.\n'
            '5. Write (output / "run_metadata.json") with completed=true, actual runtime '
            'and exact requested counts. Produce every artifact named in expected_artifacts. '
            'Do not invent results, counts, or files.\n'
            '6. Print a concise completion record to stdout.\n'
            'SANDBOX POLICY: generated code may import ONLY these module roots: '
            + allowed_imports + '.\n'
            'Do NOT import os, sys, xml, zipfile, zlib, zoneinfo, subprocess, socket, '
            'requests, importlib, or any other module outside that list. '
            'No network, host commands, environment secrets, dynamic imports, eval or exec. '
            'Use pathlib for file paths. Requirements may include ONLY numpy, pandas, '
            'scipy, scikit-learn, matplotlib, torch (unversioned); standard-library '
            'modules must NOT appear in requirements.\n'
            'Return a complete CodeBundle. Set config to the provided plan JSON. '
            'Use only actual computations for the scientific conclusions.\n\nPLAN:\n'
            + plan.model_dump_json(indent=2)
        )
    _persist_bundle_version(state,'generation',bundle)
    return _checkpoint(state, 'generation', {'code_bundle': bundle.model_dump()})


async def validation_node(state: ResearchState):
    _stage(state, 'validating', 'Applying syntax, dependency, interface, and sandbox policy checks.')
    plan = ExperimentPlan.model_validate(state['experiment_plan'])
    bundle = CodeBundle.model_validate(state['code_bundle'])
    result = validate_bundle(bundle, plan)
    job = _job(state)
    retry_count = int(state.get('retry_count', 0))
    if result.valid:
        next_step = 'execute'
    elif retry_count < job['max_retries']:
        next_step = 'debug'
    else:
        next_step = 'failed'
    errors = list(state.get('errors', [])) + result.errors
    if not result.valid:
        storage.add_experiment_event(state['experiment_id'],job['room_id'],'experiment.validation_failed',{
            'experiment_id':state['experiment_id'],'stage':'validating','errors':result.errors,
            'warnings':result.warnings,'code_hash':result.code_hash,'retry_count':retry_count,
            'message':'Generated code failed sandbox/contract validation and will not execute.'})
    return _checkpoint(state, 'validation', {
        'validation_result': result.model_dump(), 'errors': errors, 'next_step': next_step,
    })


async def execution_node(state: ResearchState):
    _stage(state, 'running', 'Launching the experiment in the isolated, network-disabled Docker executor.')
    plan = ExperimentPlan.model_validate(state['experiment_plan'])
    bundle = CodeBundle.model_validate(state['code_bundle'])
    validation = state['validation_result']
    attempt_no = int(state.get('retry_count', 0)) + 1
    attempt_id = storage.add_experiment_attempt(
        state['experiment_id'], attempt_no, code_hash=validation.get('code_hash', ''),
    )
    result = await execute_bundle(
        state['experiment_id'], attempt_no, bundle, plan,
        cancel_check=lambda: storage.experiment_cancel_requested(state['experiment_id']),
    )
    storage.finish_experiment_attempt(
        attempt_id,result.status,result.exit_code,result.logs,result.error,
        runtime_seconds=result.runtime_seconds,container_name=result.container_name,
        executor_image=result.executor_image,command=result.command,
        metadata={'output_count':len(result.outputs),'template':bundle.template},
        started_at=result.started_at,completed_at=result.completed_at,
    )
    job = storage.get_experiment_job(state['experiment_id'])
    for item in result.outputs:
        artifact_id = storage.add_experiment_artifact(
            state['experiment_id'], attempt_no, item.get('kind','output'), item['name'], item['relative_path'],
            item['mime_type'], item['size_bytes'], item['sha256'],
        )
        storage.add_experiment_event(state['experiment_id'], job['room_id'], 'experiment.artifact_created', {
            'experiment_id': state['experiment_id'], 'artifact_id': artifact_id,
            'name': item['name'], 'mime_type': item['mime_type'], 'size_bytes': item['size_bytes'],
        })
    errors = list(state.get('errors', []))
    if result.error:
        errors.append(result.error)
    if result.status == 'completed':
        next_step = 'evaluate'
    elif result.status == 'cancelled':
        next_step = 'cancelled'
    elif result.status == 'unavailable':
        next_step = 'failed'
    elif int(state.get('retry_count', 0)) < job['max_retries']:
        next_step = 'debug'
    else:
        next_step = 'failed'
    return _checkpoint(state, 'execution', {
        'execution_result': result.model_dump(), 'artifacts': result.outputs,
        'errors': errors, 'next_step': next_step,
    })


async def debugging_node(state: ResearchState):
    retry_count = int(state.get('retry_count', 0)) + 1
    _stage(state, 'debugging', f'Diagnosing the failed bundle and preparing bounded repair {retry_count}.',
           retry_count=retry_count)
    if not model_available(getattr(settings, 'debug_model_provider', 'auto')):
        return _checkpoint(state, 'debugging', {
            'retry_count': retry_count, 'next_step': 'failed',
            'errors': list(state.get('errors', [])) + ['A repair needs a configured debug model.'],
        })
    old_bundle = CodeBundle.model_validate(state['code_bundle'])
    model = get_model('debug').with_structured_output(CodeBundle)
    actionable={
        'validation_errors':(state.get('validation_result') or {}).get('errors',[]),
        'execution_error':(state.get('execution_result') or {}).get('error'),
        'required_interface':{
            'arguments':['--config','--output-dir'],
            'required_outputs':['metrics.json','run_metadata.json'],
            'expected_artifacts':state['experiment_plan'].get('expected_artifacts',[]),
            'metrics_contract':'JSON object with non-empty verdict',
            'metadata_contract':'completed=true plus exact requested counts',
        },
        'allowed_imports': sorted(ALLOWED_IMPORT_ROOTS),
    }
    repaired = await model.ainvoke(
        'Repair this scientific experiment using the structured diagnostics below. Return a COMPLETE replacement '
        'bundle, not a patch. The replacement MUST be different from the current code, implement both CLI '
        'arguments, write metrics.json and run_metadata.json under output-dir, produce every expected artifact, '
        'and use only allowed imports. Do not use os, sys, xml, zipfile, zlib, or zoneinfo. '
        'Use argparse, json and pathlib for the executor interface. Preserve the scientific '
        'procedure and exact requested counts.\n\n'
        'PLAN:\n' + json.dumps(state['experiment_plan'], indent=2) +
        '\n\nACTIONABLE DIAGNOSTICS:\n' + json.dumps(actionable,indent=2) +
        '\n\nCURRENT CODE:\n' + old_bundle.experiment_py
    )
    old_hash = hashlib.sha256(old_bundle.experiment_py.encode()).hexdigest()
    new_hash = hashlib.sha256(repaired.experiment_py.encode()).hexdigest()
    if old_hash == new_hash:
        # One targeted from-scratch regeneration is allowed inside this bounded repair attempt.
        regeneration_role=('coding' if model_available(getattr(settings,'coding_model_provider','auto')) else 'debug')
        regeneration_model=get_model(regeneration_role).with_structured_output(CodeBundle)
        repaired=await regeneration_model.ainvoke(
            'Regenerate this experiment FROM SCRATCH because a repair model returned unchanged invalid code. '
            'Do not reuse the previous implementation. Obey the exact executor contract and diagnostics below. '
            'Return a complete CodeBundle.\n\nPLAN:\n'+json.dumps(state['experiment_plan'],indent=2)+
            '\n\nCONTRACT AND ERRORS:\n'+json.dumps(actionable,indent=2))
        new_hash=hashlib.sha256(repaired.experiment_py.encode()).hexdigest()
        strategy='targeted_regeneration'
    else:strategy='repair'
    if old_hash == new_hash:
        return _checkpoint(state,'debugging',{
            'retry_count':retry_count,'next_step':'failed',
            'errors':list(state.get('errors',[]))+[
                'Repair and targeted regeneration both returned identical code; bounded repair stopped.'],
        })
    _persist_bundle_version(state,f'{strategy}-{retry_count}',repaired)
    storage.add_experiment_event(state['experiment_id'],_job(state)['room_id'],'experiment.repair_generated',{
        'experiment_id':state['experiment_id'],'stage':'debugging','strategy':strategy,
        'retry_count':retry_count,'old_code_hash':old_hash,'new_code_hash':new_hash,
        'message':f'Generated changed code via {strategy}; returning it to validation.'})
    return _checkpoint(state, 'debugging', {
        'code_bundle': repaired.model_dump(), 'retry_count': retry_count, 'next_step': 'validate',
    })


def _safe_artifact_path(experiment_id: str, relative_path: str):
    root = experiment_root(experiment_id)
    path = (root / relative_path).resolve()
    if root not in path.parents:
        raise RuntimeError('Artifact path escaped its experiment workspace.')
    return path


async def evaluation_node(state: ResearchState):
    _stage(state, 'evaluating', 'Reading measured outputs and checking the result contract.')
    plan=ExperimentPlan.model_validate(state['experiment_plan']);items=state.get('artifacts',[])
    output_items={item['name']:item for item in items if item.get('kind','output')=='output'}
    checks=[]
    def check(name,passed,expected=None,actual=None):
        checks.append({'name':name,'passed':bool(passed),'expected':expected,'actual':actual})
    expected=set(plan.expected_artifacts)|{'metrics.json','run_metadata.json'}
    for name in sorted(expected):check(f'artifact:{name}',name in output_items,'present',name if name in output_items else 'missing')
    for name,item in output_items.items():
        path=_safe_artifact_path(state['experiment_id'],item['relative_path'])
        digest=hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        check(f'hash:{name}',digest==item.get('sha256'),item.get('sha256'),digest)
    def load_json(name):
        item=output_items.get(name)
        if not item:return None
        try:return json.loads(_safe_artifact_path(state['experiment_id'],item['relative_path']).read_text(encoding='utf-8'))
        except (OSError,json.JSONDecodeError):return None
    metrics=load_json('metrics.json');metadata=load_json('run_metadata.json')
    check('metrics_json_object',isinstance(metrics,dict),'JSON object',type(metrics).__name__ if metrics is not None else None)
    check('metrics_verdict',bool(isinstance(metrics,dict) and metrics.get('verdict')),'non-empty verdict',
          metrics.get('verdict') if isinstance(metrics,dict) else None)
    check('run_metadata_completed',bool(isinstance(metadata,dict) and metadata.get('completed') is True),True,
          metadata.get('completed') if isinstance(metadata,dict) else None)
    execution=state.get('execution_result') or {}
    check('process_exit_code',execution.get('exit_code')==0,0,execution.get('exit_code'))
    if plan.experiment_type=='classification_comparison' and isinstance(metadata,dict):
        seed_count=len(plan.random_seeds)
        check('random_seed_count',metadata.get('seed_count')==seed_count,seed_count,metadata.get('seed_count'))
        check('training_run_count',metadata.get('training_runs')==plan.expected_training_runs,
              plan.expected_training_runs,metadata.get('training_runs'))
        check('model_count',metadata.get('model_count')==len(plan.methods),len(plan.methods),metadata.get('model_count'))
        results_item=output_items.get('results.csv');rows=[]
        if results_item:
            with _safe_artifact_path(state['experiment_id'],results_item['relative_path']).open(newline='',encoding='utf-8') as handle:
                rows=list(csv.DictReader(handle))
        check('raw_results_rows',len(rows)==plan.expected_training_runs,plan.expected_training_runs,len(rows))
        check('raw_results_unique_seeds',len({row.get('seed') for row in rows})==seed_count,seed_count,
              len({row.get('seed') for row in rows}))
    if plan.expected_simulations is not None and isinstance(metadata,dict):
        check('simulation_count',metadata.get('total_simulations')==plan.expected_simulations,
              plan.expected_simulations,metadata.get('total_simulations'))
        raw_item=output_items.get('raw_simulation_results.csv');row_count=0
        if raw_item:
            with _safe_artifact_path(state['experiment_id'],raw_item['relative_path']).open(encoding='utf-8') as handle:
                row_count=max(0,sum(1 for _ in handle)-1)
        check('raw_simulation_rows',row_count==plan.expected_simulations,plan.expected_simulations,row_count)
    passed=all(item['passed'] for item in checks)
    verification={'status':'verified' if passed else 'failed','checks':checks,
                  'verified_at':storage.utcnow(),'template':state['code_bundle'].get('template','generated')}
    root=experiment_root(state['experiment_id']);verification_dir=root/'verification';verification_dir.mkdir(parents=True,exist_ok=True)
    verification_path=verification_dir/'verification.json'
    verification_path.write_text(json.dumps(verification,ensure_ascii=False,indent=2),encoding='utf-8')
    content=verification_path.read_bytes()
    artifact_id=storage.add_experiment_artifact(state['experiment_id'],None,'verification','verification.json',
        str(verification_path.relative_to(root)),'application/json',len(content),hashlib.sha256(content).hexdigest())
    job=_job(state)
    storage.add_experiment_event(state['experiment_id'],job['room_id'],'experiment.verification_completed',{
        'experiment_id':state['experiment_id'],'stage':'evaluating','artifact_id':artifact_id,
        'status':verification['status'],'checks':checks,
        'message':('Execution evidence satisfied every required contract.' if passed else
                   'Execution finished but required procedure/output checks failed.')})
    if not passed:
        failures=[f"{item['name']} expected={item['expected']} actual={item['actual']}" for item in checks if not item['passed']]
        raise RuntimeError('Experiment verification failed: '+'; '.join(failures[:8]))
    metrics={**metrics,'verification':verification}
    storage.update_experiment_job(state['experiment_id'], result=metrics)
    return _checkpoint(state, 'evaluation', {'metrics': metrics, 'next_step': 'report'})


async def reporting_node(state: ResearchState):
    _stage(state, 'reporting', 'Writing the reproducible report from measured outputs and recorded provenance.')
    plan = ExperimentPlan.model_validate(state['experiment_plan'])
    metrics = state['metrics']
    sources = state.get('research_sources', [])
    report = {
        'title': plan.title,
        'question': state['user_request'],
        'objective': plan.objective,
        'method': {
            'dataset': plan.dataset.model_dump(), 'methods': plan.methods,
            'metrics': plan.metrics, 'random_seed': plan.random_seed,'random_seeds':plan.random_seeds,
            'expected_training_runs':plan.expected_training_runs,
            'expected_simulations':plan.expected_simulations,'parameters':plan.parameters,
            'evaluation_procedure': plan.evaluation_procedure,
            'reproduction_level': plan.reproduction_level,
        },
        'results': metrics,
        'limitations': plan.limitations,
        'sources': [{'title': item.get('title'), 'url': item.get('url')} for item in sources[:10]],
        'provenance': {
            'experiment_id': state['experiment_id'], 'executor': 'network-disabled Docker',
            'template': state['code_bundle'].get('template', 'generated'),
            'execution':state.get('execution_result',{}),
            'verification':metrics.get('verification',{}),
        },
    }
    root = experiment_root(state['experiment_id'])
    report_dir = root / 'report'
    report_dir.mkdir(parents=True, exist_ok=True)
    report_json = report_dir / 'report.json'
    report_md = report_dir / 'report.md'
    report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    source_lines = '\n'.join(f"- [{s.get('title')}]({s.get('url')})" for s in sources[:10]) or '- No external paper was required for this verified benchmark.'
    report_md.write_text(
        f"# {plan.title}\n\n**Question:** {state['user_request']}\n\n"
        f"## Measured result\n\n{metrics.get('verdict')}\n\n"
        f"```json\n{json.dumps(metrics, indent=2)}\n```\n\n"
        f"## Method\n\nDataset: {plan.dataset.name}. Methods: {', '.join(plan.methods)}. "
        f"Seed: {plan.random_seed}. {plan.evaluation_procedure}\n\n"
        f"## Limitations\n\n" + '\n'.join(f'- {item}' for item in plan.limitations) +
        f"\n\n## Sources\n\n{source_lines}\n",
        encoding='utf-8',
    )
    for path, mime in ((report_json, 'application/json'), (report_md, 'text/markdown')):
        content = path.read_bytes()
        storage.add_experiment_artifact(
            state['experiment_id'], None, 'report', path.name,
            str(path.relative_to(root)), mime, len(content), hashlib.sha256(content).hexdigest(),
        )
    storage.update_experiment_job(
        state['experiment_id'], status='completed', stage='completed', progress=100,
        report=report, result=metrics, completed_at=storage.utcnow(), error=None,
    )
    job = storage.get_experiment_job(state['experiment_id'])
    storage.add_experiment_event(state['experiment_id'], job['room_id'], 'experiment.completed', {
        'experiment_id': state['experiment_id'], 'status': 'completed', 'stage': 'completed',
        'progress': 100, 'message': metrics.get('verdict'), 'results': metrics,
    })
    return _checkpoint(state, 'reporting', {'report': report, 'next_step': 'completed'})


def _route(state: ResearchState):
    return state.get('next_step', 'failed')


builder = StateGraph(ResearchState)
builder.add_node('research', research_node)
builder.add_node('plan', planning_node)
builder.add_node('generate', generation_node)
builder.add_node('validate', validation_node)
builder.add_node('execute', execution_node)
builder.add_node('debug', debugging_node)
builder.add_node('evaluate', evaluation_node)
builder.add_node('report', reporting_node)
builder.set_entry_point('research')
builder.add_edge('research', 'plan')
builder.add_edge('plan', 'generate')
builder.add_edge('generate', 'validate')
builder.add_conditional_edges('validate', _route, {'execute': 'execute', 'debug': 'debug', 'failed': END})
builder.add_conditional_edges('execute', _route, {
    'evaluate': 'evaluate', 'debug': 'debug', 'failed': END, 'cancelled': END,
})
builder.add_conditional_edges('debug', _route, {'validate': 'validate', 'failed': END})
builder.add_edge('evaluate', 'report')
builder.add_edge('report', END)
experiment_graph = builder.compile()


async def run_experiment_graph(job: dict):
    state: ResearchState = {
        'room_id': job['room_id'], 'user_id': job['requested_by'],
        'experiment_id': job['id'], 'user_request': job['request'],
        'intent': job['intent'], 'parent_experiment_id': job.get('parent_experiment_id'),
        'retry_count': int(job.get('retry_count', 0)), 'errors': [],
    }
    try:
        final = await experiment_graph.ainvoke(state, {'recursion_limit': 20})
        current = storage.get_experiment_job(job['id'])
        if current and current['status'] not in ('completed', 'failed', 'cancelled'):
            if final.get('next_step') == 'cancelled' or current['cancel_requested']:
                raise ExperimentCancelled('Experiment cancelled by a collaborator.')
            error = '; '.join(final.get('errors', [])[-4:]) or 'Experiment graph stopped before reporting.'
            raise RuntimeError(error)
        return final
    except ExperimentCancelled as exc:
        storage.update_experiment_job(
            job['id'], status='cancelled', stage='cancelled', completed_at=storage.utcnow(), error=str(exc),
        )
        storage.add_experiment_event(job['id'], job['room_id'], 'experiment.cancelled', {
            'experiment_id': job['id'], 'status': 'cancelled', 'stage': 'cancelled',
            'message': str(exc),
        })
    except Exception as exc:
        message = str(exc)[:1200] or type(exc).__name__
        storage.update_experiment_job(
            job['id'], status='failed', stage='failed', completed_at=storage.utcnow(), error=message,
        )
        storage.add_experiment_event(job['id'], job['room_id'], 'experiment.failed', {
            'experiment_id': job['id'], 'status': 'failed', 'stage': 'failed',
            'message': message, 'error_type': type(exc).__name__,
        })
    return storage.get_experiment_job(job['id'])
