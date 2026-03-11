#!/bin/sh
set -eu

LPAC_BIN="${LPAC_BIN:-/usr/local/bin/lpac}"
LPAC_LD_LIBRARY_PATH="${LPAC_LD_LIBRARY_PATH:-/opt/lpac/lib}"
LPAC_APDU="${LPAC_APDU:-qmi}"
LPAC_HTTP="${LPAC_HTTP:-curl}"
LPAC_APDU_QMI_DEVICE="${LPAC_APDU_QMI_DEVICE:-/dev/wwan0qmi0}"
LPAC_APDU_QMI_UIM_SLOT="${LPAC_APDU_QMI_UIM_SLOT:-1}"
LPAC_APDU_AT_DEVICE="${LPAC_APDU_AT_DEVICE:-/dev/wwan0at0}"

export LPAC_APDU
export LPAC_HTTP
export LPAC_APDU_QMI_DEVICE
export LPAC_APDU_QMI_UIM_SLOT
export LPAC_APDU_AT_DEVICE
export LD_LIBRARY_PATH="${LPAC_LD_LIBRARY_PATH}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

usage() {
    cat <<'EOF'
Usage:
  lpac-switch.sh info
  lpac-switch.sh list
  lpac-switch.sh enable <ICCID-or-AID> [refresh_flag]
  lpac-switch.sh disable <ICCID-or-AID> [refresh_flag]
  lpac-switch.sh notifications
  lpac-switch.sh process-notifications

Environment overrides:
  LPAC_BIN
  LPAC_LD_LIBRARY_PATH=/opt/lpac/lib
  LPAC_APDU=qmi|at|at_csim
  LPAC_APDU_QMI_DEVICE=/dev/wwan0qmi0
  LPAC_APDU_QMI_UIM_SLOT=1
  LPAC_APDU_AT_DEVICE=/dev/wwan0at0
EOF
}

run_lpac() {
    "${LPAC_BIN}" "$@"
}

cmd="${1:-}"

case "${cmd}" in
    info)
        shift
        run_lpac chip info "$@"
        ;;
    list)
        shift
        run_lpac profile list "$@"
        ;;
    enable)
        target="${2:-}"
        refresh="${3:-1}"
        [ -n "${target}" ] || { usage; exit 1; }
        run_lpac profile enable "${target}" "${refresh}"
        ;;
    disable)
        target="${2:-}"
        refresh="${3:-1}"
        [ -n "${target}" ] || { usage; exit 1; }
        run_lpac profile disable "${target}" "${refresh}"
        ;;
    notifications)
        shift
        run_lpac notification list "$@"
        ;;
    process-notifications)
        shift
        run_lpac notification process -a -r "$@"
        ;;
    *)
        usage
        exit 1
        ;;
esac
