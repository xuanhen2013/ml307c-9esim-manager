#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)

WEB_ADMIN_SRC="${SCRIPT_DIR}/web_admin/4g_wifi_admin.py"
WEB_ADMIN_SERVICE_SRC="${SCRIPT_DIR}/web_admin/4g-wifi-admin.service"
FRONTEND_DIST_SRC="${SCRIPT_DIR}/web_admin/frontend_dist"
SMS_FORWARDER_SRC="${SCRIPT_DIR}/sms_bark/sms_forwarder.py"
SMS_SERVICE_SRC="${SCRIPT_DIR}/sms_bark/sms-bark-forwarder.service"
SMS_CONFIG_EXAMPLE_SRC="${SCRIPT_DIR}/sms_bark/sms-bark-forwarder.conf.example"
LPAC_SWITCH_SRC="${SCRIPT_DIR}/esim/lpac-switch.sh"
LPAC_WRAPPER_SRC="${SCRIPT_DIR}/esim/lpac"
LPAC_BUNDLE_AARCH64_SRC="${SCRIPT_DIR}/esim/lpac-linux-aarch64-with-qmi.zip"

WEB_ADMIN_DST="/usr/local/bin/4g_wifi_admin.py"
SMS_FORWARDER_DST="/usr/local/bin/sms_forwarder.py"
LPAC_SWITCH_DST="/usr/local/bin/lpac-switch"
LPAC_WRAPPER_DST="/usr/local/bin/lpac"
FRONTEND_DIST_DST="/usr/local/bin/frontend_dist"
WEB_ADMIN_SERVICE_DST="/etc/systemd/system/4g-wifi-admin.service"
SMS_SERVICE_DST="/etc/systemd/system/sms-bark-forwarder.service"
SMS_CONFIG_DST="/etc/sms-bark-forwarder.conf"
LPAC_HOME_DST="/opt/lpac"

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

copy_frontend_dist() {
    require_file "${FRONTEND_DIST_SRC}/index.html"
    rm -rf "${FRONTEND_DIST_DST}"
    mkdir -p "${FRONTEND_DIST_DST}"
    cp -a "${FRONTEND_DIST_SRC}/." "${FRONTEND_DIST_DST}/"
}

check_environment() {
    ARCH=$(uname -m 2>/dev/null || echo unknown)
    OS_ID=unknown
    OS_VERSION=unknown

    if [ -r /etc/os-release ]; then
        OS_ID=$(sed -n 's/^ID=//p' /etc/os-release | tr -d '"')
        OS_VERSION=$(sed -n 's/^VERSION_ID=//p' /etc/os-release | tr -d '"')
    fi

    log "环境检查: 架构=${ARCH}, 系统=${OS_ID}, 版本=${OS_VERSION}"

    if ! command -v systemctl >/dev/null 2>&1; then
        die "未检测到 systemctl，当前系统不支持 systemd 部署方式"
    fi

    if [ ! -d /run/systemd/system ]; then
        warn "systemd 运行目录不存在，服务安装后可能无法立即启动"
    fi

    case "${OS_ID}" in
        debian|ubuntu)
            ;;
        *)
            warn "当前系统不是 Debian/Ubuntu，自动安装依赖步骤可能不适配"
            ;;
    esac
}

ensure_config() {
    if [ -f "${SMS_CONFIG_DST}" ]; then
        log "保留现有 Bark 配置: ${SMS_CONFIG_DST}"
        return
    fi

    install -m 600 "${SMS_CONFIG_EXAMPLE_SRC}" "${SMS_CONFIG_DST}"
    log "已创建 Bark 配置模板: ${SMS_CONFIG_DST}"
    warn "请编辑 ${SMS_CONFIG_DST}，填入正确的 BARK_BASE_URL 和 BARK_DEVICE_KEY"
}

config_ready() {
    [ -f "${SMS_CONFIG_DST}" ] || return 1
    if grep -Eq '^BARK_DEVICE_KEY=replace-with-your-bark-key$' "${SMS_CONFIG_DST}"; then
        return 1
    fi
    if grep -Eq '^BARK_BASE_URL=https://api\.day\.app$' "${SMS_CONFIG_DST}"; then
        return 1
    fi
    if ! grep -Eq '^BARK_DEVICE_KEY=.+' "${SMS_CONFIG_DST}"; then
        return 1
    fi
    if ! grep -Eq '^BARK_BASE_URL=.+' "${SMS_CONFIG_DST}"; then
        return 1
    fi
    return 0
}

