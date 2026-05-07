"""主入口：启动 uvicorn 后台线程 + PyWebView 主窗口。"""
import ctypes
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

APP_NAME = "AI Bridge"
APP_TITLE = f"{APP_NAME} — API 中转管理"
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _resource_path(rel: str) -> str:
    """PyInstaller bundle 兼容的资源路径。"""
    base = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    return os.path.join(base, rel)


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


def _get_auto_start() -> bool:
    if os.name != "nt":
        return False
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_READ)
        _, _ = winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


# --------- system tray (Windows) ---------
class SysTray:
    """Minimal Windows system tray icon using ctypes + Shell_NotifyIconW."""

    WM_TRAY = 0x8001  # app-defined message
    WM_DESTROY = 0x0002
    WM_COMMAND = 0x0111
    WM_CLOSE = 0x0010

    def __init__(self, window):
        self.window = window
        self._hwnd = None
        self._nid = None
        self._thread = None
        self._alive = False
        self._menu_show = 1001
        self._menu_quit = 1002

    def start(self):
        self._alive = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._alive = False
        if self._hwnd:
            ctypes.windll.user32.PostMessageW(self._hwnd, self.WM_CLOSE, 0, 0)

    def _run(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        shell32 = ctypes.windll.shell32

        # Register window class
        wnd_class = ctypes.c_wchar * 64
        class_name = "AIBridgeTrayClass"
        hinstance = kernel32.GetModuleHandleW(None)

        wndcls = ctypes.create_string_buffer(ctypes.sizeof(ctypes.c_void_p) * 12)
        #WNDCLASSEXW structure
        class WNDCLASSEX(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("style", ctypes.c_uint),
                ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int),
                ("hInstance", ctypes.c_void_p),
                ("hIcon", ctypes.c_void_p),
                ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p),
                ("lpszMenuName", ctypes.c_wchar_p),
                ("lpszClassName", ctypes.c_wchar_p),
                ("hIconSm", ctypes.c_void_p),
            ]

        @ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p)
        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == self.WM_TRAY:
                if lparam == 0x0205:  # WM_RBUTTONUP - right click
                    self._show_popup_menu(hwnd)
                elif lparam == 0x0202:  # WM_LBUTTONUP - left click
                    self._show_window()
            elif msg == self.WM_COMMAND:
                cmd = wparam & 0xFFFF
                if cmd == self._menu_show:
                    self._show_window()
                elif cmd == self._menu_quit:
                    self._quit_app(hwnd)
            elif msg == self.WM_DESTROY:
                shell32.Shell_NotifyIconW(2, ctypes.byref(self._nid))  # NIM_DELETE
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        wc = WNDCLASSEX()
        wc.cbSize = ctypes.sizeof(WNDCLASSEX)
        wc.lpfnWndProc = wnd_proc
        wc.hInstance = hinstance
        wc.lpszClassName = class_name
        user32.RegisterClassExW(ctypes.byref(wc))

        self._hwnd = user32.CreateWindowExW(
            0, class_name, "AIBridgeTray", 0, 0, 0, 0, 0, 0, 0, hinstance, 0
        )

        # NOTIFYICONDATAW
        class NOTIFYICONDATA(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("hWnd", ctypes.c_void_p),
                ("uID", ctypes.c_uint),
                ("uFlags", ctypes.c_uint),
                ("uCallbackMessage", ctypes.c_uint),
                ("hIcon", ctypes.c_void_p),
                ("szTip", ctypes.c_wchar * 128),
                ("dwState", ctypes.c_uint),
                ("dwStateMask", ctypes.c_uint),
                ("szInfo", ctypes.c_wchar * 256),
                ("uVersion", ctypes.c_uint),
                ("szInfoTitle", ctypes.c_wchar * 64),
                ("dwInfoFlags", ctypes.c_uint),
            ]

        nid = NOTIFYICONDATA()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATA)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = 0x00000007  # NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = self.WM_TRAY
        nid.hIcon = user32.LoadIconW(0, 32512)  # IDI_APPLICATION
        nid.szTip = APP_NAME
        self._nid = nid

        shell32.Shell_NotifyIconW(0, ctypes.byref(nid))  # NIM_ADD

        # Message loop
        msg = ctypes.create_string_buffer(ctypes.sizeof(ctypes.c_void_p) * 2)
        while self._alive:
            result = user32.GetMessageW(ctypes.byref(msg), 0, 0, 0)
            if result == 0:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    def _show_popup_menu(self, hwnd):
        user32 = ctypes.windll.user32
        user32.SetForegroundWindow(hwnd)
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, 0, self._menu_show, "显示主窗口")
        user32.AppendMenuW(menu, 0x800, 0, None)  # MF_SEPARATOR
        user32.AppendMenuW(menu, 0, self._menu_quit, "退出")
        pt = ctypes.create_string_buffer(ctypes.sizeof(ctypes.c_void_p) * 2)
        user32.GetCursorPos(ctypes.byref(pt))
        x = ctypes.c_long.from_buffer(pt, 0).value
        y = ctypes.c_long.from_buffer(pt, ctypes.sizeof(ctypes.c_long)).value
        user32.TrackPopupMenu(menu, 0, x, y, 0, hwnd, None)
        user32.DestroyMenu(menu)

    def _show_window(self):
        try:
            self.window.restore()
            self.window.show()
        except Exception:
            pass

    def _quit_app(self, hwnd):
        self._alive = False
        ctypes.windll.user32.DestroyWindow(hwnd)
        # Use pywebview's destroy to close the main window cleanly
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
_tray: SysTray | None = None
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
            # window was destroyed externally, just stop server
            SERVER.stop()

    window.events.closed += on_closed

    if minimize_to_tray:
        _tray = SysTray(window)

        def on_closing():
            if _quitting:
                return  # allow close
            # 隐藏窗口而非关闭
            window.hide()

        window.events.closing += on_closing

    if silent_start:
        # 延迟隐藏，等窗口创建完成后执行
        def hide_on_start():
            time.sleep(0.5)
            try:
                window.hide()
            except Exception:
                pass
        threading.Thread(target=hide_on_start, daemon=True).start()

    if _tray:
        _tray.start()

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
