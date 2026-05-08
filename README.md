# AI Bridge

**Windows 桌面 API 中转管理器** — 把任何 OpenAI-Compatible 模型（GLM、DeepSeek、通义千问、Moonshot…）转换成 OpenAI Responses API，给 Codex / Claude Code 等工具用。带 GUI、多账号、一键切换。

## 功能

- 卡片式账号管理（添加 / 编辑 / 启用 / 停用 / 删除）
- 一键切换"当前账号"，所有客户端立即生效
- 内置 Provider 预设：智谱 GLM / DeepSeek / 通义千问 / Moonshot / OpenAI / 自定义
- 本地 HTTP 服务 `http://127.0.0.1:<port>/v1`
- 同时暴露 `/v1/responses`（Responses API）和 `/v1/chat/completions`
- Responses ↔ Chat Completions 自动桥接，支持流式 + tool calling + reasoning_content
- 高可靠流式传输：独立连接池 + 分包安全解析 + 异常恢复
- 系统托盘最小化、开机自启、静默启动
- 单实例互斥锁，防止多开
- 配置持久化在 `%APPDATA%\AIBridge\config.json`

## 快速开始

### 运行（开发模式）

需要 **Windows 10/11 + Python 3.11+**

```cmd
git clone https://github.com/KongC-kc/bridge-app.git
cd bridge-app
pip install -r requirements.txt
python app.py
```

> Win10 用户如弹窗报 WebView2 缺失，去 [Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) 装 x64 版。

### 打包成 exe

```cmd
pip install pyinstaller
pyinstaller AIBridge.spec
```

产物在 `dist\AIBridge.exe`，单文件约 30-50MB，双击运行不依赖 Python。

## 客户端配置

在 Codex / Claude Code / Cockpit Tools 等工具里配置：

| 字段 | 值 |
|---|---|
| Base URL | `http://127.0.0.1:4000/v1`（端口可在设置里改） |
| API Key | 应用顶部「Key」栏的 `sk-xxxxx`，点击复制 |
| Model | 任意（开了"强制使用账号默认模型"会自动覆盖） |
| 端点类型 | Responses API |

多个中转站（GPT 中转、GLM、DeepSeek…）可以同时在工具里配不同 channel，按需切换。

## 架构

```
┌──────────────┐      ┌─────────────────────────────┐      ┌──────────────┐
│  Codex /     │      │       AI Bridge             │      │  上游模型     │
│  Claude Code │─────▶│  http://127.0.0.1:4000/v1   │─────▶│  GLM / DS /  │
│  等客户端     │◀─────│  Responses ↔ Chat 桥接      │◀─────│  Qwen / ...  │
└──────────────┘      └─────────────────────────────┘      └──────────────┘
                           │
                     ┌─────┴─────┐
                     │  GUI 管理  │
                     │  多账号切换 │
                     └───────────┘
```

核心是一个 FastAPI 服务 + PyWebView 桌面窗口。请求到达时根据 GUI 中"当前账号"的配置动态路由到对应上游。

## 文件结构

```
bridge-app/
├── app.py              # 入口：PyWebView 窗口 + uvicorn 线程 + JS API
├── bridge.py           # HTTP 服务：Responses ↔ Chat 双向转换
├── config_store.py     # 配置存储 (%APPDATA%\AIBridge)
├── providers.py        # 内置 Provider 预设
├── ui/
│   └── index.html      # 前端 UI（HTML + 内联 CSS/JS）
├── requirements.txt
├── AIBridge.spec       # PyInstaller 配置
└── README.md
```

## 技术栈

- **后端**：Python + FastAPI + Uvicorn + HTTPX
- **前端**：原生 HTML/CSS/JS（无框架，打包体积小）
- **桌面**：PyWebView（EdgeWebView2 内核，Win11 自带）+ Pystray（系统托盘）
- **打包**：PyInstaller → 单文件 exe

## 排错

**启动报"无 active account"** — UI 里加一个账号并点"设为当前"。

**Codex 报模型 404** — 进设置确认"强制使用账号默认模型"已开，且账号默认模型名是上游真实存在的。

**流式输出中断** — v0.4+ 已修复：每请求独立连接池 + SSE 分包安全解析 + 网络异常自动恢复。

**端口被占** — 进设置改端口，会自动重启 bridge。

**WebView2 报错** — Win10 装 [Edge WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)，Win11 自带。

## License

[MIT](LICENSE)
