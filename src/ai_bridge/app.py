"""主入口：启动 uvicorn 后台线程 + PyWebView 主窗口。"""
import sys
import threading
import time

import httpx
import uvicorn
import webview

from . import bridge, config, providers
from ._resources import resource_path
from .platform import get_platform
from .tray import TrayIcon
from . import quota

APP_NAME = "AI Bridge"
APP_TITLE = f"{APP_NAME} — API 中转管理"


# --------- uvicorn server in thread ---------
class BridgeServer:
    def __init__(self):
        self.server: uvicorn.Server | None = None
        self.thread: threading.Thread | None = None
        self.port: int = 4000

    def start(self, port: int):
        self.stop()
        self.port = port
        cfg = uvicorn.Config(bridge.app, host="127.0.0.1", port=port,
                             log_level="warning", access_log=False)
        self.server = uvicorn.Server(cfg)
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        for _ in range(50):
            if self.server.started:
                return True
            time.sleep(0.05)
        return self.server.started

    def stop(self):
        if self.server:
            self.server.should_exit = True
            if self.thread and self.thread.is_alive():
                self.thread.join(timeout=3)
        self.server = None
        self.thread = None

    def is_running(self) -> bool:
        return bool(self.server and self.server.started and not self.server.should_exit)


SERVER = BridgeServer()
_tray: TrayIcon | None = None
_quitting = False
_platform = get_platform()


