# eSIM SMS Forwarder

[![Build Deploy Package](https://github.com/cyDione/eSIM-SMS-Forwarder/actions/workflows/build-deploy-package.yml/badge.svg)](https://github.com/cyDione/eSIM-SMS-Forwarder/actions/workflows/build-deploy-package.yml)
[![Latest Release](https://img.shields.io/github/v/release/cyDione/eSIM-SMS-Forwarder?display_name=tag)](https://github.com/cyDione/eSIM-SMS-Forwarder/releases/latest)

一个运行在 Debian 设备上的轻量服务，用来做 eSIM 管理、短信接收、Apprise 多渠道转发，以及浏览器里的可视化控制台。

项目目标很直接：

- 在支持 eUICC 的设备上切换内置 eSIM Profile
- 接收短信并转发到 Apprise 多渠道
- 提供低负载、可实时反馈执行进度的 Web 管理页面
- 兼容普通 SIM 场景，只启用短信转发，不安装 `lpac`

## 功能描述

### eSIM 管理

- 读取 eUICC 内置 Profiles
- 一键切换当前启用的 eSIM Profile
- 切卡后自动执行基带恢复，帮助重新注册网络
- 支持按 cron 表达式执行保活任务，自动切换指定 Profile、发送短信并回切原 Profile
- Web 页面显示执行进度和 Shell 日志

### 短信转发

- 通过 `ModemManager` 读取短信
- 自动转发新短信到 Apprise 渠道
- 自动处理中文转义内容
- 自动尝试解码 Base64 短信正文
- 支持在页面里查看最近短信
- 支持立即发送测试短信
- 支持“重发最后一条短信”

### 设备控制

- 查看运营商、注册状态、信号强度、接入制式
- 重启基带
- 重启短信转发服务
- 配置 APN、网络制式、手动选网
- 维护保活任务、切卡缓冲时间、执行队列与最近记录
- 在高级设置里维护通知渠道

### Web 控制台

- React + shadcn/ui 动态前端
- 点击任意操作后立即进入任务态
- Shell 面板实时显示执行步骤
- 前后端由同一个 Python 服务托管
- 普通 SIM 模式下自动禁用 eSIM 相关功能

## 技术栈

### 设备侧

- Debian
- Python 3
- systemd
- ModemManager / `mmcli`
- libqmi / `qmicli`
- NetworkManager / `nmcli`
- `lpac`

### 前端

- React 19
- TypeScript
- Vite
- Tailwind CSS v4
- shadcn/ui
- sonner
- lucide-react

## 安装说明

### 1. 一键安装

默认安装模式为 eSIM 模式：

```bash
curl -fsSL https://raw.githubusercontent.com/cyDione/eSIM-SMS-Forwarder/main/scripts/install_latest.sh | sudo sh
```

如果设备使用普通 SIM，只需要短信转发，不需要 `lpac` 和 eSIM 管理：

```bash
curl -fsSL https://raw.githubusercontent.com/cyDione/eSIM-SMS-Forwarder/main/scripts/install_latest.sh | sudo sh -s -- --sim-type physical
```

安装脚本会自动：

- 检查系统环境、架构、systemd 和基础依赖
- 下载最新 Release，失败时回退到 `main` 分支源码包
- 安装常用依赖：`python3`、`curl`、`unzip`、`modemmanager`、`network-manager`、`libqmi-utils`
- 在 `aarch64 / arm64` 的 eSIM 模式下自动安装内置 `lpac`
- 安装并启用 Web 管理服务与短信转发服务
- 输出访问地址、服务状态和通知渠道摘要

### 2. 安装模式

#### eSIM 模式

- 参数：`--sim-type esim`
- 默认模式
- 安装 `lpac`
- 启用 eSIM 卡切换能力
- Web 前端显示 eSIM Profiles 和切卡按钮

#### 普通 SIM 模式

- 参数：`--sim-type physical`
- 不安装 `lpac`
- 删除 `lpac-switch` 包装脚本
- 后端禁用 eSIM Profile 读取与切卡接口
- Web 前端自动隐藏/禁用 eSIM 相关操作

安装完成后会写入：

```text
/etc/esim-sms-forwarder.conf
```

示例内容：

```ini
SIM_TYPE=physical
ESIM_MANAGEMENT_ENABLED=0
```

### 3. 手动部署

```bash
git clone https://github.com/cyDione/eSIM-SMS-Forwarder.git
cd eSIM-SMS-Forwarder
sudo sh ./deploy/install.sh
```

普通 SIM 模式：

```bash
sudo sh ./deploy/install.sh --sim-type physical
```

## 使用说明

### 通知渠道配置

实际配置文件路径：

```text
/etc/sms-forwarder.conf
```

至少需要填写：

```ini
MODEM_ID=any
NOTIFICATION_TARGETS_JSON=[{"id":"bark-primary","label":"Bark","url":"barks://bark.example.com/device_key?group=sms&level=active","enabled":true}]
FORWARD_SMS_STATES=received
```

`NOTIFICATION_TARGETS_JSON` 使用 Apprise URL 格式，可以同时配置多个渠道。

### 服务管理

```bash
systemctl status 4g-wifi-admin.service
systemctl status sms-forwarder.service
```

```bash
journalctl -u 4g-wifi-admin.service -f
journalctl -u sms-forwarder.service -f
```

### Web 页面

默认监听端口：

```text
http://<device-ip>:8080/
```

页面内可完成：

- 查看当前号码、运营商、信号和服务状态
- 查看最近短信
- 重发最后一条短信
- 重启基带
- 配置保活任务，按时自动切卡、发短信、通知并切回原 Profile
- 立即发送测试短信，确认短信发送链路
- 修改通知渠道配置
- 修改 APN、网络制式和选网策略
- 在 eSIM 模式下切换 Profile

## 构建说明

### 前端开发

```bash
cd frontend
npm install
npm run dev
```

### 前端构建

```bash
cd frontend
npm run lint
npm run build
```

### Python 语法检查

```bash
python -m py_compile deploy/web_admin/4g_wifi_admin.py
python -m py_compile deploy/sms_forwarder/sms_forwarder.py
```

## 目录结构

```text
deploy/
  esim/
    lpac-switch.sh
    lpac
  sms_forwarder/
    sms_forwarder.py
    sms-forwarder.service
    sms-forwarder.conf.example
  web_admin/
    4g_wifi_admin.py
    4g-wifi-admin.service
    frontend_dist/

frontend/
  src/
```

## 说明

这个项目优先解决的是“稳定可用”，而不是“大而全”。重点是：

- 切卡动作有明确反馈
- 短信链路可追踪
- 低内存设备也能长期运行
- 页面操作有实时响应

如果你只需要保号收短信，推荐普通 SIM 模式；如果你需要切换 eSIM Profile，再使用默认 eSIM 模式即可。
