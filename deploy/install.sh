#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)

WEB_ADMIN_SRC="${SCRIPT_DIR}/web_admin/4g_wifi_admin.py"
WEB_ADMIN_SERVICE_SRC="${SCRIPT_DIR}/web_admin/4g-wifi-admin.service"
FRONTEND_DIST_SRC="${SCRIPT_DIR}/web_admin/frontend_dist"
SMS_FORWARDER_SRC="${SCRIPT_DIR}/sms_forwarder/sms_forwarder.py"
SMS_SERVICE_SRC="${SCRIPT_DIR}/sms_forwarder/sms-forwarder.service"
SMS_CONFIG_EXAMPLE_SRC="${SCRIPT_DIR}/sms_forwarder/sms-forwarder.conf.example"
NOTIFICATION_UTILS_SRC="${SCRIPT_DIR}/shared/notification_utils.py"
LPAC_SWITCH_SRC="${SCRIPT_DIR}/esim/lpac-switch.sh"
LPAC_WRAPPER_SRC="${SCRIPT_DIR}/esim/lpac"
LPAC_ASSETS_DIR="${SCRIPT_DIR}/esim"

WEB_ADMIN_DST="/usr/local/bin/4g_wifi_admin.py"
SMS_FORWARDER_DST="/usr/local/bin/sms_forwarder.py"
NOTIFICATION_UTILS_DST="/usr/local/bin/notification_utils.py"
LPAC_SWITCH_DST="/usr/local/bin/lpac-switch"
LPAC_WRAPPER_DST="/usr/local/bin/lpac"
FRONTEND_DIST_DST="/usr/local/bin/frontend_dist"
WEB_ADMIN_SERVICE_DST="/etc/systemd/system/4g-wifi-admin.service"
SMS_SERVICE_DST="/etc/systemd/system/sms-forwarder.service"
SMS_CONFIG_DST="/etc/sms-forwarder.conf"
LEGACY_SMS_SERVICE_DST="/etc/systemd/system/sms-bark-forwarder.service"
LEGACY_SMS_CONFIG_DST="/etc/sms-bark-forwarder.conf"
APP_CONFIG_DST="/etc/esim-sms-forwarder.conf"
LPAC_HOME_DST="/opt/lpac"
RUNTIME_HOME_DST="/opt/esim-sms-forwarder"
RUNTIME_VENV_DST="${RUNTIME_HOME_DST}/venv"

REPO_OWNER="${REPO_OWNER:-cyDione}"
REPO_NAME="${REPO_NAME:-eSIM-SMS-Forwarder}"
LPAC_MANIFEST_NAME="${LPAC_MANIFEST_NAME:-lpac-assets.json}"
LPAC_RELEASE_BASE_URL="${LPAC_RELEASE_BASE_URL:-https://github.com/${REPO_OWNER}/${REPO_NAME}/releases/latest/download}"
LPAC_AUTO_DOWNLOAD="${LPAC_AUTO_DOWNLOAD:-1}"

SIM_TYPE="esim"
ARCH="unknown"
OS_ID="unknown"
OS_VERSION="unknown"
GLIBC_VERSION=""
TMP_DIR=""

log() {
    printf '%s\n' "[install] $*"
}

warn() {
    printf '%s\n' "[warn] $*" >&2
}

die() {
    printf '%s\n' "[error] $*" >&2
    exit 1
}

cleanup() {
    if [ -n "${TMP_DIR}" ] && [ -d "${TMP_DIR}" ]; then
        rm -rf "${TMP_DIR}"
    fi
}

usage() {
    cat <<'EOF'
Usage:
  sh ./deploy/install.sh [--sim-type esim|physical]

Options:
  --sim-type esim      默认模式，启用 eSIM 管理与短信转发
  --sim-type physical  普通 SIM 模式，只启用短信相关功能

Environment:
  REPO_OWNER / REPO_NAME
  LPAC_RELEASE_BASE_URL
  LPAC_AUTO_DOWNLOAD=1
EOF
}

require_root() {
    if [ "$(id -u)" != "0" ]; then
        die "请用 root 运行此脚本"
    fi
}

require_file() {
    [ -e "$1" ] || die "缺少文件: $1"
}

install_file() {
    src=$1
    dst=$2
    mode=$3
    install -m "$mode" "$src" "$dst"
}

