"""Photon Beta 2.0 REST integration: Users, Spaces, Messages.

Uses the documented /v1/projects/{id}/messages contract. No Stable SDK.
Polling avoids requiring public webhooks during a VS Code demo. Supports direct
conversations; WhatsApp Business group support should not be assumed.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
import httpx
from .config import settings
from .storage import claim_message, meta_get, meta_set

log=logging.getLogger(__name__)
BASE='https://api.photon.codes/v1'

def enabled():
    return bool(settings.photon_enabled and settings.photon_key and settings.photon_project)

async def request(method, path, **kwargs):
    if not enabled():
        raise RuntimeError('Photon is not configured')
    headers={'Authorization':f'Bearer {settings.photon_key}','Accept':'application/json'}
    headers.update(kwargs.pop('headers',{}))
    async with httpx.AsyncClient(timeout=20) as client:
        response=await client.request(method,f'{BASE}/projects/{settings.photon_project}{path}',headers=headers,**kwargs)
        response.raise_for_status()
        return response.json()

async def send_message(space_id, sender_id, text, idempotency, parent_id=None):
    body={'spaceId':space_id,'senderId':sender_id,
          'content':{'type':'text','format':'plain','text':text[:3000]}}
    if parent_id:body['parentMessageId']=parent_id
    # Photon accepts submission for asynchronous dispatch, not guaranteed delivery.
    return await request('POST','/messages',json=body,headers={'Idempotency-Key':idempotency})

async def pull_messages(since):
    page_token=None
    collected=[]
    for _ in range(10):
        params={'direction':'received','createdSince':since,'order':'asc','pageSize':100}
        if page_token:params['pageToken']=page_token
        data=await request('GET','/messages',params=params)
        collected+=data.get('items',[])
        page_token=data.get('nextPageToken')
        if not page_token:break
    return collected

def parse_inbound(msg):
    """Fail closed on missing IDs, non-human senders and unsupported content."""
    if msg.get('status')!='received':return None
    sender=msg.get('sentBy') or {}
    recipient=msg.get('receivedBy') or {}
    space=msg.get('space') or {}
    content=msg.get('content') or {}
    if sender.get('kind')!='human' or recipient.get('kind')!='agent':return None
    if not (msg.get('id') and space.get('id') and recipient.get('id')):return None
    if space.get('kind')!='direct' and not settings.photon_groups:return None
    if settings.photon_allowed_senders and sender.get('handle') not in settings.photon_allowed_senders:return None
    if content.get('type')!='text' or content.get('format')!='plain':return None
    text=content.get('text','').strip()
    if not text:return None
    return {'id':msg['id'],'text':text,'space_id':space['id'],
            'sender_id':recipient['id'],'human_sender':sender.get('handle',''),
            'platform':msg.get('platform'),'kind':space.get('kind','direct')}

async def polling_loop(handle_message):
    if not enabled() or not settings.photon_poll_enabled:return
    log.info('Photon Beta polling started (group processing=%s)',settings.photon_groups)
    # Start 15 seconds before server startup to avoid replying to old history.
    cursor=meta_get('photon_cursor') or (datetime.now(timezone.utc)-timedelta(seconds=15)).isoformat().replace('+00:00','Z')
    while True:
        try:
            incoming=await pull_messages(cursor)
            for item in incoming:
                parsed=parse_inbound(item)
                # Advance only after a successful pass; duplicates are handled by DB.
                if parsed and claim_message(parsed['id']):
                    await handle_message(parsed)
            cursor=(datetime.now(timezone.utc)-timedelta(seconds=6)).isoformat(timespec='milliseconds').replace('+00:00','Z')
            meta_set('photon_cursor',cursor)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception('Photon poll failed: %s',e)
        await asyncio.sleep(settings.photon_poll_seconds)
