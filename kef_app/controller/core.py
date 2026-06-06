from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from pykefcontrol.kef_connector import KefConnector

from ..config import AppConfig
from ..storage import PersistedSpeakerState, SpeakerStateStore
from ..devices.speaker_backend import W2Backend
from .actions import ControllerDeviceActionsMixin
from .discovery import ControllerDiscoveryMixin
from .logging_mixin import ControllerLoggingMixin
from .network_timeout import temporary_socket_timeout
from .power_state import ControllerStateMixin
from .session_events import ControllerSessionEventsMixin
from .standby import EarlyStandbyState, FastStandbySendCache, PrewarmedStandbySocketMonitorMixin

from ..devices.speaker_models import normalize_mac


_UNCONFIRMED_STANDBY_OUTCOMES = {
    "sent_unconfirmed_prewarmed",
    "sent_unconfirmed_fire_and_forget",
    "sent_unconfirmed_standard",
    "sent_skipped_host_unreachable",
}


def _power_action_outcome_is_success(outcome: str) -> bool:
    return outcome.startswith("success") or outcome.startswith("sent_")


class KefPowerController(
    ControllerLoggingMixin,
    ControllerStateMixin,
    ControllerDiscoveryMixin,
    ControllerDeviceActionsMixin,
    PrewarmedStandbySocketMonitorMixin,
    ControllerSessionEventsMixin,
):
    def __init__(self, config: AppConfig, log: logging.Logger, state_store: Optional[SpeakerStateStore] = None):
        self.config = config
        self.log = log
        self._state_store = state_store
        self._loaded_state = PersistedSpeakerState()
        if self._state_store is not None:
            self._loaded_state = self._state_store.load()

        self._backend = W2Backend(log)
        self._speaker: Optional[KefConnector] = None
        # Lock order for future nested sections: action/discovery locks first,
        # then state/ip locks, and never call network or persistence work while
        # holding _ip_lock or _state_lock.
        # Time-sensitive Windows callbacks and pre-suspend paths must stay
        # non-blocking: no sync network, no sync disk, no unbounded lock waits.
        # Network sends go through bounded raw_http; logging is async. New WM
        # handlers should capture event mono, dispatch off-pump, and return.
        self._speaker_lock = threading.Lock()
        self._action_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._ip_lock = threading.Lock()
        self._event_listener_lock = threading.Lock()
        self._discovery_lock = threading.Lock()
        self._blind_discovery_lock = threading.Lock()
        self._speaker_event_monitor_lock = threading.Lock()
        self._speaker_event_monitor_stop = threading.Event()
        self._prewarmed_standby_lock = threading.Lock()
        self._prewarmed_standby_stop = threading.Event()
        self._prewarmed_standby_thread: threading.Thread | None = None
        self._prewarmed_standby_holders = []
        self._prewarmed_standby_restart_reason: str | None = None
        self._fast_standby_send_cache = FastStandbySendCache()
        self._event_listeners: list[Callable[[str, dict[str, Any]], None]] = []

        self._current_kef_ip = self._loaded_state.last_ip or config.kef_ip
        self._target_kef_mac = self._loaded_state.last_mac or normalize_mac(config.kef_mac)
        self._speaker_name = self._loaded_state.last_speaker_name or ""
        self._speaker_model = self._loaded_state.last_speaker_model or ""
        self._speaker_firmware = self._loaded_state.last_firmware_version or ""
        self._last_matched_by = self._loaded_state.matched_by or ""
        self._identity_available = bool(self._current_kef_ip)
        self._identity_probe_failures = 0
        self._last_mac_discovery_mono = 0.0
        self._last_blind_discovery_mono = 0.0
        self._speaker_event_monitor_running = False
        self._speaker_event_monitor_restart_reason: str | None = None
        self._prewarmed_standby_running = False
        self._prewarmed_standby_last_ip = ""
        self._prewarmed_standby_last_ok_mono = 0.0
        self._prewarmed_standby_failures = 0
        self._prewarmed_standby_ready_logged = False
        self._speaker_event_poll_failures = 0
        self._speaker_runtime_input_source = ""
        self._speaker_runtime_volume: int | None = None
        self._speaker_runtime_power_on: bool | None = None
        # Last known player state ("playing" / "paused" / "stopped" / ...), cached
        # from the speaker event stream. Used to gate the display-off standby
        # trigger so playback is never cut when the screen blanks. None = unknown.
        self._speaker_runtime_play_state: str | None = None
        self._speaker_runtime_play_state_mono = 0.0

        self._generation = 0
        self._controller_active_power_actions = 0
        self._last_resume_event_mono = 0.0
        self._session_ending = False
        self._last_windows_event_name = ""
        self._last_windows_event_mono = 0.0

        self._early_standby_state = EarlyStandbyState()
        self._system_sleep_pending = False
        self._last_system_suspend_mono = 0.0
        self._last_system_resume_mono = 0.0
        self._network_interface_dedup: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self._network_interface_dedup_timer: threading.Timer | None = None
        self._refresh_fast_standby_send_cache()

    def _refresh_fast_standby_send_cache(self) -> None:
        with self._ip_lock:
            target_ip = self._current_kef_ip
            target_mac = normalize_mac(self.config.kef_mac) or self._target_kef_mac
        self._fast_standby_send_cache.update(
            target_ip=target_ip,
            target_mac=target_mac,
            updated_mono=self.mono(),
        )

    def add_event_listener(self, listener: Callable[[str, dict[str, Any]], None]) -> None:
        with self._event_listener_lock:
            self._event_listeners.append(listener)

    def remove_event_listener(self, listener: Callable[[str, dict[str, Any]], None]) -> None:
        with self._event_listener_lock:
            try:
                self._event_listeners.remove(listener)
            except ValueError:
                pass

    def _emit_event(self, event_name: str, **payload: Any) -> None:
        with self._event_listener_lock:
            listeners = list(self._event_listeners)

        for listener in listeners:
            try:
                listener(event_name, payload)
            except Exception as exc:
                self.log.info(f"Controller event listener failed | event={event_name} | {exc}")

    def _emit_identity_changed(self) -> None:
        self._emit_event("identity_changed", identity=self.get_current_identity())

    def _mark_power_action_started(self) -> None:
        with self._state_lock:
            self._controller_active_power_actions += 1

    def _emit_power_action_started_event(self, action: str, reason: str) -> None:
        self._emit_event("power_action_started", action=action, reason=reason)

    def _emit_power_action_started(self, action: str, reason: str) -> None:
        self._mark_power_action_started()
        self._emit_power_action_started_event(action, reason)

    def _emit_power_action_finished(self, action: str, reason: str, outcome: str) -> None:
        success = _power_action_outcome_is_success(outcome)
        with self._state_lock:
            self._controller_active_power_actions = max(0, self._controller_active_power_actions - 1)
        if success and action == "WAKE":
            self._set_speaker_runtime_state(speaker_on=True, source=f"power_action:{action}")
        elif (
            success
            and action in {"STANDBY", "EARLY_STANDBY", "ENDSESSION_STANDBY"}
            and outcome not in _UNCONFIRMED_STANDBY_OUTCOMES
        ):
            self._set_speaker_runtime_state(speaker_on=False, source=f"power_action:{action}")
        self._emit_event(
            "power_action_finished",
            action=action,
            reason=reason,
            success=success,
            outcome=outcome,
        )


__all__ = ["KefPowerController", "temporary_socket_timeout"]
