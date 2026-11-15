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
        ''')
        columns={r['name'] for r in c.execute('PRAGMA table_info(jobs)')}
        if 'room_id' not in columns:
            c.execute('ALTER TABLE jobs ADD COLUMN room_id TEXT')
        from . import research_store
        research_store.initialize(c)
        from . import source_graph
        source_graph.initialize(c)

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
        c.execute('INSERT INTO rooms VALUES(?,?,?,?,?)',(room_id,clean,participant_id,join_code,now))
        c.execute('INSERT INTO room_members VALUES(?,?,?,?)',(room_id,participant_id,'owner',now))
    return get_room(room_id, participant_id)

def join_room(room_id: str, participant_id: str, join_code: str):
    with _lock, connection() as c:
        room=c.execute('SELECT * FROM rooms WHERE id=?',(room_id,)).fetchone()
        if not room or not secrets.compare_digest(room['join_code'],join_code.strip().upper()):
            return None
        c.execute('INSERT OR IGNORE INTO room_members VALUES(?,?,?,?)',
                  (room_id,participant_id,'researcher',utcnow()))
    return get_room(room_id,participant_id)

def is_room_member(room_id: str, participant_id: str) -> bool:
    with connection() as c:
        return c.execute('SELECT 1 FROM room_members WHERE room_id=? AND participant_id=?',
                         (room_id,participant_id)).fetchone() is not None

def get_room(room_id: str, participant_id: str | None = None):
    with connection() as c:
        room=c.execute('SELECT * FROM rooms WHERE id=?',(room_id,)).fetchone()
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
            WHERE m.participant_id=? ORDER BY r.created_at DESC''',(participant_id,)).fetchall()
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
