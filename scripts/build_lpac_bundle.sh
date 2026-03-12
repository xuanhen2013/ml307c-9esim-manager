#!/bin/sh
set -eu

SOURCE_DIR=""
OUTPUT=""
CMAKE_BIN="${CMAKE_BIN:-cmake}"
BUILD_DIR=""
PKG_DIR=""

usage() {
    cat <<'EOF'
Usage:
  sh ./scripts/build_lpac_bundle.sh --source-dir <lpac-source> --output <bundle.zip>

Environment:
  CMAKE_BIN=cmake
EOF
}

cleanup() {
    if [ -n "${BUILD_DIR}" ] && [ -d "${BUILD_DIR}" ]; then
        rm -rf "${BUILD_DIR}"
    fi
    if [ -n "${PKG_DIR}" ] && [ -d "${PKG_DIR}" ]; then
        rm -rf "${PKG_DIR}"
    fi
}

die() {
    printf '%s\n' "[build-lpac] $*" >&2
    exit 1
}

log() {
    printf '%s\n' "[build-lpac] $*"
}

parse_args() {
    while [ $# -gt 0 ]; do
        case "$1" in
            --source-dir)
                [ $# -ge 2 ] || die "--source-dir 缺少参数"
                SOURCE_DIR=$2
                shift 2
                ;;
            --output)
                [ $# -ge 2 ] || die "--output 缺少参数"
                OUTPUT=$2
                shift 2
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

    [ -n "${SOURCE_DIR}" ] || die "请提供 --source-dir"
    [ -n "${OUTPUT}" ] || die "请提供 --output"
    [ -d "${SOURCE_DIR}" ] || die "lpac 源码目录不存在: ${SOURCE_DIR}"
}

require_tools() {
    command -v "${CMAKE_BIN}" >/dev/null 2>&1 || die "未找到 cmake: ${CMAKE_BIN}"
    command -v zip >/dev/null 2>&1 || die "未找到 zip"
    command -v make >/dev/null 2>&1 || die "未找到 make"
}

main() {
    parse_args "$@"
    require_tools
    trap cleanup EXIT INT TERM

    SOURCE_DIR=$(CDPATH= cd -- "${SOURCE_DIR}" && pwd)
    OUTPUT_DIR=$(CDPATH= cd -- "$(dirname -- "${OUTPUT}")" && pwd)
    OUTPUT_PATH="${OUTPUT_DIR}/$(basename -- "${OUTPUT}")"

    BUILD_DIR=$(mktemp -d /tmp/lpac-build.XXXXXX)
    PKG_DIR=$(mktemp -d /tmp/lpac-pkg.XXXXXX)

    log "配置 lpac 构建目录"
    "${CMAKE_BIN}" -S "${SOURCE_DIR}" -B "${BUILD_DIR}" \
        -DSTANDALONE_MODE=ON \
        -DLPAC_WITH_APDU_AT=ON \
        -DLPAC_WITH_APDU_QMI=ON

    log "编译 lpac"
    "${CMAKE_BIN}" --build "${BUILD_DIR}" --parallel

    log "安装到临时打包目录"
    DESTDIR="${PKG_DIR}" "${CMAKE_BIN}" --install "${BUILD_DIR}"

    BUNDLE_ROOT="${PKG_DIR}/executables"
    [ -f "${BUNDLE_ROOT}/lpac" ] || die "未找到打包后的 lpac 主程序"

    mkdir -p "${OUTPUT_DIR}"
    rm -f "${OUTPUT_PATH}"

    log "生成 bundle: ${OUTPUT_PATH}"
    (
        cd "${BUNDLE_ROOT}"
        zip -qr "${OUTPUT_PATH}" ./*
    )

    log "完成"
}

main "$@"
