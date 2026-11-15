"""Indexed, room-scoped research lists and keyset pagination."""
import base64
import binascii
import json

from . import storage

KINDS = ('sources', 'experiments', 'hypotheses', 'discoveries')
ARTIFACTS = ('reading_list.json', 'pipelines.json', 'results.json')


def changed(connection, room_id):
    if room_id:
        connection.execute('''INSERT INTO meta(key,value) VALUES(?, '1')
            ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1''',
            (f'research_revision:{room_id}',))


def initialize(connection):
    connection.executescript('''
        CREATE INDEX IF NOT EXISTS idx_jobs_room_created ON jobs(room_id,created_at DESC,id DESC);
        CREATE INDEX IF NOT EXISTS idx_room_experiment_page ON jobs(room_id,created_at DESC,id DESC)
            WHERE status NOT IN ('answering','answered');
        CREATE TABLE IF NOT EXISTS research_source_uses (
            room_id TEXT NOT NULL, job_id TEXT NOT NULL, url TEXT NOT NULL,
            created_at TEXT NOT NULL, content TEXT NOT NULL,
            PRIMARY KEY(job_id,url));
        CREATE INDEX IF NOT EXISTS idx_source_uses_room_url
            ON research_source_uses(room_id,url,created_at DESC,job_id DESC);
        CREATE TABLE IF NOT EXISTS room_research (
            room_id TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
            job_id TEXT NOT NULL, created_at TEXT NOT NULL, content TEXT NOT NULL,
            PRIMARY KEY(room_id,kind,id));
        CREATE INDEX IF NOT EXISTS idx_research_page
            ON room_research(room_id,kind,created_at DESC,id DESC);
        CREATE INDEX IF NOT EXISTS idx_research_job ON room_research(room_id,kind,job_id);
    ''')
    if connection.execute("SELECT 1 FROM meta WHERE key='research_index_v1'").fetchone():
        return
    rows=connection.execute('''SELECT j.*,a.name,a.content FROM jobs j
        JOIN artifacts a ON a.job_id=j.id WHERE j.room_id IS NOT NULL
        AND a.name IN ('reading_list.json','pipelines.json','results.json')
        ORDER BY j.created_at,j.id''').fetchall()
    for row in rows:
        data=json.loads(row['content'])
        if row['name']=='reading_list.json':
            # Recover sources omitted by the former per-job reading-list caps.
            urls={item.get('url') for item in data}
            for artifact in connection.execute('''SELECT name,content FROM artifacts
                WHERE job_id=? AND name IN ('papers.json','review.json')''',(row['id'],)):
                value=json.loads(artifact['content'])
                items=value if artifact['name']=='papers.json' else value.get('web_results',[])
                for item in items:
                    if item.get('url') and item['url'] not in urls:
                        urls.add(item['url'])
                        data.append({'title':item.get('title'),'url':item['url'],
                            'reason':(item.get('summary') or item.get('content',''))[:350],
                            'evidence_level':item.get('evidence_level','Retrieved research source')})
        index_artifact(connection,dict(row),row['name'],data)
    connection.execute("INSERT INTO meta(key,value) VALUES('research_index_v1','1')")


def _save(connection,job,kind,item_id,data):
    connection.execute('''INSERT INTO room_research VALUES(?,?,?,?,?,?)
        ON CONFLICT(room_id,kind,id) DO UPDATE SET
        job_id=excluded.job_id,created_at=excluded.created_at,content=excluded.content''',
        (job['room_id'],kind,item_id,job['id'],job['created_at'],json.dumps(data)))


def index_artifact(connection,job,name,data):
    if not job['room_id'] or name not in ARTIFACTS:
        return
    room_id=job['room_id']
    if name=='reading_list.json':
        affected={row['url'] for row in connection.execute(
            'SELECT url FROM research_source_uses WHERE job_id=?',(job['id'],))}
        connection.execute('DELETE FROM research_source_uses WHERE job_id=?',(job['id'],))
        for source in data:
            url=source.get('url')
            if not url:
                continue
            affected.add(url)
            connection.execute('INSERT OR IGNORE INTO research_source_uses VALUES(?,?,?,?,?)',
                (room_id,job['id'],url,job['created_at'],json.dumps(source)))
        for url in affected:
            latest=connection.execute('''SELECT * FROM research_source_uses
                WHERE room_id=? AND url=? ORDER BY created_at DESC,job_id DESC LIMIT 1''',
                (room_id,url)).fetchone()
            if latest:
                _save(connection,{'room_id':room_id,'id':latest['job_id'],
                    'created_at':latest['created_at']},'sources',url,json.loads(latest['content']))
            else:
                connection.execute("DELETE FROM room_research WHERE room_id=? AND kind='sources' AND id=?",
                    (room_id,url))
    elif name=='pipelines.json':
        connection.execute("DELETE FROM room_research WHERE room_id=? AND kind='hypotheses' AND job_id=?",
            (room_id,job['id']))
        for index,recipe in enumerate(data):
            item_id=f"{job['id']}-{recipe.get('id','proposal')}-{index}"
            _save(connection,job,'hypotheses',item_id,{'id':item_id,'job_id':job['id'],
                'title':recipe.get('name'),'description':recipe.get('rationale')})
    elif name=='results.json':
        connection.execute("DELETE FROM room_research WHERE room_id=? AND kind='discoveries' AND id=?",
            (room_id,job['id']))
        if data:
            _save(connection,job,'discoveries',job['id'],{'id':job['id'],'job_id':job['id'],
                'title':data.get('verdict'),
                'detail':('Stylized traffic model; interpret as mechanism evidence, not a city forecast.'
                    if data.get('experiment_type')=='traffic_simulation' else
                    'One-seed toy experiment; interpret as preliminary evidence.')})
    changed(connection,room_id)


