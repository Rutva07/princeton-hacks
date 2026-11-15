"""Live OpenAlex retrieval, explicit offline catalog fallback. Never invent retrieval."""
import re
from urllib.parse import quote
import httpx
from .config import settings

DEMO_PAPERS = [
  {'id':'arxiv:2210.02747', 'title':'Flow Matching for Generative Modeling',
   'authors':'Yaron Lipman et al.', 'year':2022,'url':'https://arxiv.org/abs/2210.02747',
   'summary':'Introduces flow matching for training continuous normalizing flows via regression on conditional vector fields.',
   'method':'Continuous-time flow matching; linear interpolation; vector field regression.'},
  {'id':'arxiv:2209.03003','title':'Flow Straight and Fast: Learning to Generate and Transfer Data with Rectified Flow',
   'authors':'Xingchao Liu et al.','year':2022,'url':'https://arxiv.org/abs/2209.03003',
   'summary':'Studies rectified flow and trajectory straightening in generative transport.',
   'method':'Rectified trajectories and velocity-field learning.'},
  {'id':'arxiv:1412.6980','title':'Adam: A Method for Stochastic Optimization',
   'authors':'Diederik P. Kingma and Jimmy Ba','year':2014,'url':'https://arxiv.org/abs/1412.6980',
   'summary':'Proposes Adam, an adaptive moment-based first-order optimizer.',
   'method':'Adam optimization.'},
  {'id':'arxiv:1711.05101','title':'Decoupled Weight Decay Regularization',
   'authors':'Ilya Loshchilov and Frank Hutter','year':2017,'url':'https://arxiv.org/abs/1711.05101',
   'summary':'Introduces decoupled weight decay for adaptive optimization, commonly implemented as AdamW.',
   'method':'AdamW optimizer with decoupled weight decay.'},
  {'id':'arxiv:1608.03983','title':'SGDR: Stochastic Gradient Descent with Warm Restarts',
   'authors':'Ilya Loshchilov and Frank Hutter','year':2016,'url':'https://arxiv.org/abs/1608.03983',
   'summary':'Investigates cosine learning-rate annealing and warm restarts.',
   'method':'Cosine annealing schedules.'},
]

def abstract_from_index(index):
    if not index:return ''
    positions={i:word for word, ids in index.items() for i in ids}
    return ' '.join(positions[i] for i in sorted(positions))[:900]

async def search_papers(query, limit=8):
    # OpenAlex /works provides verified metadata, abstract when available.
    params={'search':query,'per-page':limit,
            'select':'id,title,publication_year,authorships,doi,primary_location,abstract_inverted_index,cited_by_count,type,open_access,referenced_works'}
    if settings.openalex_email:params['mailto']=settings.openalex_email
    try:
        async with httpx.AsyncClient(timeout=12,follow_redirects=True,headers={'User-Agent':'CausalForge-HackPrinceton/1.0 (literature demo)'}) as client:
            res=await client.get('https://api.openalex.org/works',params=params)
            res.raise_for_status()
            works=res.json().get('results',[])
        papers=[]
        for w in works:
            url = w.get('doi') or ((w.get('primary_location') or {}).get('landing_page_url')) or w.get('id','')
            if not url.startswith('https://'): continue
            authors=', '.join((a.get('author') or {}).get('display_name','') for a in w.get('authorships',[])[:3])
            papers.append({'id':w.get('id'),'title':w.get('title') or 'Untitled', 'year':w.get('publication_year'),
                'authors':authors, 'url':url, 'summary':abstract_from_index(w.get('abstract_inverted_index')),
                'method':'Requires full-paper verification', 'origin':'openalex',
                'citation_count':w.get('cited_by_count',0),'work_type':w.get('type'),
                'doi':w.get('doi'),'referenced_works':w.get('referenced_works'),
                'open_access':bool((w.get('open_access') or {}).get('is_oa')),
                'evidence_level':'OpenAlex metadata/abstract; citation count is contextual, not a quality score'})
        if papers:return papers, 'openalex'
        return [], 'openalex_empty'
    except (httpx.HTTPError, ValueError, KeyError):
        if re.search(r'generative|diffusion|flow|train|model|optimizer|neural|machine learning',query,re.I):
            return [{**p,'origin':'demo_catalog','evidence_level':'curated synopsis; not full-paper reviewed'} for p in DEMO_PAPERS], 'demo_catalog'
        return [], 'unavailable'


