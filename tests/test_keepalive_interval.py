import sys
from pathlib import Path
import tempfile
import unittest
from datetime import datetime
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'deploy'/'shared'))
from keepalive_interval import BJ, IntervalKeepalive, first_due, normalize
from ml307_modem import Modem, SmsRejected, encode_submit
from ml307_backend import ML307Backend


TASK = dict(id='lebara', label='Lebara 保号', profile_iccid='8944100000000000001',
            target_number='+447700900123', message='Keep alive', enabled=True,
            start_date='2026-09-27', send_time='09:00', first_delay_days=70, interval_days=85)


class Schedules(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/'keepalive.sqlite3'
        self.now = datetime(2026,9,27,12,tzinfo=BJ).timestamp()
        self.backend, self.enqueued, self.notices = Mock(), [], []
        self.service = self.create()
        self.service.configure({}, [TASK])

    def create(self):
        return IntervalKeepalive(self.path, self.backend,
            lambda task, run_id:self.enqueued.append(run_id),
            lambda body,key:self.notices.append((body,key)) or True,
            clock=lambda:self.now)

    def queue(self):
        self.now = first_due(TASK)
        self.service.tick()
        return self.enqueued[-1]

    def task(self):
        return self.service.snapshot([])['tasks'][0]

    def success(self, task, before, accepted, original, log):
        original('8944100000000000002')
        before()
        accepted(42)

    def test_dates_and_no_early_send(self):
        self.assertEqual(self.task()['next_run'], '2026-12-06T09:00:00+08:00')
        self.service.tick()
        self.assertFalse(self.enqueued)
        self.assertFalse(self.backend.keepalive_send.called)
        self.service = self.create()
        self.assertEqual(self.task()['next_run_label'], '2026-12-06 09:00')

    def test_success_next_interval_and_dedup(self):
        run_id = self.queue()
        self.service.tick()
        self.assertEqual(len(self.enqueued),1)
        self.backend.keepalive_send.side_effect = self.success
        self.service.run(run_id, lambda m:None)
        self.assertEqual(self.task()['next_run'], '2027-03-01T09:00:00+08:00')
        with self.assertRaises(ValueError):
            self.service.run(run_id, lambda m:None)
        self.service = self.create()
        self.service.tick()
        self.assertEqual(self.backend.keepalive_send.call_count,1)
        self.assertEqual(len(self.notices),1)

    def test_late_recovery_runs_once_from_actual_success(self):
        self.now = datetime(2026,12,8,10,tzinfo=BJ).timestamp()
        self.service.tick()
        self.backend.keepalive_send.side_effect = self.success
        self.service.run(self.enqueued[-1], lambda m:None)
        self.assertEqual(self.task()['next_run'], '2027-03-03T10:00:00+08:00')

    def test_failed_does_not_advance_or_retry(self):
        run_id = self.queue()
        self.backend.keepalive_send.side_effect = RuntimeError('未注册')
        with self.assertRaises(RuntimeError):
            self.service.run(run_id, lambda m:None)
        self.assertEqual(self.task()['runtime_state'],'failed')
        self.assertEqual(self.task()['next_run_label'],'2026-12-06 09:00')
        self.now += 86400
        self.service = self.create()
        self.service.tick()
        self.assertEqual(len(self.enqueued),1)
        self.service.configure({}, [dict(TASK, enabled=False)])
        self.service.configure({}, [TASK])
        self.service.tick()
        self.assertEqual(len(self.enqueued),2)

    def test_unknown_and_crash_are_not_retried(self):
        run_id = self.queue()
        def timeout(task,before,*args):
            before()
            raise RuntimeError('USB disconnected')
        self.backend.keepalive_send.side_effect = timeout
        with self.assertRaises(RuntimeError):
            self.service.run(run_id, lambda m:None)
        self.assertEqual(self.task()['runtime_state'],'attention')
        self.service.configure({}, [dict(TASK,enabled=False)])
        self.service.configure({}, [TASK])
        self.now += 86400
        self.service = self.create()
        self.service.tick()
        self.assertEqual(len(self.enqueued),1)

    def test_crash_during_submit(self):
        run_id = self.queue()
        with self.service.db() as db:
            db.execute("UPDATE tasks SET state='submitting'")
            db.execute("UPDATE runs SET state='submitting' WHERE id=?",(run_id,))
        self.service = self.create()
        self.service.tick()
        self.assertEqual(self.task()['runtime_state'],'attention')
        self.assertEqual(len(self.enqueued),1)
        self.assertTrue(self.notices)

    def test_restore_failure_keeps_success(self):
        run_id = self.queue()
        def restore_fails(*args):
            self.success(*args)
            raise RuntimeError('恢复失败')
        self.backend.keepalive_send.side_effect = restore_fails
        with self.assertRaises(RuntimeError):
            self.service.run(run_id, lambda m:None)
        self.assertEqual(self.task()['next_run_label'],'2027-03-01 09:00')
        self.assertEqual(self.task()['runtime_state'],'scheduled')

    def test_restart_after_acceptance_does_not_resend(self):
        run_id = self.queue()
        with self.service.db() as db:
            db.execute("UPDATE tasks SET state='submitting',last_success=?,due=?", (self.now,self.now+85*86400))
            db.execute("UPDATE runs SET state='accepted' WHERE id=?",(run_id,))
        self.service = self.create()
        self.service.tick()
        self.assertEqual(self.task()['runtime_state'],'scheduled')
        self.assertEqual(self.task()['next_run_label'],'2027-03-01 09:00')
        self.assertEqual(len(self.enqueued),1)

    def test_edit_does_not_reset_success_and_blocks_active(self):
        run_id = self.queue()
        with self.assertRaises(ValueError):
            self.service.configure({},[dict(TASK,enabled=False)])
        self.backend.keepalive_send.side_effect = self.success
        self.service.run(run_id, lambda m:None)
        self.now += 86400
        self.service.configure({},[dict(TASK,message='Still here')])
        self.assertEqual(self.task()['next_run_label'],'2027-03-01 09:00')

    def test_validation_and_draft(self):
        self.assertFalse(normalize(dict(TASK,enabled=False,target_number=''))['enabled'])
        for fields in ({'target_number':''},{'target_number':'123;AT'},{'message':'x'*71},{'message':'😀'},{'interval_days':0}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                normalize(dict(TASK,**fields))

    def test_unavailable_notification_is_persisted(self):
        self.service.notify = lambda *a:False
        run_id = self.queue()
        self.backend.keepalive_send.side_effect = self.success
        self.service.run(run_id,lambda m:None)
        self.assertFalse(self.notices)
        self.service = self.create()
        self.service.tick()
        self.service.tick()
        self.assertEqual(len(self.notices),1)


class SmsSubmission(unittest.TestCase):
    def modem(self, replies):
        modem = Modem.__new__(Modem)
        modem.at = Mock(return_value='+CMGF: 0\r\nOK')
        modem.transport = Mock()
        modem.transport.read.side_effect = replies
        return modem

    def test_pdu_known_vector_and_commit_before_ctrl_z(self):
        self.assertEqual(encode_submit('+1234567','A'), ('0001000791214365F70008020041',13))
        modem = self.modem([b'\r\n> ',b'\r\n+CMGS: 17\r\nOK\r\n'])
        def commit():
            self.assertNotIn(((b'\x1a',),{}),modem.transport.write.call_args_list)
        self.assertEqual(modem.send_sms('+1234567','A',commit),17)
        self.assertEqual(modem.transport.write.call_args_list[-1].args,(b'\x1a',))

    def test_prompt_rejection_never_submits(self):
        modem = self.modem([b'\r\nERROR\r\n'])
        before = Mock()
        with self.assertRaises(SmsRejected):
            modem.send_sms('+1234567','A',before)
        before.assert_not_called()
        self.assertEqual(modem.transport.write.call_args_list[-1].args,(b'\x1b',))

    def test_persistence_failure_aborts(self):
        modem = self.modem([b'\r\n> '])
        with self.assertRaises(OSError):
            modem.send_sms('+1234567','A',Mock(side_effect=OSError('disk full')))
        self.assertNotIn(((b'\x1a',),{}),modem.transport.write.call_args_list)

    def test_reject_and_disconnect_after_submission(self):
        for response, error in ((b'\r\n+CMS ERROR: 500\r\n',SmsRejected),(OSError('disconnected'),OSError)):
            modem = self.modem([b'\r\n> ',response])
            before = Mock()
            with self.assertRaises(error):
                modem.send_sms('+1234567','A',before)
            before.assert_called_once()
            self.assertEqual(modem.transport.write.call_args_list[-1].args,(b'\x1a',))

    def test_failed_switch_still_attempts_original_card(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = ML307Backend('fake',directory,lambda:[],lambda *a:None)
            backend.switch = Mock(side_effect=[RuntimeError('注册超时'),None])
            with patch('ml307_backend.Modem') as factory:
                factory.return_value.__enter__.return_value.profiles.return_value = [{'iccid':'old','enabled':True}]
                with self.assertRaises(RuntimeError):
                    backend.keepalive_send(TASK,Mock(),Mock(),Mock(),Mock())
            self.assertEqual([c.args[0] for c in backend.switch.call_args_list],[TASK['profile_iccid'],'old'])


if __name__ == '__main__':
    unittest.main()
