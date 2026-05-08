"""Windows platform: registry auto-start, mutex single-instance."""
import ctypes
import sys
import winreg

from .base import BasePlatform

APP_NAME = "AI Bridge"
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
MUTEX_NAME = "Global\\AIBridge_SingleInstance"


class WindowsPlatform(BasePlatform):
    def set_auto_start(self, enabled: bool) -> None:
        exe_path = self.get_app_path()
        if not exe_path:
            return
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

    def check_single_instance(self) -> bool:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW(None, False, MUTEX_NAME)
        return ctypes.GetLastError() != 183

    def get_app_path(self) -> str:
        if getattr(sys, "frozen", False):
            return sys.executable
        return ""
