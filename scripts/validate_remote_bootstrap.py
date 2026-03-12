#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import paramiko


DEFAULT_SCRIPT_URL = (
    "https://raw.githubusercontent.com/"
    "cyDione/eSIM-SMS-Forwarder/main/scripts/install_latest.sh"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the public one-click install script on a remote Debian device",
    )
    parser.add_argument("--host", required=True, help="Remote host or IP")
    parser.add_argument("--user", default="root", help="Remote SSH username")
    parser.add_argument("--password", required=True, help="Remote SSH password")
    parser.add_argument(
        "--script-url",
        default=DEFAULT_SCRIPT_URL,
        help="Public bootstrap script URL to validate",
    )
    parser.add_argument(
        "--sim-type",
        default="esim",
        choices=("esim", "physical"),
        help="Pass-through --sim-type argument",
    )
    return parser.parse_args()


def build_remote_script(script_url: str, sim_type: str) -> str:
    quoted_url = script_url.replace('"', '\\"')
    return f"""#!/bin/bash
set -euo pipefail

ORIG_LPAC="/opt/lpac"
BACKUP_LPAC=""
VERIFY_LOG="/tmp/esim-sms-forwarder-validate-lpac.log"

restore_lpac() {{
    if [ -n "${{BACKUP_LPAC}}" ] && [ -d "${{BACKUP_LPAC}}" ]; then
        rm -rf "${{ORIG_LPAC}}"
        mv "${{BACKUP_LPAC}}" "${{ORIG_LPAC}}"
        echo "[validate] restored original /opt/lpac"
    fi
}}

trap 'status=$?; restore_lpac; exit $status' EXIT INT TERM

if [ -d "${{ORIG_LPAC}}" ]; then
    BACKUP_LPAC="/tmp/lpac-backup-$(date +%s)"
    mv "${{ORIG_LPAC}}" "${{BACKUP_LPAC}}"
    echo "[validate] moved /opt/lpac -> ${{BACKUP_LPAC}}"
else
    echo "[validate] /opt/lpac not present, skipping backup"
fi

curl -fsSL "{quoted_url}" | sh -s -- --sim-type {sim_type}

/usr/local/bin/lpac-switch list > "${{VERIFY_LOG}}"
cat "${{VERIFY_LOG}}"

restore_lpac
trap - EXIT INT TERM
"""


def run_remote_script(
    host: str,
    user: str,
    password: str,
    script_content: str,
) -> tuple[int, str, str]:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=password, timeout=20)
    try:
        sftp = ssh.open_sftp()
        remote_path = "/tmp/validate-esim-sms-forwarder-bootstrap.sh"
        with sftp.file(remote_path, "w") as file_obj:
            file_obj.write(script_content)
        sftp.chmod(remote_path, 0o755)

        stdin, stdout, stderr = ssh.exec_command(
            f"bash {remote_path}",
            get_pty=True,
            timeout=3600,
        )
        output = stdout.read().decode("utf-8", errors="replace")
        error = stderr.read().decode("utf-8", errors="replace")
        status = stdout.channel.recv_exit_status()
        return status, output, error
    finally:
        ssh.close()


def main() -> int:
    args = parse_args()
    script_content = build_remote_script(args.script_url, args.sim_type)
    status, output, error = run_remote_script(
        host=args.host,
        user=args.user,
        password=args.password,
        script_content=script_content,
    )

    if output:
        print(output, end="")
    if error:
        print(error, end="", file=sys.stderr)

    if status != 0:
        print(f"[validate] remote bootstrap failed with exit status {status}", file=sys.stderr)
        return status

    if '"code":0' not in output and '"code": 0' not in output:
        print("[validate] lpac-switch output did not contain a success code", file=sys.stderr)
        return 1

    print("[validate] bootstrap verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
