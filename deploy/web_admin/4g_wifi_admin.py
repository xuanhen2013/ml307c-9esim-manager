#!/usr/bin/env python3
import json
import os
import re
import subprocess
import time
from base64 import b64decode
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse


HOST = os.environ.get("FOURG_WIFI_ADMIN_HOST", "0.0.0.0")
PORT = int(os.environ.get("FOURG_WIFI_ADMIN_PORT", "8080"))
BARK_CONFIG_PATH = Path("/etc/sms-bark-forwarder.conf")
PROFILE_APN_DEFAULTS = {
    "giffgaff": {"apn": "giffgaff.com", "username": "giffgaff", "password": "password", "ip_type": "ipv4"},
    "t-mobile": {"apn": "fast.t-mobile.com", "username": "", "password": "", "ip_type": "ipv4v6"},
}


def run_command(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=check, capture_output=True, text=True)


def parse_lpac_json(raw: str) -> dict:
    data = json.loads(raw)
    return data.get("payload", {})


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
        dt = datetime.fromisoformat(raw_timestamp)
        beijing = timezone(timedelta(hours=8))
        return dt.astimezone(beijing).strftime("%Y年%m月%d日 %H时%M分%S秒")
    except Exception:
        return raw_timestamp


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


def get_profiles() -> list[dict]:
    result = run_command(["/usr/local/bin/lpac-switch", "list"])
    payload = parse_lpac_json(result.stdout)
    if payload.get("code") != 0:
        raise RuntimeError(payload.get("message", "lpac failed"))
    return payload.get("data", [])


def get_modem_info() -> tuple[dict[str, str], Optional[str]]:
    result = run_command(["mmcli", "-m", "any", "-K"], check=False)
    if result.returncode != 0:
        error = (result.stderr or result.stdout).strip() or "无法读取基带状态"
        return {}, error
    return parse_mmcli_kv(result.stdout), None


