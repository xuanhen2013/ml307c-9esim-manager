# 4G WiFi deployment assets

This folder contains the files deployed to the Debian device:

- `install.sh`: one-click installer for Debian. Copies files, installs systemd services, and starts the web admin.
- `esim/lpac-switch.sh`: wrapper around `lpac` for profile inspection and switching.
- `esim/lpac`: wrapper that points to `/opt/lpac/lpac`.
- `sms_forwarder/sms_forwarder.py`: polls ModemManager for newly received SMS and forwards them through Apprise.
- `sms_forwarder/sms-forwarder.service`: systemd unit for the SMS forwarder.
- `shared/notification_utils.py`: shared Apprise target parsing and delivery helpers.
- `sms_forwarder/sms-forwarder.conf.example`: example configuration for Apprise targets.
- `web_admin/4g_wifi_admin.py`: lightweight backend that serves both the API and built frontend assets.
- `web_admin/4g-wifi-admin.service`: systemd unit for the web admin.

## lpac asset auto-selection

`deploy/install.sh` downloads the official [lpac v2.3.0 release](https://github.com/estkme-group/lpac/releases/tag/v2.3.0) for aarch64 or x86_64 and checks its pinned SHA-256. The official QMI archives retain all their license files when extracted into `/opt/lpac`. libqmi comes from the system package manager, not a bundled shared library.

Installer priority:

- Use a matching local `deploy/esim/lpac-linux-*.zip` bundle when present.
- Otherwise use the official release for GLIBC >= 2.34. On older systems, build from the official source for that platform; the untraceable GLIBC 2.31 binary has been removed.
- If an administrator explicitly sets `LPAC_RELEASE_BASE_URL`, use its `lpac-assets.json` manifest and choose by architecture, OS and GLIBC instead. The administrator is responsible for that custom source and its redistribution terms.

Recommended asset naming:

- `lpac-linux-aarch64-glibc2.31.zip`
- `lpac-linux-aarch64-debian12-glibc2.36.zip`
- `lpac-linux-x86_64-ubuntu22.04-glibc2.35.zip`

Release workflow behavior:

- Locally supplied `lpac-linux-*.zip` files are ignored by Git and excluded from deployment archives. The manual workflow does not republish third-party binaries.
- `scripts/build_lpac_manifest.py` remains available for administrators maintaining their own custom release.
- Deployment archives include the project license and [third-party notices](../THIRD_PARTY_NOTICES.md); font licenses are also carried with the frontend assets.
- `scripts/install_latest.sh` is retained from upstream; its default repository is the upstream project. It is not the NAS/ML307C deployment path.
