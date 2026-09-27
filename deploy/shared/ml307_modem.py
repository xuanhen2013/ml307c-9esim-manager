"""ML307 AT/eUICC transport and single-part scheduled SMS submission."""
from __future__ import annotations

import contextlib
import ctypes
import errno
import glob
import math
import os
from pathlib import Path
import re
import time
from datetime import datetime, timedelta, timezone


# ITU E.212 names; unknown PLMNs remain numeric instead of being guessed from SPN.
PLMN_NAMES = {
    '46000': '中国移动',
    '46001': '中国联通',
}


def quantized_signal(index, highest, first_lower, step, unit):
    """27.007 CESQ reports intervals, not an exact dBm/dB measurement."""
    if index == 0:
        return f'< {first_lower:g} {unit}'
    if index == highest:
        return f'≥ {first_lower+(highest-1)*step:g} {unit}'
    if 1 <= index < highest:
        lower = first_lower+(index-1)*step
        return f'{lower:g}～{lower+step:g} {unit}'
    return None  # 255 = not known or not detectable.


def parse_signal(csq_reply, cesq_reply=''):
    match = re.search(r'\+CSQ:\s*(\d+)', csq_reply)
    csq = int(match[1]) if match and 0 <= int(match[1]) <= 31 else None
    rssi = None if csq is None else 2*csq-113
    rssi_text = None if rssi is None else f'{rssi} dBm'
    if csq == 0:
        rssi_text = '≤ -113 dBm'
    elif csq == 31:
        rssi_text = '≥ -51 dBm'
    result = {'csq':csq, 'rssi_dbm':rssi, 'rssi_text':rssi_text,
              'rsrp_text':None, 'rsrq_text':None, 'rsrp_index':None, 'rsrq_index':None}
    match = re.search(r'\+CESQ:\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)', cesq_reply)
    if match:
        rsrq, rsrp = int(match[5]), int(match[6])
        result.update(rsrq_text=quantized_signal(rsrq,34,-19.5,0.5,'dB'),
                      rsrp_text=quantized_signal(rsrp,97,-140,1,'dBm'),
                      rsrq_index=rsrq if rsrq <= 34 else None,
                      rsrp_index=rsrp if rsrp <= 97 else None)
    return result


def tlvs(data: bytes):
    offset = 0
    while offset < len(data):
        start = offset
        first = data[offset]
        offset += 1
        if first & 31 == 31:
            while True:
                b = data[offset]
                offset += 1
                if not b & 128:
                    break
        tag = data[start:offset].hex().upper()
        size = data[offset]
        offset += 1
        if size & 128:
            n = size & 127
            if not n or offset + n > len(data):
                raise ValueError('Invalid TLV length')
            size = int.from_bytes(data[offset:offset+n], 'big')
            offset += n
        if offset + size > len(data):
            raise ValueError('Truncated TLV')
        yield tag, data[offset:offset+size]
        offset += size


class SerialTransport:
    def __init__(self, port):
        import serial
        self.port = serial.Serial(port, 115200, timeout=0.15, write_timeout=2)
        self.port.dtr = self.port.rts = True
        self.port.reset_input_buffer()

    def write(self, data):
        if self.port.write(data) != len(data):
            raise RuntimeError('串口写入不完整')

    def read(self):
        return self.port.read(self.port.in_waiting or 1)

    def close(self):
        self.port.close()


class Bulk(ctypes.Structure):
    _fields_ = [('ep', ctypes.c_uint), ('length', ctypes.c_uint),
                ('timeout', ctypes.c_uint), ('data', ctypes.c_void_p)]


def ioc(direction, number, size):
    return (direction << 30) | (size << 16) | (ord('U') << 8) | number


