"""Research stage -> explicit human approval -> bounded experiment stage."""
import asyncio
import json
import logging
from pathlib import Path
from .config import settings
from . import storage
from .papers import search_papers, plan_pipelines, ingest_arxiv_papers
from .experiment import run_experiment
from .photon import enabled, send_message

log=logging.getLogger(__name__)

async def notify(job_id, text, tag):
    job=storage.get_job(job_id)
    if not job or not job['origin_space'] or not enabled():return
    try:
        await send_message(job['origin_space'],job['origin_sender'],text,
                           idempotency=f'researchforge-{tag}-{job_id}',
                           parent_id=job['origin_message'] if tag=='approval' else None)
        storage.emit(job_id,'messaging','Photon 2.0','Reply accepted for asynchronous delivery.')
    except Exception as e:
        storage.emit(job_id,'messaging','Photon 2.0',f'Message failed: {type(e).__name__}: {str(e)[:180]}')

async def research(job_id):
    job=storage.get_job(job_id)
    try:
        storage.emit(job_id,'research','Literature Scout','Searching OpenAlex for relevant papers.')
        papers,source=await search_papers(job['prompt'])
        requested=storage.get_artifacts(job_id).get('requested_papers.json',[])
        if requested:
            supplied=await ingest_arxiv_papers(requested)
            papers=supplied+[p for p in papers if p['id'] not in {q['id'] for q in supplied}]
            storage.emit(job_id,'research','Literature Scout',f'Processed {len(supplied)} user-supplied arXiv links.')
        storage.put_artifact(job_id,'papers.json',papers)
        storage.emit(job_id,'research','Literature Scout',f'Found {len(papers)} papers; source={source}.')
        storage.emit(job_id,'extraction','Method Extractor','Normalizing method summaries and evidence levels.')
        recipes=plan_pipelines(job['prompt'],papers)
        storage.put_artifact(job_id,'pipelines.json',recipes)
        if any(word in job['prompt'].lower() for word in ('traffic','congestion','road','braess')):
            scope=('CausalForge experiment scope: a deterministic four-node traffic assignment model '
                   'testing the Braess-paradox mechanism under fixed demand. It is an explanatory toy '
                   'network, not a city forecast or a paper reproduction. Papers are from ')
        else:
            scope=('CausalForge experiment scope: CPU-based 2D flow matching on synthetic two-moons. '
                   'The optimizer/scheduler composition is a toy proxy, not a reproduction or '
                   'validation of arbitrary retrieved papers. Papers are from ')
        storage.put_artifact(job_id,'readme.md',scope+source+'.')
        storage.emit(job_id,'design','Pipeline Architect','Designed an executable matched-control baseline and candidate.')
        review={'mode':'deterministic_demo','text':'A toy flow-matching training comparison is feasible. '
                'Try Adam versus AdamW + cosine schedule with matched data and updates. '
                'This is an experimental hypothesis, not a proven improvement.',
                'limitations':'No LLM analysis in demo mode. OpenAlex metadata is not full-text reading.',
                'tool_call_batches':0,'verified_delegations':0}
        if settings.research_mode=='deepagents':
            try:
                work_dir=settings.data_dir/'jobs'/job_id
                from .deepagent import analyze_with_deepagents
                review=await analyze_with_deepagents(job_id,job['prompt'],papers,recipes,work_dir)
                for name in ('literature_review.md','pipeline_hypothesis.md','critique.md'):
                    if (work_dir/name).is_file():
                        storage.put_artifact(job_id,name,(work_dir/name).read_text(encoding='utf-8')[:40000])
            except Exception as e:
                storage.emit(job_id,'warning','Deep Agents',f'LLM analysis failed: {type(e).__name__}: {str(e)[:250]}. Transparent fallback used.')
                review['mode']='degraded_demo_fallback'
                review['error']=str(e)[:250]
        storage.put_artifact(job_id,'review.json',review)
        storage.put_artifact(job_id,'reading_list.json',[
           {'title':p['title'],'url':p['url'],'reason':p.get('summary','')[:250],
            'evidence_level':p.get('evidence_level','')} for p in papers])
        storage.emit(job_id,'review','Scientific Critic','Approval required before using compute. Real results do not exist yet.')
        storage.set_status(job_id,'awaiting_approval')
        await notify(job_id,f'CausalForge found {len(papers)} sources and proposed two small '
                     f'training recipes. Reply APPROVE {job_id} to run the real toy experiment '
                     f'(max {job["budget"]} sec), or REJECT {job_id}.', 'approval')
    except Exception as e:
        log.exception('Research error')
        storage.set_status(job_id,'failed',str(e)[:500])
        storage.emit(job_id,'failed','Supervisor',f'Research failed: {e}')

