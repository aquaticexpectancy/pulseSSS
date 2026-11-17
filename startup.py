"""Start pulseSSS when you sign in to Windows (current user only, via the Run registry key)."""

import sys
from pathlib import Path

APP = "pulseSSS"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
LAUNCHER = Path(__file__).resolve().parent / "pulseSSS.pyw"


def pythonw() -> str:
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def command() -> str:
    return f'"{pythonw()}" "{LAUNCHER}" --quiet'


def enabled() -> bool:
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP)
            return True
    except OSError:
        return False


def set_enabled(on: bool):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(k, APP)
            except OSError:
                pass