def _cursor(room_id,kind,created_at,item_id):
    return base64.urlsafe_b64encode(json.dumps([room_id,kind,created_at,item_id]).encode()).decode().rstrip('=')


def _decode_cursor(value,room_id,kind):
    try:
        decoded=json.loads(base64.b64decode(value+'='*(-len(value)%4),altchars=b'-_',validate=True))
        if (not isinstance(decoded,list) or len(decoded)!=4 or
                not all(isinstance(item,str) for item in decoded) or decoded[:2]!=[room_id,kind]):
            raise ValueError
        return decoded[2:]
    except (ValueError,TypeError,UnicodeError,binascii.Error):
        raise ValueError('Invalid research cursor') from None


def _experiment(row):
    item=dict(row)
    result=item.pop('result_content')
    item['results']=json.loads(result) if result else None
    return item


def page(room_id,kind,limit=20,cursor=None):
    if kind not in KINDS:
        raise ValueError('Unknown research list')
    if not 1<=limit<=100:
        raise ValueError('Page size must be between 1 and 100')
    after=_decode_cursor(cursor,room_id,kind) if cursor else None
    with storage.connection() as connection:
        if kind=='experiments':
            sql='''SELECT j.*,a.content AS result_content FROM jobs j
                LEFT JOIN artifacts a ON a.job_id=j.id AND a.name='results.json'
                WHERE j.room_id=? AND j.status NOT IN ('answering','answered')'''
            params=[room_id]
            if after:
                sql+=' AND (j.created_at,j.id)<(?,?)'
                params.extend(after)
            sql+=' ORDER BY j.created_at DESC,j.id DESC LIMIT ?'
            rows=connection.execute(sql,(*params,limit+1)).fetchall()
            items=[_experiment(row) for row in rows[:limit]]
        else:
            sql='''SELECT r.*,j.status,j.updated_at FROM room_research r
                JOIN jobs j ON j.id=r.job_id WHERE r.room_id=? AND r.kind=?'''
            params=[room_id,kind]
            if after:
                sql+=' AND (r.created_at,r.id)<(?,?)'
                params.extend(after)
            sql+=' ORDER BY r.created_at DESC,r.id DESC LIMIT ?'
            rows=connection.execute(sql,(*params,limit+1)).fetchall()
            items=[]
            for row in rows[:limit]:
                item=json.loads(row['content'])
                if kind=='hypotheses':
                    item['status']='tested' if row['status']=='completed' else 'proposed'
                elif kind=='discoveries':
                    item['created_at']=row['updated_at']
                items.append(item)
    has_more=len(rows)>limit
    last=rows[limit-1] if has_more else None
    return {'items':items,'has_more':has_more,
        'next_cursor':_cursor(room_id,kind,last['created_at'],last['id']) if last else None}


def summary(room_id):
    with storage.connection() as connection:
        counts={kind:0 for kind in KINDS}
        for row in connection.execute('''SELECT kind,COUNT(*) AS n FROM room_research
            WHERE room_id=? GROUP BY kind''',(room_id,)):
            counts[row['kind']]=row['n']
        counts['experiments']=connection.execute('''SELECT COUNT(*) FROM jobs
            WHERE room_id=? AND status NOT IN ('answering','answered')''',(room_id,)).fetchone()[0]
        revision=connection.execute('SELECT value FROM meta WHERE key=?',
            (f'research_revision:{room_id}',)).fetchone()
    return counts,int(revision['value']) if revision else 0


def message_jobs(room_id,messages):
    ids=list({message['job_id'] for message in messages if message.get('job_id')})
    if not ids:
        return {}
    with storage.connection() as connection:
        rows=connection.execute(f'''SELECT j.*,a.content AS result_content FROM jobs j
            LEFT JOIN artifacts a ON a.job_id=j.id AND a.name='results.json'
            WHERE j.room_id=? AND j.id IN ({','.join('?' for _ in ids)})''',(room_id,*ids)).fetchall()
    return {row['id']:_experiment(row) for row in rows}
