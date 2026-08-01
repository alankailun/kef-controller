from __future__ import annotations

import ctypes
import os
import socket
import sys
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from typing import Callable, Optional

from ...structured_logging import log_structured

MB_ICONINFORMATION = 0x40
ERROR_ALREADY_EXISTS = 183
ERROR_SUCCESS = 0
ERROR_NETWORK_UNREACHABLE = 1231
ERROR_HOST_UNREACHABLE = 1232
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
AF_UNSPEC = 0
SCOPE_LEVEL_COUNT = 16
IF_MAX_STRING_SIZE = 256

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

ShutdownBlockReasonCreate = user32.ShutdownBlockReasonCreate
ShutdownBlockReasonCreate.argtypes = [wintypes.HWND, wintypes.LPCWSTR]
ShutdownBlockReasonCreate.restype = wintypes.BOOL
ShutdownBlockReasonDestroy = user32.ShutdownBlockReasonDestroy
ShutdownBlockReasonDestroy.argtypes = [wintypes.HWND]
ShutdownBlockReasonDestroy.restype = wintypes.BOOL


def create_shutdown_block_reason(hwnd: int, reason: str) -> None:
    if not ShutdownBlockReasonCreate(hwnd, reason):
        raise ctypes.WinError(ctypes.get_last_error())


def destroy_shutdown_block_reason(hwnd: int) -> None:
    if not ShutdownBlockReasonDestroy(hwnd):
        raise ctypes.WinError(ctypes.get_last_error())

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


iphlpapi = ctypes.WinDLL("Iphlpapi", use_last_error=True)


class MIB_IPINTERFACE_ROW_PREFIX(ctypes.Structure):
    _fields_ = [
        ("Family", wintypes.USHORT),
        ("InterfaceLuid", ctypes.c_ulonglong),
        ("InterfaceIndex", wintypes.ULONG),
        ("MaxReassemblySize", wintypes.ULONG),
        ("InterfaceIdentifier", ctypes.c_ulonglong),
        ("MinRouterAdvertisementInterval", wintypes.ULONG),
        ("MaxRouterAdvertisementInterval", wintypes.ULONG),
        ("AdvertisingEnabled", ctypes.c_ubyte),
        ("ForwardingEnabled", ctypes.c_ubyte),
        ("WeakHostSend", ctypes.c_ubyte),
        ("WeakHostReceive", ctypes.c_ubyte),
        ("UseAutomaticMetric", ctypes.c_ubyte),
        ("UseNeighborUnreachabilityDetection", ctypes.c_ubyte),
        ("ManagedAddressConfigurationSupported", ctypes.c_ubyte),
        ("OtherStatefulConfigurationSupported", ctypes.c_ubyte),
        ("AdvertiseDefaultRoute", ctypes.c_ubyte),
        ("RouterDiscoveryBehavior", ctypes.c_int),
        ("DadTransmits", wintypes.ULONG),
        ("BaseReachableTime", wintypes.ULONG),
        ("RetransmitTime", wintypes.ULONG),
        ("PathMtuDiscoveryTimeout", wintypes.ULONG),
        ("LinkLocalAddressBehavior", ctypes.c_int),
        ("LinkLocalAddressTimeout", wintypes.ULONG),
        ("ZoneIndices", wintypes.ULONG * SCOPE_LEVEL_COUNT),
        ("SitePrefixLength", wintypes.ULONG),
        ("Metric", wintypes.ULONG),
        ("NlMtu", wintypes.ULONG),
        ("Connected", ctypes.c_ubyte),
    ]


_IP_INTERFACE_CHANGE_CALLBACK = ctypes.WINFUNCTYPE(
    None,
    wintypes.LPVOID,
    ctypes.POINTER(MIB_IPINTERFACE_ROW_PREFIX),
    ctypes.c_int,
)

NotifyIpInterfaceChange = iphlpapi.NotifyIpInterfaceChange
NotifyIpInterfaceChange.argtypes = [
    wintypes.USHORT,
    _IP_INTERFACE_CHANGE_CALLBACK,
    wintypes.LPVOID,
    ctypes.c_ubyte,
    ctypes.POINTER(wintypes.HANDLE),
]
NotifyIpInterfaceChange.restype = wintypes.DWORD

CancelMibChangeNotify2 = iphlpapi.CancelMibChangeNotify2
CancelMibChangeNotify2.argtypes = [wintypes.HANDLE]
CancelMibChangeNotify2.restype = wintypes.DWORD

ConvertInterfaceLuidToAlias = iphlpapi.ConvertInterfaceLuidToAlias
ConvertInterfaceLuidToAlias.argtypes = [ctypes.POINTER(ctypes.c_ulonglong), wintypes.LPWSTR, ctypes.c_size_t]
ConvertInterfaceLuidToAlias.restype = wintypes.DWORD

GetBestInterfaceEx = iphlpapi.GetBestInterfaceEx
GetBestInterfaceEx.argtypes = [wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD)]
GetBestInterfaceEx.restype = wintypes.DWORD

_IP_INTERFACE_NOTIFICATION_TYPES = {
    0: "ParameterNotification",
    1: "AddInstance",
    2: "DeleteInstance",
    3: "InitialNotification",
}


@dataclass(frozen=True)
class IpInterfaceChangeEvent:
    notification: str
    notification_type: int
    family: int | None
    interface_index: int | None
    interface_alias: str
    connected: bool | None
    metric: int | None
    nl_mtu: int | None


