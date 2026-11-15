"""Authenticated, replayable WebSocket collaboration for Labthread rooms."""
from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Literal

from fastapi import WebSocket
from pydantic import BaseModel, Field, ValidationError

from . import storage
from .config import settings
from .workflow import approve, research

EventType = Literal[
    'room.join','room.leave','participant.updated','message.created','message.history','typing.started',
    'typing.stopped','presence.updated','agent.started','agent.thinking',
    'agent.tool_started','agent.tool_completed','agent.completed',
    'experiment.proposed','experiment.started','experiment.progress',
    'experiment.queued','experiment.stage_changed','experiment.research_completed',
    'experiment.artifact_created','experiment.validation_failed','experiment.repair_generated',
    'experiment.verification_completed','experiment.cancelled',
    'experiment.completed','experiment.failed','error'
]


class ClientEvent(BaseModel):
    """Events accepted from browsers. Identity and room fields are never trusted."""
    type: Literal['message.send','typing.started','typing.stopped']
    event_id: str = Field(min_length=6,max_length=100)
    room_id: str | None = None
    timestamp: str | None = None
    payload: dict = Field(default_factory=dict)


class ServerEvent(BaseModel):
    event_id: str
    sequence: int = Field(ge=0)
    type: EventType
    room_id: str
    timestamp: str
    payload: dict = Field(default_factory=dict)


class ConnectionManager:
    def __init__(self):
        self.rooms: dict[str,dict[WebSocket,dict]] = defaultdict(dict)
        self.lock=asyncio.Lock()

    async def connect(self, room_id: str, websocket: WebSocket, participant: dict):
        await websocket.accept()
        async with self.lock:
            self.rooms[room_id][websocket]=participant

    async def disconnect(self, room_id: str, websocket: WebSocket):
        async with self.lock:
            participant=self.rooms.get(room_id,{}).pop(websocket,None)
            if not self.rooms.get(room_id):self.rooms.pop(room_id,None)
        return participant

    async def broadcast(self, room_id: str, event: dict):
        dead=[]
        for websocket in list(self.rooms.get(room_id,{})):
            try:await websocket.send_json(event)
            except Exception:dead.append(websocket)
        for websocket in dead:
            await self.disconnect(room_id,websocket)

    def presence(self, room_id: str):
        people={p['id']:p for p in self.rooms.get(room_id,{}).values()}
        return list(people.values())


manager=ConnectionManager()
_room_agent_tasks: dict[str,asyncio.Task] = {}
_room_locks: dict[str,asyncio.Lock] = defaultdict(asyncio.Lock)


def ephemeral_event(room_id: str, event_type: str, payload: dict):
    return ServerEvent(event_id=f'live-{storage.utcnow()}',
        sequence=storage.latest_room_sequence(room_id),type=event_type,room_id=room_id,
        timestamp=storage.utcnow(),payload=payload).model_dump()


async def publish(room_id: str, event_type: str, payload: dict):
    event=ServerEvent.model_validate(storage.add_room_event(room_id,event_type,payload)).model_dump()
    await manager.broadcast(room_id,event)
    return event


async def publish_message(room_id: str, content: str, kind='text', job_id=None):
    message,_=storage.add_room_message(room_id,None,'agent',content,kind=kind,job_id=job_id)
    await publish(room_id,'message.created',{'message':message})
    from .photon_rooms import relay
    await relay(room_id,message)
    return message


def _compare_experiment_results(current, previous):
    if not current or not previous:return None
    if current.get('experiment_type')!='classification_comparison':return None
    old={row.get('method'):row for row in previous.get('methods',[])}
    deltas=[]
    for row in current.get('methods',[]):
        prior=old.get(row.get('method'))
        if not prior:continue
        deltas.append({
            'method':row['method'],
            'accuracy_delta':round(float(row.get('accuracy',0))-float(prior.get('accuracy',0)),6),
            'weighted_f1_delta':round(float(row.get('weighted_f1',0))-float(prior.get('weighted_f1',0)),6),
        })
    return {'previous_seed':previous.get('random_seed'),'current_seed':current.get('random_seed'),
            'metric_deltas':deltas}


