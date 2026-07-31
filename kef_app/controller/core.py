from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from ..config import AppConfig
from ..storage import PersistedSpeakerState, SpeakerStateStore
from ..devices.speaker_backend import W2Backend
from .actions import ControllerDeviceActionsMixin
from .discovery import ControllerDiscoveryMixin
from .logging_mixin import ControllerLoggingMixin
from .network_timeout import temporary_socket_timeout
from .power_state import ControllerStateMixin, power_action_outcome_is_success
from .session_events import ControllerSessionEventsMixin
from .state_models import (
    IdentityState,
    DisplayOffDispatcherState,
    ControllerEventListenersState,
    PowerActionState,
    PrewarmedStandbyState,
    RuntimeSpeakerState,
    SpeakerConnectionState,
    SpeakerEventMonitorState,
    WindowsEventState,
)
from .standby import FastStandbySendCache, PrewarmedStandbySocketMonitorMixin

from ..devices.speaker_models import normalize_mac


_UNCONFIRMED_STANDBY_OUTCOMES = {
    "sent_unconfirmed_prewarmed",
    "sent_unconfirmed_fire_and_forget",
    "sent_unconfirmed_standard",
    "sent_skipped_host_unreachable",
}


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
        self._speaker_connection = SpeakerConnectionState()
        # Lock order for future nested sections: action/discovery locks first,
        # then state/ip locks, and never call network or persistence work while
        # holding _ip_lock or _state_lock.
        # Time-sensitive Windows callbacks and pre-suspend paths must stay
        # non-blocking: no sync network, no sync disk, no unbounded lock waits.
        # Network sends go through bounded raw_http; logging is async. New WM
        # handlers should capture event mono, dispatch off-pump, and return.
        self._action_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._ip_lock = threading.Lock()
        self._discovery_lock = threading.Lock()
        self._blind_discovery_lock = threading.Lock()
        self._fast_standby_send_cache = FastStandbySendCache()
        # Construction/cleanup placeholder.  start_display_off_standby_dispatcher
        # replaces its queue immediately before starting the resident worker.
        self._display_off_dispatcher = DisplayOffDispatcherState()
        self._event_listeners = ControllerEventListenersState()

        self._identity = IdentityState(
            current_ip=self._loaded_state.last_ip or config.kef_ip,
            target_mac=self._loaded_state.last_mac or normalize_mac(config.kef_mac),
            speaker_name=self._loaded_state.last_speaker_name or "",
            speaker_model=self._loaded_state.last_speaker_model or "",
            speaker_firmware=self._loaded_state.last_firmware_version or "",
            last_matched_by=self._loaded_state.matched_by or "",
        )
        self._identity.available = bool(self._identity.current_ip)
        self._speaker_events = SpeakerEventMonitorState()
        self._prewarmed = PrewarmedStandbyState()
        # UI polling retries quickly so a speaker that just came back online
        # becomes usable straight away.  Keep the retries, but rate-limit the
        # matching network-error diagnostics instead of writing one line per
        # field every poll cycle while the speaker is unreachable.
        self._runtime_speaker = RuntimeSpeakerState()
        self._power = PowerActionState()
        self._windows_events = WindowsEventState()
        self._refresh_fast_standby_send_cache()

    def _refresh_fast_standby_send_cache(self) -> None:
        with self._ip_lock:
            target_ip = self._identity.current_ip
            target_mac = normalize_mac(self.config.kef_mac) or self._identity.target_mac
        self._fast_standby_send_cache.update(
            target_ip=target_ip,
            target_mac=target_mac,
            updated_mono=self.mono(),
        )

    def add_event_listener(self, listener: Callable[[str, dict[str, Any]], None]) -> None:
        with self._event_listeners.lock:
            self._event_listeners.listeners.append(listener)

    def remove_event_listener(self, listener: Callable[[str, dict[str, Any]], None]) -> None:
        with self._event_listeners.lock:
            try:
                self._event_listeners.listeners.remove(listener)
            except ValueError:
                pass

    def _emit_event(self, event_name: str, **payload: Any) -> None:
        with self._event_listeners.lock:
            listeners = list(self._event_listeners.listeners)

        for listener in listeners:
            try:
                listener(event_name, payload)
            except Exception as exc:
                self.log.info(f"Controller event listener failed | event={event_name} | {exc}")

    def _emit_identity_changed(self) -> None:
        self._emit_event("identity_changed", identity=self.get_current_identity())

    def run_user_power_action(self, action: str, reason: str) -> bool:
        """Run a user-requested wake/standby action with controller-owned generation state."""
        normalized_action = str(action or "").strip().lower()
        if normalized_action == "wake":
            generation = self._new_generation("wake", reason)
            return bool(self.wake_kef(generation, reason))
        if normalized_action in {"standby", "sleep"}:
            generation = self._new_generation("sleep", reason)
            return bool(self.standby_kef(generation, reason))
        raise ValueError(f"Unsupported user power action: {action}")

    def _mark_power_action_started(self) -> None:
        with self._state_lock:
            self._power.active_actions += 1

    def _emit_power_action_started_event(self, action: str, reason: str) -> None:
        self._emit_event("power_action_started", action=action, reason=reason)

    def _emit_power_action_started(self, action: str, reason: str) -> None:
        self._mark_power_action_started()
        self._emit_power_action_started_event(action, reason)

    def _emit_power_action_finished(self, action: str, reason: str, outcome: str) -> None:
        success = power_action_outcome_is_success(outcome)
        with self._state_lock:
            self._power.active_actions = max(0, self._power.active_actions - 1)
        if (
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
