from __future__ import annotations

import time
from typing import Callable, Optional, TypeVar

from pykefcontrol.kef_connector import KefConnector

from ..network_timeout import temporary_socket_timeout
from ...devices.speaker_models import normalize_input_source


T = TypeVar("T")


class ControllerDeviceControlsMixin:
    def _read_ui_value(
        self,
        reason: str,
        trigger: str,
        *,
        fresh: bool,
        step: str,
        reader: Callable[[KefConnector], T],
    ) -> tuple[T | None, bool]:
        try:
            with temporary_socket_timeout(self.config.socket_timeout):
                speaker = self.get_speaker(fresh=fresh)
                return reader(speaker), True
        except Exception as exc:
            self.reset_speaker()
            self._log_structured(
                "WARN",
                action="POLL_EXTERNAL_STATE",
                reason=reason,
                trigger=trigger,
                step=step,
                error=repr(exc),
                mono=f"{self.mono():.3f}",
            )
            return None, False

    def poll_external_ui_state(self, reason: str, trigger: str) -> tuple[Optional[str], Optional[int], Optional[bool]]:
        if not self.get_current_kef_ip():
            return None, None, None

        speaker_on, power_ok = self._read_ui_value(
            reason,
            trigger,
            fresh=True,
            step="speaker_status",
            reader=lambda speaker: self._normalize_speaker_power_state(speaker.status),
        )
        input_source, input_ok = self._read_ui_value(
            reason,
            trigger,
            fresh=not power_ok,
            step="input_source",
            reader=lambda speaker: normalize_input_source(speaker.source),
        )
        volume, volume_ok = self._read_ui_value(
            reason,
            trigger,
            fresh=not (power_ok or input_ok),
            step="volume",
            reader=lambda speaker: speaker.volume,
        )
        identity_seen = False
        ip_refreshed = False
        reachable = power_ok or input_ok or volume_ok

        if reachable:
            availability_changed = self._mark_identity_probe_success(source=trigger)
            if availability_changed:
                self._emit_identity_changed()
        else:
            identity_seen, ip_refreshed = self.probe_external_identity(reason=reason, trigger=trigger)
            reachable = identity_seen or ip_refreshed

        should_log_poll = not (reachable and power_ok and input_ok and volume_ok and not identity_seen and not ip_refreshed)
        if should_log_poll:
            self._log_structured(
                "STEP",
                action="POLL_EXTERNAL_STATE",
                reason=reason,
                trigger=trigger,
                power_ok=power_ok,
                speaker_on=speaker_on,
                input_ok=input_ok,
                volume_ok=volume_ok,
                fallback_identity=identity_seen,
                fallback_ip_refresh=ip_refreshed,
                reachable=reachable,
                mono=f"{self.mono():.3f}",
            )
        return input_source, volume, speaker_on

    def change_input_live(self, new_input: str) -> bool:
        requested_input = new_input
        new_input = normalize_input_source(new_input)
        if not new_input or not self._is_configurable_input_source(new_input):
            self._log_structured(
                "SKIP",
                action="CHANGE_INPUT",
                requested_input=requested_input,
                normalized_input=new_input or "<empty>",
                cause="unsupported_input_source",
                mono=f"{self.mono():.3f}",
            )
            return False
        previous_input = self.get_input_source()
        previous_player_source = self.get_player_source_hint()
        if not self.get_current_kef_ip():
            return False
        if not self._action_lock.acquire(timeout=2.0):
            self._log_structured("SKIP", action="CHANGE_INPUT", cause="action_lock_busy", mono=f"{self.mono():.3f}")
            return False

        try:
            for attempt in range(1, 3):
                if attempt > 1:
                    time.sleep(0.6)
                try:
                    self._set_speaker_source(new_input, fresh=True)
                    actual_input = self._wait_for_input_source(new_input)
                    if actual_input != new_input:
                        self._log_structured(
                            "WARN",
                            action="CHANGE_INPUT",
                            requested_input=requested_input,
                            normalized_input=new_input,
                            attempt=attempt,
                            cause="source_not_verified",
                            actual_input=actual_input or "<unknown>",
                            mono=f"{self.mono():.3f}",
                        )
                        self.reset_speaker()
                        continue

                    actual_player_source, player_ok = self._verify_player_source(new_input)
                    if not player_ok:
                        self._log_structured(
                            "WARN",
                            action="CHANGE_INPUT",
                            requested_input=requested_input,
                            normalized_input=new_input,
                            attempt=attempt,
                            cause="player_source_not_verified",
                            actual_input=actual_input or "<unknown>",
                            actual_player_source=actual_player_source,
                            mono=f"{self.mono():.3f}",
                        )
                        self.reset_speaker()
                        continue
                    self._log_structured(
                        "STEP",
                        action="CHANGE_INPUT",
                        requested_input=requested_input,
                        new_input=new_input,
                        previous_input=previous_input or "<unknown>",
                        previous_player_source=previous_player_source or "<unknown>",
                        attempt=attempt,
                        status="success",
                        actual_input=actual_input,
                        actual_player_source=actual_player_source or "<unknown>",
                        mono=f"{self.mono():.3f}",
                    )
                    return True
                except Exception as exc:
                    self.reset_speaker()
                    self._log_structured(
                        "WARN",
                        action="CHANGE_INPUT",
                        requested_input=requested_input,
                        new_input=new_input,
                        attempt=attempt,
                        error=repr(exc),
                        mono=f"{self.mono():.3f}",
                    )
            return False
        finally:
            self._action_lock.release()

    def get_volume(self) -> Optional[int]:
        if not self.get_current_kef_ip():
            return None
        try:
            with temporary_socket_timeout(self.config.socket_timeout):
                speaker = self.get_speaker(fresh=False)
                return speaker.volume
        except Exception as exc:
            self.reset_speaker()
            self._log_structured("WARN", action="GET_VOLUME", error=repr(exc), mono=f"{self.mono():.3f}")
            return None

    def set_volume(self, level: int) -> bool:
        if not self.get_current_kef_ip():
            return False
        requested_level = level
        coerced_level = self._coerce_volume_level(level)
        if coerced_level is None:
            self._log_structured(
                "SKIP",
                action="SET_VOLUME",
                requested_level=requested_level,
                cause="invalid_volume_level",
                mono=f"{self.mono():.3f}",
            )
            return False
        level = coerced_level
        if not self._action_lock.acquire(timeout=2.0):
            self._log_structured("SKIP", action="SET_VOLUME", cause="action_lock_busy", mono=f"{self.mono():.3f}")
            return False
        try:
            with temporary_socket_timeout(self.config.socket_timeout):
                speaker = self.get_speaker(fresh=False)
                speaker.volume = level
            self._log_structured("STEP", action="SET_VOLUME", level=level, status="success", mono=f"{self.mono():.3f}")
            return True
        except Exception as exc:
            self.reset_speaker()
            self._log_structured("WARN", action="SET_VOLUME", level=level, error=repr(exc), mono=f"{self.mono():.3f}")
            return False
        finally:
            self._action_lock.release()
