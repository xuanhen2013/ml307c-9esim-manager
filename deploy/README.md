# 4G WiFi deployment assets

This folder contains the files deployed to the Debian device:

- `esim/lpac-switch.sh`: wrapper around `lpac` for profile inspection and switching.
- `sms_bark/sms_forwarder.py`: polls ModemManager for newly received SMS and forwards them to Bark.
- `sms_bark/sms-bark-forwarder.service`: systemd unit for the SMS forwarder.
- `sms_bark/sms-bark-forwarder.conf.example`: example configuration for Bark.
