from __future__ import annotations

import winreg

from .common import STARTUP_KEY


def read_registry_command(task_name: str) -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, task_name)
            return str(value or "")
    except (FileNotFoundError, OSError):
        return ""


def delete_registry_command(task_name: str) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, task_name)
            except FileNotFoundError:
                pass
    except OSError:
        pass


def write_registry_command(task_name: str, command: str) -> tuple[bool, str]:
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY) as key:
            winreg.SetValueEx(key, task_name, 0, winreg.REG_SZ, command)
        return True, ""
    except OSError as exc:
        return False, str(exc)
