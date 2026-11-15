"""Query/source provenance and recorded citations; never infer citations from similarity."""
import hashlib
import asyncio
import json
import re
import time
from urllib.parse import unquote, urlsplit, urlunsplit

import httpx

from . import storage, research_store
from .config import settings


def canonical_url(url):
    parts=urlsplit(url.strip())
    host=(parts.hostname or '').lower().removeprefix('www.')
    if host in ('doi.org','dx.doi.org'):
        return 'https://doi.org/'+unquote(parts.path).strip('/').lower()
    if host=='arxiv.org':
        path=re.sub(r'v\d+$','',parts.path.removesuffix('.pdf').rstrip('/'))
        return 'https://arxiv.org'+path.replace('/pdf/','/abs/')
    return urlunsplit((parts.scheme.lower(),parts.netloc.lower(),parts.path.rstrip('/'),parts.query,''))


def work_id(value):
    match=re.fullmatch(r'(?:https?://(?:api\.)?openalex\.org/(?:works/)?)?(W\d+)',str(value or ''),re.I)
    return 'https://openalex.org/'+match.group(1).upper() if match else None


def index_papers(connection,job_id,papers):
    job=connection.execute('SELECT room_id,created_at FROM jobs WHERE id=?',(job_id,)).fetchone()
    if not job or not job['room_id']:
        return
    for paper in papers:
        url=paper.get('url')
        if not isinstance(url,str) or not url:
            continue
        references=paper.get('referenced_works')
        if references is not None and not isinstance(references,list):
            references=None
        identifier=work_id(paper.get('id')) or work_id(url)
        # A newer retrieval without reference data must not erase a known reference list.
        connection.execute('''INSERT INTO source_graph_papers
            (room_id,url,work_id,metadata,referenced_works,checked_at)
            VALUES(?,?,?,?,?,?) ON CONFLICT(room_id,url) DO UPDATE SET
            work_id=COALESCE(excluded.work_id,source_graph_papers.work_id),
            metadata=excluded.metadata,
            referenced_works=COALESCE(excluded.referenced_works,source_graph_papers.referenced_works),
            checked_at=CASE WHEN excluded.referenced_works IS NOT NULL
                THEN excluded.checked_at ELSE source_graph_papers.checked_at END''',
            (job['room_id'],url,identifier,json.dumps({key:paper.get(key)
                for key in ('title','year','authors','doi')}),
             json.dumps(references) if references is not None else None,
             time.time() if references is not None else 0))
    research_store.changed(connection,job['room_id'])


def initialize(connection):
    connection.executescript('''
        CREATE TABLE IF NOT EXISTS source_graph_papers (
            room_id TEXT NOT NULL, url TEXT NOT NULL, work_id TEXT,
            metadata TEXT NOT NULL, referenced_works TEXT, checked_at REAL NOT NULL DEFAULT 0,
            PRIMARY KEY(room_id,url));
        CREATE INDEX IF NOT EXISTS idx_source_graph_work ON source_graph_papers(room_id,work_id);
    ''')
    if connection.execute("SELECT 1 FROM meta WHERE key='source_graph_v1'").fetchone():
        return
    for row in connection.execute('''SELECT a.job_id,a.content FROM artifacts a
        JOIN jobs j ON j.id=a.job_id WHERE a.name='papers.json' AND j.room_id IS NOT NULL
        ORDER BY j.created_at,j.id''').fetchall():
        index_papers(connection,row['job_id'],json.loads(row['content']))
    connection.execute("INSERT INTO meta(key,value) VALUES('source_graph_v1','1')")


async def enrich_references(room_id,urls):
    """Lazily fill missing references for older OpenAlex results, with a retry cooldown."""
    if not urls:
        return
    with storage.connection() as connection:
        rows=connection.execute(f'''SELECT DISTINCT work_id FROM source_graph_papers
            WHERE room_id=? AND url IN ({','.join('?' for _ in urls)})
            AND work_id IS NOT NULL AND referenced_works IS NULL AND checked_at<?''',
            (room_id,*urls,time.time()-900)).fetchall()
    ids=[row['work_id'] for row in rows]
    if not ids:
        return
    # Batch requests instead of making one network call for every source node.
    try:
        async with httpx.AsyncClient(timeout=8,follow_redirects=True,
                headers={'User-Agent':'Labthread-HackPrinceton/1.0 (citation graph)'}) as client:
            for offset in range(0,len(ids),50):
                batch=ids[offset:offset+50]
                params={'filter':'openalex:'+'|'.join(identifier.rsplit('/',1)[-1] for identifier in batch),
                    'select':'id,referenced_works','per-page':50}
                if settings.openalex_email:params['mailto']=settings.openalex_email
                response=await client.get('https://api.openalex.org/works',params=params)
                response.raise_for_status()
                works=response.json().get('results',[])
                if not isinstance(works,list):
                    raise ValueError('Invalid OpenAlex reference response')
                with storage._lock,storage.connection() as connection:
                    for identifier in batch:
                        connection.execute('UPDATE source_graph_papers SET checked_at=? WHERE room_id=? AND work_id=?',
                            (time.time(),room_id,identifier))
                    for paper in works:
                        if not isinstance(paper,dict):continue
                        identifier=work_id(paper.get('id'))
                        references=paper.get('referenced_works')
                        if identifier in batch and isinstance(references,list):
                            connection.execute('''UPDATE source_graph_papers SET referenced_works=?,checked_at=?
                                WHERE room_id=? AND work_id=?''',(json.dumps(references),time.time(),room_id,identifier))
    except (httpx.HTTPError,ValueError,TypeError):
        # Provenance still works offline. A missing citation edge is not evidence of no citation.
        with storage._lock,storage.connection() as connection:
            connection.executemany('UPDATE source_graph_papers SET checked_at=? WHERE room_id=? AND work_id=?',
                [(time.time(),room_id,identifier) for identifier in ids])