def _interface_alias_from_luid(interface_luid: int) -> str:
    buffer = ctypes.create_unicode_buffer(IF_MAX_STRING_SIZE + 1)
    luid = ctypes.c_ulonglong(interface_luid)
    status = ConvertInterfaceLuidToAlias(ctypes.byref(luid), buffer, len(buffer))
    if status != ERROR_SUCCESS:
        return f"<alias_unavailable:{status}>"
    return buffer.value or "<empty>"


class IpInterfaceChangeMonitor:
    def __init__(self, callback: Callable[[IpInterfaceChangeEvent], None]):
        self.callback = callback
        self.handle = wintypes.HANDLE()
        self._callback = _IP_INTERFACE_CHANGE_CALLBACK(self._handle_change)
        self.active = False
        self.error = ""

    def start(self) -> "IpInterfaceChangeMonitor":
        status = NotifyIpInterfaceChange(AF_UNSPEC, self._callback, None, False, ctypes.byref(self.handle))
        if status != ERROR_SUCCESS:
            self.error = f"NotifyIpInterfaceChange failed: {status}"
            return self
        self.active = True
        return self

    def close(self) -> None:
        if not self.active or not self.handle:
            return
        try:
            CancelMibChangeNotify2(self.handle)
        finally:
            self.active = False
            self.handle = wintypes.HANDLE()

    def _handle_change(self, _context, row_ptr, notification_type: int) -> None:
        try:
            row = row_ptr.contents if row_ptr else None
            interface_alias = _interface_alias_from_luid(int(row.InterfaceLuid)) if row else "<unknown>"
            event = IpInterfaceChangeEvent(
                notification=_IP_INTERFACE_NOTIFICATION_TYPES.get(notification_type, str(notification_type)),
                notification_type=int(notification_type),
                family=int(row.Family) if row else None,
                interface_index=int(row.InterfaceIndex) if row else None,
                interface_alias=interface_alias,
                connected=bool(row.Connected) if row else None,
                metric=int(row.Metric) if row else None,
                nl_mtu=int(row.NlMtu) if row else None,
            )
            self.callback(event)
        except Exception:
            return


class SOCKADDR_IN(ctypes.Structure):
    _fields_ = [
        ("sin_family", wintypes.USHORT),
        ("sin_port", wintypes.USHORT),
        ("sin_addr", ctypes.c_ubyte * 4),
        ("sin_zero", ctypes.c_ubyte * 8),
    ]


def has_best_route_to_ipv4(ip: str) -> bool | None:
    try:
        packed_ip = socket.inet_aton(ip)
    except OSError:
        return None

    address = SOCKADDR_IN()
    address.sin_family = socket.AF_INET
    address.sin_port = 0
    address.sin_addr[:] = packed_ip
    interface_index = wintypes.DWORD()
    try:
        status = int(GetBestInterfaceEx(ctypes.byref(address), ctypes.byref(interface_index)))
    except OSError:
        return None

    if status == ERROR_SUCCESS:
        return bool(interface_index.value)
    if status in {ERROR_NETWORK_UNREACHABLE, ERROR_HOST_UNREACHABLE}:
        return False
    return None


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
# Display on/off/dim. On Windows 11 Modern Standby (S0 idle) the display turning
# off is the earliest reliable "user stepped away" signal: it usually arrives
# while the message pump is still alive and well before a clean PBT_APMSUSPEND
# (which under Modern Standby may be late or never fire). We use it as an extra
# display-off standby trigger.
GUID_CONSOLE_DISPLAY_STATE = GUID.from_string("{6FE69556-704A-47A0-8F24-C28D936FDA47}")

MONITOR_DISPLAY_OFF = 0
MONITOR_DISPLAY_ON = 1
MONITOR_DISPLAY_DIM = 2

POWER_SETTING_GUIDS: tuple[tuple[str, GUID], ...] = (
    ("GUID_LIDSWITCH_STATE_CHANGE", GUID_LIDSWITCH_STATE_CHANGE),
    ("GUID_CONSOLE_DISPLAY_STATE", GUID_CONSOLE_DISPLAY_STATE),
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
    if name == "GUID_CONSOLE_DISPLAY_STATE":
        return {
            MONITOR_DISPLAY_OFF: "DisplayOff",
            MONITOR_DISPLAY_ON: "DisplayOn",
            MONITOR_DISPLAY_DIM: "DisplayDim",
        }.get(value, f"CONSOLE_DISPLAY_STATE({value})")
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
        log_structured(
            log, "WARN", action="SINGLE_INSTANCE", reason="startup", cause="mutex_create_failed", error_code=last_error
        )
        return None

    if last_error == ERROR_ALREADY_EXISTS:
        log_structured(log, "EVENT", action="SINGLE_INSTANCE", reason="startup", name="EXISTING_INSTANCE_DETECTED")
        try:
            ctypes.windll.user32.MessageBoxW(
                0,
                "KEF Controller is already running.\nCheck the system tray or Task Manager.",
                "KEF Controller",
                MB_ICONINFORMATION,
            )
        except Exception as exc:
            log_structured(
                log, "WARN", action="SINGLE_INSTANCE", reason="startup", cause="message_box_show_failed", error=repr(exc)
            )
        CloseHandle(handle)
        sys.exit(0)

    log_structured(log, "EVENT", action="SINGLE_INSTANCE", reason="startup", name="MUTEX_ACQUIRED")
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
