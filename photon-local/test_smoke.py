import unittest
from unittest.mock import Mock

from smoke import scan


class PhotonSmokeTest(unittest.TestCase):
    def setUp(self):
        self.message = {
            'id': 'm1', 'status': 'received', 'platform': 'imessage',
            'sentBy': {'kind': 'human', 'handle': '+15555550100'},
            'receivedBy': {'kind': 'agent', 'id': 'agent-1'},
            'space': {'kind': 'direct', 'id': 'space-1'},
            'content': {'type': 'text', 'format': 'plain', 'text': 'PING'},
        }

    def test_ping_replies_in_same_space(self):
        request = Mock(side_effect=[{'items': [self.message]}, {}])
        self.assertTrue(scan(request, 'now', '+15555550100'))
        request.assert_called_with(message_id='m1', body={
            'spaceId': 'space-1', 'senderId': 'agent-1',
            'content': {'type': 'text', 'format': 'plain', 'text': 'PONG from Labthread'},
        })

    def test_ignores_other_platforms_and_senders(self):
        request = Mock(return_value={'items': [
            {**self.message, 'platform': 'sms'},
            {**self.message, 'sentBy': {'kind': 'human', 'handle': '+15555550101'}},
        ]})
        self.assertFalse(scan(request, 'now', '+15555550100'))
        request.assert_called_once()

    def test_read_only_check_does_not_send(self):
        request = Mock(return_value={'items': [self.message]})
        self.assertFalse(scan(request, 'now', check_only=True))
        request.assert_called_once()