def _source_node(row):
    source=json.loads(row['source_content'])
    metadata=json.loads(row['metadata']) if row['metadata'] else {}
    identifier=row['work_id'] or canonical_url(source['url'])
    return {'id':'source:'+hashlib.sha256(identifier.encode()).hexdigest()[:24],
        'kind':'source','title':metadata.get('title') or source.get('title') or source['url'],
        'url':source['url'],'year':metadata.get('year'),'authors':metadata.get('authors'),
        'work_id':row['work_id'],'evidence_level':source.get('evidence_level'),
        'citation_data':'available' if row['referenced_works'] is not None else 'unavailable'}


SOURCE_SELECT='''SELECT u.content AS source_content,g.work_id,g.metadata,g.referenced_works
    FROM research_source_uses u LEFT JOIN source_graph_papers g
    ON g.room_id=u.room_id AND g.url=u.url'''


async def page(room_id,limit=8,cursor=None):
    after=research_store._decode_cursor(cursor,room_id,'source-graph') if cursor else None
    with storage.connection() as connection:
        sql='SELECT * FROM jobs WHERE room_id=?'
        params=[room_id]
        if after:
            sql+=' AND (created_at,id)<(?,?)'
            params.extend(after)
        jobs=connection.execute(sql+' ORDER BY created_at DESC,id DESC LIMIT ?',(*params,limit+1)).fetchall()
        total=connection.execute('SELECT COUNT(*) FROM jobs WHERE room_id=?',(room_id,)).fetchone()[0]
        job_ids=[job['id'] for job in jobs[:limit]]
        urls=[row['url'] for row in connection.execute(
            f"SELECT DISTINCT url FROM research_source_uses WHERE room_id=? AND job_id IN ({','.join('?' for _ in job_ids)})",
            (room_id,*job_ids))] if job_ids else []
    try:
        await asyncio.wait_for(enrich_references(room_id,urls),timeout=10)
    except TimeoutError:
        pass
    nodes={}
    edges={}
    source_rows={}
    with storage.connection() as connection:
        for job in jobs[:limit]:
            query_id='query:'+job['id']
            nodes[query_id]={'id':query_id,'kind':'query','title':re.sub(r'@chatbot\b','',job['prompt'],flags=re.I).strip(),
                'job_id':job['id'],'created_at':job['created_at'],'status':job['status']}
            rows=connection.execute(SOURCE_SELECT+' WHERE u.room_id=? AND u.job_id=? ORDER BY u.url',
                (room_id,job['id'])).fetchall()
            for row in rows:
                node=_source_node(row)
                nodes[node['id']]=node
                key=f"found:{query_id}:{node['id']}"
                edges[key]={'id':key,'source':query_id,'target':node['id'],'kind':'retrieved'}
                source_rows[node['id']]=row
        # Include recorded links to any source already used in this room, even if its
        # originating query is on an older page. Never expand into unrelated papers.
        for row in source_rows.values():
            source=_source_node(row)
            references={identifier for ref in json.loads(row['referenced_works'] or '[]')
                if (identifier:=work_id(ref))}
            for offset in range(0,len(references),200):
                batch=sorted(references)[offset:offset+200]
                targets=connection.execute(SOURCE_SELECT+f''' WHERE u.room_id=? AND
                    g.work_id IN ({','.join('?' for _ in batch)}) GROUP BY u.url''',(room_id,*batch)).fetchall()
                for target_row in targets:
                    target=_source_node(target_row)
                    if source['id']==target['id']:continue
                    nodes[target['id']]=target
                    key=f"cites:{source['id']}:{target['id']}"
                    edges[key]={'id':key,'source':source['id'],'target':target['id'],
                        'kind':'cites','evidence':'OpenAlex referenced_works'}
    has_more=len(jobs)>limit
    last=jobs[limit-1] if has_more else None
    return {'nodes':list(nodes.values()),'edges':list(edges.values()),'query_total':total,
        'has_more':has_more,'next_cursor':research_store._cursor(room_id,'source-graph',last['created_at'],last['id']) if last else None}