def room_snapshot(room_id: str, participant_id: str):
    room=storage.get_room(room_id,participant_id)
    if not room:return None
    from . import research_store
    from .config import settings
    room['agent_mode']=settings.research_mode
    autonomous_experiments=[]
    for job in storage.list_room_experiments(room_id):
        detail=storage.get_experiment_detail(job['id'])
        parent=storage.get_experiment_job(job['parent_experiment_id']) if job.get('parent_experiment_id') else None
        experiment={
            **job,
            'prompt':job['request'],
            'results':job.get('result'),
            'autonomous':True,
            'artifacts':detail.get('artifacts',[]),
            'attempts':detail.get('attempts',[]),
            'events':detail.get('events',[])[-30:],
            'execution_evidence':detail.get('execution_evidence'),
            'parent':({'id':parent['id'],'request':parent['request'],'status':parent['status']}
                      if parent else None),
            'comparison':_compare_experiment_results(job.get('result'),parent.get('result') if parent else None),
        }
        autonomous_experiments.append(experiment)
    room['messages']=storage.get_room_messages(room_id)
    room['autonomous_experiments']=autonomous_experiments
    room['message_jobs']=research_store.message_jobs(room_id,room['messages'])
    room['research_counts'],room['research_revision']=research_store.summary(room_id)
    recent=research_store.page(room_id,'experiments',limit=1)['items']
    candidates=autonomous_experiments+recent
    room['active_experiment']=max(candidates,key=lambda item:item.get('created_at','')) if candidates else None
    room['latest_sequence']=storage.latest_room_sequence(room_id)
    return room


async def send_initial_state(websocket: WebSocket, room_id: str, since: int):
    if since>0:
        replay=[]
        cursor=since
        while True:
            batch=storage.get_room_events(room_id,after=cursor)
            replay.extend(batch)
            if len(batch)<250:break
            cursor=batch[-1]['sequence']
        payload={'events':replay,'replay':True,'after_sequence':since}
    else:
        payload={'messages':storage.get_room_messages(room_id),'replay':False}
    await websocket.send_json(ephemeral_event(room_id,'message.history',payload))


async def announce_presence(room_id: str, participant: dict, joined: bool):
    event_type='room.join' if joined else 'room.leave'
    await manager.broadcast(room_id,ephemeral_event(room_id,event_type,{'participant':participant}))
    await manager.broadcast(room_id,ephemeral_event(room_id,'presence.updated',{
        'online':manager.presence(room_id),'count':len(manager.presence(room_id))}))


async def handle_socket_event(websocket: WebSocket, room_id: str, participant: dict, raw):
    if not storage.is_room_member(room_id,participant['id']):
        await websocket.close(code=4403)
        return
    try:event=ClientEvent.model_validate(raw)
    except ValidationError as exc:
        await websocket.send_json(ephemeral_event(room_id,'error',{'code':'invalid_event','detail':str(exc)}))
        return
    if event.type.startswith('typing.'):
        await manager.broadcast(room_id,ephemeral_event(room_id,event.type,{
            'participant':participant,'client_event_id':event.event_id}))
        return
    content=str(event.payload.get('content','')).strip()
    if not content or len(content)>12000:
        await websocket.send_json(ephemeral_event(room_id,'error',{'code':'invalid_message','detail':'Message must contain 1–12,000 characters.'}))
        return
    # The room and sender always come from the authenticated socket, never payload data.
    message,created=storage.add_room_message(room_id,participant['id'],'human',content,
                                             client_message_id=event.event_id)
    if created:
        await publish(room_id,'message.created',{'message':message})
        maybe_start_agent(room_id,participant,content)
    else:
        await websocket.send_json(ephemeral_event(room_id,'message.created',{
            'message':message,'deduplicated':True}))


def maybe_start_agent(room_id: str, participant: dict, content: str):
    if not re.search(r'(?i)(?:^|\s)@chatbot\b',content):return
    clean=re.sub(r'(?i)(?:^|\s)@chatbot\b[:,]?',' ',content).strip()
    if not clean:
        asyncio.create_task(publish_message(room_id,
            'Mention me with a question, for example: “@chatbot find the key papers on Braess paradox.” You can also ask me to suggest or run an experiment.',
            kind='notice'))
        return
    text=clean.lower()
    run_command=bool(re.search(
        r'\b(run|start|approve|execute|conduct|perform)\b.{0,60}\b(experiment|simulation|study|test)\b'
        r'|\b(reproduce|replicate)\b.{0,80}\b(paper|study|experiment|result)\b'
        r'|\b(rerun|repeat)\b|^run it\.?$',text))
    experiment_requested=(run_command or bool(re.search(
        r'\b(suggest|propose|design|plan|create|recommend|outline)\b.{0,50}\b(experiment|simulation|study|test)\b|\bhow (?:can|could|would) (?:we|i) test\b',text)))
    task=_room_agent_tasks.get(room_id)
    if task and not task.done():
        asyncio.create_task(publish_message(room_id,
            f"I’m already working on this room’s active request. I’ll keep everyone updated here.",job_id=None))
        return
    task=asyncio.create_task(_handle_agent_request(
        room_id,participant,clean,run_command,experiment_requested))
    _room_agent_tasks[room_id]=task


