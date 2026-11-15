"""CausalForge REST API, authenticated WebSockets, and optional Photon adapter."""
import asyncio
import re
from typing import Literal
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from .config import settings
from . import storage, research_store, source_graph
from .workflow import research, approve
from .photon import enabled, polling_loop, send_message
from .realtime import (announce_presence, handle_socket_event, manager,
                       room_snapshot, send_initial_state)

class CreateJob(BaseModel):
    prompt: str = Field(min_length=12,max_length=2000)
    budget_seconds: int = Field(default=90,ge=30,le=240)
    paper_urls: list[str] = Field(default_factory=list,max_length=5)

class Decision(BaseModel):
    approve: bool

class SessionRequest(BaseModel):
    display_name: str = Field(min_length=2,max_length=48)

class RoomRequest(BaseModel):
    title: str = Field(min_length=3,max_length=80)

class JoinRoomRequest(BaseModel):
    join_code: str = Field(min_length=6,max_length=12)

def bearer_token(authorization: str | None = Header(default=None)):
    if not authorization or not authorization.lower().startswith('bearer '):
        raise HTTPException(401,'A bearer session is required')
    participant=storage.authenticate(authorization.split(' ',1)[1].strip())
    if not participant:raise HTTPException(401,'Session is invalid or expired')
    return participant

async def inbound_message(message):
    from .photon_rooms import handle
    if await handle(message):
        return
    text=message['text']
    command=re.fullmatch(r'\s*(APPROVE|REJECT)(?:\s+([a-f0-9]{10}))?\s*',text,re.I)
    if command:
        if command.group(2):
            job=storage.get_job(command.group(2).lower())
        else:
            with storage.connection() as db:
                row=db.execute("SELECT * FROM jobs WHERE origin_space=? AND status='awaiting_approval' ORDER BY created_at DESC LIMIT 1",(message['space_id'],)).fetchone()
            job=dict(row) if row else None
        # Only accept approval from the same Photon Space where job originated.
        if not job or job['origin_space']!=message['space_id']:
            await send_message(message['space_id'],message['sender_id'],
                'Job ID not found in this conversation.',f'researchforge-unknown-{message["id"]}')
            return
        ok,_=await approve(job['id'],command.group(1).upper()=='APPROVE')
        if not ok:
            await send_message(message['space_id'],message['sender_id'],
                f'Experiment {job["id"]} is no longer awaiting approval.',
                f'researchforge-decision-{message["id"]}')
    elif 12<=len(text)<=2000 and not re.match(r'^\s*(APPROVE|REJECT)\b',text,re.I):
        origin={'space_id':message['space_id'],'sender_id':message['sender_id'],'message_id':message['id']}
        job_id=storage.add_job(text,90,origin=origin)
        asyncio.create_task(research(job_id))
    else:
        await send_message(message['space_id'],message['sender_id'],
            'Send a research question (12–2000 characters), APPROVE <job-id>, or REJECT <job-id>.',
            f'researchforge-help-{message["id"]}')

@asynccontextmanager
async def lifespan(app):
    storage.init_db()
    task=asyncio.create_task(polling_loop(inbound_message)) if (enabled() and settings.photon_poll_enabled) else None
    yield
    if task:
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass

app=FastAPI(title='CausalForge',description='Collaborative scientific discovery with WebSockets and Deep Agents',version='0.2.0',lifespan=lifespan)
app.add_middleware(CORSMiddleware,allow_origins=list(settings.cors_origins),allow_methods=['GET','POST','PATCH','DELETE'],allow_headers=['Content-Type','Authorization'])

from .room_admin import install
install(app, bearer_token)

@app.get('/api/health')
async def health():
    return {'ok':True,'research_mode':settings.research_mode,
            'photon_configured':enabled(),'photon_polling':enabled() and settings.photon_poll_enabled,
            'photon_groups_enabled':settings.photon_groups,'experiment':'real PyTorch CPU',
            'transport':'native WebSockets','websocket_endpoint':'/ws/rooms/{room_id}'}

@app.post('/api/session',status_code=201)
async def create_session(data:SessionRequest):
    return storage.create_session(data.display_name)

@app.get('/api/me')
async def me(participant=Depends(bearer_token)):
    return participant

@app.get('/api/rooms')
async def rooms(participant=Depends(bearer_token)):
    return storage.list_rooms(participant['id'])

@app.post('/api/rooms',status_code=201)
async def create_room(data:RoomRequest,participant=Depends(bearer_token)):
    room=storage.create_room(data.title,participant['id'])
    message,_=storage.add_room_message(room['id'],None,'agent',
        'Welcome to the room. Mention @chatbot when you want the LangChain research agent—for example, “@chatbot find the key papers on this question” or “@chatbot suggest an experiment.”',kind='welcome')
    storage.add_room_event(room['id'],'message.created',{'message':message})
    return room

