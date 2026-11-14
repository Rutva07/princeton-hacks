import asyncio
import os
from unittest.mock import patch
os.environ['RESEARCH_MODE']='demo'
os.environ['PHOTON_POLLING_ENABLED']='false'
from fastapi.testclient import TestClient
from app.main import app
from app.papers import DEMO_PAPERS

async def stub_search(prompt,limit=8):
    return [{**p,'origin':'demo_catalog','evidence_level':'curated demo synopsis'} for p in DEMO_PAPERS], 'demo_catalog'


def test_full_experiment_with_approval():
    with patch('app.workflow.search_papers',new=stub_search), TestClient(app) as client:
        response=client.post('/api/jobs',json={
            'prompt':'Can we improve flow matching using AdamW and cosine scheduling?',
            'budget_seconds':45
        })
        assert response.status_code==201
        job_id=response.json()['id']
        import time
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            response=client.get(f'/api/jobs/{job_id}')
            if response.json()['job']['status']=='awaiting_approval':break
            time.sleep(.1)
        assert response.json()['job']['status']=='awaiting_approval'
        assert 'papers.json' in response.json()['artifacts']
        assert 'results.json' not in response.json()['artifacts']
        approved=client.post(f'/api/jobs/{job_id}/decision',json={'approve':True})
        assert approved.status_code==200
        deadline=time.monotonic()+55
        while time.monotonic()<deadline:
            snapshot=client.get(f'/api/jobs/{job_id}').json()
            if snapshot['job']['status'] in ('completed','failed'):break
            time.sleep(.15)
        assert snapshot['job']['status']=='completed',snapshot['job']['error']
        res=snapshot['artifacts']['results.json']
        assert res['steps_per_recipe']>0
        assert 'baseline' in res['results'] and 'candidate' in res['results']
        assert 'experiment_report.md' in snapshot['artifacts']
