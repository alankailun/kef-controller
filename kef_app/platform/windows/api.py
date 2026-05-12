from __future__ import annotations

import ctypes
import os
import sys
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from typing import Optional

MB_ICONINFORMATION = 0x40
ERROR_ALREADY_EXISTS = 183
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

WM_POWERBROADCAST = 0x0218
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012
PBT_POWERSETTINGCHANGE = 0x8013
DEVICE_NOTIFY_WINDOW_HANDLE = 0

LID_CLOSED = 0
LID_OPENED = 1

WM_WTSSESSION_CHANGE = 0x02B1
WTS_SESSION_LOCK = 0x0007
WTS_SESSION_UNLOCK = 0x0008
WTS_SESSION_DESKTOP_READY = 0x000F
NOTIFY_FOR_THIS_SESSION = 0

ENDSESSION_CLOSEAPP = 0x00000001
ENDSESSION_CRITICAL = 0x40000000
ENDSESSION_LOGOFF = 0x80000000

wtsapi32 = ctypes.WinDLL("Wtsapi32.dll")
WTSRegisterSessionNotification = wtsapi32.WTSRegisterSessionNotification
WTSRegisterSessionNotification.argtypes = [ctypes.c_void_p, ctypes.c_uint]
WTSRegisterSessionNotification.restype = ctypes.c_int
WTSUnRegisterSessionNotification = wtsapi32.WTSUnRegisterSessionNotification
WTSUnRegisterSessionNotification.argtypes = [ctypes.c_void_p]
WTSUnRegisterSessionNotification.restype = ctypes.c_int

user32 = ctypes.WinDLL("user32", use_last_error=True)
RegisterPowerSettingNotification = user32.RegisterPowerSettingNotification
RegisterPowerSettingNotification.restype = wintypes.HANDLE
UnregisterPowerSettingNotification = user32.UnregisterPowerSettingNotification
UnregisterPowerSettingNotification.argtypes = [wintypes.HANDLE]
UnregisterPowerSettingNotification.restype = wintypes.BOOL

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
GetCommandLineW = kernel32.GetCommandLineW
GetCommandLineW.restype = wintypes.LPWSTR

OpenProcess = kernel32.OpenProcess
OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
OpenProcess.restype = wintypes.HANDLE

QueryFullProcessImageNameW = kernel32.QueryFullProcessImageNameW
QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
QueryFullProcessImageNameW.restype = wintypes.BOOL

CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [wintypes.HANDLE]
CloseHandle.restype = wintypes.BOOL


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    @classmethod
    def from_string(cls, value: str) -> "GUID":
        parsed = uuid.UUID(value)
        node = parsed.node.to_bytes(6, "big")
        data4 = (ctypes.c_ubyte * 8)(parsed.clock_seq_hi_variant, parsed.clock_seq_low, *node)
        return cls(parsed.time_low, parsed.time_mid, parsed.time_hi_version, data4)

    def to_uuid(self) -> uuid.UUID:
        node = int.from_bytes(bytes(self.Data4[2:]), "big")
        return uuid.UUID(
            fields=(
                int(self.Data1),
                int(self.Data2),
                int(self.Data3),
                int(self.Data4[0]),
                int(self.Data4[1]),
                node,
            )
        )

    def canonical(self) -> str:
        return str(self.to_uuid()).upper()


class POWERBROADCAST_SETTING(ctypes.Structure):
    _fields_ = [
        ("PowerSetting", GUID),
        ("DataLength", wintypes.DWORD),
        ("Data", ctypes.c_ubyte * 1),
    ]


RegisterPowerSettingNotification.argtypes = [wintypes.HANDLE, ctypes.POINTER(GUID), wintypes.DWORD]

GUID_LIDSWITCH_STATE_CHANGE = GUID.from_string("{BA3E0F4D-B817-4094-A2D1-D56379E6A0F3}")

