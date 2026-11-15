import os

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from unittest.mock import patch

os.environ['RESEARCH_MODE']='demo'
os.environ['PHOTON_ENABLED']='false'
os.environ['PHOTON_POLLING_ENABLED']='false'

from app.main import app
from app import storage
from app.experiment import run_traffic_experiment
from app.papers import DEMO_PAPERS


def auth(token):
    return {'Authorization':f'Bearer {token}'}


def receive_until(socket,event_type,max_events=20):
    for _ in range(max_events):
        event=socket.receive_json()
        if event['type']==event_type:return event
    raise AssertionError(f'{event_type} was not received')


def make_room(client):
    owner=client.post('/api/session',json={'display_name':'Ada Rivera'}).json()
    collaborator=client.post('/api/session',json={'display_name':'Lin Chen'}).json()
    outsider=client.post('/api/session',json={'display_name':'Sam Outside'}).json()
    room=client.post('/api/rooms',headers=auth(owner['token']),json={'title':'Causal systems lab'}).json()
    joined=client.post(f"/api/rooms/{room['id']}/join",headers=auth(collaborator['token']),
                       json={'join_code':room['join_code']})
    assert joined.status_code==200
    return owner,collaborator,outsider,room


def test_room_permissions_and_membership():
    with TestClient(app) as client:
        owner,collaborator,outsider,room=make_room(client)
        assert client.get(f"/api/rooms/{room['id']}",headers=auth(owner['token'])).status_code==200
        assert client.get(f"/api/rooms/{room['id']}",headers=auth(collaborator['token'])).status_code==200
        assert client.get(f"/api/rooms/{room['id']}",headers=auth(outsider['token'])).status_code==403
        with pytest.raises(WebSocketDisconnect) as denied:
            with client.websocket_connect(f"/ws/rooms/{room['id']}?token={outsider['token']}"):
                pass
        assert denied.value.code==4403


def test_websocket_sync_dedup_and_replay():
    with TestClient(app) as client:
        owner,collaborator,_,room=make_room(client)
        path_a=f"/ws/rooms/{room['id']}?token={owner['token']}"
        path_b=f"/ws/rooms/{room['id']}?token={collaborator['token']}"
        with client.websocket_connect(path_a) as first, client.websocket_connect(path_b) as second:
            receive_until(first,'message.history')
            receive_until(second,'message.history')
            sent={'type':'message.send','event_id':'client-message-001','room_id':'forged-room',
                  'payload':{'content':'I added the control assumptions to our notes.'}}
            first.send_json(sent)
            event_a=receive_until(first,'message.created')
            event_b=receive_until(second,'message.created')
            assert event_a['payload']['message']['id']==event_b['payload']['message']['id']
            assert event_a['room_id']==room['id']
            sequence=event_a['sequence']
            first.send_json(sent)
            duplicate=receive_until(first,'message.created')
            assert duplicate['payload']['deduplicated'] is True
            assert duplicate['payload']['message']['id']==event_a['payload']['message']['id']

        message,_=storage.add_room_message(room['id'],collaborator['participant']['id'],'human',
            'A missed message for replay.',client_message_id='client-message-002')
        missed=storage.add_room_event(room['id'],'message.created',{'message':message})
        with client.websocket_connect(path_a+f'&since={sequence}') as reconnected:
            history=receive_until(reconnected,'message.history')
            replay=history['payload']['events']
            assert any(item['event_id']==missed['event_id'] for item in replay)


def test_real_traffic_simulation_exposes_braess_effect():
    result=run_traffic_experiment()
    assert result['experiment_type']=='traffic_simulation'
    assert result['results']['baseline']['average_travel_time']==65.0
    assert result['results']['candidate']['average_travel_time']==80.0
    assert result['delta_average_travel_time']==15.0


async def stub_search(prompt,limit=8):
    return [{**paper,'origin':'demo_catalog','evidence_level':'curated test source'}
            for paper in DEMO_PAPERS[:3]],'demo_catalog'