show_dependency_warnings() {
    for cmd in python3 systemctl mmcli nmcli qmicli; do
        if ! command -v "${cmd}" >/dev/null 2>&1; then
            warn "未检测到命令 ${cmd}，相关功能可能无法正常工作"
        fi
    done

    if [ ! -x /opt/lpac/bin/lpac ]; then
        warn "未检测到 /opt/lpac/bin/lpac，eSIM 切卡功能暂时不可用"
    fi
}

install_system_packages() {
    missing_packages=""

    if ! command -v python3 >/dev/null 2>&1; then
        missing_packages="${missing_packages} python3"
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

install_lpac() {
    ARCH=$(uname -m 2>/dev/null || echo unknown)

    if [ -x "${LPAC_HOME_DST}/bin/lpac" ]; then
        log "检测到已安装 lpac: ${LPAC_HOME_DST}/bin/lpac"
        return
    fi

    case "${ARCH}" in
        aarch64|arm64)
            require_file "${LPAC_BUNDLE_AARCH64_SRC}"
            ;;
        *)
            warn "当前架构 ${ARCH} 没有内置 lpac 安装包，跳过自动安装"
            return
            ;;
    esac

    log "自动安装 lpac 到 ${LPAC_HOME_DST}"
    tmp_dir=$(mktemp -d /tmp/lpac-install.XXXXXX)
    extract_lpac_bundle "${LPAC_BUNDLE_AARCH64_SRC}" "${tmp_dir}"

    mkdir -p "${LPAC_HOME_DST}/bin" "${LPAC_HOME_DST}/share/licenses"
    install -m 755 "${tmp_dir}/lpac" "${LPAC_HOME_DST}/bin/lpac"

    for license_name in LICENSE-cjson LICENSE-dlfcn-win32 LICENSE-libeuicc LICENSE-lpac; do
        if [ -f "${tmp_dir}/${license_name}" ]; then
            install -m 644 "${tmp_dir}/${license_name}" "${LPAC_HOME_DST}/share/licenses/${license_name}"
        fi
    done

    if [ -f "${tmp_dir}/README.md" ]; then
        install -m 644 "${tmp_dir}/README.md" "${LPAC_HOME_DST}/README.md"
    fi

    rm -rf "${tmp_dir}"
    log "lpac 安装完成"
}

main() {
    require_root

    require_file "${WEB_ADMIN_SRC}"
    require_file "${WEB_ADMIN_SERVICE_SRC}"
    require_file "${SMS_FORWARDER_SRC}"
    require_file "${SMS_SERVICE_SRC}"
    require_file "${SMS_CONFIG_EXAMPLE_SRC}"
    require_file "${LPAC_SWITCH_SRC}"
    require_file "${LPAC_WRAPPER_SRC}"

    check_environment
    install_system_packages
    install_lpac

    mkdir -p /usr/local/bin /etc/systemd/system

    log "安装管理服务脚本"
    install_file "${WEB_ADMIN_SRC}" "${WEB_ADMIN_DST}" 755
    install_file "${SMS_FORWARDER_SRC}" "${SMS_FORWARDER_DST}" 755
    install_file "${LPAC_SWITCH_SRC}" "${LPAC_SWITCH_DST}" 755
    install_file "${LPAC_WRAPPER_SRC}" "${LPAC_WRAPPER_DST}" 755

    log "同步前端静态资源"
    copy_frontend_dist

    log "安装 systemd 服务"
    install_file "${WEB_ADMIN_SERVICE_SRC}" "${WEB_ADMIN_SERVICE_DST}" 644
    install_file "${SMS_SERVICE_SRC}" "${SMS_SERVICE_DST}" 644

    ensure_config
    show_dependency_warnings

    log "重载 systemd"
    systemctl daemon-reload

    log "启用服务"
    systemctl enable 4g-wifi-admin.service >/dev/null
    systemctl enable sms-bark-forwarder.service >/dev/null

    log "重启管理服务"
    systemctl restart 4g-wifi-admin.service

    if config_ready; then
        log "Bark 配置已就绪，重启短信转发服务"
        systemctl restart sms-bark-forwarder.service
    else
        warn "Bark 配置仍是示例值，已跳过启动 sms-bark-forwarder.service"
        warn "完成配置后可执行: systemctl restart sms-bark-forwarder.service"
    fi

    log "部署完成"
    log "项目目录: ${PROJECT_DIR}"
    log "管理页面: http://<device-ip>:8080/"
}

main "$@"
