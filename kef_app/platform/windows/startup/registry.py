from __future__ import annotations

from dataclasses import dataclass

import winreg

from .common import STARTUP_KEY


@dataclass(frozen=True, slots=True)
class RegistryStartupEntry:
    name: str
    command: str


def read_registry_commands() -> tuple[RegistryStartupEntry, ...]:
    entries: list[RegistryStartupEntry] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_READ) as key:
            index = 0
            while True:
                try:
                    name, value, _value_type = winreg.EnumValue(key, index)
                except OSError:
                    break
                entries.append(RegistryStartupEntry(name=str(name), command=str(value or "")))
                index += 1
    except (FileNotFoundError, OSError):
        return ()
    return tuple(entries)


def read_registry_command(task_name: str) -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, task_name)
            return str(value or "")
    except (FileNotFoundError, OSError):
        return ""


def delete_registry_commands(value_names: tuple[str, ...]) -> None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_SET_VALUE) as key:
            for name in value_names:
                try:
                    winreg.DeleteValue(key, name)
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
