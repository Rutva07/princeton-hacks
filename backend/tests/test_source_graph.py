import asyncio
from dataclasses import replace
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app import storage, source_graph
from app.main import app
from app.papers import search_papers


@pytest.fixture
def graph_room(tmp_path,monkeypatch):
    monkeypatch.setattr(storage,'DB',tmp_path/'graph.sqlite3')
    monkeypatch.setattr(storage,'settings',replace(storage.settings,data_dir=tmp_path))
    with TestClient(app) as client:
        owner=client.post('/api/session',json={'display_name':'Graph Researcher'}).json()
        outsider=client.post('/api/session',json={'display_name':'Other Researcher'}).json()
        headers={'Authorization':f"Bearer {owner['token']}"}
        room=client.post('/api/rooms',headers=headers,json={'title':'Dependency graph lab'}).json()
        yield client,headers,room,outsider


def paper(number,references=()):
    result={'id':f'https://openalex.org/W{number}', 'url':f'https://doi.org/10.1000/paper{number}',
        'title':f'Graph test paper {number}','authors':'Test authors','year':2025}
    if references is not None:
        result['referenced_works']=[f'https://openalex.org/W{ref}' for ref in references]
    return result


def query(room_id,title,papers):
    job_id=storage.add_job(title,90,room_id=room_id)
    storage.put_artifact(job_id,'papers.json',papers)
    storage.put_artifact(job_id,'reading_list.json',[
        {'title':item['title'],'url':item['url'],'evidence_level':'Test metadata'} for item in papers])
    storage.set_status(job_id,'answered')
    return 'query:'+job_id


def get_graph(client,headers,room_id,**params):
    response=client.get(f'/api/rooms/{room_id}/source-graph',headers=headers,params=params)
    assert response.status_code==200,response.text
    return response.json()


def test_graph_links_queries_deduplicates_shared_sources_and_directs_citations(graph_room):
    client,headers,room,_=graph_room
    papers=[paper(1,[2,999])]+[paper(number) for number in range(2,7)]
    first=query(room['id'],'@chatbot first research question',papers)
    second=query(room['id'],'@chatbot second research question',[papers[0],paper(7)])
    graph=get_graph(client,headers,room['id'])
    assert graph['query_total']==2
    assert not graph['has_more']
    queries=[node for node in graph['nodes'] if node['kind']=='query']
    sources=[node for node in graph['nodes'] if node['kind']=='source']
    assert len(queries)==2 and len(sources)==7
    assert all('@chatbot' not in node['title'] for node in queries)
    shared=next(node for node in sources if node['work_id']=='https://openalex.org/W1')
    cited=next(node for node in sources if node['work_id']=='https://openalex.org/W2')
    retrieval=[edge for edge in graph['edges'] if edge['kind']=='retrieved']
    assert len(retrieval)==8
    assert {edge['source'] for edge in retrieval if edge['target']==shared['id']}=={first,second}
    citations=[edge for edge in graph['edges'] if edge['kind']=='cites']
    assert len(citations)==1
    assert citations[0]['source']==shared['id'] and citations[0]['target']==cited['id']
    assert citations[0]['evidence']=='OpenAlex referenced_works'
    assert not any(node.get('work_id')=='https://openalex.org/W999' for node in sources)


def test_graph_pages_merge_without_duplicates_and_include_older_cited_sources(graph_room):
    client,headers,room,_=graph_room
    first=query(room['id'],'Older query',[paper(1),paper(2)])
    second=query(room['id'],'Newer query',[paper(1,[2])])
    first_page=get_graph(client,headers,room['id'],limit=1)
    assert first_page['has_more']
    assert {node['work_id'] for node in first_page['nodes'] if node['kind']=='source'}=={
        'https://openalex.org/W1','https://openalex.org/W2'}
    assert any(edge['kind']=='cites' for edge in first_page['edges'])
    second_page=get_graph(client,headers,room['id'],limit=1,cursor=first_page['next_cursor'])
    assert not second_page['has_more'] and second_page['next_cursor'] is None
    merged={node['id']:node for page in (first_page,second_page) for node in page['nodes']}
    assert len(merged)==4
    assert {node['id'] for node in merged.values() if node['kind']=='query'}=={first,second}


def test_graph_same_work_with_different_urls_and_doi_spelling_is_one_source(graph_room):
    client,headers,room,_=graph_room
    original=paper(1)
    alias={**original,'url':'https://dx.doi.org/10.1000/PAPER1/'}
    query(room['id'],'First URL',[original])
    query(room['id'],'Second URL',[alias])
    graph=get_graph(client,headers,room['id'])
    assert len([node for node in graph['nodes'] if node['kind']=='source'])==1
    assert len(graph['edges'])==2
    assert source_graph.canonical_url(original['url'])==source_graph.canonical_url(alias['url'])


