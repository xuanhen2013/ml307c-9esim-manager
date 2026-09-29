import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'deploy/shared'))
from notification_utils import NotificationError, send_apprise_notification
from ml307_backend import ML307Backend

TARGET = {'id':'wechat','label':'WeChat','url':'pushplus://'+'a'*32,'enabled':True}


class Notifications(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.send = Mock()
        self.backend = ML307Backend('fake',self.temp.name,lambda:[TARGET],self.send)
        self.backend.store.ingest([], {})
        self.backend.store.ingest([{'pdu':'00','number':'123','text':'private OTP 123456','timestamp':'now'}],{'iccid':'test'})

    def test_pushplus_business_error_is_safe_and_non_retryable(self):
        response = Mock(status_code=200)
        response.json.return_value = {'code':905,'msg':'secret-token private OTP 123456'}
        with patch('requests.post',return_value=response) as post:
            with self.assertRaises(NotificationError) as caught:
                send_apprise_notification([TARGET],'test','neutral message')
        self.assertIn('实名认证',str(caught.exception))
        self.assertNotIn('secret',str(caught.exception))
        self.assertNotIn('123456',str(caught.exception))
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(post.call_args.args[0],'https://www.pushplus.plus/send')
        self.assertFalse(post.call_args.kwargs['allow_redirects'])

    def test_accepted_notification_is_not_reposted(self):
        response = Mock(status_code=200)
        response.json.return_value = {'code':200,'data':'receipt'}
        with patch('requests.post',return_value=response) as post:
            send_apprise_notification([TARGET],'test','neutral')
        self.assertEqual(post.call_count,1)

    def test_limit_token_validation_and_unknown_errors_stop(self):
        for code in (900,903,905,999,'secret-token'):
            response = Mock(status_code=200)
            response.json.return_value = {'code':code,'msg':'private data'}
            with self.subTest(code=code),patch('requests.post',return_value=response):
                with self.assertRaises(NotificationError) as caught:
                    send_apprise_notification([TARGET],'test','neutral')
                self.assertFalse(caught.exception.retryable)
                self.assertNotIn('secret-token',str(caught.exception))

    def test_uncertain_timeout_is_not_automatically_resent(self):
        with patch('requests.post',side_effect=requests.ReadTimeout('secret-token')):
            with self.assertRaises(NotificationError) as caught:
                send_apprise_notification([TARGET],'test','neutral')
        self.assertFalse(caught.exception.retryable)
        self.assertNotIn('secret',str(caught.exception))

    def test_account_error_blocks_future_messages_and_survives_restart(self):
        self.send.side_effect = NotificationError('PushPlus 905：账户未进行实名认证',retryable=False)
        self.backend.notify_once()
        self.backend.store.ingest([{'pdu':'01','number':'123','text':'new','timestamp':'now'}],{'iccid':'test'})
        restarted = ML307Backend('fake',self.temp.name,lambda:[TARGET],self.send)
        restarted.notify_once()
        self.assertEqual(self.send.call_count,1)
        self.assertIn('905',restarted.notify_error)
        self.assertTrue(restarted.store.notification_state(TARGET)['blocked'])

    def test_temporary_errors_back_off_and_stop_after_three(self):
        self.send.side_effect = RuntimeError('secret-token private message')
        with patch('ml307_backend.time.time',return_value=1000): self.backend.notify_once()
        with patch('ml307_backend.time.time',return_value=1001): self.backend.notify_once()
        self.assertEqual(self.send.call_count,1)
        for now in (1100,1300,2000):
            with patch('ml307_backend.time.time',return_value=now): self.backend.notify_once()
        self.assertEqual(self.send.call_count,3)
        self.assertNotIn('secret',self.backend.notify_error)
        self.assertIn('三次',self.backend.notify_error)

    def test_old_unbounded_retries_are_not_replayed_on_upgrade(self):
        with self.backend.store.connect() as db:
            db.execute("INSERT INTO deliveries VALUES (1,'wechat',0,3000,0)")
        self.backend.notify_once()
        self.send.assert_not_called()

    def test_channel_failure_does_not_block_another_channel(self):
        other = dict(TARGET,id='other')
        self.backend.targets_callback = lambda:[TARGET,other]
        self.send.side_effect = [NotificationError('905',retryable=False),None]
        self.backend.notify_once()
        with self.backend.store.connect() as db:
            delivered = dict(db.execute('SELECT target_id,delivered FROM deliveries'))
        self.assertEqual(delivered,{'wechat':0,'other':1})

    def test_changed_credentials_clear_channel_block_but_do_not_log_token(self):
        self.send.side_effect = NotificationError('903',retryable=False)
        self.backend.notify_once()
        changed = dict(TARGET,url='pushplus://'+'b'*32)
        state = self.backend.store.notification_state(changed)
        self.assertFalse(state['blocked'])
        self.assertEqual(state['error'],'')
        self.assertNotIn('b'*32,json.dumps(state))


if __name__ == '__main__': unittest.main()
