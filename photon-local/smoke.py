"""Local Photon 2.0 iMessage smoke test. Send PING to receive PONG."""
import argparse
from datetime import datetime, timezone
import getpass
import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

API = 'https://api.photon.codes/v1/projects'


def request_messages(project, key, *, params=None, body=None, message_id=None):
    url = f'{API}/{quote(project, safe="")}/messages'
    headers = {'Authorization': f'Bearer {key}', 'Accept': 'application/json'}
    if params:
        url += '?' + urlencode(params)
    if body is not None:
        headers['Content-Type'] = 'application/json'
        headers['Idempotency-Key'] = f'causalforge-smoke-{message_id}'
    request = Request(url, headers=headers, data=None if body is None else json.dumps(body).encode())
    with urlopen(request, timeout=15) as response:
        payload = response.read()
        return json.loads(payload) if payload else {}


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
            'content': {'type': 'text', 'format': 'plain', 'text': 'PONG from CausalForge'}}


def scan(request, since, allowed_sender=None, *, check_only=False):
    params = {'direction': 'received', 'createdSince': since, 'order': 'asc', 'pageSize': 100}
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
    project = os.getenv('PHOTON_PROJECT_ID') or input('Photon Beta project ID: ').strip()
    key = os.getenv('PHOTON_API_KEY') or getpass.getpass('Photon Beta API key: ').strip()
    if not project or not key:
        parser.exit(1, 'Both Photon Beta credentials are required.\n')

    def request(**kwargs):
        return request_messages(project, key, **kwargs)

    since = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    try:
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
    except HTTPError as error:
        parser.exit(1, f'Photon returned HTTP {error.code}. Check the project, key, and Beta dashboard logs.\n')
    except (URLError, TimeoutError, ValueError) as error:
        parser.exit(1, f'Photon test failed: {error}\n')


if __name__ == '__main__':
    main()
