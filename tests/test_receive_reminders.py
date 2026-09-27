from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'deploy'/'shared'))
from ml307_backend import SmsStore
from receive_reminders import BJ, DAY, ReceiveReminders, looks_like_otp

PROFILE = '8944100000000000001'
OTHER = '8944100000000000002'
RULE = dict(id='receive-test',label='Test card 收码提醒',profile_iccid=PROFILE,
            enabled=True,baseline_date='2026-09-17')


class OtpDetection(unittest.TestCase):
    def test_common_otp_templates(self):
        for text in ('验证码：123456，五分钟内有效。','【服务】123456 是您的校验码。',
                     'Your verification code is 123456.','G-123456 is your Google verification code.',
                     'Security code: A7BC12','Your OTP: 123 456','驗證碼：１２３４５６',
                     'Your one-time password is 456789'):
            with self.subTest(text=text):
                self.assertTrue(looks_like_otp(text))

    def test_tests_and_non_otp_do_not_count(self):
        for text in ('Feishu SMS test 20260927 code 584219','测试验证码：123456',
                     'Your test message','验证码服务已经启用','订单号 123456 已发货',
                     '账户余额 12.34','Please call 12345678901 for a verification code.',
                     'coupon code SAVE20','Hello 123456'):
            with self.subTest(text=text):
                self.assertFalse(looks_like_otp(text))


class ReminderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.sms = SmsStore(root/'sms.sqlite3')
        self.sms.ingest([],{})
        self.path = root/'reminders.sqlite3'
        self.now = datetime(2026,9,27,12,tzinfo=BJ).timestamp()
        self.baseline = datetime(2026,9,17,tzinfo=BJ).timestamp()
        self.due = self.baseline+365*DAY
        self.notices, self.available = [], True
        self.service = self.new_service()
        self.service.configure([RULE])

    def notify(self, body, key):
        if not self.available:
            return False
        self.notices.append((body,key))
        return True

    def new_service(self):
        return ReceiveReminders(self.path,self.sms,self.notify,clock=lambda:self.now)

    def rule(self):
        return self.service.snapshot([])['rules'][0]

    def add(self,text='验证码：123456',profile=PROFILE,at=None,imported=False):
        at = self.now if at is None else at
        unique = f'{self.sms.latest_id()}:{text}'
        self.sms.ingest([dict(pdu=unique.encode().hex(),number='sender',text=text,
                              timestamp='2099-01-01T00:00:00+00:00')],{'iccid':profile,'profileName':'Test card'})
        sms_id = self.sms.latest_id()
        with self.sms.connect() as db:
            db.execute('UPDATE sms SET received_at=?,imported=? WHERE id=?',
                       (datetime.fromtimestamp(at,BJ).isoformat(),int(imported),sms_id))
        return sms_id

    def test_initial_deadline_timezone_and_no_early_notice(self):
        self.assertEqual(self.rule()['due_at'],'2027-09-17T00:00:00+08:00')
        self.assertEqual(self.rule()['last_sms_id'],'')
        self.service.tick()
        self.assertFalse(self.notices)
        self.service = self.new_service()
        self.assertEqual(self.rule()['due_at'],'2027-09-17T00:00:00+08:00')

    def test_each_stage_once_across_restarts(self):
        for stage in (30,7,1,0):
            self.now = self.due-stage*DAY
            self.service.tick()
            count = len(self.notices)
            self.service = self.new_service()
            self.service.tick()
            self.assertEqual(len(self.notices),count)
        self.assertEqual(len(self.notices),4)
        self.now += 60*DAY
        self.service.tick()
        self.assertEqual(len(self.notices),4)

    def test_recovery_skips_missed_stages_and_old_delivery(self):
        self.now = self.due-30*DAY
        self.service.tick()
        old_key = self.notices[0][1]
        self.now = self.due-2*DAY
        self.service.tick()
        self.assertEqual(len(self.notices),2)
        self.assertTrue(self.notices[-1][1].endswith(':7'))
        self.assertFalse(self.service.notice_is_current(old_key))

    def test_only_matching_card_real_non_imported_otp_resets(self):
        for text,profile,imported in [('验证码：123456',OTHER,False),('验证码：123456','',False),
                                     ('验证码：123456',PROFILE,True),('测试验证码：123456',PROFILE,False),
                                     ('订单 123456',PROFILE,False)]:
            self.add(text,profile,imported=imported)
        self.service.tick()
        self.assertEqual(self.rule()['anchor_label'],'2026-09-17 00:00')
        sms_id = self.add()
        self.service.tick()
        self.assertEqual(self.rule()['last_sms_id'],str(sms_id))
        self.assertEqual(self.rule()['due_at'],'2027-09-27T12:00:00+08:00')

    def test_stale_history_and_future_timestamp_do_not_reset(self):
        self.add(at=self.baseline-DAY)
        self.add(at=self.now+DAY)
        self.service.tick()
        self.assertEqual(self.rule()['anchor_label'],'2026-09-17 00:00')

    def test_new_otp_cancels_queued_feishu_notice_at_delivery(self):
        self.now = self.due-7*DAY
        self.service.tick()
        key = self.notices[0][1]
        self.assertTrue(self.service.notice_is_current(key))
        self.now += 60
        self.add()
        # No intervening scheduler tick: actual delivery still sees the new OTP.
        self.assertFalse(self.service.notice_is_current(key))
        self.assertTrue(self.service.notice_is_current('sms:42'))

    def test_unbound_retry_survives_restart_then_is_cancelled_by_otp(self):
        self.available = False
        self.now = self.due-30*DAY
        self.service.tick()
        self.service = self.new_service()
        self.now += DAY
        self.add()
        self.available = True
        self.service.tick()
        self.assertFalse(self.notices)
        with self.service.db() as db:
            self.assertEqual(db.execute('SELECT state FROM notices').fetchone()[0],'superseded')

    def test_notification_retry_uses_stable_key(self):
        self.available = False
        self.now = self.due-30*DAY
        self.service.tick()
        with self.service.db() as db:
            key = db.execute('SELECT id FROM notices').fetchone()[0]
        self.service = self.new_service()
        self.available = True
        self.service.tick()
        self.assertEqual(self.notices[0][1],key)
        self.service.tick()
        self.assertEqual(len(self.notices),1)

    def test_edit_name_keeps_anchor_manual_correction_overrides(self):
        self.add()
        self.service.tick()
        self.service.configure([dict(RULE,label='Renamed')])
        self.assertEqual(self.rule()['anchor_label'],'2026-09-27 12:00')
        self.service.configure([dict(RULE,reset_baseline=True)])
        self.assertEqual(self.rule()['anchor_label'],'2026-09-17 00:00')
        self.service.tick()
        self.assertEqual(self.rule()['last_sms_id'],'')
        self.now += DAY
        self.add()
        self.service.tick()
        self.assertEqual(self.rule()['anchor_label'],'2026-09-28 12:00')

    def test_pause_and_remove_cancel_outbox_then_enable_current_stage(self):
        self.now = self.due-30*DAY
        self.service.tick()
        key = self.notices[0][1]
        self.service.configure([dict(RULE,enabled=False)])
        self.assertFalse(self.service.notice_is_current(key))
        self.service.tick()
        self.assertEqual(len(self.notices),1)
        self.service.configure([RULE])
        self.service.tick()
        self.assertEqual(len(self.notices),2)
        key = self.notices[-1][1]
        self.service.configure([])
        self.assertFalse(self.service.notice_is_current(key))

    def test_more_than_one_batch_and_existing_sms_on_creation(self):
        messages = [dict(pdu=f'{i:06x}',number='sender',text='账单信息',timestamp='2026-09-27') for i in range(250)]
        messages[-1]['text'] = '验证码：123456'
        self.sms.ingest(messages,{'iccid':PROFILE})
        with self.sms.connect() as db:
            db.execute('UPDATE sms SET received_at=?',(datetime.fromtimestamp(self.now,BJ).isoformat(),))
        self.service.configure([])
        self.service.configure([RULE])
        self.assertEqual(self.rule()['last_sms_id'],'250')

    def test_config_validation_is_atomic(self):
        for change in ({'baseline_date':'2026-09-28'},{'baseline_date':'bad'},{'profile_iccid':'bad'}):
            with self.subTest(change=change),self.assertRaises(ValueError):
                self.service.configure([dict(RULE,**change)])
        with self.assertRaises(ValueError):
            self.service.configure([RULE,dict(RULE,id='duplicate-card')])
        self.assertEqual(self.rule()['due_at'],'2027-09-17T00:00:00+08:00')


if __name__ == '__main__':
    unittest.main()
