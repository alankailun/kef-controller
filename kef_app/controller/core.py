from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from pykefcontrol.kef_connector import KefConnector

from ..config import AppConfig
from ..storage import PersistedSpeakerState, SpeakerStateStore
from ..devices.speaker_backend import W2Backend
from .actions import ControllerDeviceActionsMixin
from .identity_discovery import ControllerDiscoveryMixin
from .logging_mixin import ControllerLoggingMixin
from .network_timeout import temporary_socket_timeout
from .power_state import ControllerStateMixin
from .session_events import ControllerSessionEventsMixin

from ..devices.speaker_models import normalize_mac


class KefPowerController(
    ControllerLoggingMixin,
    ControllerStateMixin,
    ControllerDiscoveryMixin,
    ControllerDeviceActionsMixin,
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
        self._speaker_lock = threading.Lock()
        self._action_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._ip_lock = threading.Lock()
        self._event_listener_lock = threading.Lock()
        self._discovery_lock = threading.Lock()
        self._blind_discovery_lock = threading.Lock()
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

        self._generation = 0
        self._last_resume_event_mono = 0.0
        self._session_ending = False

        self._last_lock_standby_ok_mono = 0.0
        self._system_sleep_pending = False
        self._last_system_suspend_mono = 0.0
        self._last_system_resume_mono = 0.0

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

    def _emit_power_action_started(self, action: str, reason: str) -> None:
        self._emit_event("power_action_started", action=action, reason=reason)

    def _emit_power_action_finished(self, action: str, reason: str, outcome: str) -> None:
        success = outcome.startswith("success") or outcome == "skipped_recent_lock_pre_standby_ok"
        self._emit_event(
            "power_action_finished",
            action=action,
            reason=reason,
            success=success,
            outcome=outcome,
        )


__all__ = ["KefPowerController", "temporary_socket_timeout"]