@app.post('/api/rooms/{room_id}/join')
async def join_room(room_id:str,data:JoinRoomRequest,participant=Depends(bearer_token)):
    room=storage.join_room(room_id,participant['id'],data.join_code)
    if not room:raise HTTPException(404,'Room or invitation code not found')
    return room

@app.get('/api/rooms/{room_id}')
async def room_detail(room_id:str,participant=Depends(bearer_token)):
    snapshot=room_snapshot(room_id,participant['id'])
    if not snapshot:raise HTTPException(403,'Room membership required')
    return snapshot

@app.get('/api/rooms/{room_id}/source-graph')
async def room_source_graph(room_id:str,limit:int=Query(default=8,ge=1,le=25),
                            cursor:str | None=Query(default=None,max_length=4096),
                            participant=Depends(bearer_token)):
    if not storage.is_room_member(room_id,participant['id']):
        raise HTTPException(403,'Room membership required')
    try:
        return await source_graph.page(room_id,limit,cursor)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc

@app.get('/api/rooms/{room_id}/{kind}')
async def room_research(room_id:str,kind:Literal['sources','experiments','hypotheses','discoveries'],
                        limit:int=Query(default=20,ge=1,le=100),
                        cursor:str | None=Query(default=None,max_length=4096),
                        participant=Depends(bearer_token)):
    if not storage.is_room_member(room_id,participant['id']):
        raise HTTPException(403,'Room membership required')
    try:
        return research_store.page(room_id,kind,limit,cursor)
    except ValueError as exc:
        raise HTTPException(400,str(exc)) from exc

@app.websocket('/ws/rooms/{room_id}')
async def room_socket(websocket:WebSocket,room_id:str,token:str='',since:int=0):
    participant=storage.authenticate(token)
    if not participant:
        await websocket.close(code=4401,reason='Invalid or expired session')
        return
    if not storage.is_room_member(room_id,participant['id']):
        await websocket.close(code=4403,reason='Room membership required')
        return
    await manager.connect(room_id,websocket,participant)
    await send_initial_state(websocket,room_id,since)
    await announce_presence(room_id,participant,True)
    try:
        while True:
            raw=await websocket.receive_json()
            await handle_socket_event(websocket,room_id,participant,raw)
    except WebSocketDisconnect:
        pass
    finally:
        left=await manager.disconnect(room_id,websocket)
        if left:await announce_presence(room_id,left,False)

@app.post('/api/jobs',status_code=201)
async def create_job(data:CreateJob,participant=Depends(bearer_token)):
    room=storage.create_room(data.prompt[:80],participant['id'])
    job_id=storage.add_job(data.prompt.strip(),data.budget_seconds,room_id=room['id'])
    storage.put_artifact(job_id,"requested_papers.json",data.paper_urls)
    asyncio.create_task(research(job_id))
    return {'id':job_id,'status':'researching'}

@app.get('/api/jobs')
async def jobs(participant=Depends(bearer_token)):
    return [job for job in storage.list_jobs() if storage.is_room_member(job['room_id'],participant['id'])]

@app.get('/api/jobs/{job_id}')
async def job_detail(job_id:str,participant=Depends(bearer_token)):
    job=storage.get_job(job_id)
    if not job or not storage.is_room_member(job['room_id'],participant['id']):raise HTTPException(404,'Job not found')
    return {'job':job,'events':storage.get_events(job_id),'artifacts':storage.get_artifacts(job_id)}

@app.post('/api/jobs/{job_id}/decision')
async def decide(job_id:str,decision:Decision,participant=Depends(bearer_token)):
    job=storage.get_job(job_id)
    if not job or not storage.is_room_member(job['room_id'],participant['id']):raise HTTPException(404,'Job not found')
    ok,state=await approve(job_id,decision.approve)
    if not ok:raise HTTPException(409,'Experiment is not awaiting approval')
    return {'id':job_id,'status':state}

@app.get('/api/integration')
async def integration():
    return {'photon_version':'Beta 2.0','photon_transport':'documented polling API',
            'photon_configured':enabled(),'photon_polling_enabled':settings.photon_poll_enabled,
            'group_support':'Do not assume ordinary WhatsApp group support; depends on Photon/Meta.',
            'agents_mode':settings.research_mode,
            'agents_available':bool(settings.openrouter_api_key or settings.openai_api_key),
            'chatbot_framework':'LangChain Deep Agents','chatbot_mention':'@chatbot',
            'academic_search':'OpenAlex','general_web_search_configured':bool(settings.tavily_api_key),
            'primary_transport':'native_websocket','photon_optional':True}
