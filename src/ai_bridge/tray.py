"""System tray icon management."""
import os
import threading

import pystray
from PIL import Image

from ._resources import resource_path


def _load_icon_image():
    icon_path = resource_path("assets/icon.ico")
    if os.path.exists(icon_path):
        return Image.open(icon_path)
    return Image.new("RGBA", (64, 64), (99, 102, 241))


class TrayIcon:
    def __init__(self, window, on_quit=None):
        self.window = window
        self._on_quit = on_quit
        self._icon = None

    def start(self):
        image = _load_icon_image()
        menu = pystray.Menu(
            pystray.MenuItem("显示主窗口", self._show_window, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", self._quit),
        )
        self._icon = pystray.Icon("AI Bridge", image, "AI Bridge", menu)
        threading.Thread(target=self._icon.run, daemon=True).start()

    def stop(self):
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass

    def _show_window(self, icon, item):
        try:
            self.window.restore()
            self.window.show()
        except Exception:
            pass

    def _quit(self, icon, item):
        if self._on_quit:
            self._on_quit()
        self.stop()
        try:
            self.window.destroy()
        except Exception:
            pass
