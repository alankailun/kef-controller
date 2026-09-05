from __future__ import annotations

import queue
import threading
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class SpeakerUIPollResult:
    values: tuple[str | None, int | None, bool | None] = (None, None, None)
    status: Literal["success", "failed", "skipped"] = "skipped"


@dataclass(slots=True)
class IdentityState:
    current_ip: str = ""
    target_mac: str = ""
    speaker_name: str = ""
    speaker_model: str = ""
    speaker_firmware: str = ""
    last_matched_by: str = ""
    available: bool = False
    probe_failures: int = 0
    last_mac_discovery_mono: float = 0.0
    last_blind_discovery_mono: float = 0.0


@dataclass(slots=True)
class RuntimeSpeakerState:
    input_source: str = ""
    volume: int | None = None
    power_on: bool | None = None
    last_ui_poll_failure_log_mono: float = 0.0
    last_ui_target_success_mono: float = 0.0
    last_ui_target_ip: str = ""
    event_poll_failures: int = 0


@dataclass(slots=True)
class PowerActionState:
    generation: int = 0
    desired_state: str = ""
    desired_reason: str = ""
    display_off_standby_intent: Any = None
    # A DisplayOn notification received while Windows is still locked is not a
    # reason to wake immediately.  The controller keeps a generation-fenced
    # request here and lets WTS_SESSION_UNLOCK consume it when it is still
    # current.
    deferred_display_on_wake: Any = None
    active_actions: int = 0
    last_resume_event_mono: float = 0.0
    last_wake_schedule_mono: float = 0.0
    session_ending: bool = False
    session_locked: bool = False


@dataclass(slots=True)
class WindowsEventState:
    last_event_name: str = ""
    last_event_mono: float = 0.0
    system_sleep_pending: bool = False
    last_system_suspend_mono: float = 0.0
    last_system_resume_mono: float = 0.0
    network_interface_dedup: dict[tuple[str, str, str, str], dict[str, Any]] = field(default_factory=dict)
    network_interface_dedup_timer: Any = None


@dataclass(slots=True)
class SpeakerEventMonitorState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    stop: threading.Event = field(default_factory=threading.Event)
    running: bool = False
    restart_reason: str | None = None


@dataclass(slots=True)
class PrewarmedStandbyState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None
    holders: list[Any] = field(default_factory=list)
    running: bool = False
    restart_reason: str | None = None
    last_ip: str = ""
    last_ok_mono: float = 0.0
    failures: int = 0
    last_error: str = ""
    ready_logged: bool = False


@dataclass(slots=True)
class DisplayOffDispatcherState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    queue: queue.SimpleQueue[object] = field(default_factory=queue.SimpleQueue)
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread | None = None


@dataclass(slots=True)
class SpeakerConnectionState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    connector: Any = None


@dataclass(slots=True)
class ControllerEventListenersState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    listeners: list[Any] = field(default_factory=list)
