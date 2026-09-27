"""Persistent receive service and serialized device operations for the original UI."""
from __future__ import annotations

import copy
from contextlib import contextmanager
import hashlib
from pathlib import Path
import sqlite3
import threading
import time
from datetime import datetime, timezone

from ml307_modem import Modem


def complete_messages(messages):
    complete, groups = [], {}
    for message in messages:
        part = message.get('concat')
        if not part:
            complete.append(message)
            continue
        key = (message['number'], message['timestamp'][:13], part['ref'], part['total'])
        groups.setdefault(key, {})[part['part']] = message
    for key, parts in groups.items():
        count = key[-1]
        if count < 1 or set(parts) != set(range(1, count+1)):
            continue
        merged = dict(parts[1])
        merged['text'] = ''.join(parts[i]['text'] for i in range(1,count+1))
        merged['pdu'] = '|'.join(parts[i]['pdu'] for i in range(1,count+1))
        complete.append(merged)
    return complete


class SmsStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS sms (
                id INTEGER PRIMARY KEY, fingerprint TEXT UNIQUE NOT NULL,
                number TEXT, text TEXT, timestamp TEXT, profile_iccid TEXT,
                profile_name TEXT, imported INTEGER NOT NULL DEFAULT 0,
                received_at TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS deliveries (
                sms_id INTEGER NOT NULL, target_id TEXT NOT NULL, delivered INTEGER DEFAULT 0,
                attempts INTEGER DEFAULT 0, retry_at REAL DEFAULT 0,
                PRIMARY KEY(sms_id,target_id));
              CREATE INDEX IF NOT EXISTS sms_profile_id ON sms(profile_iccid,id);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def ingest(self, messages, profile):
        with self.connect() as db:
            first_run = db.execute("SELECT value FROM meta WHERE key='initialized'").fetchone() is None
            for message in complete_messages(messages):
                fingerprint = hashlib.sha256(message['pdu'].encode('ascii')).hexdigest()
                db.execute('''INSERT OR IGNORE INTO sms
                  (fingerprint,number,text,timestamp,profile_iccid,profile_name,imported,received_at)
                  VALUES (?,?,?,?,?,?,?,?)''', (fingerprint, message['number'], message['text'], message['timestamp'],
                  '' if first_run else profile.get('iccid',''),
                  '历史短信（收件卡未确认）' if first_run else profile.get('serviceProviderName') or profile.get('profileName',''),
                  int(first_run), datetime.now(timezone.utc).isoformat()))
            db.execute("INSERT OR IGNORE INTO meta VALUES ('initialized','1')")

    def messages(self, limit=100):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('SELECT * FROM sms ORDER BY id DESC LIMIT ?', (limit,)).fetchall()
        return [dict(row, id=str(row['id']), state='received', state_label='已接收') for row in rows]

    def pending(self, target_id, after_id=0):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('''SELECT sms.* FROM sms LEFT JOIN deliveries d
              ON d.sms_id=sms.id AND d.target_id=?
              WHERE sms.imported=0 AND sms.id>? AND coalesce(d.delivered,0)=0 AND coalesce(d.retry_at,0)<=?
              ORDER BY sms.id LIMIT 10''', (target_id,after_id,time.time())).fetchall()
        return [dict(row) for row in rows]

    def latest_id(self):
        with self.connect() as db:
            return db.execute('SELECT coalesce(max(id),0) FROM sms').fetchone()[0]

    def received_after(self, profile_iccid, after_id, through_id, limit=200):
        with self.connect() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute('''SELECT id,text,received_at FROM sms
                WHERE profile_iccid=? AND imported=0 AND id>? AND id<=? ORDER BY id LIMIT ?''',
                (profile_iccid,after_id,through_id,limit)).fetchall()
        return [dict(row) for row in rows]

    def delivery_result(self, sms_id, target_id, success):
        with self.connect() as db:
            db.execute('''INSERT INTO deliveries (sms_id,target_id,delivered,attempts,retry_at) VALUES (?,?,?,1,?)
              ON CONFLICT(sms_id,target_id) DO UPDATE SET delivered=excluded.delivered,
              attempts=attempts+1,retry_at=excluded.retry_at''', (sms_id,target_id,int(success),time.time()+60))


class ML307Backend:
    def __init__(self, port, data_dir, targets_callback, notify_callback):
        self.port = port
        self.store = SmsStore(Path(data_dir)/'sms.sqlite3')
        self.targets_callback, self.notify_callback = targets_callback, notify_callback
        self.operation_lock = threading.RLock()
        self.state_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.profiles = []
        self.modem = {}
        self.error = '正在连接模组'
        self.notify_error = ''
        self.busy = False
        self.last_profiles = 0
        self.updated = ''

    def update(self, **values):
        with self.state_lock:
            for key,value in values.items():
                setattr(self,key,value)

    def snapshot(self):
        with self.state_lock:
            state = copy.deepcopy({key:getattr(self,key) for key in
                                   ('profiles','modem','error','notify_error','busy','updated')})
        state['sms'] = self.store.messages()
        return state

    def refresh(self, force_profiles=False):
        if not self.operation_lock.acquire(blocking=False):
            return
        try:
            with Modem(self.port) as modem:
                if force_profiles or not self.profiles or time.monotonic()-self.last_profiles > 30:
                    self.update(profiles=modem.profiles())
                    self.last_profiles = time.monotonic()
                status = modem.status()
                self.update(modem=status, error='', updated=datetime.now(timezone.utc).isoformat())
                active = next((p for p in self.profiles if p['enabled']), {})
                self.store.ingest(modem.inbox(), active)
        except Exception as exc:
            self.update(error=str(exc))
        finally:
            self.operation_lock.release()

    def switch(self, iccid, log):
        # Same lock covers polling, card mutation, re-registration and final verification.
        with self.operation_lock:
            self.update(busy=True)
            try:
                with Modem(self.port) as modem:
                    profiles = modem.profiles()
                    target = next((p for p in profiles if p['iccid'] == iccid), None)
                    if not target:
                        raise ValueError('目标配置不在当前卡列表中')
                    active = next((p for p in profiles if p['enabled']), {})
                    # Drain old-card messages before changing identity.
                    self.store.ingest(modem.inbox(), active)
                    if target['enabled']:
                        log('这张卡已经启用')
                    else:
                        label = target.get('serviceProviderName') or target.get('profileName') or iccid[-6:]
                        log('正在启用 ' + label)
                        modem.enable(iccid)
                        time.sleep(3)
                    profiles = modem.profiles()
                    self.update(profiles=profiles)
                    self.last_profiles = time.monotonic()
                    if not any(p['iccid']==iccid and p['enabled'] for p in profiles):
                        raise RuntimeError('卡片未确认目标配置已启用')
                    log('卡片已确认配置，等待网络注册')
                    end = time.monotonic()+90
                    previous = None
                    last_error = None
                    while time.monotonic() < end:
                        try:
                            status = modem.status()
                        except RuntimeError as exc:
                            # The modem briefly rejects CPIN/COPS while applying
                            # a SIM refresh. This is not an EnableProfile failure.
                            if str(exc) != last_error:
                                log('网络切换中，等待模组就绪：' + str(exc))
                                last_error = str(exc)
                            time.sleep(3)
                            continue
                        last_error = None
                        self.update(modem=status, error='', updated=datetime.now(timezone.utc).isoformat())
                        if status['registration'] != previous:
                            log('网络状态：' + status['registration'])
                            previous = status['registration']
                        if status['state'] == 'registered' and status['sim_ready']:
                            self.store.ingest(modem.inbox(), target)
                            log('已注册网络：' + status['operator_name'])
                            return
                        time.sleep(3)
                    raise RuntimeError('配置已切换，但网络注册超时；请检查信号或稍后刷新')
            except Exception as exc:
                self.update(error=str(exc))
                raise
            finally:
                self.update(busy=False)

    def keepalive_send(self, task, before_submit, accepted, original_saved, log):
        # Hold the same lock through switch -> submit -> restore; polling cannot
        # consume the CMGS response and other operations cannot change the SIM.
        with self.operation_lock:
            with Modem(self.port) as modem:
                profiles = modem.profiles()
            original = next((p['iccid'] for p in profiles if p['enabled']), '')
            if not original:
                raise RuntimeError('无法确认原卡，暂不执行保号切卡')
            original_saved(original)
            main_error = None
            try:
                self.switch(task['profile_iccid'], log)
                with Modem(self.port) as modem:
                    if not any(p['iccid'] == task['profile_iccid'] and p['enabled'] for p in modem.profiles()):
                        raise RuntimeError('发送前核对卡片失败')
                    status = modem.status()
                    if status['state'] != 'registered' or not status['sim_ready']:
                        raise RuntimeError('发送前网络未就绪')
                    reference = modem.send_sms(task['target_number'], task['message'], before_submit)
                    accepted(reference)
                    log('模组已确认短信提交，参考号：' + str(reference))
            except Exception as exc:
                main_error = exc
            finally:
                if original != task['profile_iccid']:
                    try:
                        self.switch(original, log)
                        log('已恢复原卡')
                    except Exception as exc:
                        # Preserve the SMS outcome; a restore error must never cause a resend.
                        if main_error is None:
                            main_error = RuntimeError('短信已提交，但恢复原卡失败：' + str(exc))
                        else:
                            main_error.args = (str(main_error) + '；恢复原卡失败：' + str(exc),)
            if main_error:
                raise main_error

    def _poll(self):
        while not self.stop_event.is_set():
            self.refresh()
            self.stop_event.wait(5)

    def _notify(self):
        # Network delivery never holds the modem lock. Each channel has its own retry state.
        while not self.stop_event.is_set():
            errors = []
            try:
                for target in self.targets_callback():
                    for message in self.store.pending(target['id']):
                        try:
                            self.notify_callback(target, message)
                            self.store.delivery_result(message['id'],target['id'],True)
                        except Exception:
                            self.store.delivery_result(message['id'],target['id'],False)
                            errors.append('通知未送达：' + str(target.get('label') or '未命名渠道'))
                self.update(notify_error='；'.join(errors))
            except Exception:
                self.update(notify_error='读取通知配置失败')
            self.stop_event.wait(10)

    def start(self):
        threading.Thread(target=self._poll, name='ml307-receive', daemon=True).start()
        threading.Thread(target=self._notify, name='ml307-notify', daemon=True).start()

    def close(self):
        self.stop_event.set()
