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
            assert storage.list_room_jobs(room['id'])==[]
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


async def stub_langchain_answer(question,room_context):
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
        assert snapshot['experiments']==[]
        assert snapshot['sources'][0]['url']=='https://example.org/paper'
        assert any(message['kind']=='research_answer' for message in snapshot['messages'])