def list_sms() -> tuple[list[dict[str, str]], Optional[str]]:
    result = run_command(["mmcli", "-m", "any", "--messaging-list-sms"], check=False)
    if result.returncode != 0:
        error = (result.stderr or result.stdout).strip() or "无法读取短信列表"
        return [], error
    paths = re.findall(r"(/org/freedesktop/ModemManager1/SMS/\d+)", result.stdout)
    messages: list[dict[str, str]] = []
    for path in paths:
        detail = run_command(["mmcli", "-s", path, "-K"], check=False)
        if detail.returncode != 0:
            continue
        kv = parse_mmcli_kv(detail.stdout)
        state = kv.get("sms.properties.state", "")
        messages.append(
            {
                "path": path,
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
    return messages, None


def service_state(name: str) -> str:
    result = run_command(["systemctl", "is-active", name], check=False)
    return (result.stdout or result.stderr).strip()


def get_connection_info() -> dict[str, str]:
    result = run_command(["nmcli", "connection", "show", "modem"], check=False)
    return parse_mmcli_kv(result.stdout) if result.returncode == 0 else {}


def get_profile_apn_defaults(provider_name: str, profile_name: str) -> Optional[dict[str, str]]:
    haystack = f"{provider_name} {profile_name}".lower()
    for key, value in PROFILE_APN_DEFAULTS.items():
        if key in haystack:
            return value
    return None


def get_profile_by_iccid(iccid: str) -> dict:
    return next((profile for profile in get_profiles() if profile.get("iccid") == iccid), {})


def infer_apn_defaults_from_connection(apn: str, username: str = "") -> Optional[dict[str, str]]:
    for value in PROFILE_APN_DEFAULTS.values():
        if value["apn"] == apn:
            if not value["username"] or value["username"] == username:
                return value
    return None


def get_status() -> dict:
    status_message = ""
    errors: list[str] = []
    bark = read_env_config(BARK_CONFIG_PATH)
    connection = get_connection_info()
    connection_defaults = infer_apn_defaults_from_connection(
        "" if connection.get("gsm.apn", "") == "--" else connection.get("gsm.apn", ""),
        "" if connection.get("gsm.username", "") == "--" else connection.get("gsm.username", ""),
    )

    try:
        profiles = get_profiles()
    except Exception as exc:
        profiles = []
        errors.append(f"读取 eSIM 列表失败：{exc}")

    modem, modem_error = get_modem_info()
    if modem_error:
        status_message = "基带当前离线或正在重连，页面还能继续操作，建议点一次“基带恢复”后再刷新。"
        errors.append(modem_error)

    sms_messages, sms_error = list_sms()
    if sms_error:
        if not status_message:
            status_message = "暂时拿不到短信列表，可能是基带还在重连。"
        errors.append(sms_error)

    return {
        "profiles": profiles,
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
            "ip_type": connection_defaults["ip_type"] if connection_defaults else "",
            "network_id": "" if connection.get("gsm.network-id", "") == "--" else connection.get("gsm.network-id", ""),
        },
        "services": {
            "modemmanager": service_state("ModemManager"),
            "sms_forwarder": service_state("sms-bark-forwarder.service"),
            "web_admin": service_state("4g-wifi-admin.service"),
        },
        "bark": {
            "base_url": bark.get("BARK_BASE_URL", ""),
            "device_key": bark.get("BARK_DEVICE_KEY", ""),
            "group": bark.get("BARK_GROUP", "sms"),
            "level": bark.get("BARK_LEVEL", "active"),
        },
        "sms": sms_messages,
        "timestamp": format_beijing_timestamp(datetime.now(timezone.utc).isoformat()),
    }


def apply_apn_settings(apn: str, username: str, password: str, ip_type: str) -> list[str]:
    messages: list[str] = []
    settings = f"apn={apn},ip-type={ip_type}"
    if username:
        settings += f",user={username}"
    if password:
        settings += f",password={password}"
    mm = run_command(["mmcli", "-m", "any", f"--3gpp-set-initial-eps-bearer-settings={settings}"], check=False)
    if mm.returncode == 0:
        messages.append("已更新 ModemManager 初始 EPS bearer。")
    else:
        messages.append(f"ModemManager 初始 EPS bearer 更新失败：{(mm.stderr or mm.stdout).strip()}")
        messages.append("这颗基带可能不支持在线改这个值，页面会以当前连接配置作为准。")
    run_command(
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
        ]
    )
    messages.append("已更新 NetworkManager modem 连接。")
    return messages


def recover_modem() -> list[str]:
    steps = [
        ["systemctl", "stop", "ModemManager"],
        ["qmicli", "-d", "/dev/wwan0qmi0", "--uim-sim-power-off=1"],
        ["qmicli", "-d", "/dev/wwan0qmi0", "--uim-sim-power-on=1"],
        ["systemctl", "start", "ModemManager"],
    ]
    notes: list[str] = []
    for index, cmd in enumerate(steps):
        result = run_command(cmd, check=False)
        output = (result.stdout or result.stderr).strip()
        if output:
            notes.append(output)
        if index == 0:
            time.sleep(3)
        elif index in (1, 2):
            time.sleep(3)
        elif index == 3:
            time.sleep(10)
    run_command(["systemctl", "restart", "sms-bark-forwarder.service"], check=False)
    return notes


def switch_profile(iccid: str) -> dict:
    result = run_command(["/usr/local/bin/lpac-switch", "enable", iccid], check=False)
    payload = parse_lpac_json(result.stdout) if result.stdout else {"code": -1, "message": result.stderr.strip()}
    if payload.get("code") != 0:
        raise RuntimeError(payload.get("message", "切卡失败"))
    notes = ["已下发切卡命令。"]
    notes.extend(recover_modem())
    return {"message": "\n".join(notes)}


def save_bark_config(base_url: str, device_key: str, group: str, level: str) -> list[str]:
    config = read_env_config(BARK_CONFIG_PATH)
    config["MODEM_ID"] = config.get("MODEM_ID", "any")
    config["BARK_BASE_URL"] = base_url
    config["BARK_DEVICE_KEY"] = device_key
    config["BARK_GROUP"] = group
    config["BARK_LEVEL"] = level
    config["BARK_ICON"] = config.get("BARK_ICON", "")
    config["FORWARD_SMS_STATES"] = config.get("FORWARD_SMS_STATES", "received")
    write_env_config(BARK_CONFIG_PATH, config)
    run_command(["systemctl", "restart", "sms-bark-forwarder.service"], check=False)
    return ["已保存 Bark 配置并重启短信转发服务。"]