async def approve(job_id, accept=True):
    job=storage.get_job(job_id)
    if not job:return False,'not_found'
    if job['status']!='awaiting_approval':return False,'invalid_state'
    if not accept:
        storage.set_status(job_id,'rejected')
        storage.emit(job_id,'approval','Researcher','Experiment rejected; no compute executed.')
        await notify(job_id,f'CausalForge experiment {job_id} canceled. Research artifacts remain available.','reject')
        return True,'rejected'
    storage.set_status(job_id,'running')
    storage.emit(job_id,'approval','Researcher','Experiment approved. Launching isolated predefined Python runner.')
    asyncio.create_task(execute(job_id))
    return True,'running'

async def execute(job_id):
    job=storage.get_job(job_id)
    try:
        if any(word in job['prompt'].lower() for word in ('traffic','congestion','road','braess')):
            start_message='Solving baseline and intervention traffic equilibria with matched demand.'
        else:
            start_message='Training two real PyTorch 2D flow-matching models on CPU.'
        storage.emit(job_id,'experiment','Experiment Engineer',start_message)
        if settings.research_mode=='deepagents':
            from .deepagent import run_approved_experiment_with_deepagent
            result=await run_approved_experiment_with_deepagent(job_id,job['prompt'],job['budget'])
        else:
            result=await asyncio.to_thread(run_experiment,max_seconds=job['budget'],prompt=job['prompt'])
        storage.put_artifact(job_id,'results.json',result)
        storage.emit(job_id,'evaluation','Scientific Critic',result['verdict'])
        if result.get('experiment_type')=='traffic_simulation':
            measurements=(f'Baseline travel time: {result["results"]["baseline"]["average_travel_time"]} minutes\n\n'
                          f'With connector: {result["results"]["candidate"]["average_travel_time"]} minutes')
            method=f'{result["experiment"]}; {result["iterations"]} traffic-assignment iterations.'
        else:
            measurements=(f'Baseline MMD²: {result["results"]["baseline"]["sample_mmd2"]}\n\n'
                          f'Candidate MMD²: {result["results"]["candidate"]["sample_mmd2"]}')
            method=f'{result["experiment"]}; {result["steps_per_recipe"]} matched steps per recipe.'
        report=(f'# CausalForge Experimental Report\n\nQuestion: {job["prompt"]}\n\n'
                f'Method: {method}\n\n{measurements}\n\nConclusion: {result["verdict"]}\n\n'
                f'**Limitations:** {"; ".join(result["limitations"])}.\n\n'
                f'All experiments are bounded model studies, not universal causal claims.\n')
        storage.put_artifact(job_id,'experiment_report.md',report)
        storage.set_status(job_id,'completed')
        storage.emit(job_id,'completed','Research Advisor','Reproducible results and full research artifacts available.')
        if result.get('experiment_type')=='traffic_simulation':
            metric=(f'baseline travel time={result["results"]["baseline"]["average_travel_time"]}; '
                    f'with connector={result["results"]["candidate"]["average_travel_time"]}.')
        else:
            metric=(f'baseline MMD²={result["results"]["baseline"]["sample_mmd2"]}; '
                    f'candidate MMD²={result["results"]["candidate"]["sample_mmd2"]}.')
        await notify(job_id,f'CausalForge experiment {job_id} completed. {metric} '
                     f'{result["verdict"]} Bounded toy study.', 'results')
    except Exception as e:
        log.exception('Experiment error')
        storage.set_status(job_id,'failed',str(e)[:500])
        storage.emit(job_id,'failed','Experiment Engineer',f'Experiment failed: {type(e).__name__}: {e}')
        await notify(job_id,f'Experiment {job_id} failed: {type(e).__name__}. Inspect dashboard logs.','failed')
