# eSIM-SMS-Forwarder

eUICC/eSIM management, SMS forwarding, and lightweight web admin for Debian-based cellular devices.

## Included Components

- `deploy/esim/lpac-switch.sh`
  Wrapper around `lpac` for profile listing and switching.
- `deploy/sms_bark/sms_forwarder.py`
  Polls ModemManager for received SMS and forwards messages to Bark.
- `deploy/sms_bark/sms-bark-forwarder.service`
  `systemd` unit for the SMS forwarding service.
- `deploy/sms_bark/sms-bark-forwarder.conf.example`
  Example Bark configuration.
- `deploy/web_admin/4g_wifi_admin.py`
  Lightweight web admin for eSIM switching, Bark config, APN editing, modem recovery, and SMS viewing.
- `deploy/web_admin/4g-wifi-admin.service`
  `systemd` unit for the web admin service.

## Layout

```text
deploy/
  esim/
  sms_bark/
  web_admin/
```

## Notes

- These files are the deployment assets currently used on the Debian device.
- Python bytecode caches are intentionally excluded from the repository.