def restart_sms_service() -> list[str]:
    run_command(["systemctl", "restart", "sms-bark-forwarder.service"])
    return ["已重启短信转发服务。"]


def apply_radio_mode(mode: str) -> list[str]:
    commands = {
        "4g_only": ["mmcli", "-m", "any", "--set-allowed-modes=4g"],
        "3g4g_prefer4g": ["mmcli", "-m", "any", "--set-allowed-modes=3g|4g", "--set-preferred-mode=4g"],
        "3g_only": ["mmcli", "-m", "any", "--set-allowed-modes=3g"],
    }
    if mode not in commands:
        raise ValueError("不支持的制式选项")
    result = run_command(commands[mode], check=False)
    output = (result.stdout or result.stderr).strip()
    if result.returncode != 0:
        raise RuntimeError(output or "制式切换失败")
    labels = {
        "4g_only": "仅 4G",
        "3g4g_prefer4g": "3G/4G，优先 4G",
        "3g_only": "仅 3G",
    }
    return [f"已切换到 {labels[mode]}。", output] if output else [f"已切换到 {labels[mode]}。"]


def apply_network_selection(operator_code: str) -> list[str]:
    notes: list[str] = []
    run_command(["nmcli", "connection", "modify", "modem", "gsm.network-id", operator_code], check=False)
    if not operator_code:
        notes.append("已切回自动选网。")
        notes.extend(recover_modem())
        return notes
    result = run_command(["mmcli", "-m", "any", f"--3gpp-register-in-operator={operator_code}"], check=False)
    output = (result.stdout or result.stderr).strip()
    if result.returncode == 0:
        notes.append(f"已请求手动注册 {operator_code}。")
    else:
        notes.append(f"手动注册 {operator_code} 返回：{output or '失败'}")
    time.sleep(5)
    modem, modem_error = get_modem_info()
    if modem_error:
        notes.append(f"当前基带状态：{modem_error}")
    else:
        registration = modem.get("modem.3gpp.registration-state", "--")
        operator_name = modem.get("modem.3gpp.operator-name", "--")
        operator_now = modem.get("modem.3gpp.operator-code", "--")
        notes.append(f"当前注册状态：{operator_name} / {operator_now} / {registration}")
    return notes


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>4G WiFi 控制台</title>
  <style>
    :root {
      --bg: #f4efe6;
      --card: rgba(255,255,255,0.84);
      --card-strong: #fffdf9;
      --ink: #1e2430;
      --muted: #6c7280;
      --line: rgba(30,36,48,0.1);
      --accent: #0c7a6b;
      --accent-2: #b85c38;
      --shadow: 0 20px 50px rgba(75, 52, 27, 0.14);
      --radius: 22px;
      --font: "IBM Plex Sans", "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: var(--font);
      color: var(--ink);
      background:
        radial-gradient(circle at top left, rgba(12,122,107,0.18), transparent 32%),
        radial-gradient(circle at bottom right, rgba(184,92,56,0.16), transparent 28%),
        linear-gradient(160deg, #efe4d2 0%, #f9f5ef 46%, #ecf3f0 100%);
      min-height: 100vh;
    }
    .shell { width: min(1180px, calc(100vw - 28px)); margin: 18px auto 28px; }
    .hero { display: grid; grid-template-columns: 1.4fr 1fr; gap: 18px; margin-bottom: 18px; }
    .panel { background: var(--card); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); backdrop-filter: blur(10px); }
    .hero-main { padding: 24px; position: relative; overflow: hidden; }
    .hero-main::after { content: ""; position: absolute; inset: auto -80px -100px auto; width: 240px; height: 240px; border-radius: 50%; background: rgba(12,122,107,0.12); filter: blur(8px); }
    .eyebrow { letter-spacing: .14em; font-size: 12px; text-transform: uppercase; color: var(--accent); font-weight: 700; }
    h1 { margin: 10px 0 12px; font-size: clamp(28px, 4vw, 44px); line-height: 1.02; }
    .sub { color: var(--muted); max-width: 46rem; line-height: 1.6; }
    .hero-side { padding: 20px; display: flex; flex-direction: column; justify-content: space-between; gap: 12px; }
    .stamp { display: inline-flex; align-items: center; gap: 8px; padding: 9px 12px; border-radius: 999px; background: rgba(12,122,107,0.08); color: var(--accent); font-weight: 700; width: fit-content; }
    .grid { display: grid; grid-template-columns: repeat(12, 1fr); gap: 18px; align-items: start; }
    .span-12 { grid-column: span 12; } .span-8 { grid-column: span 8; } .span-7 { grid-column: span 7; } .span-6 { grid-column: span 6; } .span-5 { grid-column: span 5; } .span-4 { grid-column: span 4; }
    .section { padding: 18px; }
    .section h2 { margin: 0 0 14px; font-size: 18px; }
    .section-head { display: flex; justify-content: space-between; gap: 12px; align-items: center; margin-bottom: 14px; flex-wrap: wrap; }
    .stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .stat { padding: 16px; border-radius: 18px; background: var(--card-strong); border: 1px solid var(--line); }
    .stat-label { font-size: 12px; color: var(--muted); margin-bottom: 8px; }
    .stat-value { font-size: 22px; font-weight: 700; word-break: break-word; line-height: 1.25; }
    .stat-value.compact { font-size: 17px; font-weight: 600; }
    .muted { color: var(--muted); }
    .row { display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }
    .button { border: 0; border-radius: 14px; padding: 11px 14px; background: var(--ink); color: white; font: inherit; font-weight: 700; cursor: pointer; transition: transform .16s ease, opacity .16s ease; }
    .button:hover { transform: translateY(-1px); }
    .button:disabled { opacity: .5; cursor: not-allowed; transform: none; }
    .button.alt { background: var(--accent); }
    .button.soft { background: rgba(12,122,107,0.09); color: var(--accent); }
    .profile-list, .sms-list { display: grid; gap: 12px; }
    .profile-card, .sms-card { border-radius: 18px; border: 1px solid var(--line); background: var(--card-strong); padding: 16px; }
    .profile-top { display: flex; justify-content: space-between; gap: 16px; align-items: flex-start; }
    .stack { display: grid; gap: 18px; }
    .panel-note { margin: -4px 0 14px; color: var(--muted); line-height: 1.5; font-size: 13px; }
    .aligned-panel { display: flex; flex-direction: column; min-height: 620px; }
    .scroll-frame { flex: 1; min-height: 0; overflow: auto; padding-right: 6px; }
    .scroll-frame::-webkit-scrollbar { width: 10px; }
    .scroll-frame::-webkit-scrollbar-thumb { background: rgba(30,36,48,0.15); border-radius: 999px; }
    .scroll-frame::-webkit-scrollbar-track { background: transparent; }
    .badge { display: inline-flex; align-items: center; padding: 6px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; background: rgba(12,122,107,0.1); color: var(--accent); }
    .badge.dim { background: rgba(108,114,128,0.12); color: var(--muted); }
    .fields { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; }
    .fields.single-gap { margin-top: 14px; }
    .field { display: flex; flex-direction: column; gap: 6px; }
    .field label { font-size: 13px; color: var(--muted); font-weight: 600; }
    .field input, .field select { width: 100%; padding: 12px 13px; border-radius: 14px; border: 1px solid var(--line); background: white; font: inherit; color: var(--ink); }
    .notice { margin-top: 12px; padding: 12px 14px; border-radius: 16px; border: 1px solid rgba(12,122,107,0.15); background: rgba(12,122,107,0.07); white-space: pre-wrap; line-height: 1.5; }
    .notice.warn { border-color: rgba(184,92,56,0.2); background: rgba(184,92,56,0.09); color: #7b3e28; }
    .advanced { margin-top: 4px; }
    .advanced summary { cursor: pointer; font-weight: 700; color: var(--accent); list-style: none; }
    .advanced summary::-webkit-details-marker { display: none; }
    .advanced summary::after { content: "展开"; font-size: 12px; margin-left: 10px; color: var(--muted); }
    .advanced[open] summary::after { content: "收起"; }
    .advanced-body { margin-top: 14px; padding-top: 14px; border-top: 1px dashed var(--line); }
    .advanced-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; align-items: start; }
    .form-block { padding: 16px; border-radius: 18px; border: 1px solid var(--line); background: var(--card-strong); }
    .small { font-size: 12px; color: var(--muted); }
    .sms-text { white-space: pre-wrap; line-height: 1.6; margin-top: 8px; }
    .skeleton { height: 16px; border-radius: 999px; background: linear-gradient(90deg, rgba(0,0,0,.05), rgba(255,255,255,.45), rgba(0,0,0,.05)); background-size: 200% 100%; animation: shimmer 1.4s linear infinite; }
    @keyframes shimmer { from { background-position: 200% 0; } to { background-position: -200% 0; } }
    @media (max-width: 980px) { .hero, .stats, .fields, .advanced-grid { grid-template-columns: 1fr; } .span-8, .span-7, .span-6, .span-5, .span-4 { grid-column: span 12; } .profile-top { flex-direction: column; } .profile-top .button { width: 100%; } .aligned-panel { min-height: auto; } .scroll-frame { max-height: 420px; } }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div class="panel hero-main">
        <div class="eyebrow">4G WiFi Admin</div>
        <h1>用网页管理切卡、短信和 Bark</h1>
        <div class="sub">这页直接跑在你的 Debian 设备上。主区域专注切卡、短信和 Bark，APN 这类低频设置收进高级选项里，页面会清爽一些。</div>
      </div>
      <div class="panel hero-side">
        <div><div class="stamp">实时状态</div><p class="sub" style="margin-top:12px">页面操作会调用你已经验证过的命令链路，不额外引入 Node 运行时。eSIM 列表每 15 秒会自动重新读取一次。</p></div>
        <div class="small" id="last-updated">正在加载状态...</div>
      </div>
    </section>
    <section class="grid">
      <div class="panel section span-12">
        <div class="section-head">
          <h2>总览</h2>
          <div class="row">
            <button class="button soft" onclick="refreshStatus()">刷新状态</button>
            <button class="button soft" onclick="recoverModem()">基带恢复</button>
            <button class="button soft" onclick="restartSms()">重启短信转发</button>
          </div>
        </div>
        <div class="stats" id="stats"><div class="stat"><div class="skeleton"></div></div><div class="stat"><div class="skeleton"></div></div><div class="stat"><div class="skeleton"></div></div><div class="stat"><div class="skeleton"></div></div></div>
        <div id="status-banner"></div>
        <div id="action-result"></div>
      </div>
      <div class="panel section span-6 aligned-panel">
        <div class="section-head"><h2>eSIM Profiles</h2><span class="small">每 15 秒自动刷新一次</span></div>
        <div class="panel-note">这里会实时读取设备当前的 eSIM profile 列表。你在别的地方新增或切换 profile，页面下一轮刷新会自动更新。</div>
        <div class="scroll-frame">
          <div class="profile-list" id="profiles"></div>
        </div>
      </div>
      <div class="panel section span-6 aligned-panel">
        <div class="section-head"><h2>最近短信</h2><span class="small">右侧可滚动查看历史</span></div>
        <div class="panel-note">短信正文会自动做中文解码和常见 base64 解码，时间按北京时间显示。</div>
        <div class="scroll-frame">
          <div class="sms-list" id="sms-list"></div>
        </div>
      </div>
      <div class="panel section span-12">
        <h2>高级设置</h2>
        <div class="panel-note">低频配置统一收在这里，默认不展开，避免主页面出现大块空白区。</div>
        <details class="advanced">
          <summary>展开高级设置</summary>
          <div class="advanced-body">
            <div class="advanced-grid">
              <div class="form-block">
                <h2>Bark 配置</h2>
                <div class="small">修改推送地址或设备 Key 后，会自动重启短信转发服务。</div>
                <div class="fields single-gap">
                  <div class="field"><label>Base URL</label><input id="bark_base_url" placeholder="https://bark.example.com"></div>
                  <div class="field"><label>Device Key</label><input id="bark_device_key" placeholder="你的 Bark Key"></div>
                  <div class="field"><label>Group</label><input id="bark_group" placeholder="sms"></div>
                  <div class="field"><label>Level</label><select id="bark_level"><option value="active">active</option><option value="timeSensitive">timeSensitive</option><option value="passive">passive</option></select></div>
                </div>
                <div class="row" style="margin-top:14px"><button class="button alt" onclick="saveBark()">保存 Bark 配置</button></div>
              </div>
              <div class="form-block">
                <h2>APN 设置</h2>
                <div class="small">APN 一般不需要频繁改。切卡后默认不再自动改 APN，也不再触发重启。</div>
                <div class="fields single-gap">
                  <div class="field"><label>APN</label><input id="apn" placeholder="giffgaff.com 或 fast.t-mobile.com"></div>
                  <div class="field"><label>IP 类型</label><select id="ip_type"><option value="ipv4">ipv4</option><option value="ipv6">ipv6</option><option value="ipv4v6">ipv4v6</option></select></div>
                  <div class="field"><label>用户名</label><input id="apn_user" placeholder="可留空"></div>
                  <div class="field"><label>密码</label><input id="apn_pass" type="password" placeholder="可留空"></div>
                </div>
                <div class="row" style="margin-top:14px"><button class="button alt" onclick="saveApn()">保存 APN</button></div>
                <div class="small" style="margin-top:18px">下面这组适合拿来测试 T-Mobile 在国内的驻网情况。</div>
                <div class="fields single-gap">
                  <div class="field"><label>制式</label><select id="radio_mode"><option value="4g_only">仅 4G</option><option value="3g4g_prefer4g">3G / 4G，优先 4G</option><option value="3g_only">仅 3G</option></select></div>
                  <div class="field"><label>手动选网</label><select id="network_select"><option value="">自动</option><option value="46001">中国联通 46001</option><option value="46000">中国移动 46000</option><option value="46011">中国电信 46011</option></select></div>
                </div>
                <div class="row" style="margin-top:14px"><button class="button alt" onclick="saveRadioMode()">应用制式</button><button class="button alt" onclick="saveNetworkSelection()">应用选网</button></div>
              </div>
            </div>
          </div>
        </details>
      </div>
      <div class="panel section span-12">
        <h2>快捷说明</h2>
        <div class="panel-note">切卡按钮会自动串联执行 `lpac` 切换和基带恢复流程。短信列表展示的是设备本地已收短信，不会删卡里的消息。</div>
        <div class="small">如果你刚新增了 eSIM profile，等 15 秒左右，或者点一下“刷新状态”，页面就会出现新 profile。</div>
      </div>
    </section>
  </div>
  <script>
    function notice(message, kind = "info") { document.getElementById("action-result").innerHTML = `<div class="notice ${kind === "warn" ? "warn" : ""}">${escapeHtml(message)}</div>`; }
    function setStatusBanner(message) {
      document.getElementById("status-banner").innerHTML = message ? `<div class="notice warn">${escapeHtml(message)}</div>` : "";
    }
    function escapeHtml(value) { const div = document.createElement("div"); div.textContent = value; return div.innerHTML; }
    async function api(path, method = "GET", body = null) {
      const options = { method, headers: {} };
      if (body) { options.headers["Content-Type"] = "application/json"; options.body = JSON.stringify(body); }
      const response = await fetch(path, options);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "请求失败");
      return data;
    }
    function renderStats(data) {
      const items = [
        { label: "当前号码", value: data.modem_available ? (data.modem.number || "--") : "基带离线", compact: !data.modem_available },
        { label: "网络状态", value: data.modem_available ? `${data.modem.operator_name || data.modem.operator_code || "--"} / ${data.modem.registration || "--"}` : "等待重连", compact: true },
        { label: "信号 / 制式", value: data.modem_available ? `${data.modem.signal || "--"} / ${data.modem.access_tech || "--"}` : "-- / --", compact: true },
        { label: "服务状态", value: `短信 ${data.services.sms_forwarder} / MM ${data.services.modemmanager}`, compact: true },
      ];
      document.getElementById("stats").innerHTML = items.map((item) => `<div class="stat"><div class="stat-label">${escapeHtml(item.label)}</div><div class="stat-value ${item.compact ? "compact" : ""}">${escapeHtml(item.value)}</div></div>`).join("");
    }
    function renderProfiles(data) {
      document.getElementById("profiles").innerHTML = data.profiles.map((profile) => {
        const active = profile.profileState === "enabled";
        return `<div class="profile-card"><div class="profile-top"><div><div class="row" style="margin-bottom:6px"><strong>${escapeHtml(profile.serviceProviderName || profile.profileName || "未命名 Profile")}</strong><span class="badge ${active ? "" : "dim"}">${active ? "当前启用" : "已停用"}</span></div><div class="small">ICCID: ${escapeHtml(profile.iccid || "--")}</div><div class="small">名称: ${escapeHtml(profile.profileName || "--")}</div></div><button class="button ${active ? "soft" : "alt"}" ${active ? "disabled" : ""} onclick="switchProfile('${escapeHtml(profile.iccid || "")}', '${escapeHtml(profile.serviceProviderName || "")}')">${active ? "当前使用中" : "切到这张卡"}</button></div></div>`;
      }).join("");
    }
    function renderSms(data) {
      const sms = data.sms || [];
      if (!sms.length) {
        const emptyText = data.modem_available ? "暂时还没有短信。" : "基带离线，暂时无法读取短信历史。";
        document.getElementById("sms-list").innerHTML = `<div class="sms-card muted">${emptyText}</div>`;
        return;
      }
      document.getElementById("sms-list").innerHTML = sms.map((item) => `<div class="sms-card"><div class="row" style="justify-content:space-between"><strong>${escapeHtml(item.number || "未知发件人")}</strong><span class="badge">${escapeHtml(item.state_label || item.state || "--")}</span></div><div class="small" style="margin-top:6px">${escapeHtml(item.timestamp || "未知时间")}</div><div class="sms-text">${escapeHtml(item.text || "(空短信)")}</div></div>`).join("");
    }
    function fillForms(data) {
      document.getElementById("apn").value = data.connection.apn || (data.modem.apn === "--" ? "" : (data.modem.apn || ""));
      document.getElementById("apn_user").value = data.connection.username || "";
      document.getElementById("ip_type").value = data.connection.ip_type || ((data.modem.ip_type && data.modem.ip_type !== "--") ? data.modem.ip_type : "ipv4");
      const modeText = data.modem.current_modes || "";
      document.getElementById("radio_mode").value = modeText.includes("allowed: 4g;") ? "4g_only" : (modeText.includes("preferred: 4g") ? "3g4g_prefer4g" : (modeText.includes("allowed: 3g;") ? "3g_only" : "4g_only"));
      document.getElementById("network_select").value = data.connection.network_id || "";
      document.getElementById("bark_base_url").value = data.bark.base_url || "";
      document.getElementById("bark_device_key").value = data.bark.device_key || "";
      document.getElementById("bark_group").value = data.bark.group || "sms";
      document.getElementById("bark_level").value = data.bark.level || "active";
      document.getElementById("last-updated").textContent = `最后刷新：${data.timestamp}`;
    }
    async function refreshStatus() {
      const data = await api("/api/status");
      renderStats(data);
      renderProfiles(data);
      renderSms(data);
      fillForms(data);
      setStatusBanner(data.status_message || "");
    }
    async function saveApn() {
      const payload = { apn: document.getElementById("apn").value.trim(), ip_type: document.getElementById("ip_type").value, username: document.getElementById("apn_user").value, password: document.getElementById("apn_pass").value };
      const data = await api("/api/apn", "POST", payload); notice(data.message || "APN 已保存。"); await refreshStatus();
    }
    async function saveBark() {
      const payload = { base_url: document.getElementById("bark_base_url").value.trim(), device_key: document.getElementById("bark_device_key").value.trim(), group: document.getElementById("bark_group").value.trim() || "sms", level: document.getElementById("bark_level").value };
      const data = await api("/api/bark", "POST", payload); notice(data.message || "Bark 配置已保存。"); await refreshStatus();
    }
    async function saveRadioMode() {
      const payload = { mode: document.getElementById("radio_mode").value };
      const data = await api("/api/modem/mode", "POST", payload); notice(data.message || "制式已应用。"); await refreshStatus();
    }
    async function saveNetworkSelection() {
      const payload = { operator_code: document.getElementById("network_select").value };
      const data = await api("/api/modem/network", "POST", payload); notice(data.message || "选网设置已应用。"); await refreshStatus();
    }
    async function switchProfile(iccid, provider) {
      const data = await api("/api/profile/switch", "POST", { iccid });
      notice(`${provider} 切换完成。\n${data.message || ""}`);
      await refreshStatus().catch(() => {});
    }
    async function recoverModem() { const data = await api("/api/modem/recover", "POST", {}); notice(data.message || "基带恢复完成。"); await refreshStatus(); }
    async function restartSms() { const data = await api("/api/service/restart-sms", "POST", {}); notice(data.message || "短信转发服务已重启。"); await refreshStatus(); }
    refreshStatus().catch((error) => { notice(error.message || String(error)); });
    setInterval(() => { refreshStatus().catch(() => {}); }, 15000);
  </script>