def test_natural_language_research_and_experiment_execution():
    with patch('app.workflow.search_papers',new=stub_search), TestClient(app) as client:
        session=client.post('/api/session',json={'display_name':'Traffic Researcher'}).json()
        room=client.post('/api/rooms',headers=auth(session['token']),json={'title':'Mobility science'}).json()
        path=f"/ws/rooms/{room['id']}?token={session['token']}"
        with client.websocket_connect(path) as socket:
            receive_until(socket,'message.history')
            socket.send_json({'type':'message.send','event_id':'plain-question-001',
                'payload':{'content':'Can adding a road increase congestion?'}})
            receive_until(socket,'message.created')
            assert client.get(f"/api/rooms/{room['id']}/experiments",
                headers=auth(session['token'])).json()['items']==[]
            socket.send_json({'type':'message.send','event_id':'traffic-question-001',
                'payload':{'content':'@chatbot suggest an experiment: can adding a road increase congestion?'}})
            proposed=receive_until(socket,'experiment.proposed',max_events=60)
            assert proposed['payload']['status']=='awaiting_approval'
            socket.send_json({'type':'message.send','event_id':'traffic-run-001',
                'payload':{'content':'@chatbot run the experiment.'}})
            completed=receive_until(socket,'experiment.completed',max_events=80)
            result=completed['payload']['results']
            assert result['experiment_type']=='traffic_simulation'
            assert result['results']['candidate']['average_travel_time'] > result['results']['baseline']['average_travel_time']


async def stub_langchain_answer(question,room_context,room_id=None):
    return {'text':'The strongest starting point is [A key paper](https://example.org/paper).',
            'papers':[{'id':'paper:1','title':'A key paper','url':'https://example.org/paper',
                       'summary':'Relevant abstract evidence.','evidence_level':'metadata/abstract'}],
            'web_results':[],'mode':'langchain_deep_agent','source':'test'}


def test_chatbot_mention_answers_and_persists_sources_without_experiment():
    with patch('app.deepagent.answer_with_langchain',new=stub_langchain_answer), TestClient(app) as client:
        session=client.post('/api/session',json={'display_name':'Paper Researcher'}).json()
        room=client.post('/api/rooms',headers=auth(session['token']),json={'title':'Paper review'}).json()
        path=f"/ws/rooms/{room['id']}?token={session['token']}"
        with client.websocket_connect(path) as socket:
            receive_until(socket,'message.history')
            socket.send_json({'type':'message.send','event_id':'paper-question-001',
                'payload':{'content':'@chatbot find the key papers on causal representation learning'}})
            completed=receive_until(socket,'agent.completed',max_events=50)
            assert completed['payload']['status']=='answered'
        snapshot=client.get(f"/api/rooms/{room['id']}",headers=auth(session['token'])).json()
        assert snapshot['research_counts']['experiments']==0
        sources=client.get(f"/api/rooms/{room['id']}/sources",headers=auth(session['token'])).json()
        assert sources['items'][0]['url']=='https://example.org/paper'
        assert any(message['kind']=='research_answer' for message in snapshot['messages'])


def collect_research(client,room_id,token,kind,limit=7):
    items=[]
    cursor=None
    while True:
        params={'limit':limit}
        if cursor:params['cursor']=cursor
        response=client.get(f'/api/rooms/{room_id}/{kind}',headers=auth(token),params=params)
        assert response.status_code==200,response.text
        page=response.json()
        assert len(page['items'])<=limit
        items.extend(page['items'])
        if not page['has_more']:
            assert page['next_cursor'] is None
            return items
        assert page['next_cursor'] and page['next_cursor']!=cursor
        cursor=page['next_cursor']


