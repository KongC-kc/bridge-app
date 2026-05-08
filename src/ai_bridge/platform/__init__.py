"""Platform abstraction layer."""
import sys


def get_platform():
    if sys.platform == "win32":
        from .windows import WindowsPlatform
        return WindowsPlatform()
    elif sys.platform == "darwin":
        raise NotImplementedError("macOS support coming soon")
    else:
        raise NotImplementedError("Linux support coming soon")
