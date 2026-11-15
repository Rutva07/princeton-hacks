import asyncio
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
from app.main import app
from app import storage, photon_rooms


def make(client, name='Research owner'):
    session=client.post('/api/session',json={'display_name':name}).json()
    return session, {'Authorization':'Bearer '+session['token']}


def test_room_lifecycle_profile_reports_and_access():
    with TestClient(app) as client:
        owner, headers=make(client)
        outsider, other=make(client,'Outside researcher')
        room=client.post('/api/rooms',headers=headers,json={'title':'Lifecycle room'}).json()
        rid=room['id']
        job=storage.add_job('Traffic experiment test question',30,room_id=rid)
        storage.set_status(job,'completed')
        storage.put_artifact(job,'experiment_report.md','# Measured result\n65 to 80')
        assert client.get('/api/jobs').status_code==401
        assert client.get('/api/jobs/'+job,headers=other).status_code==404
        path=f'/api/rooms/{rid}'
        assert client.get(path+'/reports/'+job,headers=other).status_code==404
        report=client.get(path+'/reports/'+job,headers=headers)
        assert report.status_code==200 and '65 to 80' in report.text
        assert client.patch('/api/me',headers=headers,json={'display_name':'Updated Name'}).json()['display_name']=='Updated Name'
        assert client.delete(path,headers=other).status_code==403
        storage.set_status(job,'running')
        assert client.post(path+'/archive',headers=headers).status_code==409
        assert client.delete(path,headers=headers).status_code==409
        storage.set_status(job,'completed')
        assert client.post(path+'/archive',headers=headers).status_code==200
        assert not storage.is_room_member(rid,owner['participant']['id'])
        assert client.post(path+'/restore',headers=other).status_code==403
        assert client.post(path+'/restore',headers=headers).status_code==200
        assert storage.is_room_member(rid,owner['participant']['id'])
        assert client.delete(path,headers=headers).status_code==200
        assert storage.get_job(job) is None
        assert storage.get_artifacts(job)=={}
        assert client.post('/api/logout',headers=headers).status_code==200
        assert client.get('/api/me',headers=headers).status_code==401


def test_three_phones_join_one_room_and_receive_shared_result():
    with TestClient(app) as client:
        _, headers=make(client)
        room=client.post('/api/rooms',headers=headers,json={'title':'Shared phone room'}).json()
        async def scenario():
            with patch('app.photon_rooms.send_message',new_callable=AsyncMock) as send,                  patch('app.photon_rooms.enabled',return_value=True),                  patch('app.realtime.maybe_start_agent') as start:
                for index in range(3):
                    assert await photon_rooms.handle({'id':f'join-{index}','space_id':f'test-space-{index}',
                        'sender_id':f'agent-{index}','text':f"JOIN {room['id']} {room['join_code']}"})
                send.reset_mock()
                await photon_rooms.relay(room['id'],{'id':'result1','kind':'experiment_result','content':'Measured result 65 to 80'})
                assert send.await_count==3
                assert {call.args[0] for call in send.await_args_list}=={f'test-space-{i}' for i in range(3)}
                msg={'id':'question-1','space_id':'test-space-0','sender_id':'agent-0','text':'Propose a traffic experiment'}
                await photon_rooms.handle({**msg, 'id': 'name-1', 'text': 'NAME\tTest Teammate'})
                assert 'Your room name is now Test Teammate.' in send.await_args.args[2]
                await photon_rooms.handle(msg)
                await photon_rooms.handle(msg)
                assert start.call_count==1
                assert start.call_args.args[1]['display_name']=='Test Teammate'
                assert start.call_args.args[0]==room['id']
                job=storage.add_job('Traffic experiment test question',30,room_id=room['id'])
                storage.set_status(job,'awaiting_approval')
                await photon_rooms.handle({**msg,'id':'reject-1','text':'REJECT'})
                assert storage.get_job(job)['status']=='rejected'
                await photon_rooms.handle({**msg,'id':'leave-1','text':'LEAVE'})
                send.reset_mock()
                await photon_rooms.relay(room['id'],{'id':'result2','kind':'experiment_result','content':'Second result'})
                assert send.await_count==2
        asyncio.run(scenario())
        client.delete('/api/rooms/'+room['id'],headers=headers)
