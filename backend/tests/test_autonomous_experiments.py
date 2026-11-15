import asyncio
import hashlib
import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from app import storage
from app.executor import experiment_root, validate_bundle
from app.experiment_graph import debugging_node, run_experiment_graph
from app.experiment_models import CodeBundle, ExecutionResult
from app.experiment_templates import (classification_bundle, classification_plan,
                                      clt_bundle, clt_plan)
from app.main import app


def auth(token):
    return {'Authorization': f'Bearer {token}'}


def receive_until(socket, event_type, max_events=30):
    for _ in range(max_events):
        event = socket.receive_json()
        if event['type'] == event_type:
            return event
    raise AssertionError(f'{event_type} was not received')


def clear_queued_jobs():
    with storage.connection() as connection:
        connection.execute(
            "UPDATE experiment_jobs SET status='cancelled',stage='cancelled' WHERE status='queued'"
        )


def test_direct_chat_request_queues_autonomous_job_without_approval():
    clear_queued_jobs()
    with TestClient(app) as client:
        session = client.post('/api/session', json={'display_name': 'Autonomous Researcher'}).json()
        room = client.post('/api/rooms', headers=auth(session['token']),
                           json={'title': 'Autonomous lab'}).json()
        with client.websocket_connect(
            f"/ws/rooms/{room['id']}?token={session['token']}"
        ) as socket:
            receive_until(socket, 'message.history')
            socket.send_json({
                'type': 'message.send', 'event_id': 'autonomous-direct-001',
                'payload': {'content': '@chatbot run an experiment comparing logistic regression and random forest'},
            })
            queued = receive_until(socket, 'experiment.queued')
        job = storage.get_experiment_job(queued['payload']['experiment_id'])
        assert job['status'] == 'queued'
        assert job['intent'] == 'direct_execution'
        assert storage.latest_room_job(room['id'], ('awaiting_approval',)) is None


def test_generated_code_policy_rejects_host_and_dynamic_execution():
    plan = classification_plan('compare logistic regression and random forest')
    unsafe = CodeBundle(
        experiment_py=(
            'import argparse\nimport os\n'
            'def main():\n    parser=argparse.ArgumentParser()\n'
            '    parser.add_argument("--output-dir")\n    parser.add_argument("--config")\n'
            '    eval("1+1")\n    print("metrics.json")\n'
        ),
        config={}, requirements=[], readme='Unsafe policy validation fixture.',
    )
    result = validate_bundle(unsafe, plan)
    assert result.valid is False
    assert any('os' in error for error in result.errors)
    assert any('eval' in error for error in result.errors)


def test_prevalidated_clt_template_accepts_safe_warnings_and_exact_contract():
    plan=clt_plan('Investigate the Central Limit Theorem with 10,000 independent sample means from an exponential distribution')
    bundle=clt_bundle(plan)
    result=validate_bundle(bundle,plan)
    assert result.valid is True
    assert plan.expected_simulations==60_000
    assert '--output-dir' in bundle.experiment_py
    assert 'metrics.json' in bundle.experiment_py
    assert 'warnings' in bundle.experiment_py


def test_single_worker_claims_each_queued_job_once():
    storage.init_db()
    clear_queued_jobs()
    session = storage.create_session('Queue Researcher')
    room = storage.create_room('Queue serialization lab', session['participant']['id'])
    first = storage.create_experiment_job(room['id'], session['participant']['id'],
                                          'compare logistic regression and random forest')
    second = storage.create_experiment_job(room['id'], session['participant']['id'],
                                           'compare logistic regression and gradient boosting')
    claimed = storage.claim_next_experiment('test-worker')
    assert claimed['id'] == first['id']
    next_claim = storage.claim_next_experiment('test-worker')
    assert next_claim['id'] == second['id']
    assert storage.claim_next_experiment('test-worker') is None


async def fake_search(_query, limit=10):
    return ([{
        'id': 'paper:test', 'title': 'Verified benchmark context', 'year': 2024,
        'url': 'https://example.org/paper', 'summary': 'A test evidence record.',
        'origin': 'test', 'evidence_level': 'test fixture',
    }], 'test')


async def fake_web_search(_query, limit=5):
    return [], 'not_configured'


async def fake_execute(experiment_id, attempt_no, bundle, plan, cancel_check):
    assert cancel_check() is False
    root = experiment_root(experiment_id)
    output = root / 'attempts' / str(attempt_no) / 'outputs'
    output.mkdir(parents=True, exist_ok=True)
    metrics = {
        'experiment_type': 'classification_comparison',
        'dataset': plan.dataset.model_dump(), 'random_seed': plan.random_seed,
        'methods': [
            {'method': 'Logistic Regression', 'accuracy': .94, 'weighted_f1': .94,
             'roc_auc': .98, 'cv_accuracy_mean': .95, 'cv_accuracy_std': .01},
            {'method': 'Random Forest', 'accuracy': .96, 'weighted_f1': .96,
             'roc_auc': .99, 'cv_accuracy_mean': .96, 'cv_accuracy_std': .02},
        ],
        'best_method': 'Random Forest',
        'verdict': 'Random Forest had the highest held-out weighted F1 in this run.',
        'limitations': plan.limitations,
    }
    metadata={'completed':True,'random_seeds':plan.random_seeds,'seed_count':len(plan.random_seeds),
              'model_count':len(plan.methods),'training_runs':plan.expected_training_runs,
              'expected_training_runs':plan.expected_training_runs}
    files={'metrics.json':json.dumps(metrics),'run_metadata.json':json.dumps(metadata),
           'results.csv':'seed,method\n42,Logistic Regression\n42,Random Forest\n',
           'comparison.png':'plot','roc_curves.png':'plot','confusion_matrices.png':'plot'}
    outputs=[]
    for name,value in files.items():
        path=output/name;path.write_text(value,encoding='utf-8');content=path.read_bytes()
        outputs.append({'kind':'output','name':name,'relative_path':str(path.relative_to(root)),
            'size_bytes':len(content),'sha256':hashlib.sha256(content).hexdigest(),
            'mime_type':'application/json' if name.endswith('.json') else ('text/csv' if name.endswith('.csv') else 'image/png')})
    return ExecutionResult(status='completed', exit_code=0, runtime_seconds=.2,
        logs=['fixture completed'], outputs=outputs,container_name='fixture',executor_image='fixture:latest',
        command=['python','experiment.py'],started_at=storage.utcnow(),completed_at=storage.utcnow())


