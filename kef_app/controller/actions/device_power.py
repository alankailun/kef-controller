from __future__ import annotations

import socket

from ..network_timeout import temporary_socket_timeout
from .device_common import (
    _LOCK_PRE_STANDBY_ATTEMPT_DELAYS,
    _LOCK_PRE_STANDBY_VERIFY_TIMEOUT,
    _STANDBY_VERIFY_TIMEOUT,
    StandbyVerificationError,
)
from ...devices.speaker_models import normalize_input_source


class ControllerDevicePowerActionsMixin:
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
                if not self._interruptible_sleep(
                    min(self.config.reachability_poll_interval, remaining),
                    generation,
                    "wait_reachable",
                ):
                    return False

    def wake_kef(self, generation: int, reason: str) -> bool:
        outcome = "unknown"
        start_mono = self._log_action_begin("WAKE", generation, reason)
        c = self.config
        target_input = normalize_input_source(c.kef_input)
        self._emit_power_action_started("WAKE", reason)

        try:
            if target_input and not self._is_configurable_input_source(target_input):
                outcome = "skipped_unsupported_input_source"
                self._log_structured(
                    "SKIP",
                    action="WAKE",
                    gen=generation,
                    reason=reason,
                    input=target_input,
                    cause="unsupported_input_source",
                    mono=f"{self.mono():.3f}",
                )
                return False

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

            if not self._ensure_target_identity("WAKE", reason, "wake_before_wait"):
                outcome = "skipped_target_identity_not_verified"
                return False

            if not self.wait_until_reachable(c.reachability_wait_timeout, generation, reason):
                outcome = "aborted_before_attempts"
                return False
            if not self._ensure_target_identity("WAKE", reason, "wake_before_attempts"):
                outcome = "skipped_target_identity_not_verified"
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

    def standby_kef_preemptive(self, generation: int, reason: str) -> bool:
        outcome = "unknown"
        start_mono = self._log_action_begin("LOCK_PRE_STANDBY", generation, reason)
        self._emit_power_action_started("LOCK_PRE_STANDBY", reason)
        attempt_count = len(_LOCK_PRE_STANDBY_ATTEMPT_DELAYS)

        try:
            self._log_structured(
                "STEP",
                action="LOCK_PRE_STANDBY",
                gen=generation,
                reason=reason,
                step="shutdown_request",
                mono=f"{self.mono():.3f}",
            )
            if self._is_session_ending():
                outcome = "skipped_session_ending"
                self._log_structured(
                    "SKIP",
                    action="LOCK_PRE_STANDBY",
                    gen=generation,
                    reason=reason,
                    cause="session_ending",
                    mono=f"{self.mono():.3f}",
                )
                return False
            if not self._ensure_target_identity("LOCK_PRE_STANDBY", reason, "lock_pre_standby_before_attempts"):
                outcome = "skipped_target_identity_not_verified"
                return False

            for attempt, delay in enumerate(_LOCK_PRE_STANDBY_ATTEMPT_DELAYS, start=1):
                outcome = self._run_generation_pre_delay(
                    action="LOCK_PRE_STANDBY",
                    generation=generation,
                    reason=reason,
                    attempt=attempt,
                    delay=delay,
                    sleep_label=f"lock_pre_standby_pre_delay_attempt_{attempt}",
                )
                if outcome is not None:
                    return False

                if self._is_session_ending():
                    outcome = "skipped_session_ending"
                    self._log_structured(
                        "SKIP",
                        action="LOCK_PRE_STANDBY",
                        gen=generation,
                        reason=reason,
                        cause="session_ending",
                        attempt=attempt,
                        mono=f"{self.mono():.3f}",
                    )
                    return False

                outcome = self._acquire_generation_action_lock(
                    action="LOCK_PRE_STANDBY",
                    generation=generation,
                    reason=reason,
                    lock_timeout=self.config.lock_standby_action_lock_timeout,
                    purpose="lock_pre_standby",
                    log_timeout=False,
                )
                if outcome is not None:
                    if outcome == "aborted_action_lock_timeout":
                        cause = "action_lock_busy"
                        if attempt < attempt_count:
                            self._log_structured(
                                "RETRY",
                                action="LOCK_PRE_STANDBY",
                                gen=generation,
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
                            gen=generation,
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
                        generation=generation,
                        reason=reason,
                        attempt=attempt,
                        fresh=attempt > 1,
                        verify_timeout=_LOCK_PRE_STANDBY_VERIFY_TIMEOUT,
                    )
                    if self._should_abort_generation(generation):
                        outcome = "aborted_stale_generation_after_standby_request"
                        self._log_generation_abort("LOCK_PRE_STANDBY", generation, reason, "after_standby_request")
                        return False
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
                            gen=generation,
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
                        gen=generation,
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
            self._log_action_end("LOCK_PRE_STANDBY", generation, reason, outcome, start_mono)

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

            if not self._ensure_target_identity("ENDSESSION_STANDBY", reason, "endsession_before_request"):
                outcome = "skipped_target_identity_not_verified"
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
            self._log_structured(
                "STEP",
                action="STANDBY",
                gen=generation,
                reason=reason,
                step="shutdown_request",
                mono=f"{self.mono():.3f}",
            )
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
            if not self._ensure_target_identity("STANDBY", reason, "standby_before_request"):
                outcome = "skipped_target_identity_not_verified"
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
