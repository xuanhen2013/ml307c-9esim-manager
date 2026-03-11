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

WEB_ADMIN_DST="/usr/local/bin/4g_wifi_admin.py"
SMS_FORWARDER_DST="/usr/local/bin/sms_forwarder.py"
LPAC_SWITCH_DST="/usr/local/bin/lpac-switch"
LPAC_WRAPPER_DST="/usr/local/bin/lpac"
FRONTEND_DIST_DST="/usr/local/bin/frontend_dist"
WEB_ADMIN_SERVICE_DST="/etc/systemd/system/4g-wifi-admin.service"
SMS_SERVICE_DST="/etc/systemd/system/sms-bark-forwarder.service"
SMS_CONFIG_DST="/etc/sms-bark-forwarder.conf"

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

main() {
    require_root

    require_file "${WEB_ADMIN_SRC}"
    require_file "${WEB_ADMIN_SERVICE_SRC}"
    require_file "${SMS_FORWARDER_SRC}"
    require_file "${SMS_SERVICE_SRC}"
    require_file "${SMS_CONFIG_EXAMPLE_SRC}"
    require_file "${LPAC_SWITCH_SRC}"
    require_file "${LPAC_WRAPPER_SRC}"

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
