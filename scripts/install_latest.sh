#!/bin/sh
set -eu

REPO_OWNER="${REPO_OWNER:-cyDione}"
REPO_NAME="${REPO_NAME:-eSIM-SMS-Forwarder}"
ASSET_NAME="${ASSET_NAME:-eSIM-SMS-Forwarder-deploy.zip}"

TMP_DIR=""
INSTALL_ARGS=""

log() {
    printf '%s\n' "[bootstrap] $*"
}

warn() {
    printf '%s\n' "[bootstrap] $*" >&2
}

die() {
    printf '%s\n' "[bootstrap] $*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage:
  curl -fsSL <url> | sudo sh -s -- [--sim-type esim|physical]

Options:
  --sim-type esim      默认模式，启用 eSIM 管理与短信转发
  --sim-type physical  普通 SIM 模式，只启用短信相关功能
EOF
}

cleanup() {
    if [ -n "${TMP_DIR}" ] && [ -d "${TMP_DIR}" ]; then
        rm -rf "${TMP_DIR}"
    fi
}

require_root() {
    if [ "$(id -u)" != "0" ]; then
        die "请使用 root 运行，例如：curl -fsSL <url> | sudo sh"
    fi
}

download_file() {
    url=$1
    output=$2
    if command -v curl >/dev/null 2>&1; then
        curl -fL --retry 2 --connect-timeout 15 --max-time 300 -o "${output}" "${url}"
        return 0
    fi
    if command -v wget >/dev/null 2>&1; then
        wget -O "${output}" "${url}"
        return 0
    fi
    die "缺少 curl 或 wget，无法下载安装包"
}

extract_zip() {
    archive=$1
    target_dir=$2
    if command -v unzip >/dev/null 2>&1; then
        unzip -q "${archive}" -d "${target_dir}"
        return 0
    fi
    if command -v python3 >/dev/null 2>&1; then
        python3 - "$archive" "$target_dir" <<'PY'
import sys
from zipfile import ZipFile

archive, target = sys.argv[1], sys.argv[2]
ZipFile(archive).extractall(target)
PY
        return 0
    fi
    die "缺少 unzip，且没有 python3，无法解压安装包"
}

ensure_extract_dependencies() {
    if command -v unzip >/dev/null 2>&1 || command -v python3 >/dev/null 2>&1; then
        return
    fi

    if command -v apt-get >/dev/null 2>&1; then
        log "安装解压所需依赖 python3"
        export DEBIAN_FRONTEND=noninteractive
        apt-get update
        apt-get install -y python3
        return
    fi

    die "缺少 unzip 和 python3，且无法自动安装"
}

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --sim-type)
                [ $# -ge 2 ] || die "--sim-type 缺少参数"
                INSTALL_ARGS="${INSTALL_ARGS} --sim-type $2"
                shift 2
                ;;
            --sim-type=*)
                INSTALL_ARGS="${INSTALL_ARGS} $1"
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
}

main() {
    parse_args "$@"
    require_root
    trap cleanup EXIT INT TERM

    TMP_DIR=$(mktemp -d /tmp/esim-sms-forwarder.XXXXXX)
    archive_path="${TMP_DIR}/${ASSET_NAME}"
    extract_dir="${TMP_DIR}/package"
    mkdir -p "${extract_dir}"

    release_url="https://github.com/${REPO_OWNER}/${REPO_NAME}/releases/latest/download/${ASSET_NAME}"
    source_url="https://codeload.github.com/${REPO_OWNER}/${REPO_NAME}/zip/refs/heads/main"

    log "尝试下载最新发布包"
    if download_file "${release_url}" "${archive_path}"; then
        log "已下载 Release 包"
    else
        warn "最新 Release 不可用，回退到 main 分支源码包"
        download_file "${source_url}" "${archive_path}"
    fi

    ensure_extract_dependencies

    log "解压安装包"
    if ! extract_zip "${archive_path}" "${extract_dir}"; then
        warn "Release 包解压失败，回退到 main 分支源码包"
        rm -f "${archive_path}"
        download_file "${source_url}" "${archive_path}"
        extract_zip "${archive_path}" "${extract_dir}"
    fi

    if [ -f "${extract_dir}/deploy/install.sh" ]; then
        package_root="${extract_dir}"
    else
        package_root=$(find "${extract_dir}" -mindepth 1 -maxdepth 1 -type d | head -n 1 || true)
    fi

    [ -n "${package_root}" ] || die "未找到解压后的项目目录"
    [ -f "${package_root}/deploy/install.sh" ] || die "安装包中缺少 deploy/install.sh"

    log "开始执行部署脚本"
    cd "${package_root}"
    # shellcheck disable=SC2086
    sh ./deploy/install.sh ${INSTALL_ARGS}
    log "安装完成"
}

main "$@"