class UsbTransport:
    """Linux usbfs; claim only unbound ML307 AT interface 2, never detach."""
    def __init__(self):
        devices = []
        for item in glob.glob('/sys/bus/usb/devices/*'):
            p = Path(item)
            try:
                if (p/'idVendor').read_text().strip() == '2ecc' and (p/'idProduct').read_text().strip() == '3012':
                    devices.append(p)
            except OSError:
                pass
        if len(devices) != 1:
            raise RuntimeError('需要恰好一个 ML307 USB 设备，当前找到 %d 个' % len(devices))
        p = devices[0]
        interface = Path(str(p) + ':1.2')
        if (interface/'driver').is_symlink():
            raise RuntimeError('AT 接口已绑定内核驱动，请设置 ML307_PORT 为对应串口')
        for endpoint, direction in [('ep_0a', 'out'), ('ep_81', 'in')]:
            ep = interface/endpoint
            if (ep/'type').read_text().strip() != 'Bulk' or (ep/'direction').read_text().strip() != direction:
                raise RuntimeError('USB 端点布局不匹配')
        node = '/dev/bus/usb/%03d/%03d' % (int((p/'busnum').read_text()), int((p/'devnum').read_text()))
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.ioctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_void_p]
        self.libc.ioctl.restype = ctypes.c_int
        self.fd = os.open(node, os.O_RDWR)
        try:
            self.call(ioc(2, 15, 4), ctypes.byref(ctypes.c_uint(2)))
        except Exception:
            os.close(self.fd)
            raise

    def call(self, command, arg):
        result = self.libc.ioctl(self.fd, command, arg)
        if result < 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))
        return result

    def transfer(self, endpoint, data=None):
        buffer = ctypes.create_string_buffer(data, len(data)) if data is not None else ctypes.create_string_buffer(4096)
        request = Bulk(endpoint, len(buffer), 1000 if data is not None else 200, ctypes.addressof(buffer))
        count = self.call(ioc(3, 2, ctypes.sizeof(Bulk)), ctypes.byref(request))
        return count if data is not None else buffer.raw[:count]

    def write(self, data):
        if self.transfer(0x0a, data) != len(data):
            raise RuntimeError('USB 写入不完整')

    def read(self):
        try:
            return self.transfer(0x81)
        except OSError as exc:
            if exc.errno == errno.ETIMEDOUT:
                return b''
            raise

    def close(self):
        try:
            self.call(ioc(2, 16, 4), ctypes.byref(ctypes.c_uint(2)))
        finally:
            os.close(self.fd)


class SmsRejected(RuntimeError):
    """The modem explicitly rejected the SMS submission."""


def encode_submit(number, text):
    if not re.fullmatch(r'\+[1-9]\d{6,14}', number):
        raise ValueError('收件号码需要使用 +国家区号 格式')
    if not text or any(ord(c) > 0xffff or 0xd800 <= ord(c) <= 0xdfff for c in text):
        raise ValueError('短信不能为空，且暂不支持 emoji')
    data = text.encode('utf-16-be')
    if len(data) > 140:
        raise ValueError('保号短信最多 70 个字符，只发送单条短信')
    digits = number[1:]
    padded = digits + ('F' if len(digits) % 2 else '')
    address = bytes.fromhex(''.join(padded[i+1]+padded[i] for i in range(0, len(padded), 2)))
    # Default SIM SMSC; SMS-SUBMIT, no validity-period field, UCS2, one segment.
    tpdu = bytes([1, 0, len(digits), 0x91]) + address + bytes([0, 8, len(data)]) + data
    return (b'\x00' + tpdu).hex().upper(), len(tpdu)


