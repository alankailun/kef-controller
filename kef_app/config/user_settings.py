from __future__ import annotations

from dataclasses import dataclass, field, fields as dataclass_fields
from typing import Any


@dataclass(slots=True)
class GeneralSettings:
    ui_language: str = "zh"


@dataclass(slots=True)
class DeviceSettings:
    backend_name: str = "w2"
    kef_ip: str = ""
    kef_mac: str = ""
    supported_w2_models: tuple[str, ...] = ("LS50 Wireless II", "LSX II", "LS60 Wireless")
    kef_input: str = "coaxial"


@dataclass(slots=True)
class DiscoverySettings:
    mac_discovery_subnet_prefix: int = 24
    mac_discovery_extra_cidrs: list[str] = field(default_factory=list)
    mac_discovery_tcp_port: int = 80
    mac_discovery_probe_timeout: float = 0.30
    mac_discovery_max_workers: int = 128
    mac_discovery_cooldown: float = 30.0
    mac_discovery_max_hosts_per_network: int = 512
    blind_discovery_http_timeout: float = 0.80
    blind_discovery_cooldown: float = 60.0
    blind_discovery_max_workers: int = 16


@dataclass(slots=True)
class StartupSettings:
    startup_registration_mode: str = "registry"


@dataclass(slots=True)
class WakeBehavior:
    wake_on_startup: bool = True
    startup_delay: float = 0.5
    resume_wake_delay: float = 1.2
    socket_timeout: float = 0.8
    wake_on_unlock_only: bool = True
    unlock_wake_delay: float = 1.2
    wake_on_display_on: bool = True
    display_on_wake_delay: float = 1.2
    reachability_wait_timeout: float = 4.0
    reachability_poll_interval: float = 0.25
    wake_action_lock_timeout: float = 1.2
    resume_dedup_window: float = 2.0
    wake_attempt_delays: list[float] = field(default_factory=lambda: [0.00, 0.60, 1.20, 2.00, 3.00])


@dataclass(slots=True)
class SpeakerEventPolling:
    home_external_poll_interval: float = 2.0
    home_event_poll_enabled: bool = True
    home_event_poll_timeout: float = 10.0
    home_event_reconcile_interval: float = 60.0
    home_volume_debounce_ms: int = 400
    speaker_event_recovery_failure_threshold: int = 3
    tray_identity_poll_interval: float = 20.0
    identity_probe_failure_threshold: int = 2


@dataclass(slots=True)
class StandbyTriggers:
    standby_on_sleep: bool = True
    standby_on_lock: bool = True
    standby_on_lid_close: bool = True
    # Modern-Standby helper: put the speaker into standby when the display turns
    # off. No playback check. Default on, like the other power behaviors;
    # independent of standby_on_sleep so it can be toggled separately.
    standby_on_display_off: bool = True


@dataclass(slots=True)
class StandbyTuning:
    suspend_action_lock_timeout: float = 2.0
    suspend_fast_standby_enabled: bool = True
    prewarmed_standby_enabled: bool = True
    prewarmed_persist_socket: bool = True
    prewarmed_keepalive_interval_s: float = 5.0
    prewarmed_socket_timeout_s: float = 0.35
    prewarmed_send_deadline_s: float = 0.10
    prewarmed_frozen_send_multiplier: float = 3.0


@dataclass(slots=True)
class EndSessionBehavior:
    fast_exit_on_endsession: bool = True
    endsession_standby_on_shutdown: bool = True


@dataclass(slots=True)
class DiagnosticsSettings:
    log_backup_days: int = 7
    persist_runtime_state: bool = True


def _section_field_paths(section_name: str, section_type: type) -> dict[str, tuple[str, str]]:
    return {field.name: (section_name, field.name) for field in dataclass_fields(section_type)}


USER_SETTINGS_FIELD_PATHS: dict[str, tuple[str, str]] = {
    **_section_field_paths("general", GeneralSettings),
    **_section_field_paths("device", DeviceSettings),
    **_section_field_paths("discovery", DiscoverySettings),
    **_section_field_paths("startup", StartupSettings),
    **_section_field_paths("wake", WakeBehavior),
    **_section_field_paths("event_polling", SpeakerEventPolling),
    **_section_field_paths("standby_triggers", StandbyTriggers),
    **_section_field_paths("standby_tuning", StandbyTuning),
    **_section_field_paths("end_session", EndSessionBehavior),
    **_section_field_paths("diagnostics", DiagnosticsSettings),
}
USER_SETTINGS_FLAT_FIELD_NAMES = tuple(USER_SETTINGS_FIELD_PATHS)
USER_SETTINGS_SECTION_NAMES = (
    "general",
    "device",
    "discovery",
    "startup",
    "wake",
    "event_polling",
    "standby_triggers",
    "standby_tuning",
    "end_session",
    "diagnostics",
)
USER_SETTINGS_FIELD_NAMES = USER_SETTINGS_SECTION_NAMES + USER_SETTINGS_FLAT_FIELD_NAMES


@dataclass(slots=True)
class UserSettings:
    general: GeneralSettings = field(default_factory=GeneralSettings)
    device: DeviceSettings = field(default_factory=DeviceSettings)
    discovery: DiscoverySettings = field(default_factory=DiscoverySettings)
    startup: StartupSettings = field(default_factory=StartupSettings)
    wake: WakeBehavior = field(default_factory=WakeBehavior)
    event_polling: SpeakerEventPolling = field(default_factory=SpeakerEventPolling)
    standby_triggers: StandbyTriggers = field(default_factory=StandbyTriggers)
    standby_tuning: StandbyTuning = field(default_factory=StandbyTuning)
    end_session: EndSessionBehavior = field(default_factory=EndSessionBehavior)
    diagnostics: DiagnosticsSettings = field(default_factory=DiagnosticsSettings)

    def __getattr__(self, name: str) -> Any:
        path = USER_SETTINGS_FIELD_PATHS.get(name)
        if path is None:
            raise AttributeError(f"{type(self).__name__!s} has no attribute {name!r}")
        section_name, field_name = path
        return getattr(getattr(self, section_name), field_name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in USER_SETTINGS_SECTION_NAMES:
            object.__setattr__(self, name, value)
            return
        path = USER_SETTINGS_FIELD_PATHS.get(name)
        if path is not None:
            section_name, field_name = path
            setattr(getattr(self, section_name), field_name, value)
            return
        object.__setattr__(self, name, value)