download_file() {
    url=$1
    output=$2
    if command -v curl >/dev/null 2>&1; then
        if curl -fL --retry 2 --connect-timeout 15 --max-time 120 -o "${output}" "${url}"; then
            return 0
        fi
    fi
    if command -v wget >/dev/null 2>&1; then
        if wget -O "${output}" "${url}"; then
            return 0
        fi
    fi
    return 1
}

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --sim-type)
                [ $# -ge 2 ] || die "--sim-type 缺少参数"
                SIM_TYPE=$2
                shift 2
                ;;
            --sim-type=*)
                SIM_TYPE=${1#*=}
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                die "不支持的参数: $1"
                ;;
        esac
    done

    case "${SIM_TYPE}" in
        esim|physical)
            ;;
        *)
            die "--sim-type 只支持 esim 或 physical"
            ;;
    esac
}

copy_frontend_dist() {
    require_file "${FRONTEND_DIST_SRC}/index.html"
    rm -rf "${FRONTEND_DIST_DST}"
    mkdir -p "${FRONTEND_DIST_DST}"
    cp -a "${FRONTEND_DIST_SRC}/." "${FRONTEND_DIST_DST}/"
}

normalize_arch() {
    case "$1" in
        aarch64|arm64)
            printf '%s' "aarch64"
            ;;
        x86_64|amd64)
            printf '%s' "x86_64"
            ;;
        *)
            printf '%s' "$1"
            ;;
    esac
}

detect_glibc_version() {
    if command -v getconf >/dev/null 2>&1; then
        version=$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}')
        if [ -n "${version}" ]; then
            printf '%s' "${version}"
            return
        fi
    fi
    if command -v ldd >/dev/null 2>&1; then
        version=$(ldd --version 2>/dev/null | head -n 1 | sed -E 's/.* ([0-9]+\.[0-9]+).*/\1/')
        if [ -n "${version}" ]; then
            printf '%s' "${version}"
            return
        fi
    fi
    printf '%s' ""
}

check_environment() {
    ARCH=$(normalize_arch "$(uname -m 2>/dev/null || echo unknown)")

    if [ -r /etc/os-release ]; then
        OS_ID=$(sed -n 's/^ID=//p' /etc/os-release | tr -d '"')
        OS_VERSION=$(sed -n 's/^VERSION_ID=//p' /etc/os-release | tr -d '"')
    fi

    GLIBC_VERSION=$(detect_glibc_version)

    log "环境检查: 架构=${ARCH}, 系统=${OS_ID}, 版本=${OS_VERSION}, glibc=${GLIBC_VERSION:-unknown}"

    if ! command -v systemctl >/dev/null 2>&1; then
        die "未检测到 systemctl，当前系统不支持 systemd 部署方式"
    fi

    if [ ! -d /run/systemd/system ]; then
        warn "systemd 运行目录不存在，服务安装后可能无法立刻启动"
    fi

    case "${OS_ID}" in
        debian|ubuntu)
            ;;
        *)
            warn "当前系统不是 Debian/Ubuntu，自动安装依赖步骤可能不适配"
            ;;
    esac

    log "安装模式: ${SIM_TYPE}"
}

ensure_config() {
    if [ ! -f "${SMS_CONFIG_DST}" ] && [ -f "${LEGACY_SMS_CONFIG_DST}" ]; then
        cp -f "${LEGACY_SMS_CONFIG_DST}" "${SMS_CONFIG_DST}"
        chmod 600 "${SMS_CONFIG_DST}"
        log "已迁移旧通知配置到: ${SMS_CONFIG_DST}"
    fi
    if [ -f "${SMS_CONFIG_DST}" ]; then
        log "保留现有通知配置: ${SMS_CONFIG_DST}"
        return
    fi

    install -m 600 "${SMS_CONFIG_EXAMPLE_SRC}" "${SMS_CONFIG_DST}"
    log "已创建通知配置模板: ${SMS_CONFIG_DST}"
    warn "请编辑 ${SMS_CONFIG_DST}，填入至少一个 Apprise 通知渠道"
}

