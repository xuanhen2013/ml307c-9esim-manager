import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy' / 'shared'))
from feishu_bot import FeishuBot, FeishuAPI
from ml307_backend import SmsStore


class BotTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.config = root / 'feishu.json'
        self.config.write_text(json.dumps({'app_id': 'app', 'app_secret': 'secret',
            'pair_hash': hashlib.sha256(b'test-code').hexdigest(), 'pair_expires': time.time()+600}))
        self.sms = SmsStore(root / 'sms.sqlite3')
        self.sms.ingest([], {})
        self.add_sms('history')
        self.api = Mock()
        self.api.send.return_value = 'om_menu'
        self.status = {'profiles': [{'iccid': '1', 'display_name': 'Lebara', 'is_active': True},
                                   {'iccid': '2', 'display_name': 'eSIM.gg', 'is_active': False}],
                       'modem': {'operator_name': '中国联通', 'registration': 'roaming', 'access_tech': 'LTE',
                                 'signal_details': {'rssi_text': '-69 dBm', 'rsrp_text': '-99 dBm'}}}
        self.start_action = Mock(return_value='job1')
        self.get_action = Mock(return_value={'state': 'done'})
        self.bot = self.new_bot()

    def new_bot(self):
        return FeishuBot(self.config, self.sms, lambda: self.status, self.start_action, self.get_action, api=self.api)

    def add_sms(self, text):
        self.sms.ingest([{'pdu': text.encode().hex(), 'number': '123', 'text': text, 'timestamp': '2026-09-27'}],
                        {'iccid': '2', 'profileName': 'eSIM.gg'})

    def message(self, text, owner='ou_owner', chat='oc_private', kind='p2p', message_id=None):
        return {'header': {'app_id': 'app', 'tenant_key': 'tenant'}, 'event': {
            'sender': {'sender_type': 'user', 'sender_id': {'open_id': owner}, 'tenant_key': 'tenant'},
            'message': {'chat_type': kind, 'chat_id': chat, 'message_type': 'text',
                        'content': json.dumps({'text': text}), 'create_time': str(int(time.time()*1000)),
                        'message_id': message_id or str(time.time_ns())}}}

    def bind(self):
        self.bot.handle(self.message('绑定 test-code'))
        self.bot.flush()
        self.api.reset_mock()

    def callback(self, owner='ou_owner', chat='oc_private'):
        with self.bot.state.connect() as db:
            nonce = db.execute("SELECT nonce FROM buttons WHERE action='switch'").fetchone()[0]
        return {'header': {'app_id': 'app'}, 'event': {'operator': {'open_id': owner, 'tenant_key': 'tenant'},
                'context': {'open_chat_id': chat, 'open_message_id': 'om_menu'},
                'action': {'value': {'nonce': nonce}}}}

    def test_binding_is_private_fresh_and_one_owner(self):
        self.bot.handle(self.message('绑定 test-code', kind='group'))
        self.bot.handle(self.message('wrong'))
        old = self.message('绑定 test-code')
        old['event']['message']['create_time'] = '1'
        self.bot.handle(old)
        self.assertNotIn('owner', self.bot.owner)
        self.bind()
        self.bot.handle(self.message('绑定 test-code', owner='other'))
        self.assertEqual(self.new_bot().owner['owner'], 'ou_owner')

    def test_expired_pairing_cannot_bind(self):
        self.bot.config['pair_expires'] = 1
        self.bot.handle(self.message('绑定 test-code'))
        self.assertNotIn('owner', self.bot.owner)

    def test_only_owner_private_context_can_control(self):
        self.bind()
        self.bot.handle(self.callback(owner='other'), True)
        self.bot.handle(self.callback(chat='group'), True)
        self.bot.handle(self.message('状态', owner='other'))
        self.bot.flush()
        self.start_action.assert_not_called()
        self.api.send.assert_not_called()

    def test_callback_ack_does_not_touch_modem_or_network(self):
        self.bind()
        self.assertIn('已收到', self.bot.receive(self.callback(), True))
        self.start_action.assert_not_called()
        self.api.send.assert_not_called()
        self.assertEqual(self.bot.commands.qsize(), 1)

    def test_numbers_command_and_menu_show_all_saved_numbers(self):
        self.status['profiles'][0]['phone_number'] = '+447700900123'
        self.status['profiles'][1]['phone_number'] = '+12025550123'
        self.bind()
        self.bot.handle(self.message('号码', owner='other'))
        self.bot.flush()
        self.api.send.assert_not_called()
        self.bot.handle(self.message('号码'))
        self.bot.menu()
        self.bot.flush()
        text = next(c.args[2]['text'] for c in self.api.send.call_args_list if c.args[1]=='text')
        self.assertIn('Lebara：+447700900123', text)
        self.assertIn('eSIM.gg：+12025550123', text)
        card = next(c.args[2] for c in self.api.send.call_args_list if c.args[1]=='interactive')
        self.assertIn('+447700900123', json.dumps(card))
        self.assertIn('+12025550123', json.dumps(card))

    def test_sms_recipient_is_original_card_not_current_active_card(self):
        self.bind()
        self.bot.number_lookup = lambda iccid: {'1':'+447700900123','2':'+12025550123'}.get(iccid,'')
        self.add_sms('New verification code 123456')
        self.bot.collect_sms()
        self.bot.flush()
        text = self.api.send.call_args.args[2]['text']
        self.assertIn('收件号码：+12025550123', text)
        self.assertNotIn('+447700900123', text)
        self.assertIn('发件人：123', text)

    def test_stale_receive_reminder_is_dropped_before_network_delivery(self):
        self.bind()
        self.bot.notice_valid = lambda key: not key.startswith('receive-reminder:')
        self.bot.text('stale deadline',key='receive-reminder:old')
        self.bot.text('ordinary notification',key='keepalive:current')
        self.bot.flush()
        self.api.send.assert_called_once()
        self.assertEqual(self.api.send.call_args.args[2],{'text':'ordinary notification'})
        with self.bot.state.connect() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM outbox WHERE id='receive-reminder:old'").fetchone())

    def test_duplicate_buttons_do_not_switch_twice_including_restart(self):
        self.bind()
        callback = self.callback()
        self.bot.handle(callback, True)
        self.bot.handle(callback, True)
        self.bot = self.new_bot()
        self.bot.handle(callback, True)
        self.start_action.assert_called_once_with('switch_profile', {'iccid': '2'},
                                                  metadata={'kind': 'feishu'}, reject_if_busy=True)
        with self.bot.state.connect() as db:
            self.assertTrue(any('服务曾重启' in r[0] for r in db.execute('SELECT content FROM outbox').fetchall()))

    def test_stale_card_and_expired_button_are_rejected(self):
        self.bind()
        callback = self.callback()
        self.status['profiles'][0]['is_active'] = False
        self.status['profiles'][1]['is_active'] = True
        self.bot.handle(callback, True)
        self.start_action.assert_not_called()
        self.bot.menu()
        self.bot.flush()
        with self.bot.state.connect() as db:
            db.execute('UPDATE buttons SET expires=0')
        self.bot.handle(self.callback(), True)
        self.start_action.assert_not_called()

    def test_message_retry_is_deduplicated(self):
        self.bind()
        message = self.message('状态', message_id='same')
        self.bot.handle(message)
        self.bot.handle(message)
        self.bot.flush()
        self.api.send.assert_called_once()
        self.assertEqual(self.api.send.call_args.args[1], 'interactive')

    def test_sms_baseline_retry_and_restart(self):
        self.bind()
        self.bot.collect_sms()
        self.bot.flush()
        self.api.send.assert_not_called()
        self.add_sms('验证码 123456')
        self.bot.collect_sms()
        self.api.send.side_effect = RuntimeError('API error')
        self.bot.flush()
        failed_uuid = self.api.send.call_args.args[3]
        self.api.send.side_effect = None
        with self.bot.state.connect() as db:
            db.execute('UPDATE outbox SET retry_at=0')
        self.bot = self.new_bot()
        self.bot.collect_sms()
        self.bot.flush()
        self.assertEqual(self.api.send.call_args.args[3], failed_uuid)
        self.assertIn('验证码 123456', self.api.send.call_args.args[2]['text'])
        self.api.reset_mock()
        self.bot = self.new_bot()
        self.bot.collect_sms()
        self.bot.flush()
        self.api.send.assert_not_called()

    def test_failed_switch_does_not_claim_success(self):
        self.bind()
        self.bot.handle(self.callback(), True)
        self.get_action.return_value = {'state': 'error', 'error': '注册超时'}
        self.bot.monitor_jobs()
        self.bot.flush()
        texts = [c.args[2].get('text', '') for c in self.api.send.call_args_list]
        self.assertTrue(any('注册超时' in t for t in texts))
        self.assertFalse(any('完成网络注册' in t for t in texts))

    def test_api_checks_business_error_without_leaking_secret(self):
        api = FeishuAPI({'app_id': 'app', 'app_secret': 'secret'})
        api.session = Mock()
        api.session.post.return_value.json.return_value = {'code': 99991672, 'msg': 'secret'}
        with self.assertRaisesRegex(RuntimeError, '99991672') as exc:
            api.post('/auth/v3/tenant_access_token/internal', {}, authenticated=False)
        self.assertNotIn('secret', str(exc.exception))

    def test_sdk_event_roundtrip(self):
        import lark_oapi as lark
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
        from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse
        data = self.message('test')
        data['schema'] = '2.0'
        parsed = json.loads(lark.JSON.marshal(P2ImMessageReceiveV1(data)))
        self.assertEqual(parsed['header']['app_id'], 'app')
        self.assertEqual(parsed['event']['sender']['sender_type'], 'user')
        reply = P2CardActionTriggerResponse({'toast': {'type': 'info', 'content': '已收到'}})
        self.assertEqual(json.loads(lark.JSON.marshal(reply))['toast']['content'], '已收到')


if __name__ == '__main__':
    unittest.main()
