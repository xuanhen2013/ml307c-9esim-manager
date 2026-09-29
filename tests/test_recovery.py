import contextlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'deploy/shared'))
from ml307_backend import ML307Backend, SmsStore
from ml307_modem import Modem, ProfileEnableError, ATCommandError


class SimulatedModem:
    def __init__(self, results):
        self.results = iter(results)
        self.active = 'old'
        self.enable_count = self.reset_count = 0
        self.inbox_reads = []

    def __enter__(self): return self
    def __exit__(self, *args): pass
    def profiles(self):
        return [{'iccid': x, 'enabled': self.active == x, 'profileName': x} for x in ('old','new')]
    def enable(self, iccid):
        self.enable_count += 1
        result = next(self.results)
        if isinstance(result, Exception): raise result
        if result: raise ProfileEnableError(result)
        self.active = iccid
    def inbox(self):
        self.inbox_reads.append(self.active)
        return []
    def status(self):
        return {'state':'registered','sim_ready':True,'registration':'roaming','operator_name':'test'}
    def restart(self): self.reset_count += 1


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.backend = ML307Backend('fake', self.temp.name, lambda:[], lambda *args:None)

    def switch(self, modem):
        with patch('ml307_backend.Modem', return_value=modem), patch('ml307_backend.time.sleep'):
            self.backend.switch('new', lambda text: None)

    def test_busy_clears_after_wait_without_reset(self):
        modem = SimulatedModem([5,0])
        self.switch(modem)
        self.assertEqual((modem.enable_count,modem.reset_count),(2,0))
        self.assertEqual(modem.active,'new')

    def test_persistent_busy_gets_one_reset_then_verified_switch(self):
        modem = SimulatedModem([5,5,5,0])
        self.switch(modem)
        self.assertEqual((modem.enable_count,modem.reset_count),(4,1))
        self.assertEqual(modem.active,'new')
        self.assertEqual(modem.inbox_reads[-1],'new')
        self.assertFalse(self.backend.busy)
        events = SmsStore(Path(self.temp.name)/'sms.sqlite3').diagnostics()
        self.assertTrue(any('恢复完成' in r['message'] for r in events))

    def test_busy_after_reset_stops(self):
        modem = SimulatedModem([5,5,5,5])
        with self.assertRaisesRegex(RuntimeError,'仍忙'):
            self.switch(modem)
        self.assertEqual((modem.enable_count,modem.reset_count),(4,1))
        self.assertEqual(modem.active,'old')
        self.assertFalse(self.backend.busy)

    def test_policy_and_unknown_transport_failures_are_not_retried(self):
        for result in (3, RuntimeError('transport uncertain')):
            with self.subTest(result=result):
                modem = SimulatedModem([result])
                with self.assertRaises(RuntimeError): self.switch(modem)
                self.assertEqual((modem.enable_count,modem.reset_count),(1,0))

    def test_recovery_checks_original_card_and_cooldown_survives_restart(self):
        modem = SimulatedModem([])
        self.backend.update(profiles=modem.profiles())
        with patch('ml307_backend.Modem',return_value=modem), patch('ml307_backend.time.sleep'):
            self.backend.recover(lambda text:None)
            new_backend = ML307Backend('fake',self.temp.name,lambda:[],lambda *args:None)
            with self.assertRaisesRegex(RuntimeError,'两分钟'):
                new_backend.recover(lambda text:None)
        self.assertEqual(modem.reset_count,1)
        self.assertEqual(modem.active,'old')

    def test_unexpected_profile_after_reboot_stops_without_switching(self):
        modem = SimulatedModem([])
        modem.restart = lambda: setattr(modem,'active','new')
        with patch('ml307_backend.Modem',return_value=modem), patch('ml307_backend.time.sleep'):
            with self.assertRaisesRegex(ValueError,'发生变化'):
                self.backend.recover(lambda text:None)
        self.assertEqual(modem.enable_count,0)

    def test_reset_timeout_releases_lock_and_does_not_loop_resets(self):
        modem = SimulatedModem([])
        with patch('ml307_backend.Modem',return_value=modem), patch('ml307_backend.time.sleep'), \
             patch('ml307_backend.time.monotonic',side_effect=[0,91]):
            with self.assertRaisesRegex(RuntimeError,'恢复超时'):
                self.backend.recover(lambda text:None)
        self.assertEqual(modem.reset_count,1)
        self.assertFalse(self.backend.busy)
        self.assertTrue(self.backend.operation_lock.acquire(blocking=False))
        self.backend.operation_lock.release()

    def test_enable_error_is_decoded_without_iccid_in_error(self):
        modem = Modem.__new__(Modem)
        modem.channel = lambda: contextlib.nullcontext(1)
        modem.apdu = Mock(return_value=bytes.fromhex('BF3103800105'))
        with self.assertRaises(ProfileEnableError) as caught:
            modem.enable('8944100000000000001')
        self.assertEqual(caught.exception.code,5)
        self.assertIn('catBusy',str(caught.exception))
        self.assertNotIn('894410',str(caught.exception))

    def test_restart_only_validated_firmware_and_never_repeats_uncertain_command(self):
        modem = Modem.__new__(Modem)
        modem.at = Mock(side_effect=['other firmware'])
        with self.assertRaisesRegex(RuntimeError,'尚未验证'): modem.restart()
        self.assertEqual(modem.at.call_count,1)
        for failure in (ATCommandError('AT+CFUN=1,1',False), None):
            modem.at = Mock(side_effect=['ML307C-DC-CN-MBRH0S00\r\nOK', '+CFUN: (0,1,4,5),(0-1)', failure])
            modem.restart()
            self.assertEqual([c.args[0] for c in modem.at.call_args_list].count('AT+CFUN=1,1'),1)
        modem.at = Mock(side_effect=['ML307C-DC-CN-MBRH0S00\nOK','+CFUN: (0,1,4,5),(0-1)',ATCommandError('AT+CFUN=1,1',True)])
        with self.assertRaises(ATCommandError): modem.restart()


if __name__ == '__main__': unittest.main()
