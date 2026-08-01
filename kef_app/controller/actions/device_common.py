from __future__ import annotations

import re
import time
from typing import Callable, Optional

from pykefcontrol.kef_connector import KefConnector

from ..network_timeout import temporary_socket_timeout
from ...devices.speaker_models import INPUT_SOURCE_OPTIONS, normalize_input_source


_CHANGE_INPUT_POLL_INTERVAL = 0.2
_CHANGE_INPUT_VERIFY_TIMEOUT = 2.5
_STANDBY_VERIFY_POLL_INTERVAL = 0.08
_STANDBY_VERIFY_READ_TIMEOUT = 0.20
_STANDBY_VERIFY_TIMEOUT = 0.40
_STANDBY_VERIFY_OFFLINE_FAILURES = 2
_CONFIGURABLE_INPUT_SOURCES = {value for _, value in INPUT_SOURCE_OPTIONS}


class StandbyVerificationError(RuntimeError):
    pass


class ControllerDeviceCommonMixin:
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

    def _log_generation_abort(self, action: str, generation: int, reason: str, step: str) -> None:
        self._log_structured(
            "ABORT",
            action=action,
            gen=generation,
            reason=reason,
            step=step,
            current_gen=self._current_generation(),
            cause="generation_changed",
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
        log_timeout: bool = True,
    ) -> str | None:
        if self._should_abort_generation(generation):
            self._log_generation_abort(action, generation, reason, "before_action_lock")
            return "aborted_stale_generation_before_lock"

        lock_outcome = self._acquire_action_lock_interruptibly(
            lock_timeout,
            generation,
            reason,
            purpose,
            log_timeout=log_timeout,
        )
        if lock_outcome == "action_lock_timeout":
            return "aborted_action_lock_timeout"
        if lock_outcome == "stale_generation":
            return "aborted_stale_generation_while_waiting_lock"

        if self._should_abort_generation(generation):
            self._log_generation_abort(action, generation, reason, "after_action_lock")
            self._action_lock.release()
            return "aborted_stale_generation_after_lock"

        return None

    def _is_configurable_input_source(self, source: str) -> bool:
        return source in _CONFIGURABLE_INPUT_SOURCES

    def _ensure_target_identity(self, action: str, reason: str, trigger: str, *, force_recovery: bool = True) -> bool:
        if self.resolve_target(reason=reason, trigger=trigger, force_recovery=force_recovery):
            return True

        self._log_structured(
            "SKIP",
            action=action,
            reason=reason,
            cause="target_identity_not_verified",
            target_mac=self.get_effective_target_mac() or "<empty>",
            target_ip=self.get_current_kef_ip() or "<empty>",
        )
        return False

    @staticmethod
    def _coerce_volume_level(level: object) -> Optional[int]:
        try:
            value = int(level)
        except (TypeError, ValueError, OverflowError):
            return None
        return max(0, min(100, value))

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
        )
        return "failed_all_attempts"

    def get_input_source(self, fresh: bool = False) -> Optional[str]:
        if not self.get_current_kef_ip():
            return None
        try:
            with temporary_socket_timeout(self.config.socket_timeout):
                speaker = self.get_speaker(fresh=fresh)
                return normalize_input_source(speaker.source)
        except Exception as exc:
            self.reset_speaker()
            self._log_structured("WARN", action="GET_INPUT_SOURCE", error=repr(exc))
            return None

    def _wait_for_input_source(self, expected_input: str, timeout: float = _CHANGE_INPUT_VERIFY_TIMEOUT) -> Optional[str]:
        expected = normalize_input_source(expected_input)
        deadline = self.mono() + timeout
        observed = None
        fresh = True
        while self.mono() < deadline:
            observed = self.get_input_source(fresh=fresh)
            fresh = False
            if observed == expected:
                return observed
            time.sleep(_CHANGE_INPUT_POLL_INTERVAL)
        return observed

    def reset_speaker(self):
        with self._speaker_connection.lock:
            self._speaker_connection.connector = None

    def get_speaker(self, fresh: bool = False) -> KefConnector:
        with self._speaker_connection.lock:
            if fresh or self._speaker_connection.connector is None:
                self._speaker_connection.connector = self._backend.create_connector(self.get_current_kef_ip())
            return self._speaker_connection.connector
