import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'deploy/shared'))
from ml307_modem import decode_deliver, GSM, Modem, parse_signal
from ml307_backend import ML307Backend, SmsStore, complete_messages


def pdu(text, *, ucs2=False, header=b''):
    prefix = bytes.fromhex('00') + bytes([0x40 if header else 0, 4, 0x91, 0x21, 0x43, 0, 8 if ucs2 else 0])
    prefix += bytes.fromhex('62906221436523')  # 2026-09-26 12:34:56 +08:00
    if ucs2:
        data = header + text.encode('utf-16-be')
        return (prefix + bytes([len(data)]) + data).hex()
    skip = (len(header)*8+6)//7
    bits = int.from_bytes(header, 'little')
    for i, char in enumerate(text):
        bits |= GSM.index(char) << ((skip+i)*7)
    udl = skip + len(text)
    return (prefix + bytes([udl]) + bits.to_bytes((udl*7+7)//8, 'little')).hex()


class SmsTests(unittest.TestCase):
    def test_signal_intervals_and_unknown_values(self):
        actual = parse_signal('+CSQ: 26,99', '+CESQ: 50,99,255,255,20,49')
        self.assertEqual(actual['rssi_text'], '-61 dBm')
        self.assertEqual(actual['rsrp_text'], '-92～-91 dBm')
        self.assertEqual(actual['rsrq_text'], '-10～-9.5 dB')
        unknown = parse_signal('+CSQ: 99,99', '+CESQ: 99,99,255,255,255,255')
        for key in ('csq','rssi_text','rsrp_text','rsrq_text'):
            self.assertIsNone(unknown[key])
        low = parse_signal('+CSQ: 0,99', '+CESQ: 0,99,255,255,0,0')
        self.assertEqual(low['rssi_text'], '≤ -113 dBm')
        self.assertEqual(low['rsrp_text'], '< -140 dBm')
        high = parse_signal('+CSQ: 31,99', '+CESQ: 63,99,255,255,34,97')
        self.assertEqual(high['rsrp_text'], '≥ -44 dBm')
        self.assertEqual(high['rsrq_text'], '≥ -3 dB')

    def test_plmn_separates_serving_network_from_sim_brand(self):
        modem = Modem.__new__(Modem)
        def responses(command):
            if command == 'AT+COPS?':
                return operators.pop(0)
            return {'AT+CPIN?':'+CPIN: READY','AT+CEREG?':'+CEREG: 0,5',
                    'AT+CSQ':'+CSQ: 26,99','AT+COPS=3,2':'OK','AT+COPS=3,0':'OK',
                    'AT+CESQ':'+CESQ: 50,99,255,255,20,49'}[command]
        operators = ['+COPS: 0,0,"Lebara",7','+COPS: 0,2,"46001",7']
        modem.at = MagicMock(side_effect=responses)
        status = modem.status()
        self.assertEqual(status['operator_name'], '中国联通')
        self.assertEqual(status['operator_code'], '46001')
        self.assertEqual(status['operator_reported_name'], 'Lebara')
        self.assertEqual(status['registration'], 'roaming')
        self.assertEqual(status['signal_details']['rssi_dbm'], -61)
        self.assertIn(('AT+COPS=3,0',), [call.args for call in modem.at.call_args_list])

    def test_failed_numeric_read_restores_format_and_never_uses_brand_as_network(self):
        modem = Modem.__new__(Modem)
        modem.at = MagicMock(side_effect=[
            '+CPIN: READY','+CEREG: 0,5','+COPS: 0,0,"Lebara",7','+CSQ: 20,99',
            'OK',RuntimeError('temporary failure'),'OK',RuntimeError('CESQ unsupported')])
        status = modem.status()
        self.assertEqual(status['operator_code'], '--')
        self.assertNotEqual(status['operator_name'], 'Lebara')
        self.assertEqual(status['signal_details']['rssi_text'], '-73 dBm')
        self.assertEqual(modem.at.call_args_list[-2].args, ('AT+COPS=3,0',))

    def test_gsm_and_timestamp(self):
        decoded = decode_deliver(pdu('Test code 654321'))
        self.assertEqual(decoded['text'], 'Test code 654321')
        self.assertEqual(decoded['number'], '+1234')
        self.assertEqual(decoded['timestamp'], '2026-09-26T12:34:56+08:00')

    def test_ucs2(self):
        self.assertEqual(decode_deliver(pdu('验证码：123456', ucs2=True))['text'], '验证码：123456')

    def test_multipart_with_septet_padding(self):
        raw = [pdu('Test ', header=bytes.fromhex('050003AF0201')),
               pdu('654321', header=bytes.fromhex('050003AF0202'))]
        parts = [dict(decode_deliver(value), pdu=value) for value in raw]
        self.assertEqual(complete_messages(parts[:1]), [])
        self.assertEqual(complete_messages(parts[::-1])[0]['text'], 'Test 654321')

    def test_persistence_attribution_and_per_channel_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'sms.sqlite3'
            store = SmsStore(path)
            old = dict(decode_deliver(pdu('old')), pdu=pdu('old'))
            new = dict(decode_deliver(pdu('new')), pdu=pdu('new'))
            profile = {'iccid':'test-profile','serviceProviderName':'test-card'}
            store.ingest([old], profile)
            self.assertEqual(store.pending('wechat'), [])
            self.assertEqual(store.messages()[0]['profile_iccid'], '')
            store.ingest([old,new], profile)
            store = SmsStore(path)  # A restart must not duplicate or reattribute history.
            store.ingest([old,new], {'iccid':'another-card'})
            self.assertEqual(len(store.messages()), 2)
            self.assertEqual(store.messages()[0]['profile_iccid'], 'test-profile')
            message = store.pending('wechat')[0]
            store.delivery_result(message['id'], 'wechat', True)
            self.assertEqual(store.pending('wechat'), [])
            self.assertEqual(len(store.pending('other-channel')), 1)
            store.delivery_result(message['id'], 'other-channel', False)
            self.assertEqual(store.pending('other-channel'), [])  # Retry backoff.

    def test_outbound_and_keepalive_are_blocked_before_device_access(self):
        with patch.dict(os.environ, {'MODEM_BACKEND':'ml307'}):
            spec = importlib.util.spec_from_file_location('admin_test', ROOT/'deploy/web_admin/4g_wifi_admin.py')
            admin = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(admin)
        for action in ('send_test_sms','run_keepalive_task','apply_network_selection','save_profile_smsc'):
            with self.subTest(action=action), self.assertRaises(ValueError):
                admin.start_action(action, {})
            with self.subTest(sync=action), self.assertRaises(ValueError):
                admin.execute_action(action, {}, None)

    def test_keepalive_config_persists_without_immediate_modem_access(self):
        from keepalive_interval import IntervalKeepalive
        with patch.dict(os.environ, {'MODEM_BACKEND':'ml307'}):
            spec = importlib.util.spec_from_file_location('admin_config_test', ROOT/'deploy/web_admin/4g_wifi_admin.py')
            admin = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(admin)
        task = {'id':'draft','label':'Test plan','enabled':True,'profile_iccid':'8944100000000000001',
                'target_number':'+440000000000','message':'test','start_date':'2026-09-27',
                'send_time':'09:00','first_delay_days':70,'interval_days':85}
        with tempfile.TemporaryDirectory() as directory, patch.object(admin, 'APP_CONFIG_PATH', Path(directory)/'app.conf'), \
             patch.object(admin, 'ML307') as modem, patch.object(admin, 'next_keepalive_run') as next_run:
            admin.KEEPER = IntervalKeepalive(Path(directory)/'keepalive.sqlite3',modem,lambda *a:None,lambda *a:True)
            admin.execute_sync_action('save_keepalive', {'settings':{'queue_gap_seconds':90},'tasks':[task]})
            snapshot = admin.keepalive_status_snapshot([{'iccid':task['profile_iccid'],'display_name':'Test card'}])
            self.assertEqual(snapshot['settings']['queue_gap_seconds'], 90)
            self.assertEqual(snapshot['tasks'][0]['profile_name'], 'Test card')
            self.assertTrue(snapshot['tasks'][0]['enabled'])
            self.assertTrue(snapshot['scheduler_enabled'])
            self.assertEqual(snapshot['tasks'][0]['next_run'], '2026-12-06T09:00:00+08:00')
            self.assertEqual(snapshot['queued_runs'], [])
            self.assertIsNone(snapshot['active_run'])
            self.assertEqual(modem.mock_calls, [])
            next_run.assert_not_called()
            with self.assertRaises(ValueError):
                admin.start_action('run_keepalive_task', {'task_id':'draft'})
            with self.assertRaises(ValueError):
                admin.execute_sync_action('save_keepalive', {'tasks':[dict(task, interval_days=0)]})
            self.assertEqual(admin.load_keepalive_config()[1][0]['interval_days'], 85)

    def test_switch_waits_through_temporary_operator_query_error(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = ML307Backend('fake', directory, lambda: [], lambda *args: None)
            modem = MagicMock()
            modem.__enter__.return_value = modem
            modem.profiles.side_effect = [
                [{'iccid':'old','enabled':True},{'iccid':'new','enabled':False}],
                [{'iccid':'old','enabled':False},{'iccid':'new','enabled':True}]]
            modem.inbox.return_value = []
            modem.status.side_effect = [RuntimeError('AT+COPS? temporary ERROR'),
                {'registration':'roaming','state':'registered','sim_ready':True,'operator_name':'test'}]
            with patch('ml307_backend.Modem', return_value=modem), patch('ml307_backend.time.sleep'):
                backend.switch('new', lambda text: None)
            modem.enable.assert_called_once_with('new')
            self.assertEqual(modem.status.call_count, 2)
            self.assertEqual(backend.snapshot()['modem']['operator_name'], 'test')
            self.assertFalse(backend.snapshot()['busy'])


if __name__ == '__main__':
    unittest.main()