def plan_pipelines(prompt, papers):
    """Clearly scoped executable templates, not paper-faithful reproductions."""
    if re.search(r'traffic|congestion|road|braess',prompt,re.I):
        return [
          {'id':'baseline','name':'Baseline — existing two-route network',
           'description':'Assign 4,000 vehicles to two symmetric routes until route costs converge.',
           'optimizer':'MSA','learning_rate':None,'weight_decay':None,'schedule':'1 / iteration',
           'supported_experiment':'braess_traffic_assignment','is_reproduction':False,
           'rationale':'Establish equilibrium travel time before changing network topology.',
           'source_urls':[p['url'] for p in papers[:3]]},
          {'id':'candidate','name':'Intervention — add zero-cost connector',
           'description':'Add an A→B road, repeat the same demand assignment, and compare equilibrium travel time.',
           'optimizer':'MSA','learning_rate':None,'weight_decay':None,'schedule':'1 / iteration',
           'supported_experiment':'braess_traffic_assignment','is_reproduction':False,
           'rationale':'Tests whether a locally attractive road can worsen system-level congestion.',
           'source_urls':[p['url'] for p in papers[:3]]},
        ]
    return [
      {'id':'baseline','name':'Baseline — Flow Matching + Adam',
       'description':'2D flow-matching MLP; Adam with constant learning rate.',
       'optimizer':'Adam','learning_rate':0.003,'weight_decay':0,'schedule':'constant',
       'supported_experiment':'toy_2d_flow_matching','is_reproduction':False,
       'rationale':'Controlled experimental baseline; illustrative implementation, not a paper reproduction.',
       'source_urls':['https://arxiv.org/abs/2210.02747','https://arxiv.org/abs/1412.6980']},
      {'id':'candidate','name':'Candidate — Flow Matching + AdamW + Cosine LR',
       'description':'Same architecture/data/updates; AdamW with cosine-decayed learning rate.',
       'optimizer':'AdamW','learning_rate':0.003,'weight_decay':0.001,'schedule':'cosine',
       'supported_experiment':'toy_2d_flow_matching','is_reproduction':False,
       'rationale':'Combines an optimizer and schedule from published techniques; hypothesis requires validation.',
       'source_urls':['https://arxiv.org/abs/2210.02747','https://arxiv.org/abs/1711.05101','https://arxiv.org/abs/1608.03983']},
    ]

async def ingest_arxiv_papers(urls: list[str]):
    """Extract first 10 pages from user-supplied arXiv PDFs, no arbitrary URL fetching.

    Limits: at most five arXiv papers, <=12 MB each, and <=25k extracted chars.
    Snippets are saved as data; documents are not treated as instructions.
    """
    import fitz
    out=[]
    for raw in urls[:5]:
        match=re.fullmatch(r'https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v[0-9]+)?(?:\.pdf)?/?',raw.strip())
        if not match:continue
        arxiv_id=match.group(1)
        url=f'https://arxiv.org/abs/{arxiv_id}'
        try:
            async with httpx.AsyncClient(timeout=20,follow_redirects=True) as client:
                r=await client.get(f'https://arxiv.org/pdf/{arxiv_id}',headers={'User-Agent':'CausalForge-HackPrinceton/1.0'})
                r.raise_for_status()
            if len(r.content)>12_000_000:continue
            doc=fitz.open(stream=r.content,filetype='pdf')
            full_text='\n'.join(page.get_text('text') for page in list(doc)[:10])[:25000]
            doc.close()
            out.append({'id':f'arxiv:{arxiv_id}','title':f'arXiv:{arxiv_id} (user provided PDF)',
                 'authors':'See original paper','year':None,'url':url,
                 'summary':full_text[:750].replace('\n',' '),
                 'method':'Full-text excerpt available; methodology requires expert interpretation',
                 'full_text_excerpt':full_text,'origin':'user_supplied_arxiv',
                 'evidence_level':'PDF text extracted, first up to 10 pages'})
        except (httpx.HTTPError,ValueError,RuntimeError,fitz.FileDataError):
            out.append({'id':f'arxiv:{arxiv_id}','title':f'arXiv:{arxiv_id} (download failed)',
                 'authors':'Unknown','year':None,'url':url,'summary':'PDF retrieval unavailable.',
                 'method':'Not analyzed','origin':'user_supplied_arxiv','evidence_level':'URL only; no extracted text'})
    return out
