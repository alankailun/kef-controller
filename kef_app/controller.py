from __future__ import annotations

import logging
import threading
from typing import Optional

from pykefcontrol.kef_connector import KefConnector

from .appdata import AppConfig, PersistedSpeakerState, SpeakerStateStore
from .backends import W2Backend
from .controller_support import (
    ControllerDeviceActionsMixin,
    ControllerDiscoveryMixin,
    ControllerLoggingMixin,
    ControllerSessionEventsMixin,
    ControllerStateMixin,
    temporary_socket_timeout,
)
from .discovery import normalize_mac


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
        self._discovery_lock = threading.Lock()
        self._blind_discovery_lock = threading.Lock()

        self._current_kef_ip = self._loaded_state.last_ip or config.kef_ip
        self._target_kef_mac = self._loaded_state.last_mac or normalize_mac(config.kef_mac)
        self._speaker_name = self._loaded_state.last_speaker_name or ""
        self._speaker_model = self._loaded_state.last_speaker_model or ""
        self._speaker_firmware = self._loaded_state.last_firmware_version or ""
        self._last_matched_by = self._loaded_state.matched_by or ""
        self._last_mac_discovery_mono = 0.0
        self._last_blind_discovery_mono = 0.0

        self._generation = 0
        self._last_resume_event_mono = 0.0
        self._session_ending = False

        self._last_lock_standby_ok_mono = 0.0


__all__ = ["KefPowerController", "temporary_socket_timeout"]
