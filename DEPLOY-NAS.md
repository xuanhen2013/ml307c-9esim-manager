# 群晖 / Linux Docker 部署

已验证环境为 x86_64、DSM 7.2.1，Docker Compose 可用。当前离线构建说明针对 Linux amd64 / Python 3.12，ARM NAS 需要准备对应架构的依赖并单独验证。

## 首次部署

1. 克隆仓库，在电脑项目根目录准备前端和 Linux 依赖：

```sh
cd frontend
npm ci
npm run build
cd ..
python -m pip download --dest wheels --platform manylinux2014_x86_64 --python-version 312 --implementation cp --abi cp312 --only-binary=:all: -r requirements-ml307.txt
```

2. 将源码、`frontend/dist` 和 `wheels` 上传到 NAS 的项目目录，例如 `/volume1/docker/ml307c-9esim-manager`。不要上传电脑上的 `.localdata`、`node_modules` 或开发环境。
3. 将 `.env.example` 复制为 `.env`，把 `NAS_BIND_IP` 改成自己的 NAS 内网地址。`.env` 不能提交到 Git。
4. 在 NAS 的项目目录创建数据目录并构建、启动：

```sh
mkdir -p data
chmod 700 data
sudo docker compose -f compose.nas.yaml build
sudo docker compose -f compose.nas.yaml up -d
sudo docker compose -f compose.nas.yaml ps
```

较旧的群晖使用 `docker-compose` 命令，参数相同。首次构建需要获取基础镜像；Python 包从本地 `wheels` 安装。基础镜像可通过 `.env` 的 `PYTHON_IMAGE` 指定。

访问 `http://你的NAS地址:18081/`，确认卡列表、模组连接、网络和信号。飞书配置见 [FEISHU.md](FEISHU.md)。首次不会自动创建保号任务。

## USB 与运行方式

插入模组并保持供电。Linux 默认通过 usbfs 访问未绑定内核驱动的 ML307 AT 接口 2，不安装 DSM 内核驱动，不强制卸载已有驱动。只有 VID/PID 和端点布局匹配才会连接。

容器映射 `/dev/bus/usb` 并开放 USB 字符设备访问，以支持重新插拔后设备节点改变。未开启 privileged，Linux capabilities 全部移除，根文件系统只读，仅数据目录与临时目录可写。已有串口驱动时，应设置 `ML307_PORT` 为实际串口并修改设备映射；当前 Compose 是 usbfs 配置。

网页无登录功能，只绑定指定可信内网地址。手机在外面操作可以使用飞书机器人，不需要开放网页端口到公网。

## 启停与查看日志

```sh
sudo docker compose -f compose.nas.yaml logs --tail=80
sudo docker compose -f compose.nas.yaml restart
sudo docker compose -f compose.nas.yaml stop
sudo docker compose -f compose.nas.yaml up -d
```

`unless-stopped` 会在 Docker 启动后恢复容器，手动停止后保持停止。关闭电脑或网页不影响 NAS 后台收信。设备断开时后台继续尝试重新发现；Docker 健康检查只判断 HTTP 服务响应，不代表模组或飞书权限正常。

## 数据、备份与更新

`data/` 中保存：

- `sms.sqlite3`：短信与通知投递状态。
- `keepalive.sqlite3`：保号规则、发送记录与待通知事项。
- `receive_reminders.sqlite3`：365 天收码提醒、最近识别进度及已提醒阶段。
- `profile_numbers.json`：按 ICCID 保存的手机号，属于运行数据，不写入 SIM，也不提交 Git。
- `feishu.json` / `feishu.sqlite3`：应用凭据、个人绑定及通知队列。
- `app.conf` / `notifications.conf`：应用与通知配置。

更新前先确认没有切卡或保号任务正在执行。停止容器后备份整个 `data/` 目录及 `.env`，限制备份访问权限。更新源码时保留这些数据；前端修改后重新构建并上传 `frontend/dist`，依赖变化后重新准备 `wheels`，然后重新 build 和 up。

恢复旧备份前应核对备份之后是否已经发过保号短信，先停用旧任务并修正日期，避免恢复过期状态导致再次发送。不要把数据、凭据或备份打入镜像或上传 GitHub。

## 验证边界

已在一台 NAS 上验证接收测试短信、切卡回切、驻网和信号、飞书接收通知，以及重建容器后短信与定时规则持久化。尚未进行真实出站计费短信、整机重启及 USB 物理重插的完整验收。保号调度中的成功、失败、异常重启及不重复发送行为由自动测试覆盖。
