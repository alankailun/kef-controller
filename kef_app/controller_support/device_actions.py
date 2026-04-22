from __future__ import annotations

import re
import socket
import time
from typing import Callable, Optional, TypeVar

from pykefcontrol.kef_connector import KefConnector

from .common import temporary_socket_timeout
from ..models import normalize_input_source


_CHANGE_INPUT_POLL_INTERVAL = 0.2
_CHANGE_INPUT_VERIFY_TIMEOUT = 2.5
_STANDBY_VERIFY_POLL_INTERVAL = 0.08
_STANDBY_VERIFY_READ_TIMEOUT = 0.20
_STANDBY_VERIFY_TIMEOUT = 0.40
_LOCK_PRE_STANDBY_VERIFY_TIMEOUT = 0.40
_LOCK_PRE_STANDBY_ATTEMPT_DELAYS = (0.0, 0.15)
_STANDBY_VERIFY_OFFLINE_FAILURES = 2
T = TypeVar("T")


class StandbyVerificationError(RuntimeError):
    pass


class ControllerDeviceActionsMixin:
    @staticmethod
    def _normalize_speaker_power_state(raw_status: object) -> Optional[bool]:
        compact = re.sub(r"[^A-Za-z0-9]+", "", str(raw_status or "")).casefold()
        if not compact:
            return None
        if compact in {"standby", "off", "poweredoff"}:
            return False
        if compact in {"poweron", "poweredon", "on"}:
            return True
        return None

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

    def _log_generation_abort(self, action: str, generation: int, reason: str, step: str) -> None:
        self._log_structured(
            "ABORT",
            action=action,
            gen=generation,
            reason=reason,
            step=step,
            current_gen=self._current_generation(),
            cause="generation_changed",
            mono=f"{self.mono():.3f}",
        )

    def _run_generation_pre_delay(
        self,
        *,
        action: str,
        generation: int,
        reason: str,
        attempt: int,
        delay: float,
        sleep_label: str,
    ) -> str | None:
        if delay <= 0:
            return None

        self._log_structured(
            "STEP",
            action=action,
            gen=generation,
            reason=reason,
            step="pre_delay",
            attempt=attempt,
            delay_s=f"{delay:.2f}",
            mono=f"{self.mono():.3f}",
        )
        if not self._interruptible_sleep(delay, generation, sleep_label):
            return "aborted_during_pre_delay"
        return None

    def _acquire_generation_action_lock(
        self,
        *,
        action: str,
        generation: int,
        reason: str,
        lock_timeout: float,
        purpose: str,
    ) -> str | None:
        if self._should_abort_generation(generation):
            self._log_generation_abort(action, generation, reason, "before_action_lock")
            return "aborted_stale_generation_before_lock"

        if not self._acquire_action_lock_interruptibly(lock_timeout, generation, reason, purpose):
            return "aborted_action_lock_timeout_or_stale"

        if self._should_abort_generation(generation):
            self._log_generation_abort(action, generation, reason, "after_action_lock")
            self._action_lock.release()
            return "aborted_stale_generation_after_lock"

        return None

    def _verify_player_source(self, expected_input: str) -> tuple[Optional[str], bool]:
        actual_player_source = self.get_player_source_hint(fresh=True)
        return actual_player_source, (not actual_player_source or actual_player_source == expected_input)

    def _set_speaker_source(self, source: str, *, fresh: bool) -> None:
        with temporary_socket_timeout(self.config.socket_timeout):
            speaker = self.get_speaker(fresh=fresh)
            speaker.source = source

    def _request_shutdown(self, *, fresh: bool, timeout: float) -> None:
        with temporary_socket_timeout(timeout):
            speaker = self.get_speaker(fresh=fresh)
            speaker.shutdown()

    def _perform_standby_request(
        self,
        *,
        action: str,
        generation: int | None,
        reason: str,
        attempt: int,
        fresh: bool,
        verify_timeout: float,
    ) -> None:
        self._request_shutdown(fresh=fresh, timeout=self.config.socket_timeout)
        self._log_structured(
            "STEP",
            action=action,
            gen=generation,
            reason=reason,
            step="shutdown_request",
            attempt=attempt,
            fresh=fresh,
            status="sent",
            mono=f"{self.mono():.3f}",
        )
        self._ensure_standby_confirmed(
            action=action,
            generation=generation,
            reason=reason,
            timeout=verify_timeout,
        )

    @staticmethod
    def _format_power_state(value: Optional[bool]) -> str:
        if value is True:
            return "on"
        if value is False:
            return "standby"
        return "<unknown>"

    def _read_standby_snapshot(self, *, fresh: bool) -> tuple[Optional[bool], Optional[str], Optional[str]]:
        read_timeout = min(self.config.socket_timeout, _STANDBY_VERIFY_READ_TIMEOUT)
        try:
            with temporary_socket_timeout(read_timeout):
                speaker = self.get_speaker(fresh=fresh)
                power_state = self._normalize_speaker_power_state(speaker.status)
                if power_state is False:
                    return power_state, "standby", None
                input_source = normalize_input_source(speaker.source)
                return power_state, input_source, None
        except Exception as exc:
            self.reset_speaker()
            return None, None, repr(exc)

    def _wait_for_standby_confirmation(self, *, timeout: float) -> tuple[bool, dict[str, object]]:
        deadline = self.mono() + timeout
        last_power_state = None
        last_input_source = None
        last_error = None
        consecutive_failures = 0

        while True:
            power_state, input_source, error = self._read_standby_snapshot(fresh=True)
            if error is None:
                consecutive_failures = 0
                last_power_state = power_state
                last_input_source = input_source
                if power_state is False or input_source == "standby":
                    return True, {
                        "verified_by": "speaker_state",
                        "actual_power": self._format_power_state(power_state),
                        "actual_input": input_source or "<unknown>",
                    }
            else:
                consecutive_failures += 1
                last_error = error
                if consecutive_failures >= _STANDBY_VERIFY_OFFLINE_FAILURES:
                    return True, {
                        "verified_by": "device_unreachable",
                        "consecutive_failures": consecutive_failures,
                        "last_error": last_error,
                    }

            remaining = deadline - self.mono()
            if remaining <= 0:
                break
            time.sleep(min(_STANDBY_VERIFY_POLL_INTERVAL, remaining))

        return False, {
            "verified_by": "not_confirmed",
            "actual_power": self._format_power_state(last_power_state),
            "actual_input": last_input_source or "<unknown>",
            "consecutive_failures": consecutive_failures,
            "last_error": last_error,
        }

    def _ensure_standby_confirmed(
        self,
        *,
        action: str,
        generation: int | None,
        reason: str,
        timeout: float,
    ) -> None:
        self._log_structured(
            "STEP",
            action=action,
            gen=generation,
            reason=reason,
            step="verify_standby",
            status="begin",
            timeout_s=f"{timeout:.2f}",
            mono=f"{self.mono():.3f}",
        )
        verified, details = self._wait_for_standby_confirmation(timeout=timeout)
        if verified:
            self._log_structured(
                "STEP",
                action=action,
                gen=generation,
                reason=reason,
                step="verify_standby",
                status="confirmed",
                mono=f"{self.mono():.3f}",
                **details,
            )
            return

        self._log_structured(
            "WARN",
            action=action,
            gen=generation,
            reason=reason,
            step="verify_standby",
            status="failed",
            mono=f"{self.mono():.3f}",
            **details,
        )
        raise StandbyVerificationError(
            "standby_not_verified "
            f"power={details.get('actual_power')} "
            f"input={details.get('actual_input')} "
            f"failures={details.get('consecutive_failures')} "
            f"last_error={details.get('last_error')}"
        )

    def _run_generation_attempts(
        self,
        *,
        action: str,
        generation: int,
        reason: str,
        attempt_delays: list[float],
        lock_timeout: float,
        purpose: str,
        execute_attempt: Callable[[int], None],
        build_retry_fields: Callable[[int, Exception], dict[str, object]],
    ) -> str:
        for attempt, delay in enumerate(attempt_delays, start=1):
            outcome = self._run_generation_pre_delay(
                action=action,
                generation=generation,
                reason=reason,
                attempt=attempt,
                delay=delay,
                sleep_label=f"{purpose}_pre_delay_attempt_{attempt}",
            )
            if outcome is not None:
                return outcome

            outcome = self._acquire_generation_action_lock(
                action=action,
                generation=generation,
                reason=reason,
                lock_timeout=lock_timeout,
                purpose=purpose,
            )
            if outcome is not None:
                return outcome

            try:
                execute_attempt(attempt)
                return f"success_attempt_{attempt}"
            except Exception as exc:
                self.reset_speaker()
                self._log_structured(
                    "RETRY",
                    action=action,
                    gen=generation,
                    reason=reason,
                    attempt=attempt,
                    error=repr(exc),
                    mono=f"{self.mono():.3f}",
                    **build_retry_fields(attempt, exc),
                )
            finally:
                self._action_lock.release()

        self._log_structured(
            "STEP",
            action=action,
            gen=generation,
            reason=reason,
            step="attempt_loop",
            status="exhausted",
            mono=f"{self.mono():.3f}",
        )
        return "failed_all_attempts"

    def _extract_player_source_hint(self, player_data: dict) -> Optional[str]:
        candidates = [
            player_data.get("mediaRoles", {}).get("mediaData", {}).get("metaData", {}).get("serviceID"),
            player_data.get("trackRoles", {}).get("mediaData", {}).get("metaData", {}).get("serviceID"),
            player_data.get("mediaRoles", {}).get("title"),
            player_data.get("trackRoles", {}).get("title"),
            player_data.get("mediaRoles", {}).get("path"),
            player_data.get("trackRoles", {}).get("path"),
        ]
        for candidate in candidates:
            normalized = normalize_input_source(str(candidate or ""))
            if normalized:
                return normalized
        return None

    def get_player_source_hint(self, fresh: bool = False) -> Optional[str]:
        if not self.get_current_kef_ip():
            return None
        try:
            with temporary_socket_timeout(self.config.socket_timeout):
                speaker = self.get_speaker(fresh=fresh)
                player_data = speaker._get_player_data()
        except Exception as exc:
            self.reset_speaker()
            self._log_structured("WARN", action="GET_PLAYER_SOURCE", error=repr(exc), mono=f"{self.mono():.3f}")
            return None

        return self._extract_player_source_hint(player_data)

    def get_input_source(self, fresh: bool = False) -> Optional[str]:
        if not self.get_current_kef_ip():
            return None
        try:
            with temporary_socket_timeout(self.config.socket_timeout):
                speaker = self.get_speaker(fresh=fresh)
                return normalize_input_source(speaker.source)
        except Exception as exc:
            self.reset_speaker()
            self._log_structured("WARN", action="GET_INPUT_SOURCE", error=repr(exc), mono=f"{self.mono():.3f}")
            return None

    def _wait_for_input_source(self, expected_input: str, timeout: float = _CHANGE_INPUT_VERIFY_TIMEOUT) -> Optional[str]:
        expected = normalize_input_source(expected_input)
        deadline = self.mono() + timeout
        observed = None
        while self.mono() < deadline:
            observed = self.get_input_source(fresh=True)
            if observed == expected:
                return observed
            time.sleep(_CHANGE_INPUT_POLL_INTERVAL)
        return observed

    def reset_speaker(self):
        with self._speaker_lock:
            self._speaker = None

    def change_input_live(self, new_input: str) -> bool:
        requested_input = new_input
        new_input = normalize_input_source(new_input)
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
        level = max(0, min(100, level))
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

    def get_speaker(self, fresh: bool = False) -> KefConnector:
        with self._speaker_lock:
            if fresh or self._speaker is None:
                self._speaker = self._backend.create_connector(self.get_current_kef_ip())
            return self._speaker

    def wait_until_reachable(self, timeout: float, generation: int, reason: str) -> bool:
        start_mono = self.mono()
        deadline = start_mono + timeout
        waiting_logged = False
        last_error = None
        self._log_structured(
            "STEP",
            action="WAKE",
            gen=generation,
            reason=reason,
            step="wait_reachable",
            status="begin",
            timeout_s=f"{timeout:.2f}",
            target_ip=self.get_current_kef_ip(),
            mono=f"{start_mono:.3f}",
        )

        while True:
            if self._should_abort_generation(generation):
                self._log_structured(
                    "ABORT",
                    action="WAKE",
                    gen=generation,
                    reason=reason,
                    step="wait_reachable",
                    current_gen=self._current_generation(),
                    cause="generation_changed",
                    mono=f"{self.mono():.3f}",
                )
                return False

            remaining = deadline - self.mono()
            if remaining <= 0:
                refreshed = self.maybe_refresh_kef_ip(reason=reason, trigger="wait_reachable_timeout")
                self._log_structured(
                    "STEP",
                    action="WAKE",
                    gen=generation,
                    reason=reason,
                    step="wait_reachable",
                    status="timeout_continue",
                    elapsed_ms=int((self.mono() - start_mono) * 1000),
                    last_error=last_error,
                    ip_refresh_attempted=refreshed,
                    target_ip=self.get_current_kef_ip(),
                    mono=f"{self.mono():.3f}",
                )
                return True

            try:
                target_ip = self.get_current_kef_ip()
                with socket.create_connection((target_ip, 80), timeout=min(0.5, remaining)):
                    self._log_structured(
                        "STEP",
                        action="WAKE",
                        gen=generation,
                        reason=reason,
                        step="wait_reachable",
                        status="reachable",
                        elapsed_ms=int((self.mono() - start_mono) * 1000),
                        target_ip=target_ip,
                        mono=f"{self.mono():.3f}",
                    )
                    return True
            except OSError as exc:
                last_error = str(exc)
                if not waiting_logged:
                    waiting_logged = True
                    self._log_structured(
                        "STEP",
                        action="WAKE",
                        gen=generation,
                        reason=reason,
                        step="wait_reachable",
                        status="waiting",
                        poll_s=f"{self.config.reachability_poll_interval:.2f}",
                        last_error=last_error,
                        target_ip=self.get_current_kef_ip(),
                        mono=f"{self.mono():.3f}",
                    )
                if not self._interruptible_sleep(min(self.config.reachability_poll_interval, remaining), generation, "wait_reachable"):
                    return False

    def wake_kef(self, generation: int, reason: str) -> bool:
        outcome = "unknown"
        start_mono = self._log_action_begin("WAKE", generation, reason)
        c = self.config
        target_input = normalize_input_source(c.kef_input)
        self._emit_power_action_started("WAKE", reason)

        try:
            if self._is_session_ending():
                outcome = "skipped_session_ending"
                self._log_structured(
                    "SKIP",
                    action="WAKE",
                    gen=generation,
                    reason=reason,
                    cause="session_ending",
                    mono=f"{self.mono():.3f}",
                )
                return False

            self._log_structured(
                "STEP",
                action="WAKE",
                gen=generation,
                reason=reason,
                step="set_input_source",
                input=target_input,
                target_ip=self.get_current_kef_ip(),
                mono=f"{self.mono():.3f}",
            )

            if not self.wait_until_reachable(c.reachability_wait_timeout, generation, reason):
                outcome = "aborted_before_attempts"
                return False

            def execute_attempt(attempt: int) -> None:
                if target_input:
                    self._set_speaker_source(target_input, fresh=True)
                else:
                    with temporary_socket_timeout(c.socket_timeout):
                        self.get_speaker(fresh=True)

                self._clear_recent_lock_standby_marker()
                self.capture_identity_from_current_ip(reason=reason, trigger=f"wake_success_attempt_{attempt}")
                self._log_structured(
                    "STEP",
                    action="WAKE",
                    gen=generation,
                    reason=reason,
                    step="set_input_source",
                    attempt=attempt,
                    status="success",
                    mono=f"{self.mono():.3f}",
                )

            def build_retry_fields(attempt: int, _exc: Exception) -> dict[str, object]:
                refreshed = self.maybe_refresh_kef_ip(reason=reason, trigger=f"wake_attempt_{attempt}_exception")
                return {
                    "cause": "set_input_source_failed",
                    "ip_refresh_attempted": refreshed,
                    "target_ip": self.get_current_kef_ip(),
                }

            outcome = self._run_generation_attempts(
                action="WAKE",
                generation=generation,
                reason=reason,
                attempt_delays=c.wake_attempt_delays,
                lock_timeout=c.wake_action_lock_timeout,
                purpose="wake",
                execute_attempt=execute_attempt,
                build_retry_fields=build_retry_fields,
            )
            return outcome.startswith("success_attempt_")
        finally:
            self._emit_power_action_finished("WAKE", reason, outcome)
            self._log_action_end("WAKE", generation, reason, outcome, start_mono)

    def standby_kef_preemptive(self, reason: str) -> bool:
        outcome = "unknown"
        start_mono = self._log_action_begin("LOCK_PRE_STANDBY", None, reason)
        self._emit_power_action_started("LOCK_PRE_STANDBY", reason)
        attempt_count = len(_LOCK_PRE_STANDBY_ATTEMPT_DELAYS)

        try:
            self._log_structured("STEP", action="LOCK_PRE_STANDBY", reason=reason, step="shutdown_request", mono=f"{self.mono():.3f}")
            if self._is_session_ending():
                outcome = "skipped_session_ending"
                self._log_structured(
                    "SKIP",
                    action="LOCK_PRE_STANDBY",
                    reason=reason,
                    cause="session_ending",
                    mono=f"{self.mono():.3f}",
                )
                return False

            for attempt, delay in enumerate(_LOCK_PRE_STANDBY_ATTEMPT_DELAYS, start=1):
                if delay > 0:
                    self._log_structured(
                        "STEP",
                        action="LOCK_PRE_STANDBY",
                        reason=reason,
                        step="pre_delay",
                        attempt=attempt,
                        delay_s=f"{delay:.2f}",
                        mono=f"{self.mono():.3f}",
                    )
                    time.sleep(delay)

                if self._is_session_ending():
                    outcome = "skipped_session_ending"
                    self._log_structured(
                        "SKIP",
                        action="LOCK_PRE_STANDBY",
                        reason=reason,
                        cause="session_ending",
                        attempt=attempt,
                        mono=f"{self.mono():.3f}",
                    )
                    return False

                if not self._action_lock.acquire(timeout=self.config.lock_standby_action_lock_timeout):
                    cause = "action_lock_busy"
                    if attempt < attempt_count:
                        self._log_structured(
                            "RETRY",
                            action="LOCK_PRE_STANDBY",
                            reason=reason,
                            attempt=attempt,
                            cause=cause,
                            timeout_s=f"{self.config.lock_standby_action_lock_timeout:.2f}",
                            mono=f"{self.mono():.3f}",
                        )
                        continue

                    outcome = "skipped_action_lock_busy"
                    self._log_structured(
                        "SKIP",
                        action="LOCK_PRE_STANDBY",
                        reason=reason,
                        cause=cause,
                        attempt=attempt,
                        timeout_s=f"{self.config.lock_standby_action_lock_timeout:.2f}",
                        mono=f"{self.mono():.3f}",
                    )
                    return False

                try:
                    self._perform_standby_request(
                        action="LOCK_PRE_STANDBY",
                        generation=None,
                        reason=reason,
                        attempt=attempt,
                        fresh=attempt > 1,
                        verify_timeout=_LOCK_PRE_STANDBY_VERIFY_TIMEOUT,
                    )
                    self._mark_lock_prestandby_success()
                    outcome = f"success_attempt_{attempt}"
                    return True
                except Exception as exc:
                    self.reset_speaker()
                    cause = "standby_not_verified" if isinstance(exc, StandbyVerificationError) else "shutdown_failed"
                    if attempt < attempt_count:
                        self._log_structured(
                            "RETRY",
                            action="LOCK_PRE_STANDBY",
                            reason=reason,
                            attempt=attempt,
                            cause=cause,
                            error=repr(exc),
                            mono=f"{self.mono():.3f}",
                        )
                        continue

                    outcome = "failed_will_fallback_to_apmsuspend"
                    self._log_structured(
                        "WARN",
                        action="LOCK_PRE_STANDBY",
                        reason=reason,
                        attempt=attempt,
                        cause=cause,
                        status="fallback_to_apmsuspend",
                        error=repr(exc),
                        mono=f"{self.mono():.3f}",
                    )
                    return False
                finally:
                    self._action_lock.release()
        finally:
            self._emit_power_action_finished("LOCK_PRE_STANDBY", reason, outcome)
            self._log_action_end("LOCK_PRE_STANDBY", None, reason, outcome, start_mono)

    def standby_kef_end_session(self, reason: str, flags: str) -> bool:
        outcome = "unknown"
        start_mono = self._log_action_begin("ENDSESSION_STANDBY", None, reason)
        self._emit_power_action_started("ENDSESSION_STANDBY", reason)

        try:
            if not self.config.endsession_standby_on_shutdown:
                outcome = "disabled"
                self._log_structured(
                    "SKIP",
                    action="ENDSESSION_STANDBY",
                    reason=reason,
                    cause="disabled",
                    flags=flags,
                    mono=f"{self.mono():.3f}",
                )
                return False

            current_ip = self.get_current_kef_ip()
            if not current_ip:
                outcome = "skipped_no_current_ip"
                self._log_structured(
                    "SKIP",
                    action="ENDSESSION_STANDBY",
                    reason=reason,
                    cause="no_current_ip",
                    flags=flags,
                    mono=f"{self.mono():.3f}",
                )
                return False

            if not self._action_lock.acquire(timeout=self.config.endsession_standby_action_lock_timeout):
                outcome = "skipped_action_lock_busy"
                self._log_structured(
                    "SKIP",
                    action="ENDSESSION_STANDBY",
                    reason=reason,
                    cause="action_lock_busy",
                    flags=flags,
                    timeout_s=f"{self.config.endsession_standby_action_lock_timeout:.2f}",
                    mono=f"{self.mono():.3f}",
                )
                return False

            try:
                self._request_shutdown(fresh=False, timeout=self.config.endsession_standby_socket_timeout)

                outcome = "success"
                self._log_structured(
                    "STEP",
                    action="ENDSESSION_STANDBY",
                    reason=reason,
                    step="shutdown_request",
                    status="success",
                    flags=flags,
                    target_ip=current_ip,
                    mono=f"{self.mono():.3f}",
                )
                return True
            except Exception as exc:
                self.reset_speaker()
                outcome = "failed"
                self._log_structured(
                    "RETRY",
                    action="ENDSESSION_STANDBY",
                    reason=reason,
                    attempt=1,
                    cause="shutdown_failed",
                    flags=flags,
                    error=repr(exc),
                    target_ip=current_ip,
                    mono=f"{self.mono():.3f}",
                )
                return False
            finally:
                self._action_lock.release()
        finally:
            self._emit_power_action_finished("ENDSESSION_STANDBY", reason, outcome)
            self._log_action_end("ENDSESSION_STANDBY", None, reason, outcome, start_mono)

    def _recently_lock_standby_ok(self) -> bool:
        with self._state_lock:
            last = self._last_lock_standby_ok_mono
            if last <= 0:
                return False
            return (self.mono() - last) < self.config.lock_standby_dedup_window

    def standby_kef(self, generation: int, reason: str) -> bool:
        outcome = "unknown"
        start_mono = self._log_action_begin("STANDBY", generation, reason)
        c = self.config
        self._emit_power_action_started("STANDBY", reason)

        try:
            self._log_structured("STEP", action="STANDBY", gen=generation, reason=reason, step="shutdown_request", mono=f"{self.mono():.3f}")
            if self._recently_lock_standby_ok():
                outcome = "skipped_recent_lock_pre_standby_ok"
                self._log_structured(
                    "SKIP",
                    action="STANDBY",
                    gen=generation,
                    reason=reason,
                    cause="recent_lock_pre_standby_ok",
                    window_s=f"{c.lock_standby_dedup_window:.2f}",
                    mono=f"{self.mono():.3f}",
                )
                return True

            if self._is_session_ending():
                outcome = "skipped_session_ending"
                self._log_structured(
                    "SKIP",
                    action="STANDBY",
                    gen=generation,
                    reason=reason,
                    cause="session_ending",
                    mono=f"{self.mono():.3f}",
                )
                return False

            outcome = self._acquire_generation_action_lock(
                action="STANDBY",
                generation=generation,
                reason=reason,
                lock_timeout=c.suspend_action_lock_timeout,
                purpose="standby",
            )
            if outcome is not None:
                return False

            try:
                self._perform_standby_request(
                    action="STANDBY",
                    generation=generation,
                    reason=reason,
                    attempt=1,
                    fresh=False,
                    verify_timeout=_STANDBY_VERIFY_TIMEOUT,
                )
                outcome = "success_attempt_1"
                return True
            except Exception as exc:
                self.reset_speaker()
                outcome = "failed_no_retry_before_suspend"
                self._log_structured(
                    "WARN",
                    action="STANDBY",
                    gen=generation,
                    reason=reason,
                    attempt=1,
                    cause=("standby_not_verified" if isinstance(exc, StandbyVerificationError) else "shutdown_failed"),
                    status="no_retry_before_suspend",
                    error=repr(exc),
                    mono=f"{self.mono():.3f}",
                )
                return False
            finally:
                self._action_lock.release()
        finally:
            self._emit_power_action_finished("STANDBY", reason, outcome)
            self._log_action_end("STANDBY", generation, reason, outcome, start_mono)
