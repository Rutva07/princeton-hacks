"""SQLite job/event store and filesystem-backed research artifacts."""
import json
import hashlib
import secrets
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .config import settings

_lock = threading.RLock()
DB = settings.data_dir / 'researchforge.sqlite3'

def utcnow():
    return datetime.now(timezone.utc).isoformat()

def connection():
    con = sqlite3.connect(DB, timeout=20, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    return con

def init_db():
    with _lock, connection() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS jobs (
           id TEXT PRIMARY KEY, prompt TEXT NOT NULL, status TEXT NOT NULL,
           mode TEXT NOT NULL, budget INTEGER NOT NULL, created_at TEXT NOT NULL,
           updated_at TEXT NOT NULL, origin_space TEXT, origin_sender TEXT,
           origin_message TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS events (
           id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL,
           stage TEXT NOT NULL, agent TEXT NOT NULL, message TEXT NOT NULL,
           created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS artifacts (
           job_id TEXT NOT NULL, name TEXT NOT NULL, content TEXT NOT NULL,
           updated_at TEXT NOT NULL, PRIMARY KEY(job_id,name));
        CREATE TABLE IF NOT EXISTS processed_messages (
           message_id TEXT PRIMARY KEY, processed_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS participants (
           id TEXT PRIMARY KEY, display_name TEXT NOT NULL, color TEXT NOT NULL,
           created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS api_sessions (
           token_hash TEXT PRIMARY KEY, participant_id TEXT NOT NULL,
           created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS rooms (
           id TEXT PRIMARY KEY, title TEXT NOT NULL, created_by TEXT NOT NULL,
           join_code TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS room_members (
           room_id TEXT NOT NULL, participant_id TEXT NOT NULL, role TEXT NOT NULL,
           joined_at TEXT NOT NULL, PRIMARY KEY(room_id,participant_id));
        CREATE TABLE IF NOT EXISTS room_messages (
           id TEXT PRIMARY KEY, room_id TEXT NOT NULL, sender_id TEXT,
           sender_type TEXT NOT NULL, content TEXT NOT NULL, kind TEXT NOT NULL,
           client_message_id TEXT, job_id TEXT, created_at TEXT NOT NULL,
           UNIQUE(room_id,sender_id,client_message_id));
        CREATE TABLE IF NOT EXISTS room_events (
           seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
           room_id TEXT NOT NULL, event_type TEXT NOT NULL, payload TEXT NOT NULL,
           created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_room_events_room_seq ON room_events(room_id,seq);
        CREATE INDEX IF NOT EXISTS idx_room_messages_room_time ON room_messages(room_id,created_at);
        CREATE TABLE IF NOT EXISTS experiment_jobs (
           id TEXT PRIMARY KEY, room_id TEXT NOT NULL, requested_by TEXT NOT NULL,
           request TEXT NOT NULL, intent TEXT NOT NULL, status TEXT NOT NULL,
           stage TEXT NOT NULL, progress INTEGER NOT NULL DEFAULT 0,
           plan TEXT, result TEXT, report TEXT, parent_experiment_id TEXT,
           cancel_requested INTEGER NOT NULL DEFAULT 0, retry_count INTEGER NOT NULL DEFAULT 0,
           max_retries INTEGER NOT NULL, runtime_limit INTEGER NOT NULL,
           memory_mb INTEGER NOT NULL, cpu_cores REAL NOT NULL,
           worker_id TEXT, heartbeat_at TEXT, created_at TEXT NOT NULL,
           updated_at TEXT NOT NULL, started_at TEXT, completed_at TEXT, error TEXT);
        CREATE INDEX IF NOT EXISTS idx_experiment_queue ON experiment_jobs(status,created_at);
        CREATE INDEX IF NOT EXISTS idx_experiment_room ON experiment_jobs(room_id,created_at);
        CREATE TABLE IF NOT EXISTS experiment_attempts (
           id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, attempt_no INTEGER NOT NULL,
           status TEXT NOT NULL, exit_code INTEGER, code_hash TEXT, logs TEXT,
           error TEXT, started_at TEXT NOT NULL, completed_at TEXT, runtime_seconds REAL,
           container_name TEXT, executor_image TEXT, command TEXT, metadata TEXT,
           UNIQUE(experiment_id,attempt_no));
        CREATE TABLE IF NOT EXISTS experiment_events (
           seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE,
           experiment_id TEXT NOT NULL, room_id TEXT NOT NULL, event_type TEXT NOT NULL,
           payload TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_experiment_events_seq ON experiment_events(seq);
        CREATE INDEX IF NOT EXISTS idx_experiment_events_job ON experiment_events(experiment_id,seq);
        CREATE TABLE IF NOT EXISTS experiment_artifacts (
           id TEXT PRIMARY KEY, experiment_id TEXT NOT NULL, attempt_no INTEGER,
           kind TEXT NOT NULL, name TEXT NOT NULL, relative_path TEXT NOT NULL,
           mime_type TEXT, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
           created_at TEXT NOT NULL, UNIQUE(experiment_id,relative_path));
        CREATE TABLE IF NOT EXISTS experiment_sources (
           experiment_id TEXT NOT NULL, source_id TEXT NOT NULL, title TEXT NOT NULL,
           url TEXT NOT NULL, kind TEXT NOT NULL, metadata TEXT NOT NULL,
           created_at TEXT NOT NULL, PRIMARY KEY(experiment_id,source_id));
        CREATE TABLE IF NOT EXISTS experiment_checkpoints (
           experiment_id TEXT NOT NULL, node TEXT NOT NULL, state TEXT NOT NULL,
           created_at TEXT NOT NULL, PRIMARY KEY(experiment_id,node));
        ''')
        columns={r['name'] for r in c.execute('PRAGMA table_info(jobs)')}
        if 'room_id' not in columns:
            c.execute('ALTER TABLE jobs ADD COLUMN room_id TEXT')
        room_columns={r['name'] for r in c.execute('PRAGMA table_info(rooms)')}
        if 'archived' not in room_columns:
            c.execute('ALTER TABLE rooms ADD COLUMN archived INTEGER NOT NULL DEFAULT 0')
        c.execute('CREATE TABLE IF NOT EXISTS photon_links (space_id TEXT PRIMARY KEY, room_id TEXT NOT NULL, participant_id TEXT NOT NULL, agent_id TEXT NOT NULL)')
        from . import research_store
        research_store.initialize(c)
        from . import source_graph
        source_graph.initialize(c)
        attempt_columns={r['name'] for r in c.execute('PRAGMA table_info(experiment_attempts)')}
        for name,kind in (
            ('runtime_seconds','REAL'),('container_name','TEXT'),('executor_image','TEXT'),
            ('command','TEXT'),('metadata','TEXT')):
            if name not in attempt_columns:c.execute(f'ALTER TABLE experiment_attempts ADD COLUMN {name} {kind}')

def add_job(prompt, budget=90, origin=None, room_id=None):
    job_id = uuid.uuid4().hex[:10]
    origin = origin or {}
    now = utcnow()
    with _lock, connection() as c:
        c.execute('''INSERT INTO jobs
            (id,prompt,status,mode,budget,created_at,updated_at,origin_space,
             origin_sender,origin_message,error,room_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            (job_id, prompt, 'researching', settings.research_mode, budget, now, now,
             origin.get('space_id'), origin.get('sender_id'), origin.get('message_id'), None, room_id))
        from .research_store import changed
        changed(c,room_id)
    emit(job_id, 'queued', 'Supervisor', 'Research request received; assembling literature evidence.')
    return job_id

def set_status(job_id, status, error=None):
    with _lock, connection() as c:
        cur=c.execute('UPDATE jobs SET status=?,updated_at=?,error=? WHERE id=?', (status,utcnow(),error,job_id))
        job=c.execute('SELECT room_id FROM jobs WHERE id=?',(job_id,)).fetchone()
        if job:
            from .research_store import changed
            changed(c,job['room_id'])
        return cur.rowcount > 0

def get_job(job_id):
    with connection() as c:
        row=c.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if not row:return None
    return dict(row)

def list_jobs():
    with connection() as c:
        return [dict(r) for r in c.execute(
            'SELECT * FROM jobs WHERE origin_space IS NULL ORDER BY created_at DESC LIMIT 50')]

def emit(job_id, stage, agent, message):
    with _lock,connection() as c:
        c.execute('INSERT INTO events(job_id,stage,agent,message,created_at) VALUES(?,?,?,?,?)',
                  (job_id,stage,agent,message[:2500],utcnow()))

def get_events(job_id):
    with connection() as c:
        return [dict(r) for r in c.execute('SELECT * FROM events WHERE job_id=? ORDER BY id', (job_id,))]

def put_artifact(job_id, name, data):
    if '/' in name or '\\' in name or '..' in name:
        raise ValueError('Invalid artifact name')
    content = json.dumps(data, ensure_ascii=False, indent=2) if not isinstance(data,str) else data
    with _lock, connection() as c:
        c.execute('INSERT OR REPLACE INTO artifacts VALUES(?,?,?,?)', (job_id,name,content,utcnow()))
        if name=='papers.json':
            from . import source_graph
            source_graph.index_papers(c,job_id,json.loads(content))
        from . import research_store
        if name in research_store.ARTIFACTS:
            job=c.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            if job:
                research_store.index_artifact(c,dict(job),name,json.loads(content))
    workspace = settings.data_dir/'jobs'/job_id
    workspace.mkdir(parents=True,exist_ok=True)
    (workspace/name).write_text(content,encoding='utf-8')

def get_artifacts(job_id):
    with connection() as c:
        rows=c.execute('SELECT name,content FROM artifacts WHERE job_id=? ORDER BY name', (job_id,)).fetchall()
    out={}
    for r in rows:
        try:out[r['name']]=json.loads(r['content'])
        except json.JSONDecodeError:out[r['name']]=r['content']
    return out

def claim_message(message_id):
    with _lock, connection() as c:
        cur=c.execute('INSERT OR IGNORE INTO processed_messages VALUES(?,?)',(message_id,utcnow()))
        return cur.rowcount == 1

def meta_get(key, default=None):
    with connection() as c:
        row=c.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
        return row['value'] if row else default

def meta_set(key,value):
    with _lock,connection() as c:
        c.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,value))


# Collaborative room storage -------------------------------------------------

COLORS = ('#7C9CF5','#66C6A5','#E69A62','#B78BF4','#E06C8B','#55B8D4')

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()

def create_session(display_name: str):
    """Create a durable local identity and opaque bearer session."""
    participant_id=uuid.uuid4().hex[:12]
    token=secrets.token_urlsafe(32)
    now=utcnow()
    expires=(datetime.now(timezone.utc)+timedelta(days=30)).isoformat()
    name=' '.join(display_name.strip().split())[:48]
    color=COLORS[int(participant_id[:4],16)%len(COLORS)]
    with _lock, connection() as c:
        c.execute('INSERT INTO participants VALUES(?,?,?,?)',(participant_id,name,color,now))
        c.execute('INSERT INTO api_sessions VALUES(?,?,?,?)',(_token_hash(token),participant_id,now,expires))
    return {'token':token,'participant':get_participant(participant_id),'expires_at':expires}

def authenticate(token: str | None):
    if not token:return None
    with connection() as c:
        row=c.execute('''SELECT p.* FROM api_sessions s JOIN participants p ON p.id=s.participant_id
                         WHERE s.token_hash=? AND s.expires_at>?''',
                      (_token_hash(token),utcnow())).fetchone()
    return dict(row) if row else None

def get_participant(participant_id: str):
    with connection() as c:
        row=c.execute('SELECT * FROM participants WHERE id=?',(participant_id,)).fetchone()
    return dict(row) if row else None

def create_room(title: str, participant_id: str):
    room_id=uuid.uuid4().hex[:10]
    join_code=secrets.token_hex(3).upper()
    now=utcnow()
    clean=' '.join(title.strip().split())[:80]
    with _lock, connection() as c:
        c.execute('INSERT INTO rooms(id,title,created_by,join_code,created_at) VALUES(?,?,?,?,?)',(room_id,clean,participant_id,join_code,now))
        c.execute('INSERT INTO room_members VALUES(?,?,?,?)',(room_id,participant_id,'owner',now))
    return get_room(room_id, participant_id)

def join_room(room_id: str, participant_id: str, join_code: str):
    with _lock, connection() as c:
        room=c.execute('SELECT * FROM rooms WHERE id=? AND archived=0',(room_id,)).fetchone()
        if not room or not secrets.compare_digest(room['join_code'],join_code.strip().upper()):
            return None
        c.execute('INSERT OR IGNORE INTO room_members VALUES(?,?,?,?)',
                  (room_id,participant_id,'researcher',utcnow()))
    return get_room(room_id,participant_id)

def is_room_member(room_id: str, participant_id: str) -> bool:
    with connection() as c:
        return c.execute('SELECT 1 FROM room_members m JOIN rooms r ON r.id=m.room_id WHERE m.room_id=? AND m.participant_id=? AND r.archived=0',
                         (room_id,participant_id)).fetchone() is not None

def get_room(room_id: str, participant_id: str | None = None):
    with connection() as c:
        room=c.execute('SELECT * FROM rooms WHERE id=? AND archived=0',(room_id,)).fetchone()
        if not room:return None
        members=[dict(r) for r in c.execute('''SELECT p.id,p.display_name,p.color,m.role,m.joined_at
            FROM room_members m JOIN participants p ON p.id=m.participant_id
            WHERE m.room_id=? ORDER BY m.joined_at''',(room_id,))]
    out=dict(room)
    out['members']=members
    if participant_id:
        membership=next((m for m in members if m['id']==participant_id),None)
        if not membership:return None
        out['membership']=membership
    return out

def list_rooms(participant_id: str):
    with connection() as c:
        rows=c.execute('''SELECT r.*,m.role,
            (SELECT COUNT(*) FROM room_members x WHERE x.room_id=r.id) member_count,
            (SELECT content FROM room_messages z WHERE z.room_id=r.id ORDER BY z.created_at DESC LIMIT 1) last_message
            FROM rooms r JOIN room_members m ON m.room_id=r.id
            WHERE m.participant_id=? ORDER BY r.archived,r.created_at DESC''',(participant_id,)).fetchall()
    return [dict(r) for r in rows]

def add_room_message(room_id: str, sender_id: str | None, sender_type: str,
                     content: str, client_message_id: str | None = None,
                     kind: str = 'text', job_id: str | None = None):
    """Persist before broadcast. Returns (message, was_created)."""
    message_id=uuid.uuid4().hex
    now=utcnow()
    with _lock, connection() as c:
        if client_message_id and sender_id:
            prior=c.execute('''SELECT * FROM room_messages
                WHERE room_id=? AND sender_id=? AND client_message_id=?''',
                (room_id,sender_id,client_message_id)).fetchone()
            if prior:return _message_dict(prior),False
        c.execute('''INSERT INTO room_messages
            (id,room_id,sender_id,sender_type,content,kind,client_message_id,job_id,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)''',
            (message_id,room_id,sender_id,sender_type,content[:12000],kind,
             client_message_id,job_id,now))
        row=c.execute('SELECT * FROM room_messages WHERE id=?',(message_id,)).fetchone()
    return _message_dict(row),True

def _message_dict(row):
    out=dict(row)
    if out.get('sender_id'):
        participant=get_participant(out['sender_id'])
        if participant:
            out['sender_name']=participant['display_name']
            out['sender_color']=participant['color']
    elif out.get('sender_type')=='agent':
        out['sender_name']='CausalForge AI'
        out['sender_color']='#8DE2C2'
    return out

def get_room_messages(room_id: str, limit: int = 100):
    with connection() as c:
        rows=c.execute('''SELECT * FROM room_messages WHERE room_id=?
            ORDER BY created_at DESC LIMIT ?''',(room_id,min(limit,250))).fetchall()
    return [_message_dict(r) for r in reversed(rows)]

def add_room_event(room_id: str, event_type: str, payload):
    event_id=uuid.uuid4().hex
    now=utcnow()
    encoded=json.dumps(payload,ensure_ascii=False)
    with _lock, connection() as c:
        cur=c.execute('''INSERT INTO room_events(event_id,room_id,event_type,payload,created_at)
                         VALUES(?,?,?,?,?)''',(event_id,room_id,event_type,encoded,now))
        seq=cur.lastrowid
    return {'event_id':event_id,'sequence':seq,'type':event_type,'room_id':room_id,
            'timestamp':now,'payload':payload}

def get_room_events(room_id: str, after: int = 0, limit: int = 250):
    with connection() as c:
        rows=c.execute('''SELECT * FROM room_events WHERE room_id=? AND seq>?
            ORDER BY seq LIMIT ?''',(room_id,max(0,after),min(limit,500))).fetchall()
    return [{'event_id':r['event_id'],'sequence':r['seq'],'type':r['event_type'],
             'room_id':r['room_id'],'timestamp':r['created_at'],
             'payload':json.loads(r['payload'])} for r in rows]

def latest_room_sequence(room_id: str) -> int:
    with connection() as c:
        row=c.execute('SELECT MAX(seq) n FROM room_events WHERE room_id=?',(room_id,)).fetchone()
    return int(row['n'] or 0)

def latest_room_job(room_id: str, statuses: tuple[str,...] = ()): 
    sql='SELECT * FROM jobs WHERE room_id=?'
    params=[room_id]
    if statuses:
        sql+=f" AND status IN ({','.join('?' for _ in statuses)})"
        params.extend(statuses)
    sql+=' ORDER BY created_at DESC LIMIT 1'
    with connection() as c:
        row=c.execute(sql,params).fetchone()
    return dict(row) if row else None


# Autonomous experiment queue ------------------------------------------------

def _json_value(value):
    if value is None or isinstance(value,(str,int,float)):return value
    return json.dumps(value,ensure_ascii=False)

def _decode_experiment(row):
    if not row:return None
    out=dict(row)
    for key in ('plan','result','report'):
        if out.get(key):
            try:out[key]=json.loads(out[key])
            except json.JSONDecodeError:pass
    out['cancel_requested']=bool(out.get('cancel_requested'))
    return out

def create_experiment_job(room_id,requested_by,request,intent='direct_execution',
                          parent_experiment_id=None,max_retries=2,runtime_limit=300,
                          memory_mb=2048,cpu_cores=2.0):
    experiment_id='exp_'+uuid.uuid4().hex[:12]
    now=utcnow()
    with _lock,connection() as c:
        c.execute('''INSERT INTO experiment_jobs
          (id,room_id,requested_by,request,intent,status,stage,progress,plan,result,report,
           parent_experiment_id,cancel_requested,retry_count,max_retries,runtime_limit,
           memory_mb,cpu_cores,worker_id,heartbeat_at,created_at,updated_at,started_at,completed_at,error)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (experiment_id,room_id,requested_by,request,intent,'queued','queued',0,None,None,None,
           parent_experiment_id,0,0,max_retries,runtime_limit,memory_mb,cpu_cores,None,None,
           now,now,None,None,None))
    add_experiment_event(experiment_id,room_id,'experiment.queued',{
        'experiment_id':experiment_id,'status':'queued','stage':'queued','progress':0,
        'message':'Experiment added to the single-worker queue.'})
    return get_experiment_job(experiment_id)

EXPERIMENT_UPDATE_FIELDS={'status','stage','progress','plan','result','report','cancel_requested',
    'retry_count','worker_id','heartbeat_at','started_at','completed_at','error','intent'}

def update_experiment_job(experiment_id,**changes):
    changes={key:_json_value(value) for key,value in changes.items() if key in EXPERIMENT_UPDATE_FIELDS}
    if not changes:return False
    changes['updated_at']=utcnow()
    sql='UPDATE experiment_jobs SET '+','.join(f'{key}=?' for key in changes)+' WHERE id=?'
    with _lock,connection() as c:
        cur=c.execute(sql,(*changes.values(),experiment_id))
    return cur.rowcount>0

def get_experiment_job(experiment_id):
    with connection() as c:
        row=c.execute('''SELECT j.*,p.display_name requested_by_name,p.color requested_by_color
          FROM experiment_jobs j LEFT JOIN participants p ON p.id=j.requested_by
          WHERE j.id=?''',(experiment_id,)).fetchone()
    return _decode_experiment(row)

def list_room_experiments(room_id,limit=30):
    with connection() as c:
        rows=c.execute('''SELECT j.*,p.display_name requested_by_name,p.color requested_by_color
          FROM experiment_jobs j LEFT JOIN participants p ON p.id=j.requested_by
          WHERE j.room_id=? ORDER BY j.created_at DESC LIMIT ?''',(room_id,min(limit,100))).fetchall()
    return [_decode_experiment(row) for row in rows]

def claim_next_experiment(worker_id):
    with _lock,connection() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute("SELECT id FROM experiment_jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if not row:
            c.commit();return None
        now=utcnow()
        changed=c.execute("""UPDATE experiment_jobs SET status='researching',stage='researching',
            progress=5,worker_id=?,heartbeat_at=?,started_at=COALESCE(started_at,?),updated_at=?
            WHERE id=? AND status='queued'""",(worker_id,now,now,now,row['id'])).rowcount
        c.commit()
    return get_experiment_job(row['id']) if changed else None

def request_experiment_cancel(experiment_id):
    job=get_experiment_job(experiment_id)
    if not job or job['status'] in ('completed','failed','cancelled'):return job,False
    if job['status']=='queued':
        update_experiment_job(experiment_id,cancel_requested=1,status='cancelled',stage='cancelled',
                              completed_at=utcnow(),progress=job.get('progress',0))
        add_experiment_event(experiment_id,job['room_id'],'experiment.cancelled',{
            'experiment_id':experiment_id,'status':'cancelled','stage':'cancelled',
            'progress':job.get('progress',0),'message':'Queued experiment cancelled.'})
    else:update_experiment_job(experiment_id,cancel_requested=1)
    return get_experiment_job(experiment_id),True

def experiment_cancel_requested(experiment_id):
    with connection() as c:
        row=c.execute('SELECT cancel_requested FROM experiment_jobs WHERE id=?',(experiment_id,)).fetchone()
    return bool(row and row['cancel_requested'])

def add_experiment_attempt(experiment_id,attempt_no,status='running',code_hash=''):
    attempt_id=f'{experiment_id}-attempt-{attempt_no}'
    with _lock,connection() as c:
        c.execute('''INSERT OR REPLACE INTO experiment_attempts
          (id,experiment_id,attempt_no,status,exit_code,code_hash,logs,error,started_at,completed_at,
           runtime_seconds,container_name,executor_image,command,metadata)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(attempt_id,experiment_id,attempt_no,status,None,
          code_hash,'[]',None,utcnow(),None,None,None,None,'[]','{}'))
    return attempt_id

def finish_experiment_attempt(attempt_id,status,exit_code=None,logs=None,error=None,
                              runtime_seconds=None,container_name=None,executor_image=None,
                              command=None,metadata=None,started_at=None,completed_at=None):
    with _lock,connection() as c:
        c.execute('''UPDATE experiment_attempts SET status=?,exit_code=?,logs=?,error=?,
          started_at=COALESCE(?,started_at),completed_at=?,runtime_seconds=?,container_name=?,
          executor_image=?,command=?,metadata=? WHERE id=?''',
          (status,exit_code,json.dumps(logs or [],ensure_ascii=False),error,started_at,
           completed_at or utcnow(),runtime_seconds,container_name,executor_image,
           json.dumps(command or [],ensure_ascii=False),json.dumps(metadata or {},ensure_ascii=False),attempt_id))

def get_experiment_attempts(experiment_id):
    with connection() as c:
        rows=c.execute('SELECT * FROM experiment_attempts WHERE experiment_id=? ORDER BY attempt_no',(experiment_id,)).fetchall()
    out=[]
    for row in rows:
        item=dict(row)
        try:item['logs']=json.loads(item['logs'] or '[]')
        except json.JSONDecodeError:item['logs']=[]
        try:item['command']=json.loads(item.get('command') or '[]')
        except json.JSONDecodeError:item['command']=[]
        try:item['metadata']=json.loads(item.get('metadata') or '{}')
        except json.JSONDecodeError:item['metadata']={}
        out.append(item)
    return out

def add_experiment_event(experiment_id,room_id,event_type,payload):
    event_id=uuid.uuid4().hex
    now=utcnow()
    with _lock,connection() as c:
        cur=c.execute('''INSERT INTO experiment_events(event_id,experiment_id,room_id,event_type,payload,created_at)
          VALUES(?,?,?,?,?,?)''',(event_id,experiment_id,room_id,event_type,
          json.dumps(payload,ensure_ascii=False),now))
        seq=cur.lastrowid
    return {'seq':seq,'event_id':event_id,'experiment_id':experiment_id,'room_id':room_id,
            'event_type':event_type,'payload':payload,'created_at':now}

def get_experiment_events(experiment_id=None,after=0,limit=500):
    sql='SELECT * FROM experiment_events WHERE seq>?'
    params=[after]
    if experiment_id:sql+=' AND experiment_id=?';params.append(experiment_id)
    sql+=' ORDER BY seq LIMIT ?';params.append(min(limit,1000))
    with connection() as c:rows=c.execute(sql,params).fetchall()
    return [{'seq':row['seq'],'event_id':row['event_id'],'experiment_id':row['experiment_id'],
             'room_id':row['room_id'],'event_type':row['event_type'],
             'payload':json.loads(row['payload']),'created_at':row['created_at']} for row in rows]

def latest_experiment_sequence():
    with connection() as c:
        row=c.execute('SELECT MAX(seq) n FROM experiment_events').fetchone()
    return int(row['n'] or 0)

def add_experiment_artifact(experiment_id,attempt_no,kind,name,relative_path,mime_type,size_bytes,sha256):
    artifact_id='art_'+uuid.uuid4().hex[:14]
    with _lock,connection() as c:
        c.execute('''INSERT OR REPLACE INTO experiment_artifacts
          (id,experiment_id,attempt_no,kind,name,relative_path,mime_type,size_bytes,sha256,created_at)
          VALUES(?,?,?,?,?,?,?,?,?,?)''',(artifact_id,experiment_id,attempt_no,kind,name,
          relative_path,mime_type,size_bytes,sha256,utcnow()))
    return artifact_id

def get_experiment_artifacts(experiment_id):
    with connection() as c:
        return [dict(row) for row in c.execute(
            'SELECT * FROM experiment_artifacts WHERE experiment_id=? ORDER BY created_at',(experiment_id,))]

def get_experiment_artifact(artifact_id):
    with connection() as c:
        row=c.execute('SELECT * FROM experiment_artifacts WHERE id=?',(artifact_id,)).fetchone()
    return dict(row) if row else None

def add_experiment_source(experiment_id,source):
    source_id=str(source.get('id') or hashlib.sha256(source.get('url','').encode()).hexdigest()[:16])
    with _lock,connection() as c:
        c.execute('''INSERT OR REPLACE INTO experiment_sources
          (experiment_id,source_id,title,url,kind,metadata,created_at) VALUES(?,?,?,?,?,?,?)''',
          (experiment_id,source_id,source.get('title','Untitled')[:500],source.get('url','')[:1500],
           source.get('origin','research'),json.dumps(source,ensure_ascii=False),utcnow()))

def get_experiment_sources(experiment_id):
    with connection() as c:rows=c.execute(
        'SELECT * FROM experiment_sources WHERE experiment_id=? ORDER BY created_at',(experiment_id,)).fetchall()
    out=[]
    for row in rows:
        item=dict(row)
        try:item['metadata']=json.loads(item['metadata'])
        except json.JSONDecodeError:pass
        out.append(item)
    return out

def save_experiment_checkpoint(experiment_id,node,state):
    safe={key:value for key,value in state.items() if key not in ('execution_logs',)}
    with _lock,connection() as c:
        c.execute('INSERT OR REPLACE INTO experiment_checkpoints VALUES(?,?,?,?)',
                  (experiment_id,node,json.dumps(safe,ensure_ascii=False,default=str),utcnow()))

def get_experiment_detail(experiment_id):
    job=get_experiment_job(experiment_id)
    if not job:return None
    job['attempts']=get_experiment_attempts(experiment_id)
    job['events']=get_experiment_events(experiment_id)
    job['artifacts']=get_experiment_artifacts(experiment_id)
    job['sources']=get_experiment_sources(experiment_id)
    job['execution_evidence']=build_execution_evidence(job)
    return job


def _read_json_artifact(experiment_id,artifacts,name):
    item=next((artifact for artifact in artifacts if artifact['name']==name),None)
    if not item:return None
    root=(settings.data_dir/'experiments'/experiment_id).resolve()
    path=(root/item['relative_path']).resolve()
    if root not in path.parents or not path.is_file():return None
    try:return json.loads(path.read_text(encoding='utf-8'))
    except (OSError,json.JSONDecodeError):return None


def build_execution_evidence(job):
    """Reconstruct authorization-safe, hash-verified execution evidence from persisted records."""
    experiment_id=job['id']
    artifacts=job.get('artifacts') or get_experiment_artifacts(experiment_id)
    attempts=job.get('attempts') or get_experiment_attempts(experiment_id)
    root=(settings.data_dir/'experiments'/experiment_id).resolve()
    checked=[]
    for artifact in artifacts:
        path=(root/artifact['relative_path']).resolve()
        exists=root in path.parents and path.is_file()
        digest=hashlib.sha256(path.read_bytes()).hexdigest() if exists else None
        checked.append({**artifact,'absolute_path':str(path),'exists':exists,
                        'hash_verified':bool(exists and digest==artifact['sha256']),
                        'download_url':f"/api/experiments/{experiment_id}/artifacts/{artifact['id']}"})
    attempt=attempts[-1] if attempts else None
    metrics=_read_json_artifact(experiment_id,artifacts,'metrics.json')
    run_metadata=_read_json_artifact(experiment_id,artifacts,'run_metadata.json') or {}
    contract=(job.get('result') or {}).get('verification') or {}
    hashes_verified=bool(checked) and all(item['hash_verified'] for item in checked)
    execution_verified=bool(
        job.get('status')=='completed' and attempt and attempt.get('exit_code')==0 and
        hashes_verified and contract.get('status')=='verified')
    if execution_verified:verification_status='verified'
    elif job.get('status')=='completed':verification_status='legacy_or_procedure_unverified'
    elif job.get('status')=='failed':verification_status='failed'
    else:verification_status='not_completed'
    script=next((item for item in reversed(checked) if item['kind']=='source' and
                 item['relative_path'].endswith('/experiment.py')),None)
    if not script:
        script=next((item for item in reversed(checked) if item['kind']=='code_version' and
                     item['relative_path'].endswith('/experiment.py')),None)
    return {
        'verification_status':verification_status,'execution_verified':execution_verified,
        'contract_checks':contract.get('checks',[]),'hashes_verified':hashes_verified,
        'experiment_id':experiment_id,'worker_id':job.get('worker_id'),
        'script_path':script.get('absolute_path') if script else None,
        'script_sha256':script.get('sha256') if script else None,
        'started_at':attempt.get('started_at') if attempt else job.get('started_at'),
        'completed_at':attempt.get('completed_at') if attempt else job.get('completed_at'),
        'runtime_seconds':attempt.get('runtime_seconds') if attempt else None,
        'exit_code':attempt.get('exit_code') if attempt else None,
        'container_name':attempt.get('container_name') if attempt else None,
        'executor_image':attempt.get('executor_image') if attempt else None,
        'command':attempt.get('command',[]) if attempt else [],
        'code_hash':attempt.get('code_hash') if attempt else None,
        'logs':attempt.get('logs',[]) if attempt else [],
        'requested_seed_count':len((job.get('plan') or {}).get('random_seeds') or []),
        'actual_seed_count':run_metadata.get('seed_count'),
        'expected_training_runs':(job.get('plan') or {}).get('expected_training_runs'),
        'actual_training_runs':run_metadata.get('training_runs'),
        'expected_simulations':(job.get('plan') or {}).get('expected_simulations'),
        'actual_simulations':run_metadata.get('total_simulations'),
        'run_metadata':run_metadata,'raw_metrics':metrics,
        'artifact_filenames':[item['name'] for item in checked],
        'plot_filenames':[item['name'] for item in checked if (item.get('mime_type') or '').startswith('image/')],
        'artifacts':checked,
    }

def recover_interrupted_experiments():
    active=('researching','planning','generating','validating','running','debugging','evaluating','reporting')
    now=utcnow()
    with _lock,connection() as c:
        rows=c.execute(f"SELECT id,room_id,status FROM experiment_jobs WHERE status IN ({','.join('?' for _ in active)})",active).fetchall()
        for row in rows:
            message=f"Worker restarted while experiment was {row['status']}; execution was not automatically repeated. Use rerun to create a new version."
            c.execute("UPDATE experiment_jobs SET status='failed',stage='failed',error=?,completed_at=?,updated_at=? WHERE id=?",
                      (message,now,now,row['id']))
            c.execute('''INSERT INTO experiment_events(event_id,experiment_id,room_id,event_type,payload,created_at)
              VALUES(?,?,?,?,?,?)''',(uuid.uuid4().hex,row['id'],row['room_id'],'experiment.failed',
              json.dumps({'experiment_id':row['id'],'status':'failed','stage':'failed','message':message}),now))
    return len(rows)
