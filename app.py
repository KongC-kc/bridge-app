"""主入口：启动 uvicorn 后台线程 + PyWebView 主窗口。"""
import os
import sys
import threading
import time
from pathlib import Path

import httpx
import pystray
from PIL import Image

import uvicorn
import webview

import bridge
import config_store
import providers

APP_NAME = "AI Bridge"
APP_TITLE = f"{APP_NAME} — API 中转管理"
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MUTEX_NAME = "Global\\AIBridge_SingleInstance"


def _resource_path(rel: str) -> str:
    """PyInstaller bundle 兼容的资源路径。"""
    base = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    return os.path.join(base, rel)


def _load_icon_image():
    """加载托盘图标。"""
    icon_path = _resource_path("assets/icon.ico")
    if os.path.exists(icon_path):
        return Image.open(icon_path)
    # fallback: 简单色块
    return Image.new("RGBA", (64, 64), (99, 102, 241))


# --------- single instance (Windows mutex) ---------
def _check_single_instance() -> bool:
    """返回 True 表示我们是第一个实例。"""
    if os.name != "nt":
        return True
    import ctypes
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    # ERROR_ALREADY_EXISTS = 183
    return ctypes.GetLastError() != 183


# --------- auto start (Windows registry) ---------
def _set_auto_start(enabled: bool):
    if os.name != "nt":
        return
    import winreg
    exe_path = sys.executable if getattr(sys, "frozen", False) else str(Path(__file__).resolve())
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE)
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, f'"{exe_path}"')
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
    except Exception:
        pass


# --------- system tray (pystray) ---------
class TrayIcon:
    def __init__(self, window):
        self.window = window
        self._icon = None

    def start(self):
        image = _load_icon_image()
        menu = pystray.Menu(
            pystray.MenuItem("显示主窗口", self._show_window, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._quit),
        )
        self._icon = pystray.Icon(APP_NAME, image, APP_NAME, menu)
        threading.Thread(target=self._icon.run, daemon=True).start()

    def stop(self):
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass

    def _show_window(self):
        try:
            self.window.restore()
            self.window.show()
        except Exception:
            pass

    def _quit(self):
        global _quitting
        _quitting = True
        SERVER.stop()
        self.stop()
        try:
            self.window.destroy()
        except Exception:
            pass


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
            "auto_start": cfg.get("auto_start", False),
            "silent_start": cfg.get("silent_start", False),
            "minimize_to_tray": cfg.get("minimize_to_tray", False),
        }

    def update_settings(self, data):
        old_cfg = config_store.load()
        cfg = config_store.update_settings(data or {})
        # 端口变化要重启
        if SERVER.is_running() and SERVER.port != cfg.get("port"):
            SERVER.start(cfg.get("port", 4000))
        # 开机自启变化要更新注册表
        new_auto = cfg.get("auto_start", False)
        if new_auto != old_cfg.get("auto_start", False):
            _set_auto_start(new_auto)
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

    def quit_app(self):
        """供前端/托盘调用，真正退出应用。"""
        global _quitting
        _quitting = True
        SERVER.stop()
        if _tray:
            _tray.stop()
        try:
            _window.destroy()
        except Exception:
            pass


# hold reference to the window for tray access
_window = None


def main():
    global _tray, _window, _quitting

    # 单实例检测：已有实例运行则直接退出
    if not _check_single_instance():
        sys.exit(0)

    # 自动启动 bridge
    cfg = config_store.load()
    SERVER.start(cfg.get("port", 4000))

    # 同步开机自启注册表状态
    _set_auto_start(cfg.get("auto_start", False))

    api = API()
    html_path = _resource_path("ui/index.html")

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
        _tray = TrayIcon(window)
        _tray.start()

        def on_closing():
            if _quitting:
                return
            window.hide()

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


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        print("=" * 60)
        print("应用启动失败：", e)
        print("=" * 60)
        traceback.print_exc()
        print("=" * 60)
        input("按回车退出...")
