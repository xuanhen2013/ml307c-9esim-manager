#!/usr/bin/env python3
from __future__ import annotations
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode, urlparse


NOTIFICATION_TARGETS_KEY = "NOTIFICATION_TARGETS_JSON"
LEGACY_BARK_KEYS = (
    "BARK_BASE_URL",
    "BARK_DEVICE_KEY",
    "BARK_GROUP",
    "BARK_LEVEL",
    "BARK_ICON",
)
BEIJING_TZ = timezone(timedelta(hours=8))

CHANNEL_TYPE_LABELS = {
    "bark": "Bark",
    "telegram": "Telegram",
    "gotify": "Gotify",
    "ntfy": "ntfy",
    "email": "Email",
    "discord": "Discord",
    "slack": "Slack",
    "webhook": "Webhook",
    "json": "JSON",
    "matrix": "Matrix",
    "xmpp": "XMPP",
    "pushbullet": "Pushbullet",
    "pushover": "Pushover",
    "signal": "Signal",
    "line": "LINE",
    "teams": "Teams",
    "mattermost": "Mattermost",
    "office365": "Office 365",
}


def _stable_target_id(label: str, url: str) -> str:
    digest = hashlib.sha1(f"{label}\n{url}".encode("utf-8", errors="ignore")).hexdigest()
    return digest[:12]


def infer_channel_type(url: str) -> str:
    scheme = urlparse(url).scheme.strip().lower()
    if scheme in {"bark", "barks"}:
        return "bark"
    if scheme in {"mailto", "mailtos"}:
        return "email"
    if scheme in {"tgram", "telegram"}:
        return "telegram"
    if scheme:
        return scheme
    return "custom"


def channel_type_label(channel_type: str) -> str:
    normalized = channel_type.strip().lower()
    if normalized in CHANNEL_TYPE_LABELS:
        return CHANNEL_TYPE_LABELS[normalized]
    if not normalized:
        return "渠道"
    if normalized.endswith("s") and normalized[:-1] in CHANNEL_TYPE_LABELS:
        return CHANNEL_TYPE_LABELS[normalized[:-1]]
    return normalized.upper()


def format_channel_label(target: dict[str, Any]) -> str:
    label = str(target.get("label", "")).strip()
    if label:
        return label
    return channel_type_label(str(target.get("type", "")))


def normalize_notification_target(target: dict[str, Any]) -> dict[str, Any]:
    url = str(target.get("url", "")).strip()
    label = str(target.get("label", "")).strip()
    enabled_raw = target.get("enabled", True)
    if isinstance(enabled_raw, bool):
        enabled = enabled_raw
    else:
        enabled = str(enabled_raw).strip().lower() not in {"0", "false", "no", "off", ""}
    channel_type = infer_channel_type(url)
    normalized_label = label or channel_type_label(channel_type)
    target_id = str(target.get("id", "")).strip() or _stable_target_id(normalized_label, url)
    return {
        "id": target_id,
        "label": normalized_label,
        "url": url,
        "enabled": enabled,
        "type": channel_type,
    }


def build_bark_target_from_legacy(config: dict[str, str]) -> dict[str, Any] | None:
    device_key = str(config.get("BARK_DEVICE_KEY", "")).strip()
    if not device_key:
        return None

    base_url = str(config.get("BARK_BASE_URL", "https://api.day.app")).strip() or "https://api.day.app"
    parsed = urlparse(base_url)
    host = parsed.netloc or parsed.path
    path = parsed.path.strip("/") if parsed.netloc else ""
    host_path = host.rstrip("/")
    if path:
        host_path = f"{host_path}/{path}"

    scheme = "barks" if parsed.scheme.lower() == "https" else "bark"
    url = f"{scheme}://{host_path}/{device_key}"
    query: dict[str, str] = {}
    for source_key, query_key in (("BARK_GROUP", "group"), ("BARK_LEVEL", "level"), ("BARK_ICON", "image")):
        value = str(config.get(source_key, "")).strip()
        if value:
            query[query_key] = value
    if query:
        url = f"{url}?{urlencode(query)}"
    return normalize_notification_target({"label": "Bark", "url": url, "enabled": True})


def load_notification_targets(config: dict[str, str]) -> list[dict[str, Any]]:
    raw_targets = str(config.get(NOTIFICATION_TARGETS_KEY, "")).strip()
    if raw_targets:
        try:
            parsed = json.loads(raw_targets)
        except json.JSONDecodeError:
            parsed = []
        if isinstance(parsed, dict):
            parsed = parsed.get("targets", [])
        if isinstance(parsed, list):
            return [normalize_notification_target(item) for item in parsed if isinstance(item, dict)]

    legacy_bark_target = build_bark_target_from_legacy(config)
    return [legacy_bark_target] if legacy_bark_target else []


def configured_notification_targets(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [target for target in targets if str(target.get("url", "")).strip() and bool(target.get("enabled", True))]


def configured_channel_labels(targets: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    for target in configured_notification_targets(targets):
        label = format_channel_label(target)
        if label not in labels:
            labels.append(label)
    return labels


def save_notification_targets_in_config(config: dict[str, str], targets: list[dict[str, Any]]) -> dict[str, str]:
    sanitized = [normalize_notification_target(target) for target in targets if isinstance(target, dict)]
    config[NOTIFICATION_TARGETS_KEY] = json.dumps(sanitized, ensure_ascii=False, separators=(",", ":"))
    for legacy_key in LEGACY_BARK_KEYS:
        config.pop(legacy_key, None)
    return config


def ensure_notification_config(config: dict[str, str]) -> dict[str, str]:
    if "MODEM_ID" not in config:
        config["MODEM_ID"] = "any"
    if "FORWARD_SMS_STATES" not in config:
        config["FORWARD_SMS_STATES"] = "received"
    if NOTIFICATION_TARGETS_KEY not in config:
        config[NOTIFICATION_TARGETS_KEY] = "[]"
    return config


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


def format_sms_notification(detail: dict[str, str]) -> tuple[str, str]:
    number = detail.get("number") or "unknown"
    title = f"收到短信：{number}"
    body = "\n\n".join(
        [
            detail.get("text") or "(empty)",
            f"时间：{detail.get('timestamp') or '未知时间'}\n状态：{format_sms_state_label(detail.get('state', ''))}",
        ]
    )
    return title, body


def send_apprise_notification(targets: list[dict[str, Any]], title: str, body: str) -> list[str]:
    try:
        import apprise
    except ImportError as exc:
        raise RuntimeError("Apprise 未安装，无法发送通知") from exc

    configured = configured_notification_targets(targets)
    if not configured:
        raise RuntimeError("未配置任何启用的通知渠道")

    app = apprise.Apprise()
    for target in configured:
        app.add(str(target["url"]))

    result = app.notify(title=title, body=body)
    if not result:
        raise RuntimeError("Apprise 推送失败")
    return configured_channel_labels(configured)
