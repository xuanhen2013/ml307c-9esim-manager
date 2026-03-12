# 4G WiFi deployment assets

This folder contains the files deployed to the Debian device:

- `install.sh`: one-click installer for Debian. Copies files, installs systemd services, and starts the web admin.
- `esim/lpac-switch.sh`: wrapper around `lpac` for profile inspection and switching.
- `esim/lpac`: wrapper that points to `/opt/lpac/bin/lpac`.
- `sms_bark/sms_forwarder.py`: polls ModemManager for newly received SMS and forwards them through Apprise.
- `sms_bark/sms-bark-forwarder.service`: systemd unit for the SMS forwarder.
- `shared/notification_utils.py`: shared Apprise target parsing and delivery helpers.
- `sms_bark/sms-bark-forwarder.conf.example`: example configuration for Apprise targets.
- `web_admin/4g_wifi_admin.py`: lightweight backend that serves both the API and built frontend assets.
- `web_admin/4g-wifi-admin.service`: systemd unit for the web admin.

## lpac asset auto-selection

`deploy/install.sh` now supports automatic lpac selection from GitHub Releases.

Installer priority:

- Use a matching local `deploy/esim/lpac-linux-*.zip` bundle when present.
- Otherwise download `lpac-assets.json` from the latest release.
- Choose the best asset by `arch`, optional `os/os_version`, and `glibc`.

Recommended asset naming:

- `lpac-linux-aarch64-glibc2.31.zip`
- `lpac-linux-aarch64-debian12-glibc2.36.zip`
- `lpac-linux-x86_64-ubuntu22.04-glibc2.35.zip`

Release workflow behavior:

- `scripts/build_lpac_manifest.py` scans `deploy/esim/lpac-linux-*.zip`
- GitHub Actions publishes those zip files together with `lpac-assets.json`
- `scripts/install_latest.sh` downloads the latest deploy package
