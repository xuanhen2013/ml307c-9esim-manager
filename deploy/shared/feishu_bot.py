"""Single-owner Feishu bot. All modem mutations use the web admin action queue."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import json
from pathlib import Path
import queue
import secrets
import sqlite3
import threading
import time
import uuid

import requests


class FeishuAPI:
    def __init__(self, config):
        self.app_id, self.secret = config['app_id'], config['app_secret']
        self.session = requests.Session()
        self.token, self.expires = '', 0
        self.lock = threading.Lock()

    def post(self, path, data, *, authenticated=True):
        headers = {}
        if authenticated:
            with self.lock:
                if time.time() >= self.expires:
                    result = self.post('/auth/v3/tenant_access_token/internal',
                                       {'app_id': self.app_id, 'app_secret': self.secret}, authenticated=False)
                    self.token = result['tenant_access_token']
                    self.expires = time.time() + max(1, result['expire'] - 120)
                headers['Authorization'] = 'Bearer ' + self.token
        try:
            response = self.session.post('https://open.feishu.cn/open-apis' + path,
                                         json=data, headers=headers, timeout=(8, 20))
            response.raise_for_status()
            result = response.json()
        except (requests.RequestException, ValueError):
            raise RuntimeError('飞书接口连接失败，请检查 NAS 网络') from None
        if result.get('code') != 0:
            # Never copy server bodies/URLs or credentials into logs or the UI.
            code = result.get('code', 'unknown')
            if code in (99991661, 99991663, 99991664, 99991668):
                self.expires = 0
            raise RuntimeError(f'飞书接口错误 {code}，请检查应用权限与发布状态')
        return result

    def send(self, owner, kind, content, message_uuid):
        return self.post('/im/v1/messages?receive_id_type=open_id', {
            'receive_id': owner, 'msg_type': kind,
            'content': json.dumps(content, ensure_ascii=False), 'uuid': message_uuid,
        })['data']['message_id']


class BotState:
    def __init__(self, path, app_id):
        self.path = str(path)
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY,created REAL NOT NULL);
              CREATE TABLE IF NOT EXISTS buttons (nonce TEXT PRIMARY KEY,action TEXT,iccid TEXT,
                active_iccid TEXT,expires REAL,used INTEGER DEFAULT 0,message_id TEXT DEFAULT '');
              CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY,action_id TEXT,name TEXT,state TEXT);
              CREATE TABLE IF NOT EXISTS outbox (id TEXT PRIMARY KEY,kind TEXT,content TEXT,
                delivered INTEGER DEFAULT 0,retry_at REAL DEFAULT 0);
            ''')
            existing = db.execute("SELECT value FROM meta WHERE key='app_id'").fetchone()
            if existing and existing[0] != app_id:
                raise ValueError('飞书应用与绑定记录不一致')
            db.execute("INSERT OR IGNORE INTO meta VALUES ('app_id',?)", (app_id,))

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def meta(self):
        with self.connect() as db:
            return dict(db.execute('SELECT key,value FROM meta').fetchall())

    def once(self, key):
        with self.connect() as db:
            return bool(db.execute('INSERT OR IGNORE INTO events VALUES (?,?)', (key, time.time())).rowcount)

    def enqueue(self, key, kind, content):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO outbox(id,kind,content) VALUES (?,?,?)',
                       (key, kind, json.dumps(content, ensure_ascii=False)))


def safe_md(value):
    return ''.join(f'&#{ord(c)};' if c in '*~><[]()#:_&' else c for c in str(value))


class FeishuBot:
    def __init__(self, config_path, sms_store, get_status, start_action, get_action, *, api=None, notice_valid=None):
        self.config = json.loads(Path(config_path).read_text(encoding='utf-8'))
        self.state = BotState(Path(config_path).with_suffix('.sqlite3'), self.config['app_id'])
        self.api = api or FeishuAPI(self.config)
        self.notice_valid = notice_valid
        self.sms_store, self.get_status = sms_store, get_status
        self.start_action, self.get_action = start_action, get_action
        self.commands = queue.Queue(maxsize=32)
        self.stop = threading.Event()
        self.ws = None
        self.last_error, self.last_event = '', ''
        self.owner = self.state.meta()
        self.pair_attempts = []
        self.target = 'feishu:' + self.config['app_id']
        # A process restart cannot prove the outcome of an in-flight modem action.
        # Report uncertainty; never replay a switch automatically.
        with self.state.connect() as db:
            jobs = db.execute("SELECT id FROM jobs WHERE state='pending'").fetchall()
            for job in jobs:
                db.execute("UPDATE jobs SET state='interrupted' WHERE id=?", (job['id'],))
                db.execute('INSERT OR IGNORE INTO outbox(id,kind,content) VALUES (?,?,?)',
                           ('job:' + job['id'], 'text', json.dumps({'text': '服务曾重启，切卡结果待确认。发送“状态”查看当前卡。'}, ensure_ascii=False)))

    def snapshot(self):
        return {'configured': True, 'bound': bool(self.owner.get('owner')),
                'connected': bool(self.ws and self.ws._conn is not None),
                'last_error': self.last_error, 'last_event': self.last_event}

    def owner_matches(self, open_id, tenant, chat):
        return bool(self.owner.get('owner') and open_id == self.owner['owner']
                    and tenant == self.owner['tenant'] and chat == self.owner['chat'])

    def receive(self, data, callback=False):
        """No HTTP, modem I/O or SQLite here: return the card ACK immediately."""
        if data.get('header', {}).get('app_id') != self.config['app_id']:
            return '应用身份不匹配'
        if callback:
            event = data.get('event', {})
            operator, context = event.get('operator', {}), event.get('context', {})
            if not self.owner_matches(operator.get('open_id'), operator.get('tenant_key'), context.get('open_chat_id')):
                return '仅绑定账号可操作'
        try:
            self.commands.put_nowait((data, callback))
        except queue.Full:
            return '请求较多，请稍后重试'
        self.last_event = datetime.now(timezone.utc).isoformat()
        return '已收到，结果将发到私聊'

    def handle(self, data, callback=False):
        header, event = data.get('header', {}), data.get('event', {})
        if header.get('app_id') != self.config['app_id']:
            return
        if callback:
            operator, context = event.get('operator', {}), event.get('context', {})
            if not self.owner_matches(operator.get('open_id'), operator.get('tenant_key'), context.get('open_chat_id')):
                return
            nonce = event.get('action', {}).get('value', {}).get('nonce')
            with self.state.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                button = db.execute('SELECT * FROM buttons WHERE nonce=?', (str(nonce),)).fetchone()
                valid = button and not button['used'] and button['expires'] >= time.time() and button['message_id'] == context.get('open_message_id')
                if valid:
                    db.execute('UPDATE buttons SET used=1 WHERE nonce=?', (nonce,))
            if not valid:
                self.text('按钮已使用或过期，请发送“状态”获取新卡片。')
                return
            if button['action'] == 'switch':
                self.switch(button, 'button:' + nonce)
            else:
                self.menu()
            return

        message, sender = event.get('message', {}), event.get('sender', {})
        if message.get('chat_type') != 'p2p' or sender.get('sender_type') != 'user' or message.get('message_type') != 'text':
            return
        open_id = sender.get('sender_id', {}).get('open_id')
        tenant, chat = sender.get('tenant_key') or header.get('tenant_key'), message.get('chat_id')
        if not open_id or not tenant or not chat or not message.get('message_id'):
            return
        # Old/offline messages must not bind an account or execute commands on reconnect.
        try:
            age = time.time() - int(message.get('create_time', 0)) / 1000
            content = json.loads(message.get('content', '{}')).get('text', '').strip()
        except (TypeError, ValueError):
            return
        if age > 300 or age < -60 or len(content) > 256:
            return
        if not self.owner.get('owner'):
            now = time.time()
            self.pair_attempts = [t for t in self.pair_attempts if now - t < 60]
            if len(self.pair_attempts) >= 10:
                return
            self.pair_attempts.append(now)
            code = content.removeprefix('绑定 ').strip()
            if now > self.config.get('pair_expires', 0) or not hmac.compare_digest(
                    hashlib.sha256(code.encode()).hexdigest(), self.config.get('pair_hash', '')):
                return
            latest = self.sms_store.messages(1)
            baseline = str(latest[0]['id']) if latest else '0'
            with self.state.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute("SELECT 1 FROM meta WHERE key='owner'").fetchone():
                    return
                db.executemany('INSERT INTO meta VALUES (?,?)',
                               [('owner', open_id), ('tenant', tenant), ('chat', chat), ('baseline', baseline)])
            self.owner = self.state.meta()
            self.text('已绑定。此后收到的新短信会发到这里。发送“状态”或“切卡”打开控制卡片。')
            self.menu()
            return
        if not self.owner_matches(open_id, tenant, chat) or not self.state.once('message:' + message['message_id']):
            return
        if content in ('状态', '菜单', '切卡', '刷新', '/start', '/status', '帮助'):
            self.menu()
        else:
            self.text('发送“状态”查看当前卡、漫游网络和信号；发送“切卡”选择卡片。收到的新短信会自动通知。')

    def text(self, text, key=None):
        self.state.enqueue(key or str(uuid.uuid4()), 'text', {'text': text})

    def menu(self):
        status = self.get_status()
        profiles = status['profiles']
        active = next((p for p in profiles if p.get('is_active')), {})
        modem = status.get('modem', {})
        signal = modem.get('signal_details', {})
        name = active.get('display_name', '未知')
        network = modem.get('operator_name') or modem.get('operator') or '未注册'
        network = network if isinstance(network, str) else '未注册'
        label = '漫游' if modem.get('registration') == 'roaming' else str(modem.get('registration', '未知'))
        info = (f'**{safe_md(name)}**\n{safe_md(network)} · {safe_md(label)} · {safe_md(modem.get("access_tech", ""))}\n'
                f'信号 {safe_md(signal.get("rssi_text", "未知"))} · RSRP {safe_md(signal.get("rsrp_text", "未知"))}')
        if status.get('status_message'):
            info += '\n' + safe_md(status['status_message'])
        elements = [{'tag': 'column_set', 'columns': [{'tag': 'column', 'width': 'weighted', 'weight': 1,
                     'elements': [{'tag': 'markdown', 'content': info}]}]}]
        nonces = []

        def button(title, action, iccid='', primary=False):
            nonce = secrets.token_urlsafe(24)
            nonces.append(nonce)
            with self.state.connect() as db:
                db.execute('INSERT INTO buttons(nonce,action,iccid,active_iccid,expires) VALUES (?,?,?,?,?)',
                           (nonce, action, iccid, active.get('iccid', ''), time.time() + 900))
            return {'tag': 'button', 'text': {'tag': 'plain_text', 'content': title}, 'width': 'fill',
                    'type': 'primary_filled' if primary else 'default',
                    'behaviors': [{'type': 'callback', 'value': {'nonce': nonce}}]}

        elements.append(button('刷新状态', 'status', primary=True))
        for profile in profiles:
            if not profile.get('is_active'):
                elements.append(button('切换到 ' + profile['display_name'], 'switch', profile['iccid']))
        card = {'schema': '2.0', 'config': {'update_multi': True, 'enable_forward': False},
                'header': {'title': {'tag': 'plain_text', 'content': '9eSIM'}, 'template': 'blue'},
                'body': {'direction': 'vertical', 'vertical_spacing': '12px', 'elements': elements}}
        # Associate buttons with their actual private-chat message after delivery.
        self.state.enqueue('menu:' + str(uuid.uuid4()), 'interactive', {'card': card, 'nonces': nonces})

    def switch(self, button, key):
        status = self.get_status()
        active = next((p for p in status['profiles'] if p.get('is_active')), {})
        target = next((p for p in status['profiles'] if p['iccid'] == button['iccid']), None)
        if not target or active.get('iccid', '') != button['active_iccid']:
            self.text('卡状态已变化，请发送“状态”后重新选择。')
            return
        if target.get('is_active'):
            self.text('这张卡已经在使用。')
            return
        with self.state.connect() as db:
            db.execute('INSERT INTO jobs VALUES (?,?,?,?)', (key, '', target['display_name'], 'pending'))
        try:
            action_id = self.start_action('switch_profile', {'iccid': target['iccid']},
                                          metadata={'kind': 'feishu'}, reject_if_busy=True)
        except ValueError as exc:
            with self.state.connect() as db:
                db.execute("UPDATE jobs SET state='rejected' WHERE id=?", (key,))
            self.text(str(exc))
            return
        with self.state.connect() as db:
            db.execute('UPDATE jobs SET action_id=? WHERE id=?', (action_id, key))
        self.text('正在切换到 ' + target['display_name'] + '，等待网络注册。')

    def monitor_jobs(self):
        with self.state.connect() as db:
            jobs = db.execute("SELECT * FROM jobs WHERE state='pending' AND action_id<>''").fetchall()
        for job in jobs:
            try:
                result = self.get_action(job['action_id'], 0)
            except KeyError:
                result = {'state': 'error', 'error': '任务结果已过期，请查询当前状态'}
            if result['state'] not in ('done', 'error'):
                continue
            text = (f'已切换到 {job["name"]} 并完成网络注册。' if result['state'] == 'done'
                    else '切卡未完成：' + result.get('error', '未知错误'))
            self.text(text, 'job:' + job['id'])
            with self.state.connect() as db:
                db.execute('UPDATE jobs SET state=? WHERE id=?', (result['state'], job['id']))
            self.menu()

    def collect_sms(self):
        if not self.owner.get('owner'):
            return
        for message in self.sms_store.pending(self.target, after_id=int(self.owner.get('baseline', 0))):
            timestamp = datetime.fromisoformat(message['received_at']).astimezone(timezone(timedelta(hours=8)))
            text = (f'{message["profile_name"] or "未知卡"} 收到短信\n'
                    f'发件人：{message["number"]}\n时间：{timestamp:%Y-%m-%d %H:%M:%S}（北京时间）\n\n{message["text"]}')
            self.text(text, 'sms:' + str(message['id']))

    def flush(self):
        if not self.owner.get('owner'):
            return
        with self.state.connect() as db:
            rows = db.execute('SELECT * FROM outbox WHERE delivered=0 AND retry_at<=? ORDER BY rowid LIMIT 10', (time.time(),)).fetchall()
        for row in rows:
            try:
                if self.notice_valid is not None and not self.notice_valid(row['id']):
                    with self.state.connect() as db:
                        db.execute('DELETE FROM outbox WHERE id=? AND delivered=0', (row['id'],))
                    continue
                content = json.loads(row['content'])
                payload = content['card'] if row['kind'] == 'interactive' else content
                message_id = self.api.send(self.owner['owner'], row['kind'], payload,
                                           str(uuid.uuid5(uuid.NAMESPACE_URL, self.config['app_id'] + ':' + row['id'])))
                with self.state.connect() as db:
                    db.execute('UPDATE outbox SET delivered=1 WHERE id=?', (row['id'],))
                    if row['kind'] == 'interactive':
                        db.executemany('UPDATE buttons SET message_id=? WHERE nonce=?',
                                       [(message_id, n) for n in content['nonces']])
                if row['id'].startswith('sms:'):
                    self.sms_store.delivery_result(int(row['id'][4:]), self.target, True)
                self.last_error = ''
            except Exception as exc:
                self.last_error = str(exc) if isinstance(exc, RuntimeError) else '飞书通知处理失败'
                with self.state.connect() as db:
                    db.execute('UPDATE outbox SET retry_at=? WHERE id=?', (time.time() + 60, row['id']))
                break

    def command_loop(self):
        while not self.stop.is_set():
            try:
                data, callback = self.commands.get(timeout=1)
            except queue.Empty:
                continue
            try:
                self.handle(data, callback)
            except Exception:
                self.last_error = '飞书命令处理失败，请重新发送“状态”'
            finally:
                self.commands.task_done()

    def notification_loop(self):
        while not self.stop.wait(2):
            try:
                self.monitor_jobs()
                self.collect_sms()
                self.flush()
            except Exception:
                self.last_error = '飞书通知队列暂时不可用'

    def websocket_loop(self):
        # Import inside this dedicated thread: the pinned SDK owns an asyncio loop.
        import lark_oapi as lark
        from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse
        from lark_oapi.core.log import logger

        # SDK connection logs include signed URLs. Expose only our sanitized health.
        logger.disabled = True

        def receive_message(event):
            result = self.receive(json.loads(lark.JSON.marshal(event)))
            if result == '请求较多，请稍后重试':
                raise RuntimeError('command queue full')

        def receive_card(event):
            result = self.receive(json.loads(lark.JSON.marshal(event)), callback=True)
            return P2CardActionTriggerResponse({'toast': {'type': 'info', 'content': result}})

        handler = (lark.EventDispatcherHandler.builder('', '')
                   .register_p2_im_message_receive_v1(receive_message)
                   .register_p2_card_action_trigger(receive_card).build())
        while not self.stop.is_set():
            try:
                self.ws = lark.ws.Client(self.config['app_id'], self.config['app_secret'],
                                         event_handler=handler, log_level=lark.LogLevel.ERROR)
                self.ws.start()
            except Exception:
                self.last_error = '飞书长连接未建立，请检查凭证、网络和应用配置'
            if self.stop.wait(30):
                break

    def start(self):
        for target in (self.command_loop, self.notification_loop, self.websocket_loop):
            threading.Thread(target=target, daemon=True, name=target.__name__).start()

    def close(self):
        self.stop.set()
