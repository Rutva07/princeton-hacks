"""Local Photon 2.0 iMessage smoke test. Send PING to receive PONG."""
import argparse
from datetime import datetime, timezone
import getpass
import os
import time


def pong_for(message, allowed_sender=None):
    sender = message.get('sentBy') or {}
    recipient = message.get('receivedBy') or {}
    space = message.get('space') or {}
    content = message.get('content') or {}
    if (
        (message.get('platform') or space.get('platform') or '').lower() != 'imessage'
        or message.get('status') != 'received'
        or not message.get('id')
        or space.get('kind') != 'direct'
        or not space.get('id')
        or sender.get('kind') != 'human'
        or (allowed_sender and sender.get('handle') != allowed_sender)
        or recipient.get('kind') != 'agent'
        or not recipient.get('id')
        or content.get('type') != 'text'
        or content.get('format') != 'plain'
        or (content.get('text') or '').strip().upper() != 'PING'
    ):
        return None
    return {'spaceId': space['id'], 'senderId': recipient['id'],
            'content': {'type': 'text', 'format': 'plain', 'text': 'PONG from Labthread'}}


def scan(request, since, allowed_sender=None, *, check_only=False):
    params = {'direction': 'received', 'createdSince': since, 'order': 'asc',
              'pageSize': 100, 'platform': 'imessage'}
    while True:
        page = request(params=params)
        if 'items' not in page or not isinstance(page['items'], list):
            raise ValueError('Unexpected Photon message-list response: expected an items array')
        if check_only:
            return False
        for message in page['items']:
            reply = pong_for(message, allowed_sender)
            if reply:
                request(body=reply, message_id=message['id'])
                return True
        token = page.get('nextPageToken')
        if not token:
            return False
        params['pageToken'] = token


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check API access without sending anything')
    parser.add_argument('--sender', help='Optional exact Photon handle for your test phone')
    parser.add_argument('--timeout', type=int, default=120, help='Seconds to wait for PING')
    args = parser.parse_args()
    try:
        from photon_api import ApiError, Photon
        from photon_api.rpc_generated import ListMessagesInput, SendMessageHeader, SendMessageInput
    except ImportError:
        parser.exit(1, 'Install the Photon SDK first: python3 -m pip install photonhq-api==0.5.0\n')

    project = (os.getenv('PHOTON_PROJECT_ID') or input('Photon Beta project ID: ')).strip()
    key = (os.getenv('PHOTON_API_KEY') or getpass.getpass('Photon Beta API key: ')).strip()
    if not project or not key:
        parser.exit(1, 'Both Photon Beta credentials are required.\n')

    method = 'GET'
    since = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    try:
        with Photon(headers={'Authorization': f'Bearer {key}'}) as photon:
            def request(*, params=None, body=None, message_id=None):
                nonlocal method
                if body is None:
                    method = 'GET'
                    page = photon.projects.list_messages(ListMessagesInput.model_validate({
                        'path': {'id': project}, 'query': params,
                    }))
                    return page.model_dump(mode='json', by_alias=True)

                method = 'POST'
                photon.projects.send_message(SendMessageInput.model_validate({
                    'path': {'id': project},
                    'headers': SendMessageHeader.model_validate({
                        'idempotency-key': f'causalforge-smoke-{message_id}',
                    }),
                    'body': body,
                }))

            scan(request, since, check_only=True)
            if args.check:
                print('Photon accepted the credentials and returned a message list.')
                return
            print('Ready. Send PING from your iPhone to the Photon iMessage line.', flush=True)
            deadline = time.monotonic() + args.timeout
            while time.monotonic() < deadline:
                if scan(request, since, args.sender):
                    print('Photon accepted PONG. Confirm it arrived in Messages.')
                    return
                time.sleep(3)
            parser.exit(1, 'No matching iMessage PING was received before timeout.\n')
    except ApiError as error:
        status = getattr(error, 'status', None) or getattr(error, 'status_code', None)
        request_id = getattr(error, 'request_id', None) or 'unavailable'
        detail = ' '.join(str(error).replace(key, '[redacted]').split())[:500]
        parser.exit(1, f'Photon {method} /messages failed (status {status}, request ID {request_id}).\n{detail}\n')
    except (OSError, TimeoutError, ValueError) as error:
        detail = ' '.join(str(error).replace(key, '[redacted]').split())[:500]
        parser.exit(1, f'Photon test failed: {detail}\n')


if __name__ == '__main__':
    main()
