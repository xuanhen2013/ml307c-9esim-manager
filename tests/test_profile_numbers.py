import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'deploy/shared'))
from profile_numbers import ProfileNumbers

CARD_A = '8900000000000000001'
CARD_B = '8900000000000000002'


class ProfileNumberTests(unittest.TestCase):
    def test_persist_by_iccid_and_remove_only_selected_number(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'profile_numbers.json'
            store = ProfileNumbers(path)
            store.save(CARD_A, '+1 (202) 555-0123')
            store.save(CARD_B, '+447700900123')
            store = ProfileNumbers(path)
            self.assertEqual(store.get(CARD_A), '+12025550123')
            self.assertEqual(store.get(CARD_B), '+447700900123')
            self.assertEqual(store.get(''), '')
            store.save(CARD_A, '')
            self.assertEqual(store.get(CARD_A), '')
            self.assertEqual(store.get(CARD_B), '+447700900123')

    def test_invalid_or_failed_save_preserves_previous_mapping(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ProfileNumbers(Path(folder)/'profile_numbers.json')
            store.save(CARD_A, '+12025550123')
            for card, number in [(CARD_A, '2025550123'), ('bad', '+12025550123'), (CARD_A, None)]:
                with self.assertRaises(ValueError):
                    store.save(card, number)
            with patch.object(Path, 'replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    store.save(CARD_A, '+12025550124')
            self.assertEqual(store.get(CARD_A), '+12025550123')
            self.assertEqual(list(Path(folder).glob('.profile-numbers-*')), [])

    def test_current_card_number_never_leaks_to_other_cards_sms(self):
        with patch.dict(os.environ, {'MODEM_BACKEND':'ml307'}):
            spec = importlib.util.spec_from_file_location('admin_numbers_test', ROOT/'deploy/web_admin/4g_wifi_admin.py')
            admin = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(admin)
        with tempfile.TemporaryDirectory() as folder:
            store = ProfileNumbers(Path(folder)/'profile_numbers.json')
            store.save(CARD_A, '+12025550123')
            store.save(CARD_B, '+447700900123')
            state = {'profiles':[{'iccid':CARD_A,'profileName':'A','enabled':True},
                                 {'iccid':CARD_B,'profileName':'B','enabled':False}],
                     'modem':{},'error':'','notify_error':'','busy':False,'updated':'',
                     'sms':[{'id':1,'profile_iccid':CARD_B,'imported':0,'timestamp':'',
                             'received_at':'2026-09-28T00:00:00+00:00'},
                            {'id':2,'profile_iccid':'','imported':1,'timestamp':'', 'received_at':''}]}
            with patch.object(admin,'PROFILE_NUMBERS',store), patch.object(admin,'ML307',Mock(snapshot=Mock(return_value=state))), \
                 patch.object(admin,'read_env_config',return_value={}), patch.object(admin,'keepalive_status_snapshot',return_value={}):
                result=admin.get_ml307_status()
            self.assertEqual(result['modem']['number'],'+12025550123')
            self.assertEqual(result['profiles'][1]['phone_number'],'+447700900123')
            self.assertEqual(result['sms'][0]['recipient_number'],'+447700900123')
            self.assertEqual(result['sms'][1]['recipient_number'],'')


if __name__ == '__main__':
    unittest.main()