def test_research_pages_include_all_jobs_without_limit_and_stay_room_scoped():
    with TestClient(app) as client:
        owner,_,_,room=make_room(client)
        other_room=client.post('/api/rooms',headers=auth(owner['token']),
            json={'title':'Separate research room'}).json()
        expected_urls=set()
        job_ids=[]
        for index in range(31):
            job_id=storage.add_job(f'Research question {index}',90,room_id=room['id'])
            job_ids.append(job_id)
            sources=[{'title':f'Paper {index}-{number}',
                      'url':f'https://example.org/paper/{index}/{number}'}
                     for number in range(2)]
            expected_urls.update(source['url'] for source in sources)
            sources.append({'title':f'Shared paper from job {index}',
                            'url':'https://example.org/shared'})
            storage.put_artifact(job_id,'reading_list.json',sources)
            storage.put_artifact(job_id,'pipelines.json',[
                {'id':f'recipe-{number}','name':f'Hypothesis {index}-{number}','rationale':'Test it'}
                for number in range(2)])
            storage.put_artifact(job_id,'results.json',{
                'verdict':f'Discovery {index}','experiment_type':'traffic_simulation'})
            storage.set_status(job_id,'completed')

        other_job=storage.add_job('Other room research',90,room_id=other_room['id'])
        storage.put_artifact(other_job,'reading_list.json',[
            {'title':'Other room paper','url':'https://example.org/other-room'}])
        expected_urls.add('https://example.org/shared')

        response=client.get(f"/api/rooms/{room['id']}",headers=auth(owner['token']))
        assert response.status_code==200
        snapshot=response.json()
        assert snapshot['research_counts']=={
            'sources':63,'experiments':31,'hypotheses':62,'discoveries':31}
        assert not any(kind in snapshot for kind in snapshot['research_counts'])
        sources=collect_research(client,room['id'],owner['token'],'sources')
        assert len(sources)==63
        assert {source['url'] for source in sources}==expected_urls
        shared=next(source for source in sources
                    if source['url']=='https://example.org/shared')
        assert shared['title']=='Shared paper from job 30'
        experiments=collect_research(client,room['id'],owner['token'],'experiments')
        assert [job['id'] for job in experiments]==list(reversed(job_ids))
        assert all(job['results'] for job in experiments)
        hypotheses=collect_research(client,room['id'],owner['token'],'hypotheses')
        assert len({item['id'] for item in hypotheses})==62
        assert all(item['status']=='tested' for item in hypotheses)
        discoveries=collect_research(client,room['id'],owner['token'],'discoveries')
        assert {item['job_id'] for item in discoveries}==set(job_ids)


def test_research_cursor_validation_permissions_and_equal_timestamps():
    with TestClient(app) as client:
        owner,_,outsider,room=make_room(client)
        with patch('app.storage.utcnow',return_value='2026-01-01T00:00:00+00:00'):
            ids=[storage.add_job('Same timestamp experiment',90,room_id=room['id']) for _ in range(9)]
        items=collect_research(client,room['id'],owner['token'],'experiments',limit=2)
        assert [item['id'] for item in items]==sorted(ids,reverse=True)
        path=f"/api/rooms/{room['id']}/experiments"
        assert client.get(path).status_code==401
        assert client.get(path,headers=auth(outsider['token'])).status_code==403
        assert client.get(path,headers=auth(owner['token']),params={'limit':0}).status_code==422
        assert client.get(path,headers=auth(owner['token']),params={'limit':101}).status_code==422
        assert client.get(path,headers=auth(owner['token']),params={'cursor':'not-a-cursor'}).status_code==400
        cursor=client.get(path,headers=auth(owner['token']),params={'limit':2}).json()['next_cursor']
        assert client.get(f"/api/rooms/{room['id']}/sources",headers=auth(owner['token']),
            params={'cursor':cursor}).status_code==400
        other=client.post('/api/rooms',headers=auth(owner['token']),json={'title':'Other room'}).json()
        assert client.get(f"/api/rooms/{other['id']}/experiments",headers=auth(owner['token']),
            params={'cursor':cursor}).status_code==400
        assert collect_research(client,other['id'],owner['token'],'sources')==[]


def test_research_artifact_replacement_and_chat_cards():
    with TestClient(app) as client:
        owner,_,_,room=make_room(client)
        older=storage.add_job('Older research',90,room_id=room['id'])
        newer=storage.add_job('Newer research',90,room_id=room['id'])
        for job_id,title in [(older,'Older title'),(newer,'Newer title')]:
            storage.put_artifact(job_id,'reading_list.json',[{'url':'https://example.org/shared','title':title}])
        path=f"/api/rooms/{room['id']}/sources"
        assert client.get(path,headers=auth(owner['token'])).json()['items'][0]['title']=='Newer title'
        storage.put_artifact(newer,'reading_list.json',[])
        assert client.get(path,headers=auth(owner['token'])).json()['items'][0]['title']=='Older title'
        storage.put_artifact(older,'reading_list.json',[])
        assert client.get(path,headers=auth(owner['token'])).json()['items']==[]
        storage.put_artifact(older,'pipelines.json',[{'name':'Old hypothesis'}])
        storage.put_artifact(older,'pipelines.json',[{'name':'Replacement hypothesis'}])
        hypotheses=collect_research(client,room['id'],owner['token'],'hypotheses')
        assert [item['title'] for item in hypotheses]==['Replacement hypothesis']
        storage.put_artifact(older,'results.json',{'verdict':'Old experiment still visible'})
        storage.add_room_message(room['id'],None,'agent','An older experiment result',
            kind='experiment_result',job_id=older)
        # Snapshot assembly must not load every artifact for every research job.
        with patch('app.storage.get_artifacts',side_effect=AssertionError('Full artifact scan')):
            snapshot=client.get(f"/api/rooms/{room['id']}",headers=auth(owner['token'])).json()
        assert snapshot['message_jobs'][older]['results']['verdict']=='Old experiment still visible'