config_ready() {
    [ -f "${SMS_CONFIG_DST}" ] || return 1
    python3 - "${SMS_CONFIG_DST}" <<'PY'
import json
import sys
from pathlib import Path

config = {}
for raw_line in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    config[key.strip()] = value.strip().strip("\"'")

raw_targets = config.get("NOTIFICATION_TARGETS_JSON", "").strip()
if raw_targets:
    try:
        targets = json.loads(raw_targets)
    except Exception:
        raise SystemExit(1)
    if isinstance(targets, dict):
        targets = targets.get("targets", [])
    ready = any(
        isinstance(target, dict)
        and str(target.get("url", "")).strip()
        and str(target.get("enabled", True)).strip().lower() not in {"0", "false", "no", "off"}
        for target in targets
    )
    raise SystemExit(0 if ready else 1)

bark_base_url = config.get("BARK_BASE_URL", "").strip()
bark_device_key = config.get("BARK_DEVICE_KEY", "").strip()
legacy_ready = bark_base_url and bark_device_key and bark_device_key != "replace-with-your-bark-key"
raise SystemExit(0 if legacy_ready else 1)
PY
}

show_dependency_warnings() {
    for cmd in python3 systemctl mmcli nmcli qmicli; do
        if ! command -v "${cmd}" >/dev/null 2>&1; then
            warn "未检测到命令 ${cmd}，相关功能可能无法正常工作"
        fi
    done

    if [ ! -x "${RUNTIME_VENV_DST}/bin/python" ]; then
        warn "未检测到 Python 虚拟环境: ${RUNTIME_VENV_DST}"
    elif ! "${RUNTIME_VENV_DST}/bin/python" -c "import apprise" >/dev/null 2>&1; then
        warn "Apprise 尚未安装到运行环境中"
    fi

    if [ "${SIM_TYPE}" = "physical" ]; then
        return
    fi

    if ! lpac_binary_usable; then
        warn "未检测到可用的 lpac，可稍后重新执行安装或补充对应系统版本的 lpac 资产"
    fi
}

write_app_config() {
    esim_enabled=1
    if [ "${SIM_TYPE}" = "physical" ]; then
        esim_enabled=0
    fi

    cat > "${APP_CONFIG_DST}" <<EOF
SIM_TYPE=${SIM_TYPE}
ESIM_MANAGEMENT_ENABLED=${esim_enabled}
EOF

    chmod 644 "${APP_CONFIG_DST}"
    log "已写入安装模式配置: ${APP_CONFIG_DST}"
}

service_status() {
    service_name=$1
    if systemctl is-active "${service_name}" >/dev/null 2>&1; then
        printf '%s' "active"
    else
        systemctl is-active "${service_name}" 2>/dev/null || printf '%s' "unknown"
    fi
}

migrate_legacy_sms_service() {
    if systemctl list-unit-files 2>/dev/null | grep -q '^sms-bark-forwarder\.service'; then
        systemctl disable sms-bark-forwarder.service >/dev/null 2>&1 || true
        systemctl stop sms-bark-forwarder.service >/dev/null 2>&1 || true
    fi
    rm -f "${LEGACY_SMS_SERVICE_DST}"
}

detect_access_url() {
    if command -v hostname >/dev/null 2>&1; then
        first_ip=$(hostname -I 2>/dev/null | awk '{print $1}')
        if [ -n "${first_ip}" ]; then
            printf '%s' "http://${first_ip}:8080/"
            return
        fi
    fi
    printf '%s' "http://<device-ip>:8080/"
}

print_install_summary() {
    admin_state=$(service_status 4g-wifi-admin.service)
    sms_state=$(service_status sms-forwarder.service)
    access_url=$(detect_access_url)

    if lpac_binary_usable; then
        lpac_state="已安装"
    else
        lpac_state="未安装或不可用"
    fi

    if config_ready; then
        notification_state="已配置"
    else
        notification_state="未配置"
    fi

    printf '\n'
    printf '%s\n' "========== 安装摘要 =========="
    printf '%s\n' "管理页面: ${access_url}"
    printf '%s\n' "4g-wifi-admin.service: ${admin_state}"
    printf '%s\n' "sms-forwarder.service: ${sms_state}"
    printf '%s\n' "安装模式: ${SIM_TYPE}"
    printf '%s\n' "lpac: ${lpac_state}"
    printf '%s\n' "通知渠道: ${notification_state}"
    printf '%s\n' "配置文件: ${SMS_CONFIG_DST}"
    if [ "${SIM_TYPE}" = "esim" ]; then
        printf '%s\n' "切卡命令: /usr/local/bin/lpac-switch list"
    else
        printf '%s\n' "切卡命令: 当前为普通 SIM 模式，已禁用"
    fi
    printf '%s\n' "查看短信: mmcli -m any --messaging-list-sms"
    printf '%s\n' "================================"
}

