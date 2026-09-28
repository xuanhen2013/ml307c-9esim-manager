"""User-supplied phone numbers keyed by ICCID, separate from SIM contents."""
import json
import os
from pathlib import Path
import re
import tempfile
import threading


class ProfileNumbers:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def all(self):
        with self.lock:
            if not self.path.exists():
                return {}
            values = json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(values, dict):
                raise ValueError('卡片号码配置格式不正确')
            return values

    def get(self, iccid):
        return self.all().get(str(iccid or ''), '')

    def save(self, iccid, number):
        if not isinstance(iccid, str) or not re.fullmatch(r'\d{18,22}', iccid):
            raise ValueError('ICCID 格式不正确')
        if not isinstance(number, str):
            raise ValueError('手机号需要使用 +国家区号 格式')
        number = re.sub(r'[\s()-]', '', number)
        if number and not re.fullmatch(r'\+[1-9]\d{6,14}', number):
            raise ValueError('手机号需要使用 +国家区号 格式')
        with self.lock:
            values = self.all()
            if number:
                values[iccid] = number
            else:
                values.pop(iccid, None)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                                 prefix='.profile-numbers-', delete=False) as out:
                    temporary = Path(out.name)
                    json.dump(values, out, ensure_ascii=False)
                    out.flush()
                    os.fsync(out.fileno())
                temporary.chmod(0o600)
                temporary.replace(self.path)
            finally:
                if temporary is not None and temporary.exists():
                    temporary.unlink()
        return number