async def _relay_job_events(room_id: str, job_id: str, stop_states: set[str]):
    last=0
    progress=8
    while True:
        for item in storage.get_events(job_id):
            if item['id']<=last:continue
            last=item['id']
            event_type='agent.tool_completed' if item['stage'] in ('research','extraction','design','evaluation') else 'agent.thinking'
            if item['stage'] in ('experiment','evaluation'):
                progress=38 if item['stage']=='experiment' else 88
                await publish(room_id,'experiment.progress',{'job_id':job_id,'progress':progress,
                    'stage':item['stage'],'message':item['message']})
            await publish(room_id,event_type,{'job_id':job_id,'stage':item['stage'],
                'agent':item['agent'],'message':item['message']})
        job=storage.get_job(job_id)
        if not job or job['status'] in stop_states:return job
        await asyncio.sleep(.18)


async def _handle_agent_request(room_id: str, participant: dict, content: str,
                                run_command: bool, experiment_requested: bool):
    async with _room_locks[room_id]:
        if not storage.is_room_member(room_id,participant['id']):return
        if run_command:
            repeat=bool(re.search(r'\b(rerun|repeat|again|new seed|different seed)\b',content,re.I))
            generic=bool(re.fullmatch(
                r'(?:run|start|execute|approve)(?: (?:the|this|that))? (?:experiment|simulation|test)[.!]?|run it[.!]?',
                content.strip(),re.I))
            legacy=storage.latest_room_job(room_id,('awaiting_approval',)) if generic else None
            if legacy:
                ok,state=await approve(legacy['id'],True)
                if not ok:
                    await publish_message(room_id,'That experiment changed state before I could start it. Refresh the room context and try again.',kind='notice')
                    return
                await publish(room_id,'experiment.started',{'job_id':legacy['id'],'status':state,
                    'title':legacy['prompt'],'authorized_by':participant['id']})
                await publish(room_id,'agent.tool_started',{'job_id':legacy['id'],
                    'tool':'execute_simulation','message':'Starting the approved trusted simulation tool.'})
                await publish_message(room_id,'Experiment approved. I’m running the bounded legacy simulation now and will publish the measured results here.',kind='experiment',job_id=legacy['id'])
                await _finish_experiment(room_id,legacy['id'])
                return

            parent=None
            request=content
            if generic or repeat:
                completed=next((item for item in storage.list_room_experiments(room_id)
                                if item['status']=='completed'),None)
                if not completed and generic:
                    await publish_message(room_id,
                        'Tell me what to run—for example, “@chatbot run an experiment comparing logistic regression and random forest.”',
                        kind='notice')
                    return
                if completed:
                    parent=completed['id']
                    request=(f"{content}. Follow up on the previous experiment with the requested variation. "
                             f"Original request: {completed['request']}")
            job=storage.create_experiment_job(
                room_id,participant['id'],request,parent_experiment_id=parent,
                max_retries=settings.experiment_max_retries,
                runtime_limit=settings.experiment_max_runtime,
                memory_mb=settings.experiment_memory_mb,cpu_cores=settings.experiment_cpu_cores,
            )
            await publish(room_id,'experiment.queued',{
                'experiment_id':job['id'],'job_id':job['id'],'status':'queued','stage':'queued',
                'progress':0,'requested_by':participant['id'],
                'message':'Autonomous experiment queued for research, planning, isolated execution, evaluation, and reporting.',
            })
            await publish_message(room_id,
                f"Experiment {job['id']} is queued. I’ll research, plan, generate, validate, run, evaluate, and report it automatically. Everyone in this room will see the live stages.",
                kind='experiment_job',job_id=job['id'])
            return

        active=storage.latest_room_job(room_id,('researching','running','answering'))
        if active:
            await publish_message(room_id,'This room already has an active LangChain run. I’ll finish it before starting another so shared state stays consistent.',kind='notice',job_id=active['id'])
            return

        if not experiment_requested:
            job_id=storage.add_job(content,90,room_id=room_id)
            storage.set_status(job_id,'answering')
            await publish(room_id,'agent.started',{'job_id':job_id,'requested_by':participant['id'],
                'summary':'LangChain research agent started.'})
            await publish(room_id,'agent.tool_started',{'job_id':job_id,
                'tool':'search_research_papers','message':'LangChain is searching OpenAlex for relevant research papers.'})
            try:
                from .deepagent import answer_with_langchain
                context=[{'sender':m.get('sender_name'),'type':m.get('sender_type'),'content':m.get('content')}
                         for m in storage.get_room_messages(room_id,limit=40)]
                answer=await answer_with_langchain(content,context,room_id=room_id)
                papers=answer.get('papers',[])
                web_results=answer.get('web_results',[])
                storage.put_artifact(job_id,'papers.json',papers)
                storage.put_artifact(job_id,'reading_list.json',[
                    {'title':p.get('title'),'url':p.get('url'),'reason':p.get('summary','')[:350],
                     'evidence_level':p.get('evidence_level','OpenAlex metadata/abstract')}
                    for p in papers]+[
                    {'title':item.get('title'),'url':item.get('url'),'reason':(item.get('summary') or item.get('content',''))[:350],
                     'evidence_level':'General web result; independently verify'}
                    for item in web_results])
                storage.put_artifact(job_id,'review.json',answer)
                storage.set_status(job_id,'answered')
                await publish(room_id,'agent.tool_completed',{'job_id':job_id,
                    'tool':'search_research_papers','message':f'Found {len(papers)} research papers.'})
                await publish_message(room_id,answer['text'],kind='research_answer',job_id=job_id)
                await publish(room_id,'agent.completed',{'job_id':job_id,'status':'answered',
                    'mode':answer.get('mode')})
            except Exception as exc:
                storage.set_status(job_id,'failed',str(exc)[:500])
                await publish(room_id,'agent.completed',{'job_id':job_id,'status':'failed'})
                await publish_message(room_id,
                    f'I could not complete that LangChain research search: {type(exc).__name__}. Check the model/search configuration and try again.',kind='notice',job_id=job_id)
            return

        job_id=storage.add_job(content,90,room_id=room_id)
        await publish(room_id,'agent.started',{'job_id':job_id,'requested_by':participant['id'],
            'summary':'Scientific literature search and experiment planning started.'})
        await publish(room_id,'agent.tool_started',{'job_id':job_id,
            'tool':'scientific_literature_search','message':'Searching OpenAlex and the configured academic source pool.'})
        await publish_message(room_id,'I’m searching the literature and shaping a testable, bounded experiment. I’ll share the evidence and ask before using compute.',kind='agent_status',job_id=job_id)
        watcher=asyncio.create_task(_relay_job_events(room_id,job_id,{'awaiting_approval','failed'}))
        await research(job_id)
        job=await watcher
        if job and job['status']=='awaiting_approval':
            artifacts=storage.get_artifacts(job_id)
            papers=artifacts.get('papers.json',[])
            recipes=artifacts.get('pipelines.json',[])
            await publish(room_id,'experiment.proposed',{'job_id':job_id,'title':job['prompt'],
                'status':'awaiting_approval','pipelines':recipes})
            review=artifacts.get('review.json',{})
            synthesis=review.get('text','').strip()
            summary=((synthesis+'\n\n') if synthesis else '')+(
                     f"I found {len(papers)} relevant sources and designed a bounded experiment proposal. "
                     "Reply APPROVE on iMessage, mention “@chatbot run the experiment”, or use Run experiment when ready.")
            await publish_message(room_id,summary,kind='experiment_proposal',job_id=job_id)
            await publish(room_id,'agent.completed',{'job_id':job_id,'status':'awaiting_approval'})
        else:
            await publish(room_id,'agent.completed',{'job_id':job_id,'status':'failed',
                'error':(job or {}).get('error','Research failed')})
            await publish_message(room_id,'I couldn’t complete that research pass. The failure is recorded in the room experiment history.',kind='notice',job_id=job_id)


