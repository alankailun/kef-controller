from __future__ import annotations

import threading
import time
from typing import Callable, Optional, TypeVar

from pykefcontrol.kef_connector import KefConnector

from ..network_timeout import temporary_socket_timeout
from ...devices.transport import is_host_unreachable
from ...devices.speaker_models import normalize_input_source


T = TypeVar("T")
_EVENT_MONITOR_IDLE_DELAY_S = 0.2
_EVENT_MONITOR_RETRY_DELAY_S = 2.0
_EVENT_MONITOR_POWER_ACTION_DELAY_S = 0.5


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

    def _set_speaker_runtime_state(
        self,
        *,
        input_source: Optional[str] = None,
        volume: Optional[int] = None,
        speaker_on: Optional[bool] = None,
        source: str,
    ) -> bool:
        changed = False
        with self._state_lock:
            if input_source is not None and input_source != self._speaker_runtime_input_source:
                self._speaker_runtime_input_source = input_source
                changed = True
            if volume is not None and volume != self._speaker_runtime_volume:
                self._speaker_runtime_volume = volume
                changed = True
            if speaker_on is not None and speaker_on != self._speaker_runtime_power_on:
                self._speaker_runtime_power_on = speaker_on
                changed = True

            current_input = self._speaker_runtime_input_source or None
            current_volume = self._speaker_runtime_volume
            current_power = self._speaker_runtime_power_on

        if changed:
            self._emit_event(
                "speaker_state_changed",
                input_source=current_input,
                volume=current_volume,
                speaker_on=current_power,
                source=source,
            )
        return changed

    def _clear_speaker_event_poll_failures(self) -> None:
        with self._state_lock:
            self._speaker_event_poll_failures = 0

    def _reset_speaker_event_subscription(self, speaker: KefConnector | None) -> bool:
        if speaker is None:
            return False
        with self._speaker_lock:
            try:
                speaker.polling_queue = None
                speaker.last_polled = None
                speaker._previous_poll_song_status = False
            except Exception:
                return False
        return True

    def _record_speaker_event_poll_failure(
        self,
        *,
        reason: str,
        trigger: str,
        cause: str,
        speaker: KefConnector | None,
    ) -> tuple[int, int, bool]:
        threshold = max(1, int(self.config.speaker_event_recovery_failure_threshold))
        with self._state_lock:
            self._speaker_event_poll_failures += 1
            failures = self._speaker_event_poll_failures

        recovered = False
        if failures == 1:
            # First failure is most often a stale pollQueue subscription (KEF
            # GCs the queue or pykefcontrol's 50s rebuild window misfires).
            # Clearing only the queue avoids KefConnector.__init__'s volume
            # read on the next pass while still forcing a fresh subscription.
            subscription_reset = self._reset_speaker_event_subscription(speaker)
            if not subscription_reset:
                self.reset_speaker()
            self._log_structured(
                "STEP",
                log_level="info",
                action="POLL_SPEAKER_EVENTS",
                reason=reason,
                trigger=trigger,
                step="recovery",
                status=(
                    "reset_poll_subscription"
                    if subscription_reset
                    else "reset_speaker_for_subscription_refresh"
                ),
                cause=cause,
                failures=failures,
                threshold=threshold,
                target_ip=self.get_current_kef_ip() or "<empty>",
                mono=f"{self.mono():.3f}",
            )
        elif failures >= threshold:
            self.reset_speaker()
            recovered = self.maybe_refresh_kef_ip(
                reason=reason,
                trigger=f"{trigger}_event_recover",
                force=True,
            )
            with self._state_lock:
                self._speaker_event_poll_failures = 0
            self._log_structured(
                "STEP",
                log_level="info",
                action="POLL_SPEAKER_EVENTS",
                reason=reason,
                trigger=trigger,
                step="recovery",
                cause=cause,
                failures=failures,
                threshold=threshold,
                ip_refresh_attempted=recovered,
                target_ip=self.get_current_kef_ip() or "<empty>",
                mono=f"{self.mono():.3f}",
            )

        return failures, threshold, recovered

    def start_speaker_event_monitor(self, reason: str = "runtime") -> bool:
        if not self.config.home_event_poll_enabled:
            return False

        with self._speaker_event_monitor_lock:
            if self._speaker_event_monitor_running:
                return False
            self._speaker_event_monitor_running = True
            self._speaker_event_monitor_stop.clear()

        def run() -> None:
            self._run_speaker_event_monitor(reason)

        thread = threading.Thread(target=run, daemon=True, name="SpeakerEventMonitor")
        thread.start()
        return True

    def stop_speaker_event_monitor(self) -> None:
        self._speaker_event_monitor_stop.set()

    def _finish_speaker_event_monitor(self) -> None:
        with self._speaker_event_monitor_lock:
            self._speaker_event_monitor_running = False

    def _run_speaker_event_monitor(self, reason: str) -> None:
        self._log_structured(
            "STEP",
            log_level="info",
            action="POLL_SPEAKER_EVENTS",
            reason=reason,
            step="monitor",
            status="started",
            timeout_s=f"{self.config.home_event_poll_timeout:.1f}",
            mono=f"{self.mono():.3f}",
        )
        try:
            while not self._speaker_event_monitor_stop.is_set():
                if not self.config.home_event_poll_enabled:
                    if self._speaker_event_monitor_stop.wait(_EVENT_MONITOR_RETRY_DELAY_S):
                        return
                    continue

                if self._is_controller_power_action_active():
                    if self._speaker_event_monitor_stop.wait(_EVENT_MONITOR_POWER_ACTION_DELAY_S):
                        return
                    continue

                if not self.get_current_kef_ip():
                    if self._speaker_event_monitor_stop.wait(_EVENT_MONITOR_RETRY_DELAY_S):
                        return
                    continue

                result = self.poll_speaker_event_state(
                    "speaker_event_monitor",
                    "speaker_event_monitor",
                    timeout=self.config.home_event_poll_timeout,
                )
                if any(value is not None for value in result):
                    delay = _EVENT_MONITOR_IDLE_DELAY_S
                else:
                    delay = _EVENT_MONITOR_RETRY_DELAY_S
                if self._speaker_event_monitor_stop.wait(delay):
                    return
        finally:
            self._finish_speaker_event_monitor()
            self._log_structured(
                "STEP",
                log_level="info",
                action="POLL_SPEAKER_EVENTS",
                reason=reason,
                step="monitor",
                status="stopped",
                mono=f"{self.mono():.3f}",
            )

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
        if input_source is not None or volume is not None or speaker_on is not None:
            self._set_speaker_runtime_state(
                input_source=input_source,
                volume=volume,
                speaker_on=speaker_on,
                source=trigger,
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

        speaker = None
        try:
            with temporary_socket_timeout(self.config.socket_timeout):
                speaker = self.get_speaker(fresh=False)
                events = speaker.poll_speaker(timeout=max(1, int(timeout)))
        except Exception as exc:
            unreachable = is_host_unreachable(exc)
            failures, threshold, recovered = self._record_speaker_event_poll_failure(
                reason=reason,
                trigger=trigger,
                cause="host_unreachable" if unreachable else "event_poll_failed",
                speaker=speaker,
            )
            transient = failures < threshold
            self._log_structured(
                "STEP" if (unreachable or transient) else "WARN",
                log_level="info" if (unreachable or transient) else None,
                action="POLL_SPEAKER_EVENTS",
                reason=reason,
                trigger=trigger,
                cause="host_unreachable" if unreachable else "event_poll_failed",
                status="transient_failure" if transient else "escalated_failure",
                failures=failures,
                threshold=threshold,
                ip_refresh_attempted=recovered,
                error=repr(exc),
                mono=f"{self.mono():.3f}",
            )
            return None, None, None

        if not isinstance(events, dict) or not events:
            self._clear_speaker_event_poll_failures()
            return None, None, None

        input_source = normalize_input_source(events.get("source")) or None
        volume = events.get("volume") if isinstance(events.get("volume"), int) else None
        speaker_on = self._normalize_speaker_power_state(events.get("speaker_status"))

        if input_source is None and volume is None and speaker_on is None:
            self._clear_speaker_event_poll_failures()
            return None, None, None

        self._clear_speaker_event_poll_failures()
        availability_changed = self._mark_identity_probe_success(source=trigger)
        if availability_changed:
            self._emit_identity_changed()
        self._set_speaker_runtime_state(
            input_source=input_source,
            volume=volume,
            speaker_on=speaker_on,
            source=trigger,
        )

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

    def log_wifi_diagnostics(self, reason: str, trigger: str, *, fresh: bool = False) -> dict[str, object]:
        if not self.get_current_kef_ip():
            return {}

        try:
            with temporary_socket_timeout(self.config.socket_timeout):
                speaker = self.get_speaker(fresh=fresh)
                info = speaker.get_wifi_information()
        except Exception as exc:
            self.reset_speaker()
            self._log_structured(
                "STEP",
                log_level="info",
                action="WIFI_DIAGNOSTICS",
                reason=reason,
                trigger=trigger,
                status="failed",
                error=repr(exc),
                mono=f"{self.mono():.3f}",
            )
            return {}

        if not isinstance(info, dict):
            info = {}
        self._log_structured(
            "STEP",
            log_level="info",
            action="WIFI_DIAGNOSTICS",
            reason=reason,
            trigger=trigger,
            status="available" if info else "unavailable",
            target_ip=self.get_current_kef_ip() or "<empty>",
            signal_level=info.get("signalLevel", "<empty>") if info else "<empty>",
            ssid=info.get("ssid", "<empty>") if info else "<empty>",
            frequency=info.get("frequency", "<empty>") if info else "<empty>",
            bssid=info.get("bssid", "<empty>") if info else "<empty>",
            mono=f"{self.mono():.3f}",
        )
        return info

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
                        log_level="info",
                        action="CHANGE_INPUT",
                        requested_input=requested_input,
                        new_input=new_input,
                        previous_input=previous_input or "<unknown>",
                        attempt=attempt,
                        status="success",
                        actual_input=actual_input,
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
