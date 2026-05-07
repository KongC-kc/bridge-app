"""主入口：启动 uvicorn 后台线程 + PyWebView 主窗口。"""
import os
import sys
import threading
import time
from pathlib import Path

import uvicorn
import webview

import bridge
import config_store
import providers


def _resource_path(rel: str) -> str:
    """PyInstaller bundle 兼容的资源路径。"""
    base = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    return os.path.join(base, rel)


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
        # wait for startup
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


# --------- JS API exposed to frontend ---------
class API:
    # config CRUD
    def list_accounts(self):
        cfg = config_store.load()
        return cfg.get("accounts", [])

    def get_settings(self):
        cfg = config_store.load()
        return {
            "port": cfg.get("port"),
            "proxy_api_key": cfg.get("proxy_api_key"),
            "active_account_id": cfg.get("active_account_id"),
            "force_model": cfg.get("force_model", True),
        }

    def update_settings(self, data):
        cfg = config_store.update_settings(data or {})
        # 端口变化要重启
        if SERVER.is_running() and SERVER.port != cfg.get("port"):
            SERVER.start(cfg.get("port", 4000))
        return self.get_settings()

    def add_account(self, data):
        return config_store.add_account(data or {})

    def update_account(self, account_id, data):
        return config_store.update_account(account_id, data or {})

    def delete_account(self, account_id):
        return config_store.delete_account(account_id)

    def set_active(self, account_id):
        return config_store.set_active(account_id)

    def list_providers(self):
        return providers.list_providers()

    # bridge control
    def get_status(self):
        cfg = config_store.load()
        port = cfg.get("port", 4000)
        return {
            "running": SERVER.is_running(),
            "port": port,
            "url": f"http://127.0.0.1:{port}/v1",
            "proxy_api_key": cfg.get("proxy_api_key"),
        }

    def start_bridge(self):
        cfg = config_store.load()
        ok = SERVER.start(cfg.get("port", 4000))
        return {"ok": ok, **self.get_status()}

    def stop_bridge(self):
        SERVER.stop()
        return self.get_status()

    def restart_bridge(self):
        cfg = config_store.load()
        SERVER.start(cfg.get("port", 4000))
        return self.get_status()


def main():
    # 自动启动 bridge
    cfg = config_store.load()
    SERVER.start(cfg.get("port", 4000))

    api = API()
    html_path = _resource_path("ui/index.html")

    window = webview.create_window(
        "GLM Bridge — API 中转管理",
        html_path,
        js_api=api,
        width=1280, height=820, min_size=(960, 640),
        background_color="#f5f7fb",
    )

    def on_closed():
        SERVER.stop()

    window.events.closed += on_closed
    webview.start(debug=False)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print("=" * 60)
        print("应用启动失败：", e)
        print("=" * 60)
        traceback.print_exc()
        print("=" * 60)
        input("按回车退出...")
