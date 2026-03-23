#!/usr/bin/env python3
from __future__ import annotations
from collections import deque
import json
import mimetypes
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import uuid
from base64 import b64decode
from datetime import datetime, timedelta, timezone
from glob import glob
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import unquote, urlparse


SCRIPT_DIR = Path(__file__).resolve().parent
for candidate in (SCRIPT_DIR, SCRIPT_DIR.parent / "shared"):
    if (candidate / "notification_utils.py").exists() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from notification_utils import (  # noqa: E402
    configured_channel_labels,
    configured_notification_targets,
    ensure_notification_config,
    format_channel_label,
    format_sms_notification,
    load_notification_targets,
    normalize_notification_target,
    save_notification_targets_in_config,
    send_apprise_notification,
)


HOST = os.environ.get("FOURG_WIFI_ADMIN_HOST", "0.0.0.0")
PORT = int(os.environ.get("FOURG_WIFI_ADMIN_PORT", "8080"))
NOTIFICATION_CONFIG_PATH = Path("/etc/sms-forwarder.conf")
SMS_FORWARDER_SERVICE = "sms-forwarder.service"
APP_CONFIG_PATH = Path("/etc/esim-sms-forwarder.conf")
STATIC_DIR = Path(
    os.environ.get("FOURG_WIFI_ADMIN_STATIC_DIR", str(Path(__file__).resolve().with_name("frontend_dist")))
)
BEIJING_TZ = timezone(timedelta(hours=8))
ACTION_RETENTION_SECONDS = 1800
ACTION_MAX_EVENTS = 400
KEEPALIVE_TASKS_KEY = "KEEPALIVE_TASKS_JSON"
KEEPALIVE_SETTINGS_KEY = "KEEPALIVE_SETTINGS_JSON"
KEEPALIVE_ACTION_NAME = "run_keepalive_task"
KEEPALIVE_SCHEDULER_INTERVAL_SECONDS = 15
KEEPALIVE_SCHEDULE_GRACE_SECONDS = 75
KEEPALIVE_SWITCH_SETTLE_SECONDS = 20
KEEPALIVE_NETWORK_WAIT_SECONDS = 120
KEEPALIVE_NETWORK_POLL_SECONDS = 10
KEEPALIVE_RETRY_INTERVAL_SECONDS = 30
KEEPALIVE_MAX_SEND_ATTEMPTS = 3
KEEPALIVE_HISTORY_LIMIT = 10
WEEKDAY_ORDER = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
WEEKDAY_LABELS = {
    "mon": "周一",
    "tue": "周二",
    "wed": "周三",
    "thu": "周四",
    "fri": "周五",
    "sat": "周六",
    "sun": "周日",
}
PROFILE_APN_DEFAULTS = {
    "giffgaff": {"apn": "giffgaff.com", "username": "giffgaff", "password": "password", "ip_type": "ipv4"},
    "t-mobile": {"apn": "fast.t-mobile.com", "username": "", "password": "", "ip_type": "ipv4v6"},
}
FALLBACK_INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>eSIM 管理页</title>
    <style>
      body {
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        background: #0f172a;
        color: #e2e8f0;
        font: 16px/1.6 system-ui, sans-serif;
      }
      main {
        max-width: 720px;
        padding: 32px;
        border-radius: 24px;
        background: rgba(15, 23, 42, 0.9);
        box-shadow: 0 20px 60px rgba(15, 23, 42, 0.35);
      }
      code {
        padding: 2px 8px;
        border-radius: 999px;
        background: rgba(148, 163, 184, 0.2);
      }
    </style>
  </head>
  <body>
    <main>
      <h1>前端静态文件还没部署</h1>
      <p>API 已正常启动，但 <code>frontend_dist</code> 目录里没有构建后的页面文件。</p>
      <p>请先在本地执行前端构建，再把构建产物同步到设备。</p>
    </main>
  </body>