install_system_packages() {
    missing_packages=""

    if ! command -v python3 >/dev/null 2>&1; then
        missing_packages="${missing_packages} python3"
    fi
    if ! python3 -m venv --help >/dev/null 2>&1; then
        missing_packages="${missing_packages} python3-venv"
    fi
    if ! command -v mmcli >/dev/null 2>&1; then
        missing_packages="${missing_packages} modemmanager"
    fi
    if ! command -v nmcli >/dev/null 2>&1; then
        missing_packages="${missing_packages} network-manager"
    fi
    if ! command -v qmicli >/dev/null 2>&1; then
        missing_packages="${missing_packages} libqmi-utils"
    fi
    if ! command -v unzip >/dev/null 2>&1; then
        missing_packages="${missing_packages} unzip"
    fi
    if ! command -v curl >/dev/null 2>&1; then
        missing_packages="${missing_packages} curl ca-certificates"
    fi
    if ! command -v lsb_release >/dev/null 2>&1; then
        missing_packages="${missing_packages} lsb-release"
    fi

    if [ -z "${missing_packages}" ]; then
        return
    fi

    if ! command -v apt-get >/dev/null 2>&1; then
        warn "未检测到 apt-get，无法自动安装依赖:${missing_packages}"
        return
    fi

    log "安装系统依赖:${missing_packages}"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y ${missing_packages}
}

repair_lsb_release() {
    if command -v lsb_release >/dev/null 2>&1 && lsb_release -a >/dev/null 2>&1; then
        return
    fi
    if ! command -v apt-get >/dev/null 2>&1; then
        return
    fi
    warn "检测到 lsb_release 异常，尝试自动修复"
    export DEBIAN_FRONTEND=noninteractive
    apt-get install --reinstall -y lsb-release || true
}

setup_runtime_env() {
    mkdir -p "${RUNTIME_HOME_DST}"

    if [ ! -x "${RUNTIME_VENV_DST}/bin/python" ]; then
        log "创建 Python 虚拟环境: ${RUNTIME_VENV_DST}"
        venv_log="${TMP_DIR}/venv-create.log"
        mkdir -p "${TMP_DIR}"
        if ! python3 -m venv "${RUNTIME_VENV_DST}" >"${venv_log}" 2>&1; then
            repair_lsb_release
            rm -rf "${RUNTIME_VENV_DST}"
            if ! python3 -m venv "${RUNTIME_VENV_DST}" >>"${venv_log}" 2>&1; then
                cat "${venv_log}" >&2 || true
                die "Python 虚拟环境创建失败"
            fi
        fi
    fi

    if ! "${RUNTIME_VENV_DST}/bin/python" -c "import apprise" >/dev/null 2>&1; then
        log "安装 Apprise 到运行环境"
        "${RUNTIME_VENV_DST}/bin/python" -m pip install --upgrade pip >/dev/null
        "${RUNTIME_VENV_DST}/bin/python" -m pip install apprise
    else
        log "运行环境中已存在 Apprise"
    fi
}

extract_lpac_bundle() {
    archive=$1
    target_dir=$2
    mkdir -p "${target_dir}"

    if command -v unzip >/dev/null 2>&1; then
        unzip -oq "${archive}" -d "${target_dir}"
        return 0
    fi

    python3 - "$archive" "$target_dir" <<'PY'
import sys
from zipfile import ZipFile

archive, target = sys.argv[1], sys.argv[2]
ZipFile(archive).extractall(target)
PY
}

version_le() {
    [ "$1" = "$2" ] && return 0
    first=$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -n 1)
    [ "${first}" = "$1" ]
}

