# eSIM-SMS-Forwarder

这是一个面向 Debian 蜂窝设备的部署仓库，提供：

- eUICC / eSIM profile 管理
- 短信接收与转发
- 轻量级 Web 管理页面

## 包含内容

- `deploy/esim/lpac-switch.sh`
  对 `lpac` 的简单封装，用于查看 eSIM profile 列表和执行切卡。
- `deploy/sms_bark/sms_forwarder.py`
  轮询 ModemManager 中收到的短信，并转发到 Bark。
- `deploy/sms_bark/sms-bark-forwarder.service`
  短信转发服务对应的 `systemd` 单元文件。
- `deploy/sms_bark/sms-bark-forwarder.conf.example`
  Bark 推送配置示例。
- `deploy/web_admin/4g_wifi_admin.py`
  轻量级 Web 管理页面，支持切卡、Bark 配置、APN 修改、基带恢复和短信查看。
- `deploy/web_admin/4g-wifi-admin.service`
  Web 管理页面对应的 `systemd` 单元文件。

## 目录结构

```text
deploy/
  esim/
  sms_bark/
  web_admin/
```

## 说明

- 这里保存的是当前在 Debian 设备上实际使用的部署文件。
- 仓库中默认排除了 Python 字节码缓存文件。
