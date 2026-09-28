# 第三方许可证与来源

本项目代码基于 [CyDione/eSIM-SMS-Forwarder](https://github.com/cyDione/eSIM-SMS-Forwarder)，保留根目录 [MIT License](LICENSE) 与原作者署名。第三方程序、字体和依赖使用各自许可证；根目录 MIT 文件不替代它们。

## Geist 字体

- 来源：[Geist](https://github.com/vercel/geist-font)；实际使用 npm 包 `@fontsource-variable/geist` 5.2.8。
- 版权：Copyright 2024 The Geist Project Authors。
- 许可证：SIL Open Font License 1.1，完整文本保存在 [Geist-OFL-1.1.txt](third_party/Geist-OFL-1.1.txt)。
- npm 包中的 WOFF2 文件未经修改。字体随网页构建分发；`frontend/public/licenses/Geist-OFL-1.1.txt` 经 Vite 复制到输出目录，已提交的旧构建目录也包含同一许可证。

## 可选的 lpac 与 libqmi

ML307C 后端使用本项目 AT/APDU 实现，不加载或分发 lpac/libqmi。旧 Debian/ModemManager 后端可调用用户安装的 lpac。

本仓库不保留历史中来源无法完整确认的 lpac/libqmi 二进制包。默认 Debian 安装流程从 [lpac 官方 v2.3.0 Release](https://github.com/estkme-group/lpac/releases/tag/v2.3.0) 下载、校验并保留原包许可证。对应源码为 [v2.3.0](https://github.com/estkme-group/lpac/tree/v2.3.0)。

| 官方文件 | SHA-256 |
| --- | --- |
| lpac-linux-aarch64-with-qmi.zip | e8d2808dfb179c1d3453655801b9f73d9edbd334b997845f2a41c7922d9331ad |
| lpac-linux-x86_64-with-qmi.zip | f05d8fefeed27b205fd1bbb2441b93f5fa568b7647ffa7ba0e3adb3654d1408b |

上述两个包的 ELF 引用 GLIBC 2.34；安装器不会为较旧系统自动安装它们。系统兼容性仍需在实际平台验证。

官方包包含以下版权与许可文件，本仓库同时保留副本作为来源说明：

- [LICENSE-lpac](third_party/lpac-v2.3.0/LICENSE-lpac)：AGPL-3.0。
- [LICENSE-libeuicc](third_party/lpac-v2.3.0/LICENSE-libeuicc)：LGPL-2.1。
- [LICENSE-cjson](third_party/lpac-v2.3.0/LICENSE-cjson)：MIT。
- [LICENSE-dlfcn-win32](third_party/lpac-v2.3.0/LICENSE-dlfcn-win32)：原包附带的 MIT 文本。

libqmi 由 Debian 系统包依赖提供，项目不重新打包它的动态库。来源为 [freedesktop.org/libqmi](https://gitlab.freedesktop.org/mobile-broadband/libqmi)。其库和工具有不同许可；对应系统包的版权、许可证和源码信息以 `/usr/share/doc/libqmi-glib5/copyright`、`/usr/share/doc/libqmi-utils/copyright` 及发行版源码包为准。

如自行构建、修改或分发 lpac/libqmi，应同时保留对应版本的版权/许可、源码和构建信息；本项目不将自制的本地压缩包自动加入 Git 或发布附件。

## 其他依赖

Python 依赖列于 `requirements-ml307.txt`，前端依赖锁定于 `frontend/package-lock.json`；依赖仍遵循各自许可。安装在容器中的 Python 包保留包内许可证。前端构建中依赖自带的许可证注释应保留。
