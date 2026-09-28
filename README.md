# ML307C 9eSIM Manager

运行在群晖 NAS 或 Windows 上的个人短信管理工具：接收短信、通过飞书通知、远程切换 9eSIM 中已有的卡，以及按间隔发送保号短信。

基于 [cyDione/eSIM-SMS-Forwarder](https://github.com/cyDione/eSIM-SMS-Forwarder) 开发，保留原作者的 MIT 许可证。ML307C 使用新增的 AT/APDU 适配层；原来的 ModemManager / QMI / lpac 路径仍保留，说明见 [上游项目文档](README-UPSTREAM.md)。

## 已有功能

- 读取 9eSIM 配置并切卡，确认卡片启用状态、等待网络注册。
- 接收 GSM 7-bit / UCS2 短信，合并长短信，SQLite 保存与去重。
- 显示当前驻网运营商、漫游状态、LTE 信号及原始测量值。
- 网页收件箱、搜索、卡片筛选、复制短信、自动刷新和操作日志。
- 按 ICCID 保存手动提供的手机号；网页查看和复制，飞书菜单及短信通知显示对应号码，私聊“号码”查看全部卡片。
- 飞书个人机器人：短信通知、状态查询、点击卡片切号。NAS 使用出站长连接，无需公网回调地址。
- 保号任务：首次等待 N 天，此后每次短信提交成功后 M 天；到期切卡发送，再恢复原卡；失败或结果不明时暂停并通知。
- 收码提醒：按卡片记录最近识别到的验证码，重新计算 365 天期限；提前 30、7、1 天及到期时通过飞书提醒，支持手动校正起算日期。
- Apprise 通知渠道配置与最近短信重推。

## 开始使用

| 环境 | 说明 |
| --- | --- |
| 群晖 / Linux Docker | [部署、备份和更新](DEPLOY-NAS.md) |
| Windows USB 串口 | [ML307C 适配与本地启动](README-ML307C.md) |
| 飞书自建应用 | [权限、长连接与个人绑定](FEISHU.md) |

ML307 适配已在一台 x86_64、DSM 7.2.1 的 NAS 上验证接收、切卡、驻网及飞书通知。Linux 使用 usbfs 访问 ML307 的 AT 接口；Windows 使用已安装驱动提供的串口。不同固件、USB 接口布局和 NAS 平台需要单独验证。

首次部署不会创建保号任务。请在网页中填写自己的卡片、收件号码、起算日期和短信内容，再开启。程序不会查询运营商账户余额或认定号码已经完成保号。

## 当前边界

- 一次只能启用一张 eSIM，停用的卡不能同时实时收信。
- 支持已有配置间切换，尚不支持下载、删除 eSIM 配置。
- 保号调度、恢复和异常处理已有模拟测试；**真实出站计费短信尚未完成实机验收**。模组确认提交不等于对端收到或运营商确认计费。
- 验证码识别依赖短信关键词和代码格式，可能漏识别或误识别，可在保号页校正日期；这不代表运营商确认号码有效期已延长。
- 网页目前没有登录功能，只应绑定可信内网地址。远程操作使用已绑定个人身份的飞书机器人，不要直接把网页端口开放到公网。
- 不自动删除模组内的短信；长期使用需要关注设备存储空间。

## 开发与检查

需要 Python 3.12 和 Node.js 22。测试不连接真实模组，不发送短信。

```sh
python -m pip install -r requirements-ml307.txt
python -B -m unittest discover -s tests -v
cd frontend
npm ci
npm run lint
npm run build
```

`.github/workflows/check-ml307.yml` 在推送时运行后端测试、前端检查和容器构建，不会自动发布 Release 或部署到 NAS。上游 Debian 安装包工作流仅允许手动执行，不适用于 ML307 部署。

## 数据与许可证

短信、任务、通知凭据和飞书绑定均保存在运行时数据目录。`.localdata/`、`data/`、`.env`、数据库、日志及本地打包文件不提交到 Git；仓库只包含程序、测试和配置示例。

[MIT License](LICENSE) · [第三方许可证与来源](THIRD_PARTY_NOTICES.md) · [原项目](https://github.com/cyDione/eSIM-SMS-Forwarder)

本仓库不分发 lpac/libqmi 预编译二进制。ML307C 后端不依赖它们；旧 Debian 安装器按平台下载校验过的官方 lpac 包，较旧系统需自行编译。Geist 字体许可证随网页构建及部署包提供。
