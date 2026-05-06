from __future__ import annotations

import time
from typing import Callable, Optional, TypeVar

from pykefcontrol.kef_connector import KefConnector

from ..network_timeout import temporary_socket_timeout
from ...devices.speaker_models import normalize_input_source
from .device_common import _is_host_unreachable


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
            if not self.resolve_target(reason=reason, trigger=trigger, force_recovery=False):
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

        if reachable and self.get_effective_target_mac():
            identity_seen, ip_refreshed = self.probe_external_identity(reason=reason, trigger=trigger)
            reachable = identity_seen or ip_refreshed
        elif reachable:
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

    def poll_speaker_event_state(
        self,
        reason: str,
        trigger: str,
        *,
        timeout: float,
    ) -> tuple[Optional[str], Optional[int], Optional[bool]]:
        if not self.get_current_kef_ip():
            return None, None, None

        try:
            with temporary_socket_timeout(self.config.socket_timeout):
                speaker = self.get_speaker(fresh=False)
                events = speaker.poll_speaker(timeout=max(1, int(timeout)))
        except Exception as exc:
            self.reset_speaker()
            unreachable = _is_host_unreachable(exc)
            self._log_structured(
                "STEP" if unreachable else "WARN",
                action="POLL_SPEAKER_EVENTS",
                reason=reason,
                trigger=trigger,
                cause="host_unreachable" if unreachable else "event_poll_failed",
                error=repr(exc),
                mono=f"{self.mono():.3f}",
            )
            return None, None, None

        if not isinstance(events, dict) or not events:
            return None, None, None

        input_source = normalize_input_source(events.get("source")) or None
        volume = events.get("volume") if isinstance(events.get("volume"), int) else None
        speaker_on = self._normalize_speaker_power_state(events.get("speaker_status"))

        if input_source is None and volume is None and speaker_on is None:
            return None, None, None

        availability_changed = self._mark_identity_probe_success(source=trigger)
        if availability_changed:
            self._emit_identity_changed()

        self._log_structured(
            "STEP",
            action="POLL_SPEAKER_EVENTS",
            reason=reason,
            trigger=trigger,
            input_source=input_source or "<unchanged>",
            volume=volume if volume is not None else "<unchanged>",
            speaker_on=speaker_on if speaker_on is not None else "<unchanged>",
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
        if not self._ensure_target_identity("CHANGE_INPUT", "ui_live", "change_input_before_action"):
            return False
        if not self.get_current_kef_ip():
            return False
        if not self._action_lock.acquire(timeout=2.0):
            self._log_structured("SKIP", action="CHANGE_INPUT", cause="action_lock_busy", mono=f"{self.mono():.3f}")
            return False

        try:
            previous_input = self.get_input_source()
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

                    self._log_structured(
                        "STEP",
                        action="CHANGE_INPUT",
                        requested_input=requested_input,
                        new_input=new_input,
                        previous_input=previous_input or "<unknown>",
                        previous_player_source="<not_read>",
                        attempt=attempt,
                        status="success",
                        actual_input=actual_input,
                        actual_player_source="<skipped_input_confirmed>",
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
        if not self._ensure_target_identity("GET_VOLUME", "ui_live", "get_volume_before_read"):
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
        if not self._ensure_target_identity("SET_VOLUME", "ui_live", "set_volume_before_action"):
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