def test_research_index_backfills_old_artifacts_once(tmp_path,monkeypatch):
    from dataclasses import replace
    monkeypatch.setattr(storage,'DB',tmp_path/'legacy.sqlite3')
    monkeypatch.setattr(storage,'settings',replace(storage.settings,data_dir=tmp_path))
    with TestClient(app) as client:
        owner,_,_,room=make_room(client)
        job_id=storage.add_job('Legacy research',90,room_id=room['id'])
        papers=[{'title':f'Paper {n}','url':f'https://example.org/legacy/{n}'} for n in range(18)]
        web=[{'title':f'Web {n}','url':f'https://example.org/web/{n}'} for n in range(9)]
        storage.put_artifact(job_id,'papers.json',papers)
        storage.put_artifact(job_id,'review.json',{'web_results':web})
        storage.put_artifact(job_id,'reading_list.json',papers[:8])
        storage.put_artifact(job_id,'pipelines.json',[{'name':'Legacy hypothesis'}])
        storage.put_artifact(job_id,'results.json',{'verdict':'Legacy discovery'})
        # Simulate a database that predates the research index.
        with storage.connection() as connection:
            connection.execute('DELETE FROM room_research')
            connection.execute('DELETE FROM research_source_uses')
            connection.execute("DELETE FROM meta WHERE key='research_index_v1'")
        storage.init_db()
        sources=collect_research(client,room['id'],owner['token'],'sources')
        assert {item['url'] for item in sources}=={item['url'] for item in papers+web}
        assert len(collect_research(client,room['id'],owner['token'],'hypotheses'))==1
        assert len(collect_research(client,room['id'],owner['token'],'discoveries'))==1
        with patch('app.research_store.index_artifact',side_effect=AssertionError('Repeated backfill')):
            storage.init_db()


def test_chatbot_reading_list_saves_every_returned_source():
    async def answer_with_many_sources(question,room_context,room_id=None):
        return {'text':'Research complete.',
            'papers':[{'title':f'Paper {n}','url':f'https://example.org/paper/{n}'} for n in range(18)],
            'web_results':[{'title':f'Web {n}','url':f'https://example.org/web/{n}'} for n in range(9)]}

    with patch('app.deepagent.answer_with_langchain',new=answer_with_many_sources), TestClient(app) as client:
        owner,_,_,room=make_room(client)
        with client.websocket_connect(f"/ws/rooms/{room['id']}?token={owner['token']}") as socket:
            receive_until(socket,'message.history')
            socket.send_json({'type':'message.send','event_id':'many-source-question',
                'payload':{'content':'@chatbot find papers and web evidence'}})
            completed=receive_until(socket,'agent.completed',max_events=50)
            assert completed['payload']['status']=='answered'
            job_id=completed['payload']['job_id']
        assert len(storage.get_artifacts(job_id)['reading_list.json'])==27
        assert len(collect_research(client,room['id'],owner['token'],'sources'))==27


def test_experiment_reading_list_saves_every_returned_paper():
    async def many_papers(prompt):
        return [{'id':f'paper-{n}','title':f'Paper {n}',
                 'url':f'https://example.org/experiment/{n}'} for n in range(18)],'test'

    from app.workflow import research
    import asyncio
    with patch('app.workflow.search_papers',new=many_papers), TestClient(app) as client:
        owner,_,_,room=make_room(client)
        job_id=storage.add_job('Can adding a road increase congestion?',90,room_id=room['id'])
        asyncio.run(research(job_id))
        assert storage.get_job(job_id)['status']=='awaiting_approval'
        assert len(storage.get_artifacts(job_id)['reading_list.json'])==18
        assert len(collect_research(client,room['id'],owner['token'],'sources'))==18
