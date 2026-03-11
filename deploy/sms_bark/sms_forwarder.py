#!/usr/bin/env python3
import base64
import json
import logging
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path


CONFIG_PATH = Path(os.environ.get("SMS_BARK_CONFIG", "/etc/sms-bark-forwarder.conf"))
STATE_PATH = Path(os.environ.get("SMS_BARK_STATE", "/var/lib/sms-bark-forwarder/state.json"))
POLL_INTERVAL = int(os.environ.get("SMS_BARK_POLL_INTERVAL", "15"))
LOG_LEVEL = os.environ.get("SMS_BARK_LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(message)s",
)
LOG = logging.getLogger("sms-bark-forwarder")


def load_env_file(path: Path) -> dict[str, str]:
    data: dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(f"config file not found: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip("\"'")
    return data


def run_mmcli(args: list[str]) -> str:
    cmd = ["mmcli", *args]
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return result.stdout


def parse_sms_paths(raw: str) -> list[str]:
    return re.findall(r"(/org/freedesktop/ModemManager1/SMS/\d+)", raw)


def parse_kv(raw: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key.strip()] = value.strip()
    return parsed


def ensure_state() -> dict[str, object]:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not STATE_PATH.exists():
        return {"seen_sms": []}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        LOG.warning("state file is corrupted, rebuilding: %s", STATE_PATH)
        return {"seen_sms": []}


def save_state(state: dict[str, object]) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def decode_mmcli_escaped_text(raw_text: str) -> str:
    if "\\" not in raw_text:
        return raw_text
    try:
        escaped = raw_text.encode("latin1", errors="backslashreplace").decode("unicode_escape")
        return escaped.encode("latin1").decode("utf-8")
    except Exception:
        return raw_text


def maybe_decode_base64(raw_text: str) -> str:
    compact = "".join(raw_text.split())
    if len(compact) < 16 or len(compact) % 4 != 0:
        return raw_text
    if not re.fullmatch(r"[A-Za-z0-9+/=]+", compact):
        return raw_text
    try:
        decoded = base64.b64decode(compact, validate=True)
        decoded_text = decoded.decode("utf-8")
    except Exception:
        return raw_text
    printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in decoded_text)
    if not decoded_text or printable / len(decoded_text) < 0.85:
        return raw_text
    return decoded_text


def normalize_sms_text(raw_text: str) -> str:
    text = decode_mmcli_escaped_text(raw_text)
    return maybe_decode_base64(text)


def format_beijing_timestamp(raw_timestamp: str) -> str:
    if not raw_timestamp:
        return "未知时间"
    try:
        dt = datetime.fromisoformat(raw_timestamp)
        beijing = timezone(timedelta(hours=8))
        return dt.astimezone(beijing).strftime("%Y年%m月%d日 %H时%M分%S秒")
    except Exception:
        return raw_timestamp


def bark_push(base_url: str, device_key: str, title: str, body: str, group: str, level: str, icon: str) -> None:
    encoded_title = urllib.parse.quote(title, safe="")
    encoded_body = urllib.parse.quote(body, safe="")
    url = f"{base_url.rstrip('/')}/{device_key}/{encoded_title}/{encoded_body}"
    payload = {
        "group": group,
        "level": level,
    }
    if icon:
        payload["icon"] = icon
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        response_body = resp.read().decode("utf-8", errors="replace")
        LOG.info("bark delivered: status=%s body=%s", resp.status, response_body)


def fetch_sms_detail(path: str) -> dict[str, str]:
    raw = run_mmcli(["-s", path, "-K"])
    kv = parse_kv(raw)
    text = kv.get("sms.content.text", "")
    data = kv.get("sms.content.data", "")
    return {
        "path": path,
        "state": kv.get("sms.properties.state", ""),
        "number": kv.get("sms.content.number", ""),
        "text": normalize_sms_text(text or data),
        "timestamp": kv.get("sms.properties.timestamp", ""),
        "storage": kv.get("sms.properties.storage", ""),
    }


def format_message(detail: dict[str, str]) -> tuple[str, str]:
    number = detail["number"] or "unknown"
    timestamp = format_beijing_timestamp(detail["timestamp"])
    state_map = {
        "received": "已接收",
        "receiving": "接收中",
        "sent": "已发送",
        "sending": "发送中",
        "stored": "已存储",
    }
    state = state_map.get(detail["state"], detail["state"] or "unknown")
    text = detail["text"] or "(empty)"
    title = f"收到短信：{number}"
    body = f"{text}\n\n时间：{timestamp}\n状态：{state}"
    return title, body


def main() -> int:
    config = load_env_file(CONFIG_PATH)
    modem_id = config.get("MODEM_ID", "any")
    bark_base_url = config.get("BARK_BASE_URL", "https://api.day.app")
    bark_device_key = config.get("BARK_DEVICE_KEY", "")
    bark_group = config.get("BARK_GROUP", "sms")
    bark_level = config.get("BARK_LEVEL", "active")
    bark_icon = config.get("BARK_ICON", "")
    forward_states = {s.strip() for s in config.get("FORWARD_SMS_STATES", "received").split(",") if s.strip()}

    if not bark_device_key:
        raise RuntimeError("BARK_DEVICE_KEY is empty in config")

    state = ensure_state()
    seen_sms = set(state.get("seen_sms", []))
    LOG.info("sms forwarder started, modem=%s poll_interval=%s", modem_id, POLL_INTERVAL)

    while True:
        try:
            sms_list_raw = run_mmcli(["-m", modem_id, "--messaging-list-sms"])
            sms_paths = parse_sms_paths(sms_list_raw)
            current_seen = set(seen_sms)
            changed = False

            for sms_path in sms_paths:
                if sms_path in current_seen:
                    continue
                detail = fetch_sms_detail(sms_path)
                seen_sms.add(sms_path)
                changed = True

                if detail["state"] not in forward_states:
                    LOG.info("skip sms %s with state=%s", sms_path, detail["state"])
                    continue

                title, body = format_message(detail)
                bark_push(
                    bark_base_url,
                    bark_device_key,
                    title,
                    body,
                    bark_group,
                    bark_level,
                    bark_icon,
                )

            if changed:
                state["seen_sms"] = sorted(seen_sms)
                save_state(state)
        except subprocess.CalledProcessError as exc:
            LOG.error("mmcli failed: %s", exc.stderr.strip() if exc.stderr else exc)
        except urllib.error.URLError as exc:
            LOG.error("bark push failed: %s", exc)
        except Exception as exc:
            LOG.exception("unexpected failure: %s", exc)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    raise SystemExit(main())
