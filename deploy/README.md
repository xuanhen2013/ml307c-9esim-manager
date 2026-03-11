# 4G WiFi deployment assets

This folder contains the files deployed to the Debian device:

- `install.sh`: one-click installer for Debian. Copies files, installs systemd services, and starts the web admin.
- `esim/lpac-switch.sh`: wrapper around `lpac` for profile inspection and switching.
- `esim/lpac`: wrapper that points to `/opt/lpac/bin/lpac`.
- `sms_bark/sms_forwarder.py`: polls ModemManager for newly received SMS and forwards them to Bark.
- `sms_bark/sms-bark-forwarder.service`: systemd unit for the SMS forwarder.
- `sms_bark/sms-bark-forwarder.conf.example`: example configuration for Bark.
- `web_admin/4g_wifi_admin.py`: lightweight backend that serves both the API and built frontend assets.
- `web_admin/4g-wifi-admin.service`: systemd unit for the web admin.
