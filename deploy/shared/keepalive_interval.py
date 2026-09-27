"""Durable interval schedules for ML307. No automatic retry after a send attempt."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

from ml307_modem import SmsRejected, encode_submit

BJ = timezone(timedelta(hours=8))
ACTIVE = {'queued', 'running', 'submitting'}


def stamp(value):
    return datetime.fromtimestamp(value, BJ).isoformat() if value else ''


def label_time(value):
    return datetime.fromtimestamp(value, BJ).strftime('%Y-%m-%d %H:%M') if value else ''


def normalize(raw):
    task = {key: str(raw.get(key, '')).strip() for key in
            ('id', 'label', 'profile_iccid', 'target_number', 'message', 'start_date', 'send_time')}
    task['id'] = task['id'] or uuid.uuid4().hex[:12]
    if not re.fullmatch(r'[\w-]{1,80}', task['id']):
        raise ValueError('任务 ID 不正确')
    if not task['label'] or not re.fullmatch(r'\d{18,22}', task['profile_iccid']):
        raise ValueError('请填写任务名称并选择卡片')
    task['enabled'] = raw.get('enabled') is True
    task['schedule_type'] = 'interval'
    task['cron_expression'] = ''
    task['send_time'] = task['send_time'] or '09:00'
    task['first_delay_days'] = int(raw.get('first_delay_days', 70))
    task['interval_days'] = int(raw.get('interval_days', 85))
    if any(not 1 <= task[key] <= 3650 for key in ('first_delay_days', 'interval_days')):
        raise ValueError('间隔天数必须介于 1 和 3650')
    try:
        datetime.strptime(task['start_date']+' '+task['send_time'], '%Y-%m-%d %H:%M')
    except ValueError:
        raise ValueError('请填写起算日期和北京时间 HH:MM') from None
    # Incomplete drafts are useful; enabling an incomplete task is never allowed.
    if task['enabled'] or task['target_number']:
        encode_submit(task['target_number'], task['message'])
    elif task['message']:
        encode_submit('+441234567890', task['message'])
    return task


def first_due(task):
    start = datetime.strptime(task['start_date']+' '+task['send_time'], '%Y-%m-%d %H:%M').replace(tzinfo=BJ)
    return (start+timedelta(days=task['first_delay_days'])).timestamp()


class IntervalKeepalive:
    def __init__(self, path, backend, enqueue, notify, clock=time.time):
        self.path, self.backend, self.enqueue, self.notify, self.clock = str(path), backend, enqueue, notify, clock
        self.lock = threading.RLock()
        self.stop = threading.Event()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, config TEXT NOT NULL, state TEXT NOT NULL,
                    due REAL NOT NULL, last_success REAL DEFAULT 0, error TEXT DEFAULT '');
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, task_id TEXT, label TEXT, state TEXT,
                    scheduled REAL, created REAL, updated REAL, detail TEXT DEFAULT '',
                    original_iccid TEXT DEFAULT '', reference INTEGER);
                CREATE TABLE IF NOT EXISTS notices (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            ''')
            # Queued work never reached the modem. All interrupted executions need
            # attention: a crash must not silently charge for another SMS.
            db.execute("UPDATE tasks SET state='scheduled' WHERE state='queued'")
            db.execute("UPDATE runs SET state='cancelled',detail='重启后重新检查到期时间' WHERE state='queued'")
            for row in db.execute("SELECT * FROM tasks WHERE state IN ('running','submitting')").fetchall():
                if db.execute("SELECT 1 FROM runs WHERE task_id=? AND state='accepted'", (row['id'],)).fetchone():
                    db.execute("UPDATE tasks SET state='scheduled',error='短信已确认，重启后需检查原卡恢复情况' WHERE id=?", (row['id'],))
                    continue
                detail = '执行中发生重启，请检查短信记录及当前卡片；已暂停，未自动重发'
                db.execute("UPDATE tasks SET state='attention',error=? WHERE id=?", (detail, row['id']))
                self.notice(db, 'restart:'+row['id']+':'+str(row['due']), json.loads(row['config'])['label']+'：'+detail)
            # accepted is durable even if the process died while restoring the SIM.
            for row in db.execute("SELECT * FROM runs WHERE state IN ('running','submitting','accepted')").fetchall():
                detail = '进程重启，需检查原卡恢复情况。' + ('短信提交已确认。' if row['state']=='accepted' else '短信结果需人工核对。')
                db.execute("UPDATE runs SET state='attention',detail=?,updated=? WHERE id=?", (detail, self.clock(), row['id']))
                self.notice(db, 'recovery:'+row['id'], row['label']+'：'+detail)

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

    def notice(self, db, key, body):
        db.execute('INSERT OR IGNORE INTO notices VALUES (?,?)', (key, body))

    def config(self):
        with self.db() as db:
            row = db.execute("SELECT value FROM meta WHERE key='settings'").fetchone()
            tasks = [json.loads(r['config']) for r in db.execute('SELECT config FROM tasks ORDER BY rowid')]
        return json.loads(row[0]) if row else {'queue_gap_seconds':180}, tasks

    def configure(self, settings, raw_tasks):
        if not isinstance(raw_tasks, list) or not isinstance(settings, dict):
            raise ValueError('保号配置格式不正确')
        tasks = [normalize(t) for t in raw_tasks]
        if len({t['id'] for t in tasks}) != len(tasks):
            raise ValueError('任务 ID 不能重复')
        settings = {'queue_gap_seconds':max(30, min(1800, int(settings.get('queue_gap_seconds',180))))}
        with self.db() as db:
            rows = {r['id']:r for r in db.execute('SELECT * FROM tasks')}
            if any(r['state'] in ACTIVE for r in rows.values()):
                raise ValueError('保号任务正在执行，请完成后再修改')
            for task in tasks:
                row = rows.get(task['id'])
                old = json.loads(row['config']) if row else {}
                success = row['last_success'] if row else 0
                if row and old['profile_iccid'] != task['profile_iccid'] and success:
                    raise ValueError('已有发送记录，请为另一张卡新建任务')
                due = success + task['interval_days']*86400 if success else first_due(task)
                state = row['state'] if row else 'scheduled'
                error = row['error'] if row else ''
                # A known failure can be retried only through an explicit off/on.
                # Unknown submissions stay blocked, including after config edits.
                if state == 'failed' and not old.get('enabled') and task['enabled']:
                    state, error = 'scheduled', ''
                db.execute('''INSERT INTO tasks (id,config,state,due,last_success,error) VALUES (?,?,?,?,?,?)
                    ON CONFLICT(id) DO UPDATE SET config=excluded.config,state=excluded.state,
                    due=excluded.due,error=excluded.error''',
                    (task['id'],json.dumps(task,ensure_ascii=False),state,due,success,error))
            for task_id in rows.keys()-{t['id'] for t in tasks}:
                db.execute('DELETE FROM tasks WHERE id=?', (task_id,))
            db.execute("INSERT OR REPLACE INTO meta VALUES ('settings',?)", (json.dumps(settings),))
        return settings, tasks

    def snapshot(self, profiles):
        settings, _ = self.config()
        names = {p['iccid']:p.get('display_name') or p.get('serviceProviderName') or p.get('profileName') for p in profiles}
        with self.db() as db:
            rows = db.execute('SELECT * FROM tasks ORDER BY rowid').fetchall()
            runs = db.execute('SELECT * FROM runs ORDER BY created DESC LIMIT 20').fetchall()
            gap = db.execute("SELECT value FROM meta WHERE key='next_allowed'").fetchone()
        tasks = []
        for row in rows:
            task = json.loads(row['config'])
            tasks.append(dict(task, profile_name=names.get(task['profile_iccid'], task['label']),
                schedule_label=f"首次 {task['first_delay_days']} 天，之后每 {task['interval_days']} 天",
                next_run=stamp(row['due']), next_run_label=label_time(row['due']),
                last_success=stamp(row['last_success']), runtime_state=row['state'], error=row['error']))
        views = [dict(id=r['id'], task_id=r['task_id'], label=r['label'], state=r['state'],
                      updated_at=label_time(r['updated']), scheduled_for_label=label_time(r['scheduled']),
                      last_message=r['detail'], error='') for r in runs]
        return dict(settings=settings, tasks=tasks, scheduler_enabled=True,
                    active_run=next((r for r in views if r['state'] in {'running','submitting','accepted'}), None),
                    queued_runs=[r for r in views if r['state']=='queued'],
                    recent_runs=[r for r in views if r['state'] not in ACTIVE|{'accepted'}][:10],
                    next_allowed_at=stamp(float(gap[0])) if gap else '')

    def flush_notices(self):
        with self.db() as db:
            notices = db.execute('SELECT * FROM notices').fetchall()
        for row in notices:
            if self.notify(row['body'], 'keepalive:'+row['id']):
                with self.db() as db:
                    db.execute('DELETE FROM notices WHERE id=?', (row['id'],))

    def tick(self):
        self.flush_notices()
        now = self.clock()
        with self.db() as db:
            gap = db.execute("SELECT value FROM meta WHERE key='next_allowed'").fetchone()
            if gap and float(gap[0]) > now:
                return
            if db.execute("SELECT 1 FROM tasks WHERE state IN ('queued','running','submitting')").fetchone():
                return
            rows = db.execute("SELECT * FROM tasks WHERE state='scheduled' AND due<=? ORDER BY due", (now,)).fetchall()
            row = next((r for r in rows if json.loads(r['config'])['enabled']), None)
            if row is None:
                return
            task = json.loads(row['config'])
            run_id = uuid.uuid4().hex
            db.execute("UPDATE tasks SET state='queued' WHERE id=?", (row['id'],))
            db.execute('INSERT INTO runs (id,task_id,label,state,scheduled,created,updated) VALUES (?,?,?,?,?,?,?)',
                       (run_id,row['id'],task['label'],'queued',row['due'],now,now))
        try:
            self.enqueue(task, run_id)
        except Exception:
            # The action queue is busy or rejected the enqueue: nothing was sent.
            with self.db() as db:
                db.execute("UPDATE tasks SET state='scheduled' WHERE id=? AND state='queued'", (task['id'],))
                db.execute('DELETE FROM runs WHERE id=?', (run_id,))

    def run(self, run_id, log):
        with self.db() as db:
            run = db.execute("SELECT * FROM runs WHERE id=? AND state='queued'", (run_id,)).fetchone()
            if run is None:
                raise ValueError('任务未排队或已执行')
            row = db.execute('SELECT * FROM tasks WHERE id=?', (run['task_id'],)).fetchone()
            task = json.loads(row['config']) if row else None
            if not task or not task['enabled'] or row['due'] > self.clock():
                raise ValueError('任务已停用或尚未到期')
            db.execute("UPDATE tasks SET state='running' WHERE id=?", (task['id'],))
            db.execute("UPDATE runs SET state='running',updated=? WHERE id=?", (self.clock(),run_id))

        def original_saved(iccid):
            with self.db() as db:
                db.execute('UPDATE runs SET original_iccid=? WHERE id=?', (iccid,run_id))

        def before_submit():
            with self.db() as db:
                db.execute("UPDATE tasks SET state='submitting' WHERE id=?", (task['id'],))
                db.execute("UPDATE runs SET state='submitting',updated=? WHERE id=?", (self.clock(),run_id))

        def accepted(reference):
            now = self.clock()
            with self.db() as db:
                # Keep the task locked until restoration finishes. The success
                # timestamp prevents a second SMS even if restoration then fails.
                db.execute('UPDATE tasks SET last_success=?,due=? WHERE id=?',
                           (now,now+task['interval_days']*86400,task['id']))
                db.execute("UPDATE runs SET state='accepted',reference=?,updated=? WHERE id=?", (reference,now,run_id))

        error = None
        try:
            self.backend.keepalive_send(task, before_submit, accepted, original_saved, log)
        except Exception as exc:
            error = exc
        with self.db() as db:
            result = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
            success = result['state'] == 'accepted'
            if not success and error is None:
                error = RuntimeError('未取得短信提交确认')
            unknown = result['state'] == 'submitting' and not isinstance(error, SmsRejected)
            state = 'scheduled' if success else 'attention' if unknown else 'failed'
            due = db.execute('SELECT due FROM tasks WHERE id=?', (task['id'],)).fetchone()[0]
            detail = (f"短信提交已确认，下次 {label_time(due)}。" if success else
                      '发送结果不明，已暂停，未自动重发。' if unknown else '发送失败，已暂停；修复后关闭并重新开启任务可重试。')
            if error:
                detail += ' '+str(error)
            db.execute('UPDATE tasks SET state=?,error=? WHERE id=?', (state,str(error or ''),task['id']))
            db.execute('UPDATE runs SET state=?,detail=?,updated=? WHERE id=?',
                       ('done' if success and not error else 'attention' if unknown or success else 'error',detail,self.clock(),run_id))
            settings, _ = self.config()
            db.execute("INSERT OR REPLACE INTO meta VALUES ('next_allowed',?)", (str(self.clock()+settings['queue_gap_seconds']),))
            self.notice(db, run_id, task['label']+'：'+detail)
        log(detail)
        self.flush_notices()
        if error:
            raise error

    def can_run(self, run_id):
        with self.db() as db:
            return db.execute("SELECT 1 FROM runs WHERE id=? AND state='queued'", (run_id,)).fetchone() is not None

    def start(self):
        def loop():
            while not self.stop.is_set():
                try:
                    self.tick()
                except Exception as exc:
                    print('Keepalive scheduler:', type(exc).__name__)
                self.stop.wait(15)
        threading.Thread(target=loop, name='keepalive-interval', daemon=True).start()