class Modem:
    def __init__(self, port):
        self.transport = UsbTransport() if port == 'usb' else SerialTransport(port)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.transport.close()

    def at(self, command, timeout=10):
        self.transport.write((command + '\r').encode('ascii'))
        response = b''
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            response += self.transport.read()
            if re.search(rb'(?:^|[\r\n])(OK|ERROR|\+(?:CME|CMS) ERROR:[^\r\n]*)(?:[\r\n]|$)', response):
                break
        text = response.decode('ascii', errors='replace').strip()
        if not re.search(r'(?:^|[\r\n])OK(?:[\r\n]|$)', text):
            # APDU data and identifiers are intentionally excluded from errors.
            raise RuntimeError('%s 未成功：%s' % (command.split('=')[0], '设备返回 ERROR' if 'ERROR' in text else '超时'))
        return text

    @contextlib.contextmanager
    def channel(self):
        reply = self.at('AT+CCHO="A0000005591010FFFFFFFF8900000100"')
        match = re.search(r'(?:^|[\r\n])(?:\+CCHO:\s*)?(\d+)(?=[\r\n]|$)', reply)
        if not match or not 1 <= int(match[1]) <= 19:
            raise RuntimeError('无法识别逻辑通道号')
        channel = int(match[1])
        try:
            yield channel
        finally:
            # A successful profile refresh may have already closed this channel.
            try:
                self.at('AT+CCHC=%d' % channel)
            except RuntimeError:
                pass

    def apdu(self, channel, request):
        if len(request) > 255:
            raise ValueError('APDU request too long')
        cla = (0x80 | channel) if channel < 4 else (0xc0 | (channel-4))
        command = '%02XE29100%02X%s' % (cla, len(request), request.hex().upper())
        parts = []
        for _ in range(64):
            reply = self.at('AT+CGLA=%d,%d,"%s"' % (channel, len(command), command))
            match = re.search(r'\+CGLA:\s*(\d+)\s*,\s*"([0-9A-Fa-f]+)"', reply)
            if not match or int(match[1]) != len(match[2]):
                raise RuntimeError('CGLA 响应格式错误')
            data = bytes.fromhex(match[2])
            if len(data) < 2:
                raise RuntimeError('APDU 响应不完整')
            body, sw1, sw2 = data[:-2], data[-2], data[-1]
            if sw1 == 0x6c and command[2:4] == 'C0':
                command = command[:-2] + '%02X' % sw2
                continue
            parts.append(body)
            if (sw1, sw2) == (0x90, 0):
                return b''.join(parts)
            if sw1 != 0x61:
                raise RuntimeError('APDU 状态 %02X%02X' % (sw1, sw2))
            command = '%02XC00000%02X' % (cla, sw2)
        raise RuntimeError('APDU 分段响应过多')

    def profiles(self):
        with self.channel() as channel:
            data = self.apdu(channel, bytes.fromhex('BF2D00'))
        outer = dict(tlvs(dict(tlvs(data))['BF2D']))['A0']
        result = []
        for tag, body in tlvs(outer):
            if tag != 'E3':
                continue
            fields = dict(tlvs(body))
            bcd = fields['5A'].hex().upper()
            iccid = ''.join(bcd[i+1]+bcd[i] for i in range(0, len(bcd), 2)).rstrip('F')
            provider = fields.get('91', b'').decode('utf-8', errors='replace')
            name = fields.get('92', b'').decode('utf-8', errors='replace')
            result.append({'iccid': iccid, 'serviceProviderName': provider,
                           'profileName': name, 'enabled': fields.get('9F70') == b'\x01',
                           'state': 'enabled' if fields.get('9F70') == b'\x01' else 'disabled'})
        return result

    def enable(self, iccid):
        if not re.fullmatch(r'\d{18,22}', iccid):
            raise ValueError('ICCID 格式不正确')
        padded = iccid + ('F' if len(iccid) % 2 else '')
        bcd = bytes.fromhex(''.join(padded[i+1]+padded[i] for i in range(0, len(padded), 2)))
        ident = bytes([0x5a, len(bcd)]) + bcd
        inner = bytes([0xa0, len(ident)]) + ident + b'\x81\x01\xff'
        request = bytes([0xbf, 0x31, len(inner)]) + inner
        with self.channel() as channel:
            reply = self.apdu(channel, request)
        code = int.from_bytes(dict(tlvs(dict(tlvs(reply))['BF31']))['80'], 'big')
        if code:
            raise RuntimeError('启用配置失败，eUICC 返回 %d' % code)

    def status(self):
        pin = self.at('AT+CPIN?')
        registered = self.at('AT+CEREG?')
        operator = self.at('AT+COPS?')
        signal = self.at('AT+CSQ')
        reg = re.search(r'\+CEREG:\s*\d+,\s*(\d+)', registered)
        op = re.search(r'\+COPS:\s*\d+,\s*(\d+),\s*"([^"]+)"(?:,\s*(\d+))?', operator)
        state = int(reg[1]) if reg else -1
        code, reported_name, act = '', '', None
        if op:
            original_format = int(op[1])
            act = int(op[3]) if op[3] else None
            if original_format == 2:
                code = op[2]
            else:
                reported_name = op[2]
                # Mode 3 changes ONLY the display format, never operator selection.
                # Restore it even when the numeric read fails during a SIM refresh.
                try:
                    self.at('AT+COPS=3,2')
                    try:
                        numeric = self.at('AT+COPS?')
                        match = re.search(r'\+COPS:\s*\d+,\s*2,\s*"(\d{5,6})"', numeric)
                        if match:
                            code = match[1]
                    finally:
                        self.at(f'AT+COPS=3,{original_format}')
                except RuntimeError:
                    pass  # Show unavailable, never substitute a card brand as PLMN.
        if state not in (1,5) or not re.fullmatch(r'\d{5,6}', code):
            code = ''
        try:
            extended_signal = self.at('AT+CESQ')
        except RuntimeError:
            extended_signal = ''  # Older firmware can still show CSQ/RSSI.
        details = parse_signal(signal, extended_signal)
        csq = details['csq']
        access = {0:'GSM',2:'UMTS',3:'EDGE',4:'HSDPA',5:'HSUPA',6:'HSPA',7:'LTE',9:'NB-IoT'}.get(act,'--')
        return {'number':'--', 'operator_code':code or '--',
                'operator_name':PLMN_NAMES.get(code, 'PLMN '+code) if code else '未取得驻网信息',
                'operator_reported_name':reported_name,
                'registration':{1:'home',5:'roaming',2:'searching',3:'denied',0:'idle'}.get(state,'unknown'),
                'state':'registered' if state in (1,5) else 'searching',
                'signal':str(round(csq*100/31)) if csq is not None else '--',
                'signal_details':details,
                'access_tech':access, 'current_modes':'4G' if access == 'LTE' else access, 'apn':'--', 'ip_type':'--',
                'sim_ready':bool(re.search(r'\+CPIN:\s*READY', pin))}

    def inbox(self):
        if not re.search(r'\+CMGF:\s*0', self.at('AT+CMGF?')):
            raise RuntimeError('需要 PDU 短信格式；当前未自动改变设备设置')
        # ML307C rejects optional CMGL <mode>; listing can mark SMS as read.
        reply = self.at('AT+CMGL=4', timeout=15)
        messages = []
        for index, header, pdu in re.findall(r'\+CMGL:\s*(\d+),([^\r\n]*)[\r\n]+([0-9A-Fa-f]+)', reply):
            message = decode_deliver(pdu)
            if message:
                message.update(index=int(index), pdu=pdu.upper())
                messages.append(message)
        return messages

    def send_sms(self, number, text, before_submit):
        pdu, length = encode_submit(number, text)
        if not re.search(r'\+CMGF:\s*0', self.at('AT+CMGF?')):
            raise RuntimeError('需要 PDU 短信格式')
        submitted = False
        try:
            self.transport.write(f'AT+CMGS={length}\r'.encode('ascii'))
            response = b''
            end = time.monotonic()+10
            while time.monotonic() < end:
                response += self.transport.read()
                if b'ERROR' in response:
                    raise SmsRejected('模组拒绝进入短信发送状态')
                if re.search(rb'(?:^|[\r\n])> ?', response):
                    break
            else:
                raise RuntimeError('等待短信输入提示超时，未提交短信')
            self.transport.write(pdu.encode('ascii'))
            # Commit the uncertain state before Ctrl-Z can reach the device.
            before_submit()
            submitted = True
            self.transport.write(b'\x1a')
            response = b''
            end = time.monotonic()+120
            while time.monotonic() < end:
                response += self.transport.read()
                if re.search(rb'(?:^|[\r\n])(?:ERROR|\+(?:CMS|CME) ERROR:[^\r\n]*)(?:[\r\n]|$)', response):
                    raise SmsRejected('模组返回短信发送失败，请检查余额、漫游和短信服务')
                reference = re.search(rb'\+CMGS:\s*(\d+)', response)
                if reference and re.search(rb'(?:^|[\r\n])OK(?:[\r\n]|$)', response):
                    return int(reference[1])
            raise RuntimeError('短信已提交但未收到完整确认；请核对记录，系统不会自动重发')
        finally:
            if not submitted:
                self.transport.write(b'\x1b')  # Abort the input prompt without sending.


