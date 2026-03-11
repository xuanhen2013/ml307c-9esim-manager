# eSIM SMS Forwarder

一个运行在 Debian 设备上的轻量服务，提供 eSIM Profile 管理、短信接收与 Bark 转发，以及可直接在浏览器中操作的管理页面。

项目目标不是做“全家桶路由系统”，而是把 eUICC 切卡、基带控制、短信查看和短信转发这几件事稳定地串起来，方便在随身 WiFi、OpenStick 一类设备上长期运行。

## 功能描述

### 1. eSIM 管理

- 读取 eUICC 内置 Profiles
- 一键切换当前启用的 eSIM Profile
- 切卡后自动执行基带恢复，帮助新卡重新注册网络
- 支持手动刷新 Profile 列表

### 2. 短信接收与转发

- 通过 ModemManager 读取设备短信
- 自动把新收到的短信转发到 Bark
- 自动处理中文转义内容
- 自动尝试解码 Base64 短信正文
- 支持在 Web 页面里查看最近短信
- 支持“重发最后一条短信”到 Bark，便于排错

### 3. 设备控制

- 查看当前运营商、注册状态、信号强度、接入制式
- 手动重启基带
- 重启短信转发服务
- 配置网络制式和手动选网
- 在高级设置中维护 Bark 参数

### 4. Web 管理界面

- 动态前端，不需要手动刷新整个页面看结果
- 所有操作都进入后台任务流
- Shell 面板实时显示执行进度和命令输出
- 前后端由同一个轻量 Python 服务提供，无需在设备上常驻 Node.js

## 技术栈

### 设备侧

- Debian
- Python 3
- `systemd`
- `ModemManager` / `mmcli`
- `qmicli`
- `NetworkManager` / `nmcli`
- `lpac`
- 自定义 `lpac-switch.sh` 包装脚本

### 前端

- React 19
- TypeScript
- Vite
- Tailwind CSS v4
- shadcn/ui
- `sonner`
- `lucide-react`

### 服务结构

- [deploy/web_admin/4g_wifi_admin.py](deploy/web_admin/4g_wifi_admin.py)
  负责 Web 管理页面、状态 API、后台任务执行和静态文件托管
- [deploy/sms_bark/sms_forwarder.py](deploy/sms_bark/sms_forwarder.py)
  负责轮询短信并转发到 Bark
- [deploy/esim/lpac-switch.sh](deploy/esim/lpac-switch.sh)
  负责封装 `lpac` 的常用切卡命令

## 目录说明

```text
deploy/
  esim/
    lpac/
    lpac-switch.sh
  sms_bark/
    sms_forwarder.py
    sms-bark-forwarder.service
    sms-bark-forwarder.conf.example
  web_admin/
    4g_wifi_admin.py
    4g-wifi-admin.service
    frontend_dist/

frontend/
  src/
  public/
```

## 使用说明

### 1. 设备端准备

确保 Debian 设备已经具备以下基础条件：

- 已能识别基带
- 已安装并可使用 `mmcli`
- 已安装并可使用 `qmicli`
- 已安装并可使用 `nmcli`
- 已部署 `lpac`
- eUICC 卡已经可以正常列出和切换 Profile

### 2. 本地构建前端

前端只在开发机上构建，设备上不需要运行 Node.js。

```bash
cd frontend
npm install
npm run build
```

构建产物会输出到：

```text
frontend/dist/
```

部署时把它同步到设备上的：

```text
/usr/local/bin/frontend_dist
```

### 3. 一键部署

推荐直接在 Debian 上执行这一条命令：

```bash
curl -fsSL https://raw.githubusercontent.com/cyDione/eSIM-SMS-Forwarder/main/scripts/install_latest.sh | sudo sh
```

这条命令会自动：

- 优先下载 GitHub Release 中最新构建好的部署包
- 如果最新 Release 暂时不可用，则自动回退到 `main` 分支源码包
- 自动解压并执行 `deploy/install.sh`
- 自动补齐 Debian 常用依赖：`python3`、`curl`、`unzip`、`modemmanager`、`network-manager`、`libqmi-utils`
- 自动安装并启动服务

如果你更想手动下载仓库后再安装，也可以这样做：

```bash
git clone <your-repo-url>
cd 4g-wifi
chmod +x deploy/install.sh
sudo ./deploy/install.sh
```

也可以在不改权限的情况下直接执行：

```bash
sudo sh ./deploy/install.sh
```

脚本会自动完成：

- 复制 Python 服务脚本到 `/usr/local/bin`
- 复制前端静态资源到 `/usr/local/bin/frontend_dist`
- 安装并启用 systemd 服务
- 如果 `/etc/sms-bark-forwarder.conf` 不存在，则自动创建示例配置
- 如果 Bark 配置已填写完成，则自动启动短信转发服务

注意：