def test_graph_permissions_cursor_scope_and_empty_room(graph_room):
    client,headers,room,outsider=graph_room
    path=f"/api/rooms/{room['id']}/source-graph"
    assert client.get(path).status_code==401
    assert client.get(path,headers={'Authorization':f"Bearer {outsider['token']}"}).status_code==403
    empty=get_graph(client,headers,room['id'])
    assert empty['nodes']==[] and empty['edges']==[] and empty['query_total']==0
    assert client.get(path,headers=headers,params={'limit':0}).status_code==422
    assert client.get(path,headers=headers,params={'limit':26}).status_code==422
    assert client.get(path,headers=headers,params={'cursor':'invalid'}).status_code==400
    query(room['id'],'First query',[paper(1,[999])])
    query(room['id'],'Second query',[paper(2)])
    cursor=get_graph(client,headers,room['id'],limit=1)['next_cursor']
    other=client.post('/api/rooms',headers=headers,json={'title':'Other graph room'}).json()
    query(other['id'],'Private query',[paper(999)])
    assert client.get(f"/api/rooms/{other['id']}/source-graph",headers=headers,
        params={'cursor':cursor}).status_code==400
    graph=get_graph(client,headers,room['id'])
    assert not any(node.get('work_id')=='https://openalex.org/W999' for node in graph['nodes'])
    assert not any(edge['kind']=='cites' for edge in graph['edges'])


def mock_metadata(monkeypatch,handler):
    real_client=httpx.AsyncClient
    monkeypatch.setattr(source_graph.httpx,'AsyncClient',
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler),**kwargs))


def test_old_papers_fetch_reference_metadata_in_one_cached_batch(graph_room,monkeypatch):
    client,headers,room,_=graph_room
    query(room['id'],'Legacy query',[paper(1,None),paper(2,None)])
    calls=[]
    def respond(request):
        calls.append(request)
        assert request.url.host=='api.openalex.org'
        assert 'referenced_works' in request.url.params['select']
        return httpx.Response(200,json={'results':[
            {'id':'https://openalex.org/W1','referenced_works':['https://openalex.org/W2']},
            {'id':'https://openalex.org/W2','referenced_works':[]}]})
    mock_metadata(monkeypatch,respond)
    graph=get_graph(client,headers,room['id'])
    assert len(calls)==1
    assert len([edge for edge in graph['edges'] if edge['kind']=='cites'])==1
    assert all(node['citation_data']=='available' for node in graph['nodes'] if node['kind']=='source')
    get_graph(client,headers,room['id'])
    assert len(calls)==1


def test_reference_failure_preserves_provenance_and_is_not_retried_immediately(graph_room,monkeypatch):
    client,headers,room,_=graph_room
    query(room['id'],'Offline query',[paper(1,None),paper(2,None)])
    calls=[]
    def fail(request):
        calls.append(request)
        raise httpx.ConnectError('Test offline',request=request)
    mock_metadata(monkeypatch,fail)
    graph=get_graph(client,headers,room['id'])
    assert len(graph['nodes'])==3
    assert len(graph['edges'])==2
    assert all(edge['kind']=='retrieved' for edge in graph['edges'])
    assert all(node['citation_data']=='unavailable' for node in graph['nodes'] if node['kind']=='source')
    get_graph(client,headers,room['id'])
    assert len(calls)==1


def test_graph_backfill_is_idempotent_and_preserves_known_references(graph_room):
    client,headers,room,_=graph_room
    query(room['id'],'Old query',[paper(1,[2]),paper(2)])
    with storage.connection() as connection:
        connection.execute('DELETE FROM source_graph_papers')
        connection.execute("DELETE FROM meta WHERE key='source_graph_v1'")
    storage.init_db()
    with patch('app.source_graph.index_papers',side_effect=AssertionError('Repeated graph backfill')):
        storage.init_db()
    graph=get_graph(client,headers,room['id'])
    assert len([edge for edge in graph['edges'] if edge['kind']=='cites'])==1
    query(room['id'],'Later retrieval without references',[paper(1,None)])
    graph=get_graph(client,headers,room['id'])
    assert len([edge for edge in graph['edges'] if edge['kind']=='cites'])==1


def test_openalex_search_retains_reference_metadata(monkeypatch):
    def respond(request):
        assert 'referenced_works' in request.url.params['select']
        return httpx.Response(200,json={'results':[{'id':'https://openalex.org/W1',
            'title':'Recorded references','doi':'https://doi.org/10.1000/one',
            'referenced_works':['https://openalex.org/W2']}]})
    mock_metadata(monkeypatch,respond)
    papers,origin=asyncio.run(search_papers('Test scholarly query'))
    assert origin=='openalex'
    assert papers[0]['referenced_works']==['https://openalex.org/W2']