GSM = '@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ\x1bÆæßÉ !"#¤%&\'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà'
EXT = {10:'\f',20:'^',40:'{',41:'}',47:'\\',60:'[',61:'~',62:']',64:'|',101:'€'}


def gsm_decode(data, count, start=0):
    bits = int.from_bytes(data, 'little')
    result, escape = [], False
    for i in range(start, start+count):
        code = (bits >> (i*7)) & 127
        if escape:
            result.append(EXT.get(code, '?')); escape = False
        elif code == 27:
            escape = True
        else:
            result.append(GSM[code])
    return ''.join(result)


def decode_deliver(pdu):
    data = bytes.fromhex(pdu)
    pos = data[0] + 1
    first = data[pos]; pos += 1
    if first & 3 != 0:
        return None
    length, kind = data[pos:pos+2]; pos += 2
    address = data[pos:pos+(length+1)//2]; pos += (length+1)//2
    if kind & 0x70 == 0x50:
        number = gsm_decode(address, length*4//7)
    else:
        raw = address.hex().upper()
        number = ''.join(raw[i+1]+raw[i] for i in range(0, len(raw), 2))[:length]
        if kind & 0x70 == 0x10:
            number = '+' + number
    _, dcs = data[pos:pos+2]; pos += 2
    stamp = data[pos:pos+7]; pos += 7
    bcd = lambda b: (b & 15)*10 + (b >> 4)
    quarter_hours = bcd(stamp[6] & 0xf7)
    offset = timedelta(minutes=quarter_hours*15*(1 if not stamp[6] & 8 else -1))
    timestamp = datetime(2000+bcd(stamp[0]), *(bcd(b) for b in stamp[1:6]), tzinfo=timezone(offset)).isoformat()
    udl = data[pos]; pos += 1
    ud = data[pos:]
    header_size = ud[0]+1 if first & 0x40 else 0
    concat = None
    if header_size:
        end = 1
        while end < header_size:
            tag, size = ud[end:end+2]; end += 2
            value = ud[end:end+size]; end += size
            if tag == 0 and size == 3:
                concat = {'ref':value[0], 'total':value[1], 'part':value[2]}
            elif tag == 8 and size == 4:
                concat = {'ref':int.from_bytes(value[:2],'big'), 'total':value[2], 'part':value[3]}
    if dcs & 0x0c == 8:
        text = ud[header_size:udl].decode('utf-16-be', errors='replace')
    elif dcs & 0x0c == 4:
        text = ud[header_size:udl].decode('latin1', errors='replace')
    else:
        skip = math.ceil(header_size*8/7)
        text = gsm_decode(ud, udl-skip, skip)
    return {'number':number, 'timestamp':timestamp, 'text':text, 'concat':concat}
