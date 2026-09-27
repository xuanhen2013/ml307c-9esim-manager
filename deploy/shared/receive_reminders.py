"""Per-profile 365-day OTP inactivity reminders; never touches the modem."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
import threading
import time
import unicodedata
import uuid

BJ = timezone(timedelta(hours=8))
DAY = 86400
PERIOD_DAYS = 365
STAGES = (0, 1, 7, 30)
PREFIX = 'receive-reminder:'
TEST_TEXT = re.compile(r'测试|測試|调试|調試|\b(?:test|testing|demo)\b', re.I)
OTP_WORD = re.compile(r'验证码|驗證碼|校验码|校驗碼|动态(?:码|密码)|動態(?:碼|密碼)|一次性(?:密码|密碼)|'
                      r'\b(?:verification|security|authentication|login|sign[ -]?in|access)\s+code\b|'
                      r'\b(?:one[ -]?time\s+(?:password|code)|otp|passcode)\b', re.I)
OTP_VALUE = re.compile(r'(?<![A-Za-z0-9])(?:[0-9]{3}[ -][0-9]{3}|(?=[A-Za-z0-9]{4,8}(?![A-Za-z0-9]))'
                       r'(?=[A-Za-z0-9]*[0-9])[A-Za-z0-9]{4,8})(?![A-Za-z0-9])')


def looks_like_otp(text):
    text = unicodedata.normalize('NFKC', str(text))
    if TEST_TEXT.search(text):
        return False
    return any(OTP_VALUE.search(text[max(0,m.start()-60):m.end()+60]) for m in OTP_WORD.finditer(text))


def date_label(timestamp):
    return datetime.fromtimestamp(timestamp, BJ).strftime('%Y-%m-%d %H:%M') if timestamp else ''


def normalize(raw, now):
    if not isinstance(raw, dict):
        raise ValueError('收码提醒格式不正确')
    rule = {k:str(raw.get(k,'')).strip() for k in ('id','label','profile_iccid','baseline_date')}
    rule['id'] = rule['id'] or uuid.uuid4().hex[:12]
    rule['enabled'] = raw.get('enabled') is True
    if not re.fullmatch(r'[\w-]{1,80}',rule['id']) or not rule['label']:
        raise ValueError('请填写提醒名称')
    if not re.fullmatch(r'\d{18,22}',rule['profile_iccid']):
        raise ValueError('请选择收码提醒对应的卡片')
    try:
        baseline = datetime.strptime(rule['baseline_date'],'%Y-%m-%d').replace(tzinfo=BJ)
    except ValueError:
        raise ValueError('请填写最近收码或开卡日期') from None
    if baseline.year < 2000 or baseline.date() > datetime.fromtimestamp(now,BJ).date():
        raise ValueError('起算日期不能晚于今天，且应在 2000 年以后')
    return rule, baseline.timestamp()


class ReceiveReminders:
    def __init__(self, path, sms_store, notify, clock=time.time):
        self.path, self.sms_store, self.notify, self.clock = str(path), sms_store, notify, clock
        self.lock, self.stop = threading.RLock(), threading.Event()
        self.last_error = ''
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS rules (
                    id TEXT PRIMARY KEY, config TEXT NOT NULL, anchor REAL NOT NULL,
                    cursor INTEGER NOT NULL DEFAULT 0, last_sms_id INTEGER DEFAULT 0,
                    last_sms_at REAL DEFAULT 0, generation TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS notices (
                    id TEXT PRIMARY KEY, rule_id TEXT NOT NULL, generation TEXT NOT NULL,
                    stage INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL);
            ''')

    @contextmanager
    def db(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    def configure(self, raw_rules):
        if not isinstance(raw_rules,list):
            raise ValueError('收码提醒需要是列表')
        normalized = [normalize(r,self.clock()) for r in raw_rules]
        if len({r['id'] for r,_ in normalized}) != len(normalized):
            raise ValueError('提醒 ID 不能重复')
        if len({r['profile_iccid'] for r,_ in normalized}) != len(normalized):
            raise ValueError('每张卡只需设置一条收码提醒')
        with self.db() as db:
            existing = {r['id']:r for r in db.execute('SELECT * FROM rules')}
            for raw,(rule,baseline) in zip(raw_rules,normalized):
                row = existing.get(rule['id'])
                old = json.loads(row['config']) if row else {}
                reset = bool(row and (old['profile_iccid'] != rule['profile_iccid'] or
                             old['baseline_date'] != rule['baseline_date'] or raw.get('reset_baseline') is True))
                if not row:
                    db.execute('INSERT INTO rules (id,config,anchor,generation) VALUES (?,?,?,?)',
                               (rule['id'],json.dumps(rule,ensure_ascii=False),baseline,uuid.uuid4().hex))
                elif reset:
                    # An explicit manual correction supersedes old classification.
                    db.execute('''UPDATE rules SET config=?,anchor=?,cursor=?,last_sms_id=0,
                                  last_sms_at=0,generation=? WHERE id=?''',
                               (json.dumps(rule,ensure_ascii=False),baseline,self.sms_store.latest_id(),uuid.uuid4().hex,rule['id']))
                else:
                    generation = uuid.uuid4().hex if old['enabled'] != rule['enabled'] else row['generation']
                    db.execute('UPDATE rules SET config=?,generation=? WHERE id=?',
                               (json.dumps(rule,ensure_ascii=False),generation,rule['id']))
            for rule_id in existing.keys()-{r['id'] for r,_ in normalized}:
                db.execute('DELETE FROM rules WHERE id=?',(rule_id,))
        self.sync_activity()

    def sync_activity(self):
        # Snapshot the upper ID so incoming traffic cannot make this loop unbounded.
        through = self.sms_store.latest_id()
        with self.db() as db:
            for row in db.execute('SELECT * FROM rules').fetchall():
                rule = json.loads(row['config'])
                cursor, anchor = row['cursor'], row['anchor']
                sms_id, sms_at = row['last_sms_id'], row['last_sms_at']
                while cursor < through:
                    messages = self.sms_store.received_after(rule['profile_iccid'],cursor,through)
                    if not messages:
                        break
                    for message in messages:
                        cursor = message['id']
                        try:
                            received = datetime.fromisoformat(message['received_at'])
                            if received.tzinfo is None:
                                continue
                            observed = received.timestamp()
                        except (ValueError,TypeError):
                            continue
                        if anchor < observed <= self.clock() and looks_like_otp(message['text']):
                            anchor, sms_id, sms_at = observed, message['id'], observed
                generation = uuid.uuid4().hex if anchor != row['anchor'] else row['generation']
                db.execute('UPDATE rules SET cursor=?,anchor=?,last_sms_id=?,last_sms_at=?,generation=? WHERE id=?',
                           (through,anchor,sms_id,sms_at,generation,row['id']))

    def current_stage(self, anchor):
        remaining = anchor + PERIOD_DAYS*DAY - self.clock()
        return next((stage for stage in STAGES if remaining <= stage*DAY), None)

    def _valid(self, db, notice):
        row = db.execute('SELECT * FROM rules WHERE id=?',(notice['rule_id'],)).fetchone()
        if row is None or row['generation'] != notice['generation']:
            return False
        return bool(json.loads(row['config'])['enabled'] and self.current_stage(row['anchor']) == notice['stage'])

    def notice_is_current(self, key):
        if not key.startswith(PREFIX):
            return True
        # Feishu can reconnect after an OTP arrived. Recheck at actual delivery.
        with self.lock:
            self.sync_activity()
            with self.db() as db:
                row = db.execute('SELECT * FROM notices WHERE id=?',(key,)).fetchone()
                return bool(row and self._valid(db,row))

    def tick(self):
        with self.lock:
            self.sync_activity()
            with self.db() as db:
                for row in db.execute('SELECT * FROM rules').fetchall():
                    rule = json.loads(row['config'])
                    stage = self.current_stage(row['anchor'])
                    if not rule['enabled'] or stage is None:
                        continue
                    # Catch up only the most urgent phase; never send a burst of
                    # all missed warnings or replay a less urgent phase after a clock change.
                    sent = db.execute('SELECT min(stage) FROM notices WHERE rule_id=? AND generation=?',
                                      (row['id'],row['generation'])).fetchone()[0]
                    if sent is not None and sent <= stage:
                        continue
                    key = PREFIX+row['id']+':'+row['generation']+':'+str(stage)
                    db.execute('INSERT OR IGNORE INTO notices (id,rule_id,generation,stage,created) VALUES (?,?,?,?,?)',
                               (key,row['id'],row['generation'],stage,self.clock()))
            with self.db() as db:
                notices = db.execute("SELECT * FROM notices WHERE state='pending' ORDER BY created").fetchall()
            for notice in notices:
                with self.db() as db:
                    if not self._valid(db,notice):
                        db.execute("UPDATE notices SET state='superseded' WHERE id=?",(notice['id'],))
                        continue
                    row = db.execute('SELECT * FROM rules WHERE id=?',(notice['rule_id'],)).fetchone()
                    rule = json.loads(row['config'])
                due = row['anchor']+PERIOD_DAYS*DAY
                timing = '已到 365 天期限' if notice['stage']==0 else f"距 365 天期限不足或等于 {notice['stage']} 天"
                source = '最近识别到验证码' if row['last_sms_id'] else '手动起算'
                body = (f"{rule['label']}：{timing}。\n截至 {date_label(self.clock())}，"
                        f"未识别到此后的新验证码。\n{source}：{date_label(row['anchor'])}"
                        f"\n期限：{date_label(due)}（北京时间）\n请切到这张卡接收一次真实验证码；"
                        '若已经收码但未识别，可在保号页面校正日期。本提醒不会自动发短信。')
                if self.notify(body,notice['id']):
                    with self.db() as db:
                        db.execute("UPDATE notices SET state='queued' WHERE id=?",(notice['id'],))
            self.last_error = ''

    def snapshot(self, profiles):
        names = {p['iccid']:p.get('display_name') or p.get('profileName') for p in profiles}
        with self.db() as db:
            rows = db.execute('SELECT * FROM rules ORDER BY rowid').fetchall()
        rules = []
        for row in rows:
            rule = json.loads(row['config'])
            due = row['anchor']+PERIOD_DAYS*DAY
            rules.append(dict(rule,profile_name=names.get(rule['profile_iccid'],rule['label']),
                              anchor_label=date_label(row['anchor']), due_at=datetime.fromtimestamp(due,BJ).isoformat(),
                              due_label=date_label(due), days_remaining=math.ceil((due-self.clock())/DAY),
                              last_sms_id=str(row['last_sms_id']) if row['last_sms_id'] else '',
                              last_received_label=date_label(row['last_sms_at']),
                              stage=self.current_stage(row['anchor'])))
        return {'rules':rules,'period_days':PERIOD_DAYS,'warning_days':[30,7,1,0],
                'scheduler_enabled':True,'error':self.last_error}

    def start(self):
        def loop():
            while not self.stop.is_set():
                try:
                    self.tick()
                except Exception:
                    self.last_error = '收码提醒检查失败，请检查数据目录及飞书通知状态'
                self.stop.wait(30)
        threading.Thread(target=loop,name='receive-reminders',daemon=True).start()