# --------- JS API exposed to frontend ---------
class API:
    def list_accounts(self):
        cfg = config.load()
        return cfg.get("accounts", [])

    def get_settings(self):
        cfg = config.load()
        return {
            "port": cfg.get("port"),
            "proxy_api_key": cfg.get("proxy_api_key"),
            "active_account_id": cfg.get("active_account_id"),
            "force_model": cfg.get("force_model", True),
            "auto_start": cfg.get("auto_start", False),
            "silent_start": cfg.get("silent_start", False),
            "minimize_to_tray": cfg.get("minimize_to_tray", False),
        }

    def update_settings(self, data):
        old_cfg = config.load()
        cfg = config.update_settings(data or {})
        if SERVER.is_running() and SERVER.port != cfg.get("port"):
            SERVER.start(cfg.get("port", 4000))
        new_auto = cfg.get("auto_start", False)
        if new_auto != old_cfg.get("auto_start", False):
            _platform.set_auto_start(new_auto)
        return self.get_settings()

    def add_account(self, data):
        return config.add_account(data or {})

    def update_account(self, account_id, data):
        return config.update_account(account_id, data or {})

    def delete_account(self, account_id):
        return config.delete_account(account_id)

    def set_active(self, account_id):
        return config.set_active(account_id)

    def list_providers(self):
        return providers.list_providers()

    def fetch_models(self, api_base, api_key):
        try:
            url = f"{api_base.rstrip('/')}/models"
            headers = {"Authorization": f"Bearer {api_key}"}
            r = httpx.get(url, headers=headers, timeout=15.0)
            if r.status_code != 200:
                return {"error": f"请求失败 ({r.status_code})"}
            data = r.json()
            model_ids = [m.get("id", "") for m in data.get("data", []) if m.get("id")]
            model_ids.sort()
            return {"models": model_ids}
        except Exception as e:
            return {"error": str(e)}

    def test_model(self, account_id, model=None):
        cfg = config.load()
        acc = next((a for a in cfg.get("accounts", []) if a["id"] == account_id), None)
        if not acc:
            return {"ok": False, "error": "未找到账号"}
        if not acc.get("api_key") or not acc.get("api_base"):
            return {"ok": False, "error": "账号缺少 API Key 或 API Base"}
        model = model or acc.get("default_model")
        if not model:
            return {"ok": False, "error": "未指定模型"}
        base = acc["api_base"].rstrip("/")
        headers = {"Authorization": f"Bearer {acc['api_key']}", "Content-Type": "application/json"}

        # Step 1: check /models to verify API key & model existence
        models_ok = False
        model_found = False
        models_latency = 0
        try:
            start = time.monotonic()
            r = httpx.get(f"{base}/models", headers=headers, timeout=15.0)
            models_latency = round((time.monotonic() - start) * 1000)
            if r.status_code == 200:
                models_ok = True
                model_ids = [m.get("id", "") for m in r.json().get("data", []) if m.get("id")]
                model_found = model in model_ids
            else:
                return {"ok": False, "error": f"模型列表请求失败 (HTTP {r.status_code})",
                        "latency_ms": models_latency}
        except httpx.TimeoutException:
            return {"ok": False, "error": "模型列表请求超时 (15s)"}
        except Exception as e:
            return {"ok": False, "error": f"模型列表请求异常: {e}"}

        if not model_found:
            return {"ok": False, "partial": True,
                    "error": f"API Key 有效但模型列表中未找到 {model}",
                    "latency_ms": models_latency, "model": model}

        # Step 2: try chat/completions to verify model actually responds
        try:
            body = {"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5}
            start = time.monotonic()
            r = httpx.post(f"{base}/chat/completions", json=body, headers=headers, timeout=15.0)
            latency = round((time.monotonic() - start) * 1000)
            if r.status_code == 200:
                return {"ok": True, "latency_ms": latency, "model": model}
            # chat failed but models ok -> partial
            msg = r.text[:300]
            try:
                data = r.json()
                err = data.get("error") or data.get("msg") or data.get("message") or ""
                if isinstance(err, dict):
                    msg = err.get("message") or err.get("msg") or str(err)
                elif isinstance(err, str) and err:
                    msg = err
            except Exception:
                pass
            return {"ok": False, "partial": True,
                    "error": f"模型存在但推理请求失败: HTTP {r.status_code} {msg[:150]}",
                    "latency_ms": latency, "model": model}
        except httpx.TimeoutException:
            return {"ok": False, "partial": True,
                    "error": "模型存在但推理请求超时",
                    "latency_ms": models_latency, "model": model}
        except Exception as e:
            return {"ok": False, "partial": True,
                    "error": f"模型存在但推理请求异常: {e}",
                    "latency_ms": models_latency, "model": model}

    def get_status(self):
        cfg = config.load()
        port = cfg.get("port", 4000)
        return {
            "running": SERVER.is_running(),
            "port": port,
            "url": f"http://127.0.0.1:{port}/v1",
            "proxy_api_key": cfg.get("proxy_api_key"),
        }

    def check_quota(self, account_id=None):
        cfg = config.load()
        if account_id:
            acc = next((a for a in cfg.get("accounts", []) if a["id"] == account_id), None)
        else:
            acc = config.get_active_account(cfg)
        if not acc:
            return {"ok": False, "error": "未找到账号"}
        if not acc.get("api_key") or not acc.get("api_base"):
            return {"ok": False, "error": "账号缺少 API Key 或 API Base"}
        return quota.fetch_quota(acc["api_base"], acc["api_key"])

    def start_bridge(self):
        cfg = config.load()
        ok = SERVER.start(cfg.get("port", 4000))
        return {"ok": ok, **self.get_status()}

    def stop_bridge(self):
        SERVER.stop()
        return self.get_status()

    def restart_bridge(self):
        cfg = config.load()
        SERVER.start(cfg.get("port", 4000))
        return self.get_status()

    def quit_app(self):
        global _quitting
        _quitting = True
        SERVER.stop()
        if _tray:
            _tray.stop()
        try:
            _window.destroy()
        except Exception:
            pass


_window = None


def _quit_from_tray():
    global _quitting
    _quitting = True
    SERVER.stop()


def main():
    global _tray, _window, _quitting

    if not _platform.check_single_instance():
        sys.exit(0)

    cfg = config.load()
    SERVER.start(cfg.get("port", 4000))
    _platform.set_auto_start(cfg.get("auto_start", False))

    api = API()
    html_path = resource_path("ui/index.html")

    minimize_to_tray = cfg.get("minimize_to_tray", False)
    silent_start = cfg.get("silent_start", False)

    window = webview.create_window(
        APP_TITLE,
        html_path,
        js_api=api,
        width=1280, height=820, min_size=(960, 640),
        background_color="#f5f7fb",
    )
    _window = window

    def on_closed():
        if not _quitting:
            SERVER.stop()

    window.events.closed += on_closed

    if minimize_to_tray:
        _tray = TrayIcon(window, on_quit=_quit_from_tray)
        _tray.start()

        def on_closing():
            if _quitting:
                return True
            window.hide()
            return False

        window.events.closing += on_closing

    if silent_start:
        def hide_on_start():
            time.sleep(0.5)
            try:
                window.hide()
            except Exception:
                pass
        threading.Thread(target=hide_on_start, daemon=True).start()

    webview.start(debug=False)
