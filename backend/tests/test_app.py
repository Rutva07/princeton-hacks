import os
os.environ['RESEARCH_MODE']='demo'
os.environ['PHOTON_POLLING_ENABLED']='false'
from fastapi.testclient import TestClient
from app.main import app
from app import storage
from app.photon import parse_inbound
from app.experiment import run_experiment

client=TestClient(app)

def test_health():
    with client:
        r=client.get('/api/health')
        assert r.status_code==200 and r.json()['experiment']=='real PyTorch CPU'

def test_create_approve_workflow():
    with client:
        session=storage.create_session('Test researcher')
        client.headers['Authorization']='Bearer '+session['token']
        r=client.post('/api/jobs',json={'prompt':'Improve flow matching generative model efficiency','budget_seconds':60})
        assert r.status_code==201
        id=r.json()['id']
        assert len(id)==10
        detail=client.get('/api/jobs/'+id)
        assert detail.status_code==200
        assert detail.json()['job']['prompt'].startswith('Improve')

def test_reject_without_ready_state():
    storage.init_db()
    session=storage.create_session('Test researcher')
    client.headers['Authorization']='Bearer '+session['token']
    room=storage.create_room('Test research',session['participant']['id'])
    jobid=storage.add_job('A sufficiently long research question',40,room_id=room['id'])
    r=client.post(f'/api/jobs/{jobid}/decision',json={'approve':False})
    assert r.status_code==409

def test_photon_parse():
    good={'id':'abc','status':'received','platform':'whatsapp-business',
          'sentBy':{'kind':'human','handle':'+15555550100'},
          'receivedBy':{'kind':'agent','id':'agent-1'},
          'space':{'kind':'direct','id':'space-1'},
          'content':{'type':'text','format':'plain','text':'Research diffusion!'}}
    assert parse_inbound(good)['space_id']=='space-1'
    assert parse_inbound({**good,'space':{'kind':'group','id':'space-1'}}) is None
    assert parse_inbound({**good,'sentBy':{'kind':'agent','handle':'xx'}}) is None

def test_real_experiment_controls():
    res=run_experiment(max_seconds=60,steps=35)
    assert set(res['results'])=={'baseline','candidate'}
    assert res['steps_per_recipe']==35
    assert len(res['results']['baseline']['generated_samples'])==120
    assert res['results']['baseline']['sample_mmd2']>=-1e-5