</html>
"""

ACTIONS: dict[str, dict[str, Any]] = {}
ACTIONS_LOCK = threading.Lock()
ACTION_QUEUE: deque[str] = deque()
ACTION_QUEUE_CONDITION = threading.Condition()
PROFILE_CACHE: list[dict[str, Any]] = []
PROFILE_CACHE_ERROR = ""
PROFILE_CACHE_UPDATED_AT = 0.0
PROFILE_CACHE_LOCK = threading.Lock()
KEEPALIVE_RUNTIME_LOCK = threading.Lock()
KEEPALIVE_LAST_ENQUEUED: dict[str, str] = {}
KEEPALIVE_NEXT_ALLOWED_AT = 0.0


def run_command(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, check=check, capture_output=True, text=True)


def command_output_text(result: subprocess.CompletedProcess[str]) -> str:
    return (result.stdout or result.stderr or "").strip()


def format_command(args: list[str]) -> str:
    return shlex.join(args)


def parse_lpac_json(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    return data.get("payload", {})


def find_qmi_device_path() -> Optional[str]:
    candidates = [
        "/dev/wwan0qmi0",
        *sorted(glob("/dev/wwan*qmi*")),
        *sorted(glob("/dev/cdc-wdm*")),
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def wait_for_qmi_device(ctx: "ActionContext", timeout_seconds: int = 12) -> str:
    deadline = time.time() + timeout_seconds
    last_seen: Optional[str] = None
    while time.time() < deadline:
        device_path = find_qmi_device_path()
        if device_path:
            if device_path != last_seen:
                ctx.log(f"检测到 QMI 设备：{device_path}")
            return device_path
        last_seen = device_path
        time.sleep(0.5)
    raise RuntimeError("等待 QMI 设备节点超时，未找到 /dev/wwan*qmi* 或 /dev/cdc-wdm*")


def parse_mmcli_kv(raw: str) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parsed[key.strip()] = value.strip()
    return parsed


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
        decoded = b64decode(compact, validate=True)
        text = decoded.decode("utf-8")
    except Exception:
        return raw_text
    printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in text)
    return text if text and printable / len(text) >= 0.85 else raw_text


def normalize_sms_text(raw_text: str) -> str:
    return maybe_decode_base64(decode_mmcli_escaped_text(raw_text))


def format_beijing_timestamp(raw_timestamp: str) -> str:
    if not raw_timestamp:
        return "未知时间"
    try:
        normalized = raw_timestamp.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(BEIJING_TZ).strftime("%Y年%m月%d日 %H时%M分")
    except Exception:
        return raw_timestamp


def format_sms_state_label(state: str) -> str:
    return {
        "received": "已接收",
        "receiving": "接收中",
        "sent": "已发送",
        "sending": "发送中",
        "stored": "已存储",
    }.get(state, state or "未知")


def time_label_now() -> str:
    return datetime.now(BEIJING_TZ).strftime("%H:%M:%S")


def read_env_config(path: Path) -> dict[str, str]:
    config: dict[str, str] = {}
    if not path.exists():
        return config
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        config[key.strip()] = value.strip().strip("\"'")
    return config


def write_env_config(path: Path, config: dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in config.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def app_runtime_config() -> dict[str, str]:
    config = read_env_config(APP_CONFIG_PATH)
    if "SIM_TYPE" not in config and os.environ.get("SIM_TYPE"):
        config["SIM_TYPE"] = os.environ["SIM_TYPE"]
    if "ESIM_MANAGEMENT_ENABLED" not in config and os.environ.get("ESIM_MANAGEMENT_ENABLED"):
        config["ESIM_MANAGEMENT_ENABLED"] = os.environ["ESIM_MANAGEMENT_ENABLED"]
    return config


def esim_management_enabled() -> bool:
    config = app_runtime_config()
    raw = str(config.get("ESIM_MANAGEMENT_ENABLED", "")).strip().lower()
    if raw:
        return raw in {"1", "true", "yes", "enabled"}
    return str(config.get("SIM_TYPE", "esim")).strip().lower() != "physical"


def sim_type() -> str:
    return str(app_runtime_config().get("SIM_TYPE", "esim")).strip().lower() or "esim"


def parse_iso_datetime(raw_value: str) -> Optional[datetime]:
    if not raw_value:
        return None
    try:
        normalized = raw_value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=BEIJING_TZ)
        return parsed.astimezone(BEIJING_TZ)
    except Exception:
        return None


def format_runtime_timestamp(raw_timestamp: float) -> str:
    if raw_timestamp <= 0:
        return ""
    return datetime.fromtimestamp(raw_timestamp, tz=BEIJING_TZ).strftime("%Y年%m月%d日 %H时%M分%S秒")


def parse_signal_value(raw_value: str) -> int:
    try:
        return max(0, int(str(raw_value).strip()))
    except Exception:
        return 0


def normalize_weekdays(raw_value: Any) -> list[str]:
    if isinstance(raw_value, str):
        values = [item.strip().lower() for item in raw_value.split(",")]
    elif isinstance(raw_value, list):
        values = [str(item).strip().lower() for item in raw_value]
    else:
        values = []
    normalized: list[str] = []
    for value in values:
        if value in WEEKDAY_ORDER and value not in normalized:
            normalized.append(value)
    normalized.sort(key=WEEKDAY_ORDER.index)
    return normalized


def normalize_keepalive_settings(raw_value: Any) -> dict[str, int]:
    raw = raw_value if isinstance(raw_value, dict) else {}
    try:
        queue_gap_seconds = int(raw.get("queue_gap_seconds", 180))
    except Exception:
        queue_gap_seconds = 180
    queue_gap_seconds = max(30, min(queue_gap_seconds, 1800))
    return {"queue_gap_seconds": queue_gap_seconds}


def parse_keepalive_time(raw_value: Any) -> str:
    value = str(raw_value or "").strip()
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("保活时间格式必须是 HH:MM")
    return value


def parse_keepalive_task(raw_task: dict[str, Any]) -> dict[str, Any]:
    task_id = str(raw_task.get("id", "")).strip() or uuid.uuid4().hex[:12]
    label = str(raw_task.get("label", "")).strip()
    profile_iccid = str(raw_task.get("profile_iccid", "")).strip()
    target_number = str(raw_task.get("target_number", "")).strip()
    message = str(raw_task.get("message", "")).strip()
    enabled_raw = raw_task.get("enabled", True)
    if isinstance(enabled_raw, bool):
        enabled = enabled_raw
    else:
        enabled = str(enabled_raw).strip().lower() not in {"0", "false", "no", "off", ""}
    weekdays = normalize_weekdays(raw_task.get("days_of_week", []))
    if not label:
        raise ValueError("保活任务名称不能为空")
    if not profile_iccid:
        raise ValueError(f"保活任务 {label} 缺少 Profile")
    if not target_number:
        raise ValueError(f"保活任务 {label} 缺少目标手机号")
    if not message:
        raise ValueError(f"保活任务 {label} 缺少短信内容")
    if not weekdays:
        raise ValueError(f"保活任务 {label} 至少需要选择一个执行日")
    return {
        "id": task_id,
        "label": label,
        "enabled": enabled,
        "profile_iccid": profile_iccid,
        "target_number": target_number,
        "message": message,
        "time": parse_keepalive_time(raw_task.get("time", "")),
        "days_of_week": weekdays,
    }


def load_keepalive_config() -> tuple[dict[str, int], list[dict[str, Any]]]:
    config = read_env_config(APP_CONFIG_PATH)
    raw_settings = str(config.get(KEEPALIVE_SETTINGS_KEY, "")).strip()
    raw_tasks = str(config.get(KEEPALIVE_TASKS_KEY, "")).strip()

    try:
        parsed_settings = json.loads(raw_settings) if raw_settings else {}
    except json.JSONDecodeError:
        parsed_settings = {}
    settings = normalize_keepalive_settings(parsed_settings)

    try:
        parsed_tasks = json.loads(raw_tasks) if raw_tasks else []
    except json.JSONDecodeError:
        parsed_tasks = []
    if not isinstance(parsed_tasks, list):
        parsed_tasks = []

    tasks: list[dict[str, Any]] = []
    for item in parsed_tasks:
        if not isinstance(item, dict):
            continue
        tasks.append(parse_keepalive_task(item))
    return settings, tasks


def save_keepalive_config(settings: dict[str, Any], tasks: list[dict[str, Any]]) -> tuple[dict[str, int], list[dict[str, Any]]]:
    normalized_settings = normalize_keepalive_settings(settings)
    normalized_tasks = [parse_keepalive_task(task) for task in tasks]
    config = read_env_config(APP_CONFIG_PATH)
    config[KEEPALIVE_SETTINGS_KEY] = json.dumps(normalized_settings, ensure_ascii=False, separators=(",", ":"))
    config[KEEPALIVE_TASKS_KEY] = json.dumps(normalized_tasks, ensure_ascii=False, separators=(",", ":"))
    write_env_config(APP_CONFIG_PATH, config)
    active_task_ids = {task["id"] for task in normalized_tasks}
    with KEEPALIVE_RUNTIME_LOCK:
        stale_ids = [task_id for task_id in KEEPALIVE_LAST_ENQUEUED if task_id not in active_task_ids]
        for task_id in stale_ids:
            KEEPALIVE_LAST_ENQUEUED.pop(task_id, None)
    return normalized_settings, normalized_tasks


def keepalive_time_parts(task: dict[str, Any]) -> tuple[int, int]:
    hour_text, minute_text = str(task.get("time", "00:00")).split(":", 1)
    return int(hour_text), int(minute_text)


def keepalive_days_label(days_of_week: list[str]) -> str:
    labels = [WEEKDAY_LABELS[day] for day in days_of_week if day in WEEKDAY_LABELS]
    return "、".join(labels)


def next_keepalive_run(task: dict[str, Any], now: Optional[datetime] = None) -> Optional[datetime]:
    current = (now or datetime.now(BEIJING_TZ)).astimezone(BEIJING_TZ)
    hour, minute = keepalive_time_parts(task)
    for day_offset in range(0, 8):
        candidate_date = current + timedelta(days=day_offset)
        weekday = WEEKDAY_ORDER[candidate_date.weekday()]
        if weekday not in task.get("days_of_week", []):
            continue
        candidate = candidate_date.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= current:
            continue
        return candidate
    return None


def due_keepalive_run(task: dict[str, Any], now: Optional[datetime] = None) -> Optional[datetime]:
    current = (now or datetime.now(BEIJING_TZ)).astimezone(BEIJING_TZ)
    weekday = WEEKDAY_ORDER[current.weekday()]
    if weekday not in task.get("days_of_week", []):
        return None
    hour, minute = keepalive_time_parts(task)
    scheduled = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delta_seconds = (current - scheduled).total_seconds()
    if 0 <= delta_seconds <= KEEPALIVE_SCHEDULE_GRACE_SECONDS:
        return scheduled
    return None


def keepalive_schedule_key(scheduled_at: datetime) -> str:
    return scheduled_at.astimezone(BEIJING_TZ).strftime("%Y%m%d%H%M")


def active_profile_from_list(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    return next((profile for profile in profiles if profile_is_active(profile)), {})


def profile_name_for_iccid(iccid: str, profiles: list[dict[str, Any]]) -> str:
    for profile in profiles:
        if str(profile.get("iccid", "")).strip() == iccid:
            return str(profile.get("display_name") or profile_display_name(profile)).strip()
    return f"Profile {iccid[-6:]}" if len(iccid) >= 6 else iccid or "未知 Profile"


def describe_keepalive_record(record: dict[str, Any]) -> dict[str, Any]:
    metadata = record.get("metadata", {})
    scheduled_for = str(metadata.get("scheduled_for", "")).strip()
    created_at = float(record.get("created_at", 0))
    updated_at = float(record.get("updated_at", 0))
    last_message = ""
    events = record.get("events", [])
    if isinstance(events, list) and events:
        last_message = str(events[-1].get("message", "")).strip()
    return {
        "id": record.get("id", ""),
        "task_id": metadata.get("task_id", ""),
        "label": metadata.get("label", "") or "保活任务",
        "trigger": metadata.get("trigger", "manual"),
        "scheduled_for": scheduled_for,
        "scheduled_for_label": format_beijing_timestamp(scheduled_for) if scheduled_for else "",
        "profile_iccid": metadata.get("profile_iccid", ""),
        "profile_name": metadata.get("profile_name", ""),
        "target_number": metadata.get("target_number", ""),
        "state": record.get("state", ""),
        "error": record.get("error", ""),
        "last_message": last_message,
        "created_at": format_runtime_timestamp(created_at),
        "updated_at": format_runtime_timestamp(updated_at),
    }


def keepalive_status_snapshot(profiles: list[dict[str, Any]]) -> dict[str, Any]:
    now = datetime.now(BEIJING_TZ)
    settings, tasks = load_keepalive_config()
    profile_map = {str(profile.get("iccid", "")).strip(): profile for profile in profiles}

    task_views: list[dict[str, Any]] = []
    for task in tasks:
        next_run = next_keepalive_run(task, now)
        profile = profile_map.get(task["profile_iccid"], {})
        task_views.append(
            {
                **task,
                "profile_name": (
                    str(profile.get("display_name", "")).strip()
                    if profile
                    else profile_name_for_iccid(task["profile_iccid"], profiles)
                ),
                "days_label": keepalive_days_label(task["days_of_week"]),
                "next_run": next_run.isoformat() if next_run else "",
                "next_run_label": format_beijing_timestamp(next_run.isoformat()) if next_run else "",
            }
        )

    with ACTIONS_LOCK:
        keepalive_records = [
            record
            for record in ACTIONS.values()
            if record.get("action") == KEEPALIVE_ACTION_NAME and record.get("metadata", {}).get("kind") == "keepalive"
        ]

    queue_items: list[dict[str, Any]] = []
    active_run: Optional[dict[str, Any]] = None
    history: list[dict[str, Any]] = []
    for record in sorted(keepalive_records, key=lambda item: float(item.get("created_at", 0)), reverse=True):
        description = describe_keepalive_record(record)
        state = str(record.get("state", "")).strip()
        if state == "running" and active_run is None:
            active_run = description
        elif state == "queued":
            queue_items.append(description)
        elif state in {"done", "error"} and len(history) < KEEPALIVE_HISTORY_LIMIT:
            history.append(description)

    with KEEPALIVE_RUNTIME_LOCK:
        next_allowed_at = KEEPALIVE_NEXT_ALLOWED_AT

    return {
        "settings": settings,
        "tasks": task_views,
        "active_run": active_run,
        "queued_runs": sorted(queue_items, key=lambda item: item.get("scheduled_for", "")),
        "recent_runs": history,
        "next_allowed_at": format_runtime_timestamp(next_allowed_at),
    }


def keepalive_queue_delay_seconds() -> int:
    settings, _ = load_keepalive_config()
    return int(settings["queue_gap_seconds"])


def schedule_keepalive_gap(seconds: Optional[int] = None) -> None:
    gap_seconds = seconds if seconds is not None else keepalive_queue_delay_seconds()
    with KEEPALIVE_RUNTIME_LOCK:
        global KEEPALIVE_NEXT_ALLOWED_AT
        KEEPALIVE_NEXT_ALLOWED_AT = max(KEEPALIVE_NEXT_ALLOWED_AT, time.time() + max(0, gap_seconds))


def wait_for_keepalive_gap(ctx: "ActionContext", gap_seconds: Optional[int] = None) -> None:
    expected_gap = gap_seconds if gap_seconds is not None else keepalive_queue_delay_seconds()
    while True:
        with KEEPALIVE_RUNTIME_LOCK:
            remaining = KEEPALIVE_NEXT_ALLOWED_AT - time.time()
        if remaining <= 0:
            return
        ctx.sleep(max(1, int(remaining) + 1), f"等待切卡缓冲时间，避免频繁切卡（配置 {expected_gap} 秒）")


def profile_is_active(profile: dict[str, Any]) -> bool:
    for key in ("enabled", "active", "is_enabled", "is_active"):
        value = profile.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in {"1", "true", "yes", "enabled", "active"}:
            return True
    return (
        str(profile.get("state", "")).lower() in {"enabled", "active"}
        or str(profile.get("profileState", "")).lower() in {"enabled", "active"}
    )


def profile_display_name(profile: dict[str, Any]) -> str:
    for key in (
        "profileNickname",
        "nickname",
        "serviceProviderName",
        "profileName",
        "name",
        "profile_name",
        "provider",
        "carrier",
        "operator",
    ):
        raw_value = profile.get(key, "")
        if raw_value is None:
            continue
        value = str(raw_value).strip()
        if value:
            return value
    iccid = str(profile.get("iccid", "")).strip()
    return f"Profile {iccid[-6:]}" if iccid else "未知 Profile"


def enrich_profile(profile: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(profile)
    enriched["display_name"] = profile_display_name(profile)
    enriched["is_active"] = profile_is_active(profile)
    enriched["provider_name"] = str(
        profile.get("serviceProviderName")
        or profile.get("provider")
        or profile.get("carrier")
        or profile.get("operator")
        or profile.get("profileName")
        or ""
    ).strip()
    enriched["iccid_short"] = str(profile.get("iccid", ""))[-6:]
    return enriched


def get_profiles() -> list[dict[str, Any]]:
    result = run_command(["/usr/local/bin/lpac-switch", "list"])
    payload = parse_lpac_json(result.stdout)
    if payload.get("code") != 0:
        raise RuntimeError(payload.get("message", "读取 eSIM 列表失败"))
    profiles = payload.get("data", [])
    return [enrich_profile(profile) for profile in profiles]


def refresh_profile_cache(force: bool = False) -> list[dict[str, Any]]:
    global PROFILE_CACHE, PROFILE_CACHE_ERROR, PROFILE_CACHE_UPDATED_AT
    with PROFILE_CACHE_LOCK:
        if PROFILE_CACHE and not force:
            return list(PROFILE_CACHE)
        profiles = get_profiles()
        PROFILE_CACHE = profiles
        PROFILE_CACHE_ERROR = ""
        PROFILE_CACHE_UPDATED_AT = time.time()
        return list(PROFILE_CACHE)


def get_cached_profiles() -> tuple[list[dict[str, Any]], Optional[str]]:
    global PROFILE_CACHE_ERROR
    with PROFILE_CACHE_LOCK:
        if PROFILE_CACHE:
            return list(PROFILE_CACHE), None
        if PROFILE_CACHE_ERROR:
            return [], PROFILE_CACHE_ERROR
    try:
        return refresh_profile_cache(force=True), None
    except Exception as exc:
        with PROFILE_CACHE_LOCK:
            PROFILE_CACHE_ERROR = str(exc)
        return [], str(exc)


def get_profile_by_iccid(iccid: str) -> dict[str, Any]:
    profiles, _ = get_cached_profiles()
    return next((profile for profile in profiles if str(profile.get("iccid")) == iccid), {})


def get_modem_info() -> tuple[dict[str, str], Optional[str]]:
    result = run_command(["mmcli", "-m", "any", "-K"], check=False)
    if result.returncode != 0:
        error = command_output_text(result) or "无法读取基带状态"
        return {}, error
    return parse_mmcli_kv(result.stdout), None


def list_sms() -> tuple[list[dict[str, str]], Optional[str]]:
    result = run_command(["mmcli", "-m", "any", "--messaging-list-sms"], check=False)
    if result.returncode != 0:
        error = command_output_text(result) or "无法读取短信列表"
        return [], error

    paths = re.findall(r"(/org/freedesktop/ModemManager1/SMS/\d+)", result.stdout)
    messages: list[dict[str, str]] = []
    for path in paths:
        detail = run_command(["mmcli", "-s", path, "-K"], check=False)
        if detail.returncode != 0:
            continue
        kv = parse_mmcli_kv(detail.stdout)
        state = kv.get("sms.properties.state", "")
        sms_id_match = re.search(r"/SMS/(\d+)$", path)
        messages.append(
            {
                "id": sms_id_match.group(1) if sms_id_match else "",
                "number": kv.get("sms.content.number", ""),
                "text": normalize_sms_text(kv.get("sms.content.text", "") or kv.get("sms.content.data", "")),
                "timestamp": format_beijing_timestamp(kv.get("sms.properties.timestamp", "")),
                "state": state,
                "state_label": {
                    "received": "已接收",
                    "receiving": "接收中",
                    "sent": "已发送",
                    "sending": "发送中",
                    "stored": "已存储",
                }.get(state, state or "未知"),
            }
        )

    messages.sort(key=lambda item: int(item["id"] or "0"), reverse=True)
    return messages, None


def parse_sms_paths(raw: str) -> list[str]:
    return re.findall(r"(/org/freedesktop/ModemManager1/SMS/\d+)", raw)


def get_latest_sms_detail() -> dict[str, str]:
    result = run_command(["mmcli", "-m", "any", "--messaging-list-sms"], check=False)
    if result.returncode != 0:
        raise RuntimeError(command_output_text(result) or "无法读取短信列表")

    sms_paths = parse_sms_paths(result.stdout)
    if not sms_paths:
        raise RuntimeError("当前没有可重发的短信")

    latest_path = max(
        sms_paths,
        key=lambda path: int(re.search(r"/SMS/(\d+)$", path).group(1)) if re.search(r"/SMS/(\d+)$", path) else -1,
    )
    detail = run_command(["mmcli", "-s", latest_path, "-K"], check=False)
    if detail.returncode != 0:
        raise RuntimeError(command_output_text(detail) or "无法读取最后一条短信详情")

    kv = parse_mmcli_kv(detail.stdout)
    return {
        "path": latest_path,
        "state": kv.get("sms.properties.state", ""),
        "number": kv.get("sms.content.number", ""),
        "text": normalize_sms_text(kv.get("sms.content.text", "") or kv.get("sms.content.data", "")),
        "timestamp": format_beijing_timestamp(kv.get("sms.properties.timestamp", "")),
    }


def service_state(name: str) -> str:
    result = run_command(["systemctl", "is-active", name], check=False)
    return command_output_text(result) or "unknown"


def get_connection_info() -> dict[str, str]:
    result = run_command(["nmcli", "connection", "show", "modem"], check=False)
    return parse_mmcli_kv(result.stdout) if result.returncode == 0 else {}


def infer_apn_defaults_from_connection(apn: str, username: str = "") -> Optional[dict[str, str]]:
    for value in PROFILE_APN_DEFAULTS.values():
        if value["apn"] == apn and (not value["username"] or value["username"] == username):
            return value
    return None


def modem_network_ready(modem: dict[str, str]) -> bool:
    registration = str(modem.get("modem.3gpp.registration-state", "")).strip().lower()
    state = str(modem.get("modem.generic.state", "")).strip().lower()
    signal = parse_signal_value(modem.get("modem.generic.signal-quality.value", "0"))
    return signal > 0 and (
        registration in {"home", "roaming", "registered"}
        or state in {"registered", "connected"}
    )


def wait_for_modem_network_ready(
    ctx: ActionContext,
    *,
    timeout_seconds: int = KEEPALIVE_NETWORK_WAIT_SECONDS,
    poll_seconds: int = KEEPALIVE_NETWORK_POLL_SECONDS,
) -> tuple[bool, str]:
    deadline = time.time() + timeout_seconds
    last_state = ""
    while time.time() < deadline:
        modem, modem_error = get_modem_info()
        if modem_error:
            current_state = f"error:{modem_error}"
            if current_state != last_state:
                ctx.log(f"等待网络注册：{modem_error}", "warning")
                last_state = current_state
            time.sleep(poll_seconds)
            continue

        operator_name = modem.get("modem.3gpp.operator-name", "--")
        registration = modem.get("modem.3gpp.registration-state", "--")
        signal = parse_signal_value(modem.get("modem.generic.signal-quality.value", "0"))
        current_state = f"{operator_name}|{registration}|{signal}"
        if modem_network_ready(modem):
            ctx.log(f"网络已可用：{operator_name} / {registration} / 信号 {signal}%")
            return True, ""
        if current_state != last_state:
            ctx.log(f"等待网络注册：{operator_name} / {registration} / 信号 {signal}%")
            last_state = current_state
        time.sleep(poll_seconds)
    return False, f"等待网络可用超时，已等待 {timeout_seconds} 秒"


def escape_mmcli_sms_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def create_sms(ctx: ActionContext, number: str, text: str) -> str:
    request_arg = (
        "--messaging-create-sms="
        f"number={escape_mmcli_sms_value(number)},text={escape_mmcli_sms_value(text)}"
    )
    result = run_logged_command(
        ctx,
        ["mmcli", "-m", "any", request_arg],
        failure_prefix="创建短信对象失败：",
    )
    match = re.search(r"(/org/freedesktop/ModemManager1/SMS/\d+)", result.stdout or result.stderr or "")
    if not match:
        raise RuntimeError("创建短信对象失败：未返回短信路径")
    sms_path = match.group(1)
    ctx.log(f"短信对象已创建：{sms_path}")
    return sms_path


def delete_sms(ctx: ActionContext, sms_path: str) -> None:
    if not sms_path:
        return
    run_logged_command(
        ctx,
        ["mmcli", "-m", "any", f"--messaging-delete-sms={sms_path}"],
        check=False,
    )


def send_keepalive_sms(ctx: ActionContext, number: str, text: str) -> None:
    sms_path = create_sms(ctx, number, text)
    try:
        run_logged_command(
            ctx,
            ["mmcli", "-s", sms_path, "--send"],
            failure_prefix="发送保活短信失败：",
            success_message="保活短信已发送",
        )
    finally:
        delete_sms(ctx, sms_path)


def keepalive_notification_payload(
    task: dict[str, Any],
    *,
    profile_name: str,
    trigger: str,
    scheduled_for: str,
    success: bool,
    attempts: int,
    detail: str,
    original_profile_name: str,
) -> tuple[str, str]:
    title = f"{'保活成功' if success else '保活失败'}：{task['label']}"
    lines = [
        f"任务：{task['label']}",
        f"触发方式：{'定时' if trigger == 'schedule' else '手动'}",
        f"目标 Profile：{profile_name}",
        f"目标号码：{task['target_number']}",
        f"执行时间：{format_beijing_timestamp(scheduled_for) if scheduled_for else format_beijing_timestamp(datetime.now(timezone.utc).isoformat())}",
        f"尝试次数：{attempts}",
        f"结果：{'成功' if success else '失败'}",
        f"原始 Profile：{original_profile_name or '未知'}",
    ]
    if detail:
        lines.append(f"详情：{detail}")
    return title, "\n".join(lines)


def notify_keepalive_result(
    ctx: ActionContext,
    task: dict[str, Any],
    *,
    profile_name: str,
    trigger: str,
    scheduled_for: str,
    success: bool,
    attempts: int,
    detail: str,
    original_profile_name: str,
) -> None:
    config = read_env_config(NOTIFICATION_CONFIG_PATH)
    targets = load_notification_targets(config)
    labels = configured_channel_labels(targets)
    if not labels:
        ctx.log("未配置任何启用的通知渠道，已跳过保活结果通知", "warning")
        return
    title, body = keepalive_notification_payload(
        task,
        profile_name=profile_name,
        trigger=trigger,
        scheduled_for=scheduled_for,
        success=success,
        attempts=attempts,
        detail=detail,
        original_profile_name=original_profile_name,
    )
    try:
        ctx.log(f"准备发送保活结果通知：{'、'.join(labels)}")
        delivered_labels = send_apprise_notification(targets, title, body)
        ctx.log(f"保活结果通知已发送到：{'、'.join(delivered_labels)}")
    except Exception as exc:
        ctx.log(f"保活结果通知发送失败：{exc}", "warning")


def get_status(refresh_profiles: bool = False) -> dict[str, Any]:
    status_message = ""
    errors: list[str] = []
    notification_config = read_env_config(NOTIFICATION_CONFIG_PATH)
    notification_targets = load_notification_targets(notification_config)
    configured_targets = configured_notification_targets(notification_targets)
    esim_enabled = esim_management_enabled()
    current_sim_type = sim_type()
    connection = get_connection_info()
    connection_defaults = infer_apn_defaults_from_connection(
        "" if connection.get("gsm.apn", "") == "--" else connection.get("gsm.apn", ""),
        "" if connection.get("gsm.username", "") == "--" else connection.get("gsm.username", ""),
    )

    if esim_enabled:
        try:
            profiles = refresh_profile_cache(force=True) if refresh_profiles else get_cached_profiles()[0]
            if not profiles and not refresh_profiles:
                cached_profiles, cache_error = get_cached_profiles()
                profiles = cached_profiles
                if cache_error:
                    errors.append(f"读取 eSIM 列表失败：{cache_error}")
        except Exception as exc:
            profiles = []
            errors.append(f"读取 eSIM 列表失败：{exc}")
    else:
        profiles = []

    modem, modem_error = get_modem_info()
    if modem_error:
        status_message = "基带当前离线或正在重连，稍等片刻后再刷新。"
        errors.append(modem_error)

    sms_messages, sms_error = list_sms()
    if sms_error:
        if not status_message:
            status_message = "暂时拿不到短信列表，可能是基带还在重新注册。"
        errors.append(sms_error)

    try:
        keepalive = keepalive_status_snapshot(profiles)
    except Exception as exc:
        keepalive = {
            "settings": normalize_keepalive_settings({}),
            "tasks": [],
            "active_run": None,
            "queued_runs": [],
            "recent_runs": [],
            "next_allowed_at": "",
        }
        errors.append(f"读取保活配置失败：{exc}")

    return {
        "profiles": profiles,
        "capabilities": {
            "sim_type": current_sim_type,
            "esim_management_enabled": esim_enabled,
            "lpac_installed": os.path.exists("/opt/lpac/bin/lpac"),
        },
        "modem_available": not modem_error,
        "status_message": status_message,
        "errors": errors,
        "modem": {
            "number": modem.get("modem.generic.own-numbers.value[1]", "--"),
            "operator_code": modem.get("modem.3gpp.operator-code", "--"),
            "operator_name": modem.get("modem.3gpp.operator-name", "--"),
            "registration": modem.get("modem.3gpp.registration-state", "--"),
            "state": modem.get("modem.generic.state", "--"),
            "signal": modem.get("modem.generic.signal-quality.value", "--"),
            "access_tech": modem.get("modem.generic.access-technologies.value[1]", "--"),
            "current_modes": modem.get("modem.generic.current-modes", "--"),
            "apn": modem.get("modem.3gpp.eps.initial-bearer.settings.apn", "--"),
            "ip_type": modem.get("modem.3gpp.eps.initial-bearer.settings.ip-type", "--"),
        },
        "connection": {
            "apn": "" if connection.get("gsm.apn", "") == "--" else connection.get("gsm.apn", ""),
            "username": "" if connection.get("gsm.username", "") == "--" else connection.get("gsm.username", ""),
            "password": (
                ""
                if connection.get("gsm.password", "") in {"--", "<hidden>"}
                else connection.get("gsm.password", "")
            ),
            "ip_type": connection_defaults["ip_type"] if connection_defaults else "",
            "network_id": "" if connection.get("gsm.network-id", "") == "--" else connection.get("gsm.network-id", ""),
        },
        "services": {
            "modemmanager": service_state("ModemManager"),
            "sms_forwarder": service_state(SMS_FORWARDER_SERVICE),
            "web_admin": service_state("4g-wifi-admin.service"),
        },
        "notifications": {
            "configured_count": len(configured_targets),
            "configured_labels": configured_channel_labels(configured_targets),
            "targets": notification_targets,
        },
        "keepalive": keepalive,
        "sms": sms_messages,
        "timestamp": format_beijing_timestamp(datetime.now(timezone.utc).isoformat()),
    }


def cleanup_actions() -> None:
    cutoff = time.time() - ACTION_RETENTION_SECONDS
    with ACTIONS_LOCK:
        stale_ids = [
            action_id
            for action_id, record in ACTIONS.items()
            if record["updated_at"] < cutoff and record["state"] in {"done", "error"}
        ]
        for action_id in stale_ids:
            ACTIONS.pop(action_id, None)


def append_action_event(action_id: str, level: str, message: str) -> None:
    with ACTIONS_LOCK:
        record = ACTIONS.get(action_id)
        if not record:
            return
        record["events"].append({"time": time_label_now(), "level": level, "message": message})
        if len(record["events"]) > ACTION_MAX_EVENTS:
            record["events"] = record["events"][-ACTION_MAX_EVENTS:]
        record["updated_at"] = time.time()


def set_action_state(action_id: str, state: str, **extra: Any) -> None:
    with ACTIONS_LOCK:
        record = ACTIONS.get(action_id)
        if not record:
            return
        record["state"] = state
        record["updated_at"] = time.time()
        for key, value in extra.items():
            record[key] = value


class ActionContext:
    def __init__(self, action_id: str):
        self.action_id = action_id
        self.messages: list[str] = []

    def log(self, message: str, level: str = "info") -> None:
        self.messages.append(message)
        append_action_event(self.action_id, level, message)

    def command(self, args: list[str]) -> None:
        self.log(f"$ {format_command(args)}", "command")

    def sleep(self, seconds: int, reason: str) -> None:
        self.log(f"{reason}（等待 {seconds} 秒）")
        time.sleep(seconds)

    def summary(self) -> str:
        return "\n".join(self.messages)


def run_logged_command(
    ctx: ActionContext,
    args: list[str],
    *,
    check: bool = True,
    success_message: str = "",
    failure_prefix: str = "",
) -> subprocess.CompletedProcess[str]:
    ctx.command(args)
    result = run_command(args, check=False)
    output = command_output_text(result)
    if output:
        for line in output.splitlines():
            ctx.log(line)
    if result.returncode != 0 and check:
        raise RuntimeError(f"{failure_prefix}{output or '命令执行失败'}")
    if success_message:
        ctx.log(success_message)
    return result


def recover_modem(ctx: ActionContext) -> None:
    ctx.log("开始恢复基带")
    qmi_device: Optional[str] = None
    try:
        run_logged_command(ctx, ["systemctl", "stop", "ModemManager"], success_message="ModemManager 已停止")
        ctx.sleep(3, "等待 ModemManager 完全退出")
        qmi_device = wait_for_qmi_device(ctx)
        run_logged_command(
            ctx,
            ["qmicli", "-d", qmi_device, "--uim-sim-power-off=1"],
            success_message="已下发 SIM 断电",
        )
        ctx.sleep(3, "等待 SIM 断电完成")
        qmi_device = wait_for_qmi_device(ctx)
        run_logged_command(
            ctx,
            ["qmicli", "-d", qmi_device, "--uim-sim-power-on=1"],
            success_message="已下发 SIM 上电",
        )
        ctx.sleep(3, "等待 SIM 重新上电")
    finally:
        start_result = run_logged_command(
            ctx,
            ["systemctl", "start", "ModemManager"],
            check=False,
            success_message="ModemManager 已启动",
        )
        if start_result.returncode != 0:
            ctx.log("ModemManager 启动失败，后续状态读取可能继续失败", "warning")

        ctx.sleep(10, "等待基带重新枚举")
        run_logged_command(
            ctx,
            ["systemctl", "restart", SMS_FORWARDER_SERVICE],
            check=False,
            success_message="短信转发服务已尝试重启",
        )

    modem, modem_error = get_modem_info()
    if modem_error:
        ctx.log(f"当前还无法读取基带状态：{modem_error}", "warning")
    else:
        ctx.log(
            "当前注册状态："
            f"{modem.get('modem.3gpp.operator-name', '--')} / "
            f"{modem.get('modem.3gpp.operator-code', '--')} / "
            f"{modem.get('modem.3gpp.registration-state', '--')}"
        )


def apply_apn_settings(ctx: ActionContext, payload: dict[str, Any]) -> None:
    apn = str(payload.get("apn", "")).strip()
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", "")).strip()
    ip_type = str(payload.get("ip_type", "ipv4v6")).strip() or "ipv4v6"

    settings_parts = [f"ip-type={ip_type}"]
    if apn:
        settings_parts.insert(0, f"apn={apn}")
    if username:
        settings_parts.append(f"user={username}")
    if password:
        settings_parts.append(f"password={password}")

    ctx.log("开始保存 APN 配置")
    mm = run_logged_command(
        ctx,
        ["mmcli", "-m", "any", f"--3gpp-set-initial-eps-bearer-settings={','.join(settings_parts)}"],
        check=False,
    )
    if mm.returncode == 0:
        ctx.log("ModemManager 初始 EPS bearer 已更新")
    else:
        ctx.log("ModemManager 未接受在线 EPS bearer 修改，后续以 NetworkManager 配置为准", "warning")

    run_logged_command(
        ctx,
        [
            "nmcli",
            "connection",
            "modify",
            "modem",
            "gsm.apn",
            apn,
            "gsm.username",
            username,
            "gsm.password",
            password,
            "gsm.auto-config",
            "no",
            "ipv4.method",
            "auto",
            "ipv6.method",
            "auto",
        ],
        success_message="NetworkManager 的 modem 连接已更新",
    )


def switch_profile(ctx: ActionContext, payload: dict[str, Any], *, schedule_gap_after: bool = True) -> None:
    if not esim_management_enabled():
        raise RuntimeError("当前为普通 SIM 模式，eSIM 管理功能已禁用")

    iccid = str(payload.get("iccid", "")).strip()
    if not iccid:
        raise ValueError("缺少 ICCID")

    profile_name = f"Profile {iccid[-6:]}" if len(iccid) >= 6 else iccid
    try:
        profile = get_profile_by_iccid(iccid)
        profile_name = profile.get("display_name", profile_name)
    except Exception as exc:
        ctx.log(f"预读取 Profile 列表失败，改为直接按 ICCID 切换：{exc}", "warning")

    ctx.log(f"准备切换到 {profile_name}")
    result = run_logged_command(
        ctx,
        ["/usr/local/bin/lpac-switch", "enable", iccid],
        check=False,
    )
    payload_json = parse_lpac_json(result.stdout) if result.stdout else {"code": -1, "message": command_output_text(result)}
    if payload_json.get("code") != 0:
        raise RuntimeError(payload_json.get("message", "切换 eSIM 失败"))
    if payload_json.get("message"):
        ctx.log(str(payload_json["message"]))
    ctx.log("切卡命令已下发，继续恢复基带")
    recover_modem(ctx)
    try:
        refresh_profile_cache(force=True)
        ctx.log("eSIM Profiles 缓存已更新")
    except Exception as exc:
        ctx.log(f"刷新 eSIM Profiles 缓存失败：{exc}", "warning")
    if schedule_gap_after:
        schedule_keepalive_gap()
    ctx.log(f"{profile_name} 切换完成")


def save_notifications_config(ctx: ActionContext, payload: dict[str, Any]) -> None:
    raw_targets = payload.get("targets", [])
    if not isinstance(raw_targets, list):
        raise ValueError("通知渠道配置格式不正确")

    sanitized_targets: list[dict[str, Any]] = []
    for raw_target in raw_targets:
        if not isinstance(raw_target, dict):
            continue
        label = str(raw_target.get("label", "")).strip()
        url = str(raw_target.get("url", "")).strip()
        enabled_raw = raw_target.get("enabled", True)
        if isinstance(enabled_raw, bool):
            enabled = enabled_raw
        else:
            enabled = str(enabled_raw).strip().lower() not in {"0", "false", "no", "off", ""}
        if not label and not url:
            continue
        if enabled and not url:
            raise ValueError("启用中的通知渠道必须填写 Apprise URL")
        sanitized_targets.append(normalize_notification_target(raw_target))

    if not sanitized_targets:
        raise ValueError("请至少保留一个通知渠道")
    if not configured_channel_labels(sanitized_targets):
        raise ValueError("请至少启用一个通知渠道")

    config = ensure_notification_config(read_env_config(NOTIFICATION_CONFIG_PATH))
    save_notification_targets_in_config(config, sanitized_targets)
    write_env_config(NOTIFICATION_CONFIG_PATH, config)
    ctx.log(f"通知渠道配置已写入：{'、'.join(configured_channel_labels(sanitized_targets))}")
    run_logged_command(
        ctx,
        ["systemctl", "restart", SMS_FORWARDER_SERVICE],
        check=False,
        success_message="短信转发服务已重启",
    )


def save_keepalive_settings(ctx: ActionContext, payload: dict[str, Any]) -> None:
    raw_settings = payload.get("settings", {})
    raw_tasks = payload.get("tasks", [])
    if raw_settings is None:
        raw_settings = {}
    if not isinstance(raw_settings, dict):
        raise ValueError("保活设置格式不正确")
    if not isinstance(raw_tasks, list):
        raise ValueError("保活任务格式不正确")

    normalized_settings, normalized_tasks = save_keepalive_config(raw_settings, raw_tasks)
    enabled_count = sum(1 for task in normalized_tasks if task["enabled"])
    ctx.log(
        f"保活设置已写入：{len(normalized_tasks)} 条任务，"
        f"启用 {enabled_count} 条，切卡缓冲 {normalized_settings['queue_gap_seconds']} 秒"
    )


def restart_sms_service(ctx: ActionContext) -> None:
    run_logged_command(
        ctx,
        ["systemctl", "restart", SMS_FORWARDER_SERVICE],
        success_message="短信转发服务已重启",
    )


def resend_last_sms(ctx: ActionContext) -> None:
    ctx.log("开始读取最后一条短信")
    detail = get_latest_sms_detail()
    ctx.log(f"短信来源：{detail.get('number') or 'unknown'}")
    ctx.log(f"短信时间：{detail.get('timestamp') or '未知时间'}")
    for line in (detail.get("text") or "(empty)").splitlines():
        ctx.log(line)

    config = read_env_config(NOTIFICATION_CONFIG_PATH)
    targets = load_notification_targets(config)
    labels = configured_channel_labels(targets)
    if not labels:
        raise RuntimeError("未配置任何启用的通知渠道，无法重发最后一条短信")

    title, body = format_sms_notification(detail)
    ctx.log(f"准备推送到：{'、'.join(labels)}")
    delivered_labels = send_apprise_notification(targets, title, body)
    ctx.log(f"最后一条短信已重新推送到：{'、'.join(delivered_labels)}")


def apply_radio_mode(ctx: ActionContext, payload: dict[str, Any]) -> None:
    mode = str(payload.get("mode", "")).strip()
    commands = {
        "4g_only": (["mmcli", "-m", "any", "--set-allowed-modes=4g"], "仅 4G"),
        "3g4g_prefer4g": (
            ["mmcli", "-m", "any", "--set-allowed-modes=3g|4g", "--set-preferred-mode=4g"],
            "3G/4G，优先 4G",
        ),
        "3g_only": (["mmcli", "-m", "any", "--set-allowed-modes=3g"], "仅 3G"),
    }
    if mode not in commands:
        raise ValueError("不支持的制式选项")
    command, label = commands[mode]
    run_logged_command(ctx, command, failure_prefix="切换网络制式失败：")
    ctx.log(f"网络制式已切换到 {label}")


def apply_network_selection(ctx: ActionContext, payload: dict[str, Any]) -> None:
    operator_code = str(payload.get("operator_code", "")).strip()
    run_logged_command(
        ctx,
        ["nmcli", "connection", "modify", "modem", "gsm.network-id", operator_code],
        check=False,
        success_message="NetworkManager 选网配置已更新",
    )

    if not operator_code:
        ctx.log("已切回自动选网")
        recover_modem(ctx)
        return

    run_logged_command(
        ctx,
        ["mmcli", "-m", "any", f"--3gpp-register-in-operator={operator_code}"],
        check=False,
    )
    ctx.sleep(5, "等待手动选网结果")
    modem, modem_error = get_modem_info()
    if modem_error:
        ctx.log(f"当前无法读取注册状态：{modem_error}", "warning")
        return
    ctx.log(
        "当前注册状态："
        f"{modem.get('modem.3gpp.operator-name', '--')} / "
        f"{modem.get('modem.3gpp.operator-code', '--')} / "
        f"{modem.get('modem.3gpp.registration-state', '--')}"
    )


def run_keepalive_task(ctx: ActionContext, payload: dict[str, Any]) -> None:
    if not esim_management_enabled():
        raise RuntimeError("当前为普通 SIM 模式，无法执行保活任务")

    task_id = str(payload.get("task_id", "")).strip()
    if not task_id:
        raise ValueError("缺少保活任务 ID")

    settings, tasks = load_keepalive_config()
    task = next((item for item in tasks if item["id"] == task_id), None)
    if not task:
        raise RuntimeError("保活任务不存在或已删除")

    trigger = str(payload.get("trigger", "manual")).strip() or "manual"
    scheduled_for = str(payload.get("scheduled_for", "")).strip()

    profiles = refresh_profile_cache(force=True)
    target_profile_name = profile_name_for_iccid(task["profile_iccid"], profiles)
    active_profile = active_profile_from_list(profiles)
    original_profile_iccid = str(active_profile.get("iccid", "")).strip()
    original_profile_name = (
        str(active_profile.get("display_name") or profile_display_name(active_profile)).strip()
        if active_profile
        else ""
    )
    switched_to_target = False
    notification_sent = False
    attempts = 0
    main_error = ""
    restore_error = ""
    last_detail = ""

    ctx.log(f"开始执行保活任务：{task['label']}")
    ctx.log(f"目标 Profile：{target_profile_name}")
    ctx.log(f"目标号码：{task['target_number']}")
    if scheduled_for:
        ctx.log(f"计划时间：{format_beijing_timestamp(scheduled_for)}")

    try:
        if task["profile_iccid"] != original_profile_iccid:
            wait_for_keepalive_gap(ctx, settings["queue_gap_seconds"])
            switch_profile(ctx, {"iccid": task["profile_iccid"]}, schedule_gap_after=False)
            switched_to_target = True
            ctx.sleep(KEEPALIVE_SWITCH_SETTLE_SECONDS, "等待切卡后的网络重新稳定")
        else:
            ctx.log("目标 Profile 当前已在使用，跳过切卡")

        send_success = False
        for attempt in range(1, KEEPALIVE_MAX_SEND_ATTEMPTS + 1):
            attempts = attempt
            ctx.log(f"开始第 {attempt} 次保活短信发送")
            ready, detail = wait_for_modem_network_ready(ctx)
            if not ready:
                last_detail = detail
                ctx.log(detail, "warning")
            else:
                send_keepalive_sms(ctx, task["target_number"], task["message"])
                send_success = True
                last_detail = "短信发送成功"
                break
            if attempt < KEEPALIVE_MAX_SEND_ATTEMPTS:
                ctx.sleep(KEEPALIVE_RETRY_INTERVAL_SECONDS, "等待下一次保活短信尝试")

        notify_keepalive_result(
            ctx,
            task,
            profile_name=target_profile_name,
            trigger=trigger,
            scheduled_for=scheduled_for,
            success=send_success,
            attempts=attempts,
            detail=last_detail,
            original_profile_name=original_profile_name,
        )
        notification_sent = True
        if not send_success:
            main_error = f"保活短信连续 {KEEPALIVE_MAX_SEND_ATTEMPTS} 次失败：{last_detail or '未知原因'}"
    except Exception as exc:
        main_error = str(exc)
        if not notification_sent:
            notify_keepalive_result(
                ctx,
                task,
                profile_name=target_profile_name,
                trigger=trigger,
                scheduled_for=scheduled_for,
                success=False,
                attempts=attempts,
                detail=main_error,
                original_profile_name=original_profile_name,
            )
            notification_sent = True
    finally:
        if switched_to_target:
            try:
                if original_profile_iccid:
                    ctx.log(f"准备切回原 Profile：{original_profile_name or profile_name_for_iccid(original_profile_iccid, profiles)}")
                    switch_profile(ctx, {"iccid": original_profile_iccid}, schedule_gap_after=False)
                else:
                    ctx.log("当前没有可识别的原始 Profile，已跳过回切", "warning")
            except Exception as exc:
                restore_error = str(exc)
                ctx.log(f"切回原 Profile 失败：{restore_error}", "error")
        schedule_keepalive_gap(settings["queue_gap_seconds"])

    if restore_error and main_error:
        raise RuntimeError(f"{main_error}；切回原 Profile 失败：{restore_error}")
    if restore_error:
        raise RuntimeError(f"切回原 Profile 失败：{restore_error}")
    if main_error:
        raise RuntimeError(main_error)
    ctx.log("保活任务执行完成")


def execute_action(action: str, payload: dict[str, Any], ctx: ActionContext) -> None:
    if action == "switch_profile":
        switch_profile(ctx, payload)
        return
    if action == "save_apn":
        apply_apn_settings(ctx, payload)
        return
    if action == "save_notifications":
        save_notifications_config(ctx, payload)
        return
    if action == "save_keepalive":
        save_keepalive_settings(ctx, payload)
        return
    if action == "recover_modem":
        recover_modem(ctx)
        return
    if action == "restart_sms":
        restart_sms_service(ctx)
        return
    if action == "resend_last_sms":
        resend_last_sms(ctx)
        return
    if action == "apply_radio_mode":
        apply_radio_mode(ctx, payload)
        return
    if action == "apply_network_selection":
        apply_network_selection(ctx, payload)
        return
    if action == KEEPALIVE_ACTION_NAME:
        run_keepalive_task(ctx, payload)
        return
    raise ValueError("不支持的操作")


def run_action_worker(action_id: str, action: str, payload: dict[str, Any]) -> None:
    ctx = ActionContext(action_id)
    try:
        set_action_state(action_id, "running")
        ctx.log(f"开始执行：{action}")
        execute_action(action, payload, ctx)
        final_status = get_status()
        set_action_state(action_id, "done", message=ctx.summary(), error="", status=final_status)
        ctx.log("执行完成")
    except Exception as exc:
        error_message = str(exc)
        set_action_state(action_id, "error", message=ctx.summary(), error=error_message)
        ctx.log(f"执行失败：{error_message}", "error")


def start_action(action: str, payload: dict[str, Any], *, metadata: Optional[dict[str, Any]] = None) -> str:
    cleanup_actions()
    effective_metadata = dict(metadata or {})
    if action == KEEPALIVE_ACTION_NAME and not effective_metadata:
        task_id = str(payload.get("task_id", "")).strip()
        _, tasks = load_keepalive_config()
        task = next((item for item in tasks if item["id"] == task_id), None)
        if task:
            effective_metadata = {
                "kind": "keepalive",
                "task_id": task["id"],
                "label": task["label"],
                "profile_iccid": task["profile_iccid"],
                "profile_name": profile_name_for_iccid(task["profile_iccid"], get_cached_profiles()[0]),
                "target_number": task["target_number"],
                "scheduled_for": str(payload.get("scheduled_for", "")).strip(),
                "trigger": str(payload.get("trigger", "manual")).strip() or "manual",
            }
    action_id = uuid.uuid4().hex[:12]
    with ACTIONS_LOCK:
        ACTIONS[action_id] = {
            "id": action_id,
            "action": action,
            "payload": dict(payload),
            "state": "queued",
            "events": [],
            "message": "",
            "error": "",
            "status": None,
            "metadata": effective_metadata,
            "created_at": time.time(),
            "updated_at": time.time(),
        }
    with ACTION_QUEUE_CONDITION:
        ACTION_QUEUE.append(action_id)
        ACTION_QUEUE_CONDITION.notify()
    return action_id


def action_queue_worker() -> None:
    while True:
        with ACTION_QUEUE_CONDITION:
            while not ACTION_QUEUE:
                ACTION_QUEUE_CONDITION.wait()
            action_id = ACTION_QUEUE.popleft()

        with ACTIONS_LOCK:
            record = ACTIONS.get(action_id)
            if not record:
                continue
            action = str(record.get("action", "")).strip()
            payload = record.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        run_action_worker(action_id, action, payload)


def enqueue_keepalive_action(task: dict[str, Any], *, trigger: str, scheduled_for: Optional[datetime]) -> str:
    scheduled_for_text = scheduled_for.isoformat() if scheduled_for else ""
    metadata = {
        "kind": "keepalive",
        "task_id": task["id"],
        "label": task["label"],
        "profile_iccid": task["profile_iccid"],
        "profile_name": profile_name_for_iccid(task["profile_iccid"], get_cached_profiles()[0]),
        "target_number": task["target_number"],
        "scheduled_for": scheduled_for_text,
        "trigger": trigger,
    }
    return start_action(
        KEEPALIVE_ACTION_NAME,
        {
            "task_id": task["id"],
            "trigger": trigger,
            "scheduled_for": scheduled_for_text,
        },
        metadata=metadata,
    )


def keepalive_scheduler() -> None:
    while True:
        try:
            if esim_management_enabled():
                _, tasks = load_keepalive_config()
                now = datetime.now(BEIJING_TZ)
                enabled_task_ids = {task["id"] for task in tasks if task["enabled"]}
                with KEEPALIVE_RUNTIME_LOCK:
                    stale_ids = [task_id for task_id in KEEPALIVE_LAST_ENQUEUED if task_id not in enabled_task_ids]
                    for task_id in stale_ids:
                        KEEPALIVE_LAST_ENQUEUED.pop(task_id, None)

                for task in tasks:
                    if not task["enabled"]:
                        continue
                    scheduled_at = due_keepalive_run(task, now)
                    if not scheduled_at:
                        continue
                    schedule_key = keepalive_schedule_key(scheduled_at)
                    with KEEPALIVE_RUNTIME_LOCK:
                        if KEEPALIVE_LAST_ENQUEUED.get(task["id"]) == schedule_key:
                            continue
                        KEEPALIVE_LAST_ENQUEUED[task["id"]] = schedule_key
                    enqueue_keepalive_action(task, trigger="schedule", scheduled_for=scheduled_at)
        except Exception as exc:
            print(f"keepalive scheduler failed: {exc}")
        time.sleep(KEEPALIVE_SCHEDULER_INTERVAL_SECONDS)


def get_action_snapshot(action_id: str, cursor: int) -> dict[str, Any]:
    cleanup_actions()
    with ACTIONS_LOCK:
        record = ACTIONS.get(action_id)
        if not record:
            raise KeyError("任务不存在或已过期")
        events = record["events"][cursor:]
        next_cursor = cursor + len(events)
        return {
            "id": record["id"],
            "action": record["action"],
            "state": record["state"],
            "events": events,
            "cursor": next_cursor,
            "message": record["message"],
            "error": record["error"],
            "status": record.get("status"),
        }


def execute_sync_action(action: str, payload: dict[str, Any]) -> str:
    temp_action_id = uuid.uuid4().hex[:12]
    ctx = ActionContext(temp_action_id)
    execute_action(action, payload, ctx)
    return ctx.summary()


class AppHandler(BaseHTTPRequestHandler):
    server_version = "4GWiFiAdmin/2.0"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _write_json(self, code: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _write_bytes(self, code: int, content_type: str, data: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def _serve_static(self, request_path: str) -> bool:
        root = STATIC_DIR.resolve()
        path = request_path or "/"
        relative = "index.html" if path == "/" else path.lstrip("/")
        candidate = (root / unquote(relative)).resolve()

        if candidate.exists() and candidate.is_dir():
            candidate = candidate / "index.html"

        if not candidate.exists() or root not in candidate.parents and candidate != root / "index.html":
            if "." not in Path(relative).name:
                index_file = root / "index.html"
                if index_file.exists():
                    self._write_bytes(200, "text/html; charset=utf-8", index_file.read_bytes())
                    return True
            return False

        content_type, _ = mimetypes.guess_type(candidate.name)
        if candidate.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        elif candidate.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif candidate.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif candidate.suffix == ".json":
            content_type = "application/json; charset=utf-8"
        self._write_bytes(200, content_type or "application/octet-stream", candidate.read_bytes())
        return True

    def _handle_sync_action(self, action: str, payload: dict[str, Any]) -> None:
        try:
            message = execute_sync_action(action, payload)
            self._write_json(200, {"ok": True, "message": message})
        except Exception as exc:
            self._write_json(500, {"error": str(exc)})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/status":
            try:
                refresh_profiles = "refresh_profiles=1" in parsed.query
                self._write_json(200, get_status(refresh_profiles=refresh_profiles))
            except Exception as exc:
                self._write_json(500, {"error": str(exc)})
            return

        if path.startswith("/api/action/"):
            action_id = path.removeprefix("/api/action/").strip()
            try:
                cursor_match = re.search(r"(?:^|&)cursor=(\d+)(?:&|$)", parsed.query)
                cursor = int(cursor_match.group(1)) if cursor_match else 0
                self._write_json(200, {"ok": True, **get_action_snapshot(action_id, cursor)})
            except KeyError as exc:
                self._write_json(404, {"error": str(exc)})
            except Exception as exc:
                self._write_json(500, {"error": str(exc)})
            return

        if STATIC_DIR.exists() and self._serve_static(path):
            return

        if path == "/":
            self._write_bytes(200, "text/html; charset=utf-8", FALLBACK_INDEX_HTML.encode("utf-8"))
            return

        self._write_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            data = self._read_json_body()
            if path == "/api/action/start":
                action = str(data.get("action", "")).strip()
                payload = data.get("payload", {})
                if not isinstance(payload, dict):
                    raise ValueError("payload 必须是对象")
                action_id = start_action(action, payload)
                self._write_json(200, {"ok": True, "id": action_id})
                return
            if path == "/api/profile/switch":
                self._handle_sync_action("switch_profile", data)
                return
            if path == "/api/apn":
                self._handle_sync_action("save_apn", data)
                return
            if path == "/api/notifications":
                self._handle_sync_action("save_notifications", data)
                return
            if path == "/api/keepalive":
                message = execute_sync_action("save_keepalive", data)
                self._write_json(200, {"ok": True, "message": message, "status": get_status()})
                return
            if path == "/api/modem/recover":
                self._handle_sync_action("recover_modem", data)
                return
            if path == "/api/modem/mode":
                self._handle_sync_action("apply_radio_mode", data)
                return
            if path == "/api/modem/network":
                self._handle_sync_action("apply_network_selection", data)
                return
            if path == "/api/service/restart-sms":
                self._handle_sync_action("restart_sms", data)
                return
            self._write_json(404, {"error": "Not found"})
        except Exception as exc:
            self._write_json(500, {"error": str(exc)})


def main() -> None:
    if esim_management_enabled():
        try:
            refresh_profile_cache(force=True)
            print("eSIM profile cache initialized")
        except Exception as exc:
            print(f"eSIM profile cache init failed: {exc}")
    else:
        print("eSIM management disabled for physical SIM mode")
    threading.Thread(target=action_queue_worker, daemon=True).start()
    threading.Thread(target=keepalive_scheduler, daemon=True).start()
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"4G WiFi admin listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
