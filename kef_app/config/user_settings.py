from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class UserSettings:
    backend_name: str = "w2"

    kef_ip: str = ""
    kef_mac: str = ""
    supported_w2_models: tuple[str, ...] = ("LS50 Wireless II", "LSX II", "LS60 Wireless")

    mac_discovery_subnet_prefix: int = 24
    mac_discovery_extra_cidrs: list[str] = field(default_factory=list)
    mac_discovery_tcp_port: int = 80
    mac_discovery_probe_timeout: float = 0.20
    mac_discovery_max_workers: int = 48
    mac_discovery_cooldown: float = 30.0
    mac_discovery_max_hosts_per_network: int = 512
    blind_discovery_http_timeout: float = 0.80
    blind_discovery_cooldown: float = 60.0
    blind_discovery_max_workers: int = 16

    kef_input: str = "coaxial"
    startup_registration_mode: str = "registry"
    wake_on_startup: bool = True
    startup_delay: float = 0.5
    resume_wake_delay: float = 1.2
    socket_timeout: float = 0.8

    wake_on_unlock_only: bool = True
    unlock_wake_delay: float = 1.2
    reachability_wait_timeout: float = 4.0
    reachability_poll_interval: float = 0.25
    home_external_poll_interval: float = 2.0
    tray_identity_poll_interval: float = 20.0
    identity_probe_failure_threshold: int = 2

    standby_attempt_delays: list[float] = field(default_factory=lambda: [0.00, 0.12, 0.25, 0.45, 0.80])
    wake_attempt_delays: list[float] = field(default_factory=lambda: [0.00, 0.60, 1.20, 2.00, 3.00])

    suspend_action_lock_timeout: float = 2.0
    wake_action_lock_timeout: float = 1.2
    resume_dedup_window: float = 2.0

    standby_on_sleep: bool = True
    suspend_fast_standby_enabled: bool = True
    suspend_fast_standby_action_lock_timeout: float = 0.20
    suspend_fast_standby_socket_timeout: float = 0.60
    standby_on_lock: bool = True
    lock_standby_action_lock_timeout: float = 0.3
    lock_standby_dedup_window: float = 8.0

    log_backup_days: int = 7
    diagnostic_logging: bool = False
    persist_runtime_state: bool = True

    fast_exit_on_endsession: bool = True
    endsession_standby_on_shutdown: bool = True
    endsession_standby_action_lock_timeout: float = 0.20
    endsession_standby_socket_timeout: float = 0.60