</body>
</html>
"""


class AppHandler(BaseHTTPRequestHandler):
    server_version = "4GWiFiAdmin/1.0"

    def _write_json(self, code: int, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _write_html(self, html: str) -> None:
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._write_html(INDEX_HTML)
            return
        if path == "/api/status":
            try:
                self._write_json(200, get_status())
            except Exception as exc:
                self._write_json(500, {"error": str(exc)})
            return
        self._write_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            data = self._read_json_body()
            if path == "/api/profile/switch":
                iccid = data.get("iccid", "").strip()
                if not iccid:
                    raise ValueError("缺少 ICCID")
                result = switch_profile(iccid)
                self._write_json(200, {"ok": True, **result})
                return
            if path == "/api/apn":
                apn = data.get("apn", "").strip()
                ip_type = data.get("ip_type", "ipv4").strip()
                if not apn:
                    raise ValueError("APN 不能为空")
                message = "\n".join(
                    apply_apn_settings(apn, data.get("username", ""), data.get("password", ""), ip_type)
                )
                self._write_json(200, {"ok": True, "message": message})
                return
            if path == "/api/bark":
                base_url = data.get("base_url", "").strip()
                device_key = data.get("device_key", "").strip()
                if not base_url or not device_key:
                    raise ValueError("Bark 地址和 Device Key 不能为空")
                message = "\n".join(
                    save_bark_config(base_url, device_key, data.get("group", "sms"), data.get("level", "active"))
                )
                self._write_json(200, {"ok": True, "message": message})
                return
            if path == "/api/modem/recover":
                self._write_json(200, {"ok": True, "message": "\n".join(recover_modem())})
                return
            if path == "/api/modem/mode":
                mode = data.get("mode", "").strip()
                if not mode:
                    raise ValueError("缺少制式参数")
                self._write_json(200, {"ok": True, "message": "\n".join(apply_radio_mode(mode))})
                return
            if path == "/api/modem/network":
                operator_code = data.get("operator_code", "").strip()
                self._write_json(200, {"ok": True, "message": "\n".join(apply_network_selection(operator_code))})
                return
            if path == "/api/service/restart-sms":
                self._write_json(200, {"ok": True, "message": "\n".join(restart_sms_service())})
                return
            self._write_json(404, {"error": "Not found"})
        except Exception as exc:
            self._write_json(500, {"error": str(exc)})


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print(f"4G WiFi admin listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
