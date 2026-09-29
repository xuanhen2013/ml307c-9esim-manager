#!/usr/bin/env python3
from __future__ import annotations
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


NOTIFICATION_TARGETS_KEY = "NOTIFICATION_TARGETS_JSON"
BEIJING_TZ = timezone(timedelta(hours=8))
SCRIPT_DIR = Path(__file__).resolve().parent
NOTIFICATION_ICON_ENV_KEY = "ESIM_SMS_FORWARDER_NOTIFICATION_ICON"

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
    return []


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
    return config


def ensure_notification_config(config: dict[str, str]) -> dict[str, str]:
    if "MODEM_ID" not in config:
        config["MODEM_ID"] = "any"
    if "FORWARD_SMS_STATES" not in config:
        config["FORWARD_SMS_STATES"] = "received"
    if NOTIFICATION_TARGETS_KEY not in config:
        config[NOTIFICATION_TARGETS_KEY] = "[]"
    return config


def resolve_notification_icon_path() -> str | None:
    raw_override = os.environ.get(NOTIFICATION_ICON_ENV_KEY, "").strip()
    candidates = [
        Path(raw_override) if raw_override else None,
        SCRIPT_DIR / "frontend_dist" / "app-icon.png",
        SCRIPT_DIR.parent / "web_admin" / "frontend_dist" / "app-icon.png",
        SCRIPT_DIR.parent.parent / "frontend" / "public" / "app-icon.png",
    ]
    for candidate in candidates:
        if candidate and candidate.is_file():
            return str(candidate)
    return None


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

    labels = []
    for target in configured:
        app = apprise.Apprise()
        if not app.add(str(target['url'])):
            raise NotificationError('通知地址格式无效，请检查渠道配置', retryable=False)
        from apprise.plugins.pushplus import NotifyPushplus
        if isinstance(app[0], NotifyPushplus):
            send_pushplus(app[0], title, body)
        else:
            notify_kwargs: dict[str, Any] = {"title": title, "body": body}
            icon_path = resolve_notification_icon_path()
            if icon_path:
                notify_kwargs["attach"] = icon_path
            if not app.notify(**notify_kwargs):
                raise NotificationError('通知服务未确认接收，请检查网络和渠道配置')
        labels.append(format_channel_label(target))
    return labels


class NotificationError(RuntimeError):
    def __init__(self, message, *, retryable=True):
        self.retryable = retryable
        super().__init__(message)


def send_pushplus(service, title, body):
    """Keep Apprise URL parsing; expose safe business errors instead of a boolean.

    A 200 response acknowledges queuing, not final delivery to the WeChat client.
    Never log the URL/token, payload, response body, or a requests exception.
    """
    import requests
    from apprise.plugins.pushplus import PUSHPLUS_FORMAT_MAP, PUSHPLUS_FORMAT_DEFAULT

    if len(service.topics) > 1:
        raise NotificationError('请为每个 PushPlus 群组单独配置渠道，避免部分成功后重复发送', retryable=False)
    payload = {'token': service.token, 'title': title, 'content': body,
               'template': PUSHPLUS_FORMAT_MAP.get(service.notify_format, PUSHPLUS_FORMAT_DEFAULT),
               'channel': service.channel}
    if service.topics:
        payload['topic'] = service.topics[0]
    if service.webhook:
        payload['webhook'] = service.webhook
    try:
        response = requests.post('https://www.pushplus.plus/send', json=payload,
                                 timeout=(8, 20), allow_redirects=False)
    except requests.ReadTimeout:
        raise NotificationError('PushPlus 响应超时，发送结果不确定；已停止自动重试，避免重复通知', retryable=False) from None
    except requests.RequestException:
        raise NotificationError('无法连接 PushPlus，请检查 NAS 网络') from None
    if response.status_code != 200:
        code = response.status_code
        raise NotificationError(f'PushPlus HTTP {code}，请检查服务状态', retryable=code >= 500)
    try:
        result = response.json()
        code = result.get('code')
    except (ValueError, AttributeError):
        raise NotificationError('PushPlus 响应格式异常，发送结果不确定；已停止自动重试', retryable=False) from None
    if code == 200:
        return
    reasons = {900: '账号使用受限，请到 PushPlus 查看解除限制的时间',
               905: '账户未进行实名认证，请在 PushPlus 完成认证',
               903: '用户令牌无效，请检查 PushPlus Token',
               401: '请求未授权，请检查账号设置', 403: '请求 IP 未授权，请检查白名单',
               888: '账号积分不足', 999: '服务端验证失败，请到 PushPlus 查看原因',
               500: '服务暂时异常', 600: '请求数据异常，请检查渠道配置'}
    detail = reasons.get(code, '服务未确认接收，请到 PushPlus 查看原因')
    # Unknown server messages can contain secrets. Only known codes and fixed text leave here.
    shown_code = str(code) if isinstance(code, int) else '未知'
    raise NotificationError(f'PushPlus {shown_code}：{detail}', retryable=code == 500)