lpac_binary_usable() {
    if [ ! -x "${LPAC_HOME_DST}/lpac" ]; then
        return 1
    fi
    output=$(LD_LIBRARY_PATH="${LPAC_HOME_DST}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" "${LPAC_HOME_DST}/lpac" 2>&1 || true)
    case "${output}" in
        *GLIBC_*not\ found*|*version\ \`GLIBC_*|*No\ such\ file\ or\ directory*)
            return 1
            ;;
    esac
    return 0
}

select_lpac_asset_from_manifest() {
    manifest_path=$1
    python3 - "${manifest_path}" "${ARCH}" "${OS_ID}" "${OS_VERSION}" "${GLIBC_VERSION}" <<'PY'
import json
import sys
from pathlib import Path

manifest_path, arch, os_id, os_version, glibc_version = sys.argv[1:]
payload = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
assets = payload.get("assets", [])

def parse_version(value: str):
    return tuple(int(part) for part in value.split(".") if part.isdigit())

glibc_current = parse_version(glibc_version) if glibc_version else None
best = None
best_score = None

for asset in assets:
    if asset.get("arch") != arch:
        continue

    asset_os = asset.get("os", "")
    asset_os_version = asset.get("os_version", "")
    asset_glibc = asset.get("glibc", "")

    if asset_os and asset_os != os_id:
        continue
    if asset_os_version and asset_os_version != os_version:
        continue
    if asset_glibc and glibc_current:
        if parse_version(asset_glibc) > glibc_current:
            continue

    glibc_score = parse_version(asset_glibc) if asset_glibc else tuple()
    score = (
        1 if asset_os else 0,
        1 if asset_os_version else 0,
        glibc_score,
        asset.get("name", ""),
    )
    if best is None or score > best_score:
        best = asset.get("name", "")
        best_score = score

if best:
    print(best)
PY
}

find_local_lpac_bundle() {
    local_manifest="${TMP_DIR}/lpac-local-assets.json"
    python3 - "${LPAC_ASSETS_DIR}" "${local_manifest}" <<'PY'
import json
import re
import sys
from pathlib import Path

assets_dir = Path(sys.argv[1])
output_path = Path(sys.argv[2])
pattern = re.compile(
    r"^lpac-linux-(?P<arch>[a-z0-9_]+)(?:-(?P<os>(?!glibc)[a-z]+)(?P<os_version>[0-9.]+))?(?:-glibc(?P<glibc>[0-9.]+))?\.zip$"
)
assets = []
if assets_dir.exists():
    for asset_path in sorted(assets_dir.glob("lpac-linux-*.zip")):
        match = pattern.match(asset_path.name)
        if not match:
            continue
        item = {"name": asset_path.name, "arch": match.group("arch")}
        if match.group("os"):
            item["os"] = match.group("os")
        if match.group("os_version"):
            item["os_version"] = match.group("os_version")
        if match.group("glibc"):
            item["glibc"] = match.group("glibc")
        assets.append(item)
output_path.write_text(json.dumps({"assets": assets}, ensure_ascii=False), encoding="utf-8")
PY
    asset_name=$(select_lpac_asset_from_manifest "${local_manifest}" || true)
    if [ -n "${asset_name}" ] && [ -f "${LPAC_ASSETS_DIR}/${asset_name}" ]; then
        printf '%s' "${LPAC_ASSETS_DIR}/${asset_name}"
        return
    fi
    printf '%s' ""
}

download_remote_lpac_bundle() {
    if [ "${LPAC_AUTO_DOWNLOAD}" != "1" ]; then
        return
    fi

    manifest_path="${TMP_DIR}/${LPAC_MANIFEST_NAME}"
    if ! download_file "${LPAC_RELEASE_BASE_URL}/${LPAC_MANIFEST_NAME}" "${manifest_path}"; then
        return
    fi

    asset_name=$(select_lpac_asset_from_manifest "${manifest_path}" || true)
    if [ -z "${asset_name}" ]; then
        return
    fi

    output_path="${TMP_DIR}/${asset_name}"
    log "下载匹配的 lpac 资产: ${asset_name}"
    if download_file "${LPAC_RELEASE_BASE_URL}/${asset_name}" "${output_path}"; then
        printf '%s' "${output_path}"
    fi
}

install_lpac_bundle() {
    archive=$1

    log "安装 lpac: $(basename "${archive}")"
    tmp_extract_dir=$(mktemp -d /tmp/lpac-install.XXXXXX)
    extract_lpac_bundle "${archive}" "${tmp_extract_dir}"

    bundle_root="${tmp_extract_dir}"
    if [ ! -f "${bundle_root}/lpac" ] && [ -f "${tmp_extract_dir}/executables/lpac" ]; then
        bundle_root="${tmp_extract_dir}/executables"
    fi

    [ -f "${bundle_root}/lpac" ] || die "lpac bundle 缺少主可执行文件"

    rm -rf "${LPAC_HOME_DST}"
    mkdir -p "${LPAC_HOME_DST}"
    cp -a "${bundle_root}/." "${LPAC_HOME_DST}/"
    chmod 755 "${LPAC_HOME_DST}/lpac"

    rm -rf "${tmp_extract_dir}"
}

install_lpac() {
    if [ "${SIM_TYPE}" = "physical" ]; then
        log "普通 SIM 模式已启用，跳过 lpac 安装"
        return
    fi

    if lpac_binary_usable; then
        log "检测到可用 lpac: ${LPAC_HOME_DST}/lpac"
        return
    fi

    if [ -x "${LPAC_HOME_DST}/lpac" ]; then
        warn "已有 lpac 不可用，尝试自动替换为匹配当前系统的版本"
    fi

    local_bundle=$(find_local_lpac_bundle || true)
    if [ -n "${local_bundle}" ]; then
        install_lpac_bundle "${local_bundle}"
    else
        remote_bundle=$(download_remote_lpac_bundle || true)
        if [ -n "${remote_bundle}" ]; then
            install_lpac_bundle "${remote_bundle}"
        else
            warn "未找到与当前系统匹配的 lpac 资产"
            warn "可在 release 中发布命名为 lpac-linux-${ARCH}-*.zip 的预编译包"
            return
        fi
    fi

    if lpac_binary_usable; then
        log "lpac 安装完成"
    else
        warn "lpac 已安装，但当前版本仍不可用，请检查发布的 glibc/系统版本是否匹配"
    fi
}

main() {
    parse_args "$@"
    require_root
    trap cleanup EXIT INT TERM
    TMP_DIR=$(mktemp -d /tmp/esim-sms-forwarder-install.XXXXXX)

    require_file "${WEB_ADMIN_SRC}"
    require_file "${WEB_ADMIN_SERVICE_SRC}"
    require_file "${SMS_FORWARDER_SRC}"
    require_file "${SMS_SERVICE_SRC}"
    require_file "${SMS_CONFIG_EXAMPLE_SRC}"
    require_file "${NOTIFICATION_UTILS_SRC}"
    require_file "${LPAC_SWITCH_SRC}"
    require_file "${LPAC_WRAPPER_SRC}"

    check_environment
    install_system_packages
    setup_runtime_env
    install_lpac

    mkdir -p /usr/local/bin /etc/systemd/system

    log "安装管理服务脚本"
    install_file "${WEB_ADMIN_SRC}" "${WEB_ADMIN_DST}" 755
    install_file "${SMS_FORWARDER_SRC}" "${SMS_FORWARDER_DST}" 755
    install_file "${NOTIFICATION_UTILS_SRC}" "${NOTIFICATION_UTILS_DST}" 644
    if [ "${SIM_TYPE}" = "esim" ]; then
        install_file "${LPAC_SWITCH_SRC}" "${LPAC_SWITCH_DST}" 755
        install_file "${LPAC_WRAPPER_SRC}" "${LPAC_WRAPPER_DST}" 755
    else
        rm -f "${LPAC_SWITCH_DST}" "${LPAC_WRAPPER_DST}"
    fi

    log "同步前端静态资源"
    copy_frontend_dist

    log "安装 systemd 服务"
    install_file "${WEB_ADMIN_SERVICE_SRC}" "${WEB_ADMIN_SERVICE_DST}" 644
    install_file "${SMS_SERVICE_SRC}" "${SMS_SERVICE_DST}" 644

    write_app_config
    ensure_config
    show_dependency_warnings

    log "重载 systemd"
    systemctl daemon-reload

    log "启用服务"
    systemctl enable 4g-wifi-admin.service >/dev/null
    migrate_legacy_sms_service
    systemctl enable sms-forwarder.service >/dev/null

    log "重启管理服务"
    systemctl restart 4g-wifi-admin.service

    if config_ready; then
        log "通知渠道配置已就绪，重启短信转发服务"
        systemctl restart sms-forwarder.service
    else
        warn "通知渠道尚未配置，已跳过启动 sms-forwarder.service"
        warn "完成配置后可执行: systemctl restart sms-forwarder.service"
    fi

    log "部署完成"
    log "项目目录: ${PROJECT_DIR}"
    log "管理页面: $(detect_access_url)"
    print_install_summary
}

main "$@"
