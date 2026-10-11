# PanRush 🏎️

**多网盘聚合满速下载器** — 一个本地运行的开源工具：把各家网盘的文件浏览进来，取直链后用自研多连接引擎**满速下载**，支持断点续传与短时效直链自动重取。

> 思路：多数网盘只在自家客户端里限速，网页端 / 开放平台直链通常不限速。PanRush 不破解任何限速，只做「官方接口取直链 + 多连接下载」。

## ✨ 特性

- 🧩 **驱动插件化**：每个网盘一个驱动文件，几十行即可接入新盘
- 🚀 **分段下载引擎**：大文件自动分段并发（默认 16 连接，可配），按偏移直接写入目标文件
- 🐢 **全局限速**：令牌桶限速（KB/s），所有任务共享，防止触发网盘风控
- 🔄 **短时效直链适配**：夸克等直链几十秒过期 → 引擎"即取即下"，401/403/410 自动重取，**分段进度不丢**
- ⏸️ **断点续传**：分段进度每秒落 SQLite，暂停 / 重启 / 崩溃后接着下
- 🚦 **并发与限速**：全局令牌桶限速（KB/s）+ 同时下载任务数上限（排队等待），防风控
- 📝 **冲突保护**：目标文件已存在自动改名 `file (1).ext`，绝不覆盖
- 🔔 **任务体验**：浏览器完成通知、任务过滤器、ETA 剩余时间、实时速度曲线
- 🔍 **跨盘搜索**：一个搜索框扫所有已配置网盘，同名字节结果自动做 **256KB 头部哈希比对**，标注"✓ 内容一致"
- 🌉 **对外 WebDAV 出口**：`http://127.0.0.1:18400/dav/{盘}/` 直接挂到 rclone / 播放器 / 资源管理器（只读，代理流转发，支持 Range）
- 🔑 **OAuth 一键授权**：OneDrive / Google Drive / Dropbox 填好 client_id 点一下即完成授权，不用手动搞 refresh_token
- 📥 **批量下载**：文件浏览复选框多选、一键下载所选；一键清理已完成任务
- 🔄 **检查更新**：对接 GitHub Releases，点击版本号即查
- 🖥️ **完整桌面软件规格**：系统托盘常驻、开机自启、下载完成后自动关机/睡眠、滚动日志（data/panrush.log）
- ⚡ **响应速度**：全驱动共享 keep-alive 连接池（免每次 TLS 握手）+ 目录浏览 30s 缓存（↻ 强制刷新）+ DAV 出口直链缓存
- 🔐 **凭据安全**：Cookie / token 经 Windows DPAPI 加密后才落盘
- 🧱 **零构建 Web UI**：原生 JS 单页，聚合文件浏览 + 实时任务队列

## 📦 支持情况

| 网盘 | 接入方式 | 速度 | 状态 |
|---|---|---|---|
| 直链（任意 HTTP） | — | ✅ 满速 | ✅ 可用 |
| WebDAV | 标准协议 | ✅ 满速 | ✅ 可用 |
| 123云盘 | 官方开放平台 | ✅ 满速 | ✅ 可用 |
| 夸克网盘 | Web API (Cookie) | ✅ 基本满速 | ✅ 可用 |
| 阿里云盘 | 官方开放平台 | ✅ 满速 | ✅ 可用 |
| OneDrive | Microsoft Graph OAuth | ✅ 满速 | ✅ 可用 |
| Google Drive | 官方 OAuth | ✅ 满速 | ✅ 可用 |
| Dropbox | 官方 OAuth | ✅ 满速 | ✅ 可用 |
| 天翼云盘 | Web API (Cookie) | ✅ 满速 | 🧪 待真机验证 |
| 百度网盘 | 开放平台 | ⚠️ 随账号会员等级 | 骨架就绪 |
| MEGA / 迅雷 / UC / 115 | — | — | 🗺️ Roadmap（加密/签名类，需专项） |

> **关于百度**：百度在服务端按账号限速，任何多线程工具都无法绕过（这是官方计费策略，不是技术问题）。SVIP 账号的 token 即满速；本项目如实标注，不做也不接受破解类 PR。
>
> **关于状态标记**：✅ = 已实现；🧪 = 已实现但还没在真实账号上跑过，可能有接口出入；驱动跑不通时开 issue 附日志即可，修起来都是小改动。

## 🚀 快速开始

**方式一：绿色版 exe（Windows，推荐）**

从 [Releases](https://github.com/miraphael/panrush/releases) 下载 `PanRush.exe`，双击即用（自动打开浏览器，数据保存在 exe 同目录）。

**方式二：源码运行**

```bash
# 1. 安装依赖（Python 3.10+）
pip install -r requirements.txt

# 2. 启动
python -m uvicorn server.main:app --host 127.0.0.1 --port 18400
# Windows 可直接双击 run.bat

# 3. 浏览器打开
http://127.0.0.1:18400
```

首次使用：左侧选择网盘 → 点「配置凭据」→ 按提示填入 Cookie / 开放平台凭据 → 浏览 → 点文件旁的「下载」。

### 自行打包 exe

```bash
pip install pyinstaller
pyinstaller --onefile --noconsole --name PanRush --add-data "web;web" run_exe.py
# 产物在 dist/PanRush.exe
```

## 🏗️ 架构

```
Web UI (原生 JS) ── FastAPI ──┬─ providers/   驱动插件（list_dir / resolve → DirectLink）
                              ├─ engine/      分段下载引擎 + SQLite 任务库
                              └─ cred_store   DPAPI 加密凭据
```

### 写一个新驱动

继承 `PanProvider`，实现三个方法，加上 `@register` 即可：

```python
from .base import DirectLink, FileEntry, PanProvider, register

@register
class MyPanProvider(PanProvider):
    id = "mypan"
    name = "我的网盘"
    tier = "full"          # full / limited / experimental
    root = "0"
    cred_fields = [{"key": "cookie", "label": "Cookie", "secret": True}]

    async def list_dir(self, path: str) -> list[FileEntry]: ...
    async def resolve(self, entry: FileEntry) -> DirectLink: ...
```

短时效直链只需把 `expires_in` 设小，引擎会自动处理重取。

## 🧪 测试

```bash
python tests/range_server.py 8765 tests      # 直链测试服务器（含 Range 支持）
python tests/dav_server.py 8766 tests        # WebDAV 测试服务器（PROPFIND + Range）
python -m uvicorn server.main:app --port 18400 &
python tests/e2e_test.py                     # 9 节：分段/续传/冲突/并发/WebDAV/搜索/DAV出口/OAuth
```

### 挂载 WebDAV 出口

```
rclone config → 类型 webdav → url: http://127.0.0.1:18400/dav/quark/   # 任意已配置凭据的盘
# 播放器、资源管理器同理；只读，直链代理流转发
```

## 🗺️ Roadmap

- [ ] MEGA 驱动（AES+RSA 会话加密，需专项实现）
- [ ] 迅雷 / UC 网盘 / 115 驱动（签名类接口，需专项）
- [ ] 跨盘搜索哈希级匹配升级为全文件秒传比对
- [ ] DAV 出口支持写入（上传）与鉴权
- [ ] Linux / macOS 打包

## ⚖️ 使用声明

- 本项目仅供个人下载**自己有权限访问的文件**，请遵守各网盘服务条款与当地法律
- 不提供、不接受任何绕过付费/限速技术措施的功能（包括但不限于共享会员账号、破解直链签名）
- 请控制并发与请求频率，勿滥用接口导致账号风控

## 📄 License

[MIT](LICENSE)