async def _finish_experiment(room_id: str, job_id: str):
    watcher=asyncio.create_task(_relay_job_events(room_id,job_id,{'completed','failed'}))
    job=await watcher
    if job and job['status']=='completed':
        artifacts=storage.get_artifacts(job_id)
        result=artifacts.get('results.json',{})
        await publish(room_id,'experiment.completed',{'job_id':job_id,'results':result,
            'report':artifacts.get('experiment_report.md','')})
        await publish(room_id,'agent.tool_completed',{'job_id':job_id,
            'tool':'execute_simulation','message':'Simulation and statistical analysis completed.'})
        baseline_result=result.get('results',{}).get('baseline',{})
        candidate_result=result.get('results',{}).get('candidate',{})
        if result.get('experiment_type')=='traffic_simulation':
            measurement=(f"Baseline travel time: {baseline_result.get('average_travel_time')} minutes; "
                         f"with connector: {candidate_result.get('average_travel_time')} minutes.")
        else:
            measurement=(f"Baseline MMD²: {baseline_result.get('sample_mmd2')}; "
                         f"candidate MMD²: {candidate_result.get('sample_mmd2')}.")
        await publish_message(room_id,
            f"Experiment complete. {measurement} {result.get('verdict','Results are available.')} This is a bounded model, not a universal claim.",
            kind='experiment_result',job_id=job_id)
    else:
        await publish(room_id,'agent.tool_completed',{'job_id':job_id,
            'tool':'execute_simulation','message':'Simulation tool exited with a recorded failure.'})
        await publish(room_id,'experiment.failed',{'job_id':job_id,'error':(job or {}).get('error','Experiment failed')})
        await publish_message(room_id,'The experiment failed safely. No partial result is being presented as evidence.',kind='notice',job_id=job_id)