- 这个脚本会自动安装 `ModemManager`、`qmicli`、`nmcli` 对应的 Debian 包
- 如果 Bark 还是示例配置值，脚本会跳过启动 `sms-bark-forwarder.service`
- eSIM 切卡依赖 `/opt/lpac/bin/lpac` 已经存在

### 4. GitHub Actions 自动构建部署包

仓库已经包含 GitHub Actions 工作流：

- push 到 `main` 时自动构建
- 支持手动触发 `workflow_dispatch`
- 自动执行前端 `lint`、`build`
- 自动校验 Python 脚本语法
- 自动生成 Debian 可用的部署包 zip

生成后的产物可以在 GitHub 仓库的 Actions 页面下载，默认会输出：

- `eSIM-SMS-Forwarder-deploy-<commit-sha>.zip`
- `eSIM-SMS-Forwarder-deploy-latest.zip`

下载后在 Debian 设备上解压并执行：

```bash
unzip eSIM-SMS-Forwarder-deploy-latest.zip
cd eSIM-SMS-Forwarder-deploy-latest
sudo sh ./deploy/install.sh
```

如果只是要安装最新版本，仍然推荐直接使用上面的 `curl | sudo sh` 单行命令。

### 5. 部署后端与短信转发脚本

把以下文件复制到 Debian 设备：

- `deploy/web_admin/4g_wifi_admin.py` -> `/usr/local/bin/4g_wifi_admin.py`
- `deploy/sms_bark/sms_forwarder.py` -> `/usr/local/bin/sms_forwarder.py`
- `deploy/esim/lpac-switch.sh` -> `/usr/local/bin/lpac-switch`
- `deploy/web_admin/frontend_dist/*` -> `/usr/local/bin/frontend_dist/`

把以下 systemd 文件复制到：

- `deploy/web_admin/4g-wifi-admin.service` -> `/etc/systemd/system/4g-wifi-admin.service`
- `deploy/sms_bark/sms-bark-forwarder.service` -> `/etc/systemd/system/sms-bark-forwarder.service`

### 6. 配置 Bark

参考示例文件：

- [sms-bark-forwarder.conf.example](deploy/sms_bark/sms-bark-forwarder.conf.example)

设备上的实际配置文件路径：

```bash
/etc/sms-bark-forwarder.conf
```

至少需要配置：

```ini
MODEM_ID=any
BARK_BASE_URL=https://your-bark-server
BARK_DEVICE_KEY=your-device-key
BARK_GROUP=sms
BARK_LEVEL=active
FORWARD_SMS_STATES=received
```

### 7. 启动服务

```bash
systemctl daemon-reload
systemctl enable 4g-wifi-admin.service
systemctl enable sms-bark-forwarder.service
systemctl restart 4g-wifi-admin.service
systemctl restart sms-bark-forwarder.service
```

### 8. 打开 Web 页面

默认监听端口为 `8080`：

```text
http://<device-ip>:8080/
```

进入页面后可以完成：

- 查看当前 eSIM Profile
- 切换 Profile
- 查看最近短信
- 重发最后一条短信
- 重启基带
- 配置 Bark
- 调整网络模式和选网策略

## 常用命令

### 查看服务状态

```bash
systemctl status 4g-wifi-admin.service
systemctl status sms-bark-forwarder.service
```

### 查看管理服务日志

```bash
journalctl -u 4g-wifi-admin.service -f
```

### 查看短信转发日志

```bash
journalctl -u sms-bark-forwarder.service -f
```

### 手动查看短信

```bash
mmcli -m any --messaging-list-sms
mmcli -s /org/freedesktop/ModemManager1/SMS/0 -K
```

### 手动查看 eSIM Profiles

```bash
/usr/local/bin/lpac-switch list
```

### 手动切卡

```bash
/usr/local/bin/lpac-switch enable <ICCID>
```

## 运行说明

- `4g-wifi-admin.service` 同时负责前端页面和后端 API
- `sms-bark-forwarder.service` 只负责短信转发
- 前端页面是静态文件，由 Python 管理服务直接托管
- 当前方案强调低负载，适合内存较小的设备常驻运行

## 适用场景

- eSIM 卡切换管理
- 保号卡只收短信，不开蜂窝数据
- 设备通过 Wi-Fi 联网，短信通过 Bark 转发到手机
- 远程维护插卡设备或 OpenStick 类设备

## 开发说明

### 前端开发

```bash
cd frontend
npm run dev
```

### 前端校验

```bash
cd frontend
npm run lint
npm run build
```

### Python 语法检查

```bash
python -m py_compile deploy/web_admin/4g_wifi_admin.py
python -m py_compile deploy/sms_bark/sms_forwarder.py
```

## 说明

这个项目目前更偏向实际部署和个人设备管理，优先保证：

- 切卡动作明确可见
- 短信链路可追踪
- 服务负载尽量低
- 页面操作有即时反馈

如果后续继续扩展，更适合往“稳定运维工具”方向演进，而不是做成依赖很重的大型管理平台。