async def fake_execute_wrong_count(experiment_id,attempt_no,bundle,plan,cancel_check):
    result=await fake_execute(experiment_id,attempt_no,bundle,plan,cancel_check)
    metadata_item=next(item for item in result.outputs if item['name']=='run_metadata.json')
    path=experiment_root(experiment_id)/metadata_item['relative_path']
    metadata=json.loads(path.read_text());metadata['training_runs']=1
    path.write_text(json.dumps(metadata),encoding='utf-8');content=path.read_bytes()
    metadata_item['size_bytes']=len(content);metadata_item['sha256']=hashlib.sha256(content).hexdigest()
    return result


def test_langgraph_researches_executes_evaluates_and_reports_measured_output():
    storage.init_db()
    clear_queued_jobs()
    session = storage.create_session('Graph Researcher')
    room = storage.create_room('Graph execution lab', session['participant']['id'])
    queued = storage.create_experiment_job(
        room['id'], session['participant']['id'],
        'run an experiment comparing logistic regression and random forest',
    )
    job = storage.claim_next_experiment('graph-test-worker')
    assert job['id'] == queued['id']
    with patch('app.experiment_graph.search_papers', new=fake_search), \
         patch('app.experiment_graph.search_web', new=fake_web_search), \
         patch('app.experiment_graph.execute_bundle', new=fake_execute):
        asyncio.run(run_experiment_graph(job))
    completed = storage.get_experiment_detail(job['id'])
    assert completed['status'] == 'completed'
    assert completed['result']['best_method'] == 'Random Forest'
    assert completed['execution_evidence']['execution_verified'] is True
    assert {item['name'] for item in completed['artifacts']} >= {
        'metrics.json', 'report.json', 'report.md'
    }
    stages = [item['payload'].get('stage') for item in completed['events']]
    assert 'researching' in stages
    assert 'running' in stages
    assert 'completed' in stages
    from app.deepagent import answer_with_langchain
    with patch('app.deepagent.model_configured',return_value=False):
        answer=asyncio.run(answer_with_langchain(
            f"Show verified execution evidence for {job['id']}",[],room_id=room['id']))
    assert 'Verification: **verified**' in answer['text']
    assert completed['execution_evidence']['script_path'] in answer['text']
    assert 'Exit code: `0`' in answer['text']


def test_identical_model_repair_triggers_changed_targeted_regeneration():
    storage.init_db();clear_queued_jobs()
    session=storage.create_session('Repair Researcher')
    room=storage.create_room('Repair lab',session['participant']['id'])
    queued=storage.create_experiment_job(room['id'],session['participant']['id'],
        'Central Limit Theorem exponential Monte Carlo simulation')
    job=storage.claim_next_experiment('repair-test-worker')
    plan=clt_plan(job['request']);valid_bundle=clt_bundle(plan)
    invalid_bundle=CodeBundle(experiment_py=(
        'import warnings\nvalue = 1\nprint("invalid generated experiment without interface")\n'*3),
        config=plan.model_dump(),requirements=[],readme='Invalid generated fixture for repair testing.')

    class FakeModel:
        def __init__(self,role):self.role=role
        def with_structured_output(self,_schema):return self
        async def ainvoke(self,_prompt):return invalid_bundle if self.role=='debug' else valid_bundle

    state={'room_id':room['id'],'user_id':session['participant']['id'],'experiment_id':job['id'],
        'user_request':job['request'],'experiment_plan':plan.model_dump(),
        'code_bundle':invalid_bundle.model_dump(),'validation_result':{'errors':['missing contract']},
        'retry_count':0,'errors':['missing contract']}
    with patch('app.experiment_graph.model_available',return_value=True), \
         patch('app.experiment_graph.get_model',side_effect=lambda role:FakeModel(role)):
        updated=asyncio.run(debugging_node(state))
    assert updated['next_step']=='validate'
    assert updated['retry_count']==1
    assert updated['code_bundle']['experiment_py']==valid_bundle.experiment_py
    detail=storage.get_experiment_detail(job['id'])
    assert any(item['name']=='targeted_regeneration-1-experiment.py' for item in detail['artifacts'])


def test_zero_exit_code_cannot_complete_when_procedure_counts_are_wrong():
    storage.init_db();clear_queued_jobs()
    session=storage.create_session('Verification Researcher')
    room=storage.create_room('Verification gate lab',session['participant']['id'])
    queued=storage.create_experiment_job(room['id'],session['participant']['id'],
        'run an experiment comparing logistic regression and random forest')
    job=storage.claim_next_experiment('verification-test-worker')
    with patch('app.experiment_graph.search_papers',new=fake_search), \
         patch('app.experiment_graph.search_web',new=fake_web_search), \
         patch('app.experiment_graph.execute_bundle',new=fake_execute_wrong_count):
        asyncio.run(run_experiment_graph(job))
    failed=storage.get_experiment_detail(job['id'])
    assert failed['status']=='failed'
    assert 'training_run_count' in failed['error']
    assert failed['execution_evidence']['execution_verified'] is False