POWER_SETTING_GUIDS: tuple[tuple[str, GUID], ...] = (
    ("GUID_LIDSWITCH_STATE_CHANGE", GUID_LIDSWITCH_STATE_CHANGE),
)

_POWER_SETTING_NAME_BY_GUID = {guid.canonical(): name for name, guid in POWER_SETTING_GUIDS}


@dataclass(frozen=True)
class PowerSettingChange:
    name: str
    guid: str
    value: int | None
    label: str
    data_hex: str


def _label_power_setting_value(name: str, value: int | None) -> str:
    if value is None:
        return "raw"
    if name == "GUID_LIDSWITCH_STATE_CHANGE":
        return {
            LID_CLOSED: "LidClosed",
            LID_OPENED: "LidOpened",
        }.get(value, f"LIDSWITCH_STATE({value})")
    return str(value)


def decode_power_setting_change(lparam: int) -> PowerSettingChange | None:
    if not lparam:
        return None
    setting = ctypes.cast(lparam, ctypes.POINTER(POWERBROADCAST_SETTING)).contents
    guid = setting.PowerSetting.canonical()
    name = _POWER_SETTING_NAME_BY_GUID.get(guid, guid)
    data_length = int(setting.DataLength)
    data_offset = POWERBROADCAST_SETTING.Data.offset
    data = ctypes.string_at(lparam + data_offset, data_length) if data_length > 0 else b""
    value = int.from_bytes(data[:4], "little") if data_length == 4 else None
    return PowerSettingChange(
        name=name,
        guid=guid,
        value=value,
        label=_label_power_setting_value(name, value),
        data_hex=data.hex(),
    )


def ensure_single_instance(log, mutex_name: str) -> Optional[int]:
    kernel32_local = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32_local.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32_local.CreateMutexW.restype = wintypes.HANDLE

    handle = kernel32_local.CreateMutexW(None, True, mutex_name)
    last_error = ctypes.get_last_error()

    if not handle:
        log.info(f"Failed to create single-instance mutex; skipping the check | err={last_error}")
        return None

    if last_error == ERROR_ALREADY_EXISTS:
        log.info("Another KEF Controller instance is already running; exiting this instance")
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                "KEF Controller is already running.\nCheck the system tray or Task Manager.",
                "KEF Controller",
                MB_ICONINFORMATION,
            )
        except Exception as exc:
            log.info(f"Failed to show the already-running message box | {exc}")
        CloseHandle(handle)
        sys.exit(0)

    log.info("Single-instance mutex acquired")
    return handle


def get_raw_command_line() -> str:
    try:
        return GetCommandLineW() or ""
    except Exception as exc:
        return f"<unavailable: {exc}>"


def get_process_image_path(pid: int) -> str:
    handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return f"<OpenProcess failed err={ctypes.get_last_error()}>"

    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return f"<QueryFullProcessImageNameW failed err={ctypes.get_last_error()}>"
    finally:
        CloseHandle(handle)


def guess_launch_source(parent_image_path: str) -> str:
    name = os.path.basename(parent_image_path).lower()
    if not name or name.startswith("<"):
        return "unknown"
    if name in {"cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "conhost.exe"}:
        return "terminal_or_console"
    if name in {"taskeng.exe", "taskhostw.exe", "taskhost.exe"}:
        return "task_scheduler_likely"
    if name == "explorer.exe":
        return "explorer_or_startup_likely"
    if name in {"services.exe", "svchost.exe"}:
        return "service_or_system_host_likely"
    return name


def decode_query_end_session_flags(lparam: int) -> str:
    flags: list[str] = []
    if lparam & ENDSESSION_CLOSEAPP:
        flags.append("CLOSEAPP")
    if lparam & ENDSESSION_CRITICAL:
        flags.append("CRITICAL")
    if lparam & ENDSESSION_LOGOFF:
        flags.append("LOGOFF")
    return "|".join(flags) if flags else "NONE"
