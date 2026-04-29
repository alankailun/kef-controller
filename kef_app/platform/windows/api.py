from __future__ import annotations

import ctypes
import os
import sys
from ctypes import wintypes
from typing import Optional

MB_ICONINFORMATION = 0x40
ERROR_ALREADY_EXISTS = 183
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

WM_POWERBROADCAST = 0x0218
PBT_APMSUSPEND = 0x0004
PBT_APMRESUMESUSPEND = 0x0007
PBT_APMRESUMEAUTOMATIC = 0x0012

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
