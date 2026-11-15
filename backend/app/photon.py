"""Photon Beta 2.0 REST integration: Users, Spaces, Messages.

Uses the documented /v1/projects/{id}/messages contract. No Stable SDK.
Polling avoids requiring public webhooks during a VS Code demo. Supports direct
conversations; WhatsApp Business group support should not be assumed.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from .config import settings
from .storage import claim_message, meta_get, meta_set

log=logging.getLogger(__name__)

def enabled():
    return bool(settings.photon_enabled and settings.photon_key and settings.photon_project)

def photon_client():
    from photon_api import AsyncPhoton
    return AsyncPhoton(headers={'Authorization': f'Bearer {settings.photon_key}'})


async def send_message(space_id, sender_id, text, idempotency, parent_id=None):
    from photon_api.rpc_generated import SendMessageHeader, SendMessageInput

    body = {'spaceId': space_id, 'senderId': sender_id,
            'content': {'type': 'text', 'format': 'plain', 'text': text[:3000]}}
    if parent_id:
        body['parentMessageId'] = parent_id
    async with photon_client() as photon:
        return await photon.projects.send_message(SendMessageInput.model_validate({
            'path': {'id': settings.photon_project},
            'headers': SendMessageHeader.model_validate({'idempotency-key': idempotency}),
            'body': body,
        }))


async def pull_messages(since):
    from photon_api.rpc_generated import ListMessagesInput

    params = {'direction': 'received', 'createdSince': since, 'order': 'asc', 'pageSize': 100}
    if settings.photon_platform:
        params['platform'] = settings.photon_platform
    collected = []
    async with photon_client() as photon:
        while True:
            page = await photon.projects.list_messages(ListMessagesInput.model_validate({
                'path': {'id': settings.photon_project}, 'query': params,
            }))
            data = page.model_dump(mode='json', by_alias=True)
            collected.extend(data['items'])
            page_token = data.get('nextPageToken')
            if not page_token:
                return collected
            params['pageToken'] = page_token


def parse_inbound(msg):
    """Fail closed on missing IDs, non-human senders and unsupported content."""
    if msg.get('status')!='received':return None
    sender=msg.get('sentBy') or {}
    recipient=msg.get('receivedBy') or {}
    space=msg.get('space') or {}
    content=msg.get('content') or {}
    if settings.photon_platform and msg.get('platform')!=settings.photon_platform:return None
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
    cursor_key=f'photon_cursor:{settings.photon_project}:{settings.photon_platform}'
    cursor=meta_get(cursor_key) or (datetime.now(timezone.utc)-timedelta(seconds=15)).isoformat().replace('+00:00','Z')
    while True:
        try:
            poll_started=datetime.now(timezone.utc)
            incoming=await pull_messages(cursor)
            for item in incoming:
                parsed=parse_inbound(item)
                # Advance only after a successful pass; duplicates are handled by DB.
                if parsed and claim_message(parsed['id']):
                    await handle_message(parsed)
            cursor=(poll_started-timedelta(seconds=6)).isoformat(timespec='milliseconds').replace('+00:00','Z')
            meta_set(cursor_key,cursor)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception('Photon poll failed: %s',e)
        await asyncio.sleep(settings.photon_poll_seconds)
