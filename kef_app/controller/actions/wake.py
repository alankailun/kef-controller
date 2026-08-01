from __future__ import annotations

import socket
from ...devices.speaker_models import normalize_input_source


class ControllerDeviceWakeMixin:
    def wait_until_reachable(self, timeout: float, generation: int, reason: str) -> bool:
        start_mono = self.mono()
        deadline = start_mono + timeout
        waiting_logged = False
        last_error = None
        log = self._action_log("WAKE", generation, reason)
        log.write(
            "STEP",
            step="wait_reachable",
            status="begin",
            timeout_s=f"{timeout:.2f}",
            target_ip=self.get_current_kef_ip(),
            mono=f"{start_mono:.3f}",
        )

        while True:
            if self._should_abort_generation(generation):
                log.write(
                    "ABORT",
                    step="wait_reachable",
                    current_gen=self._current_generation(),
                    cause="generation_changed",
                )
                return False

            remaining = deadline - self.mono()
            if remaining <= 0:
                refreshed = self.maybe_refresh_kef_ip(reason=reason, trigger="wait_reachable_timeout")
                log.write(
                    "STEP",
                    step="wait_reachable",
                    status="timeout_continue",
                    elapsed_ms=int((self.mono() - start_mono) * 1000),
                    last_error=last_error,
                    ip_refresh_attempted=refreshed,
                    target_ip=self.get_current_kef_ip(),
                )
                return True

            try:
                target_ip = self.get_current_kef_ip()
                with socket.create_connection((target_ip, 80), timeout=min(0.5, remaining)):
                    log.write(
                        "STEP",
                        step="wait_reachable",
                        status="reachable",
                        elapsed_ms=int((self.mono() - start_mono) * 1000),
                        target_ip=target_ip,
                    )
                    return True
            except OSError as exc:
                last_error = str(exc)
                if not waiting_logged:
                    waiting_logged = True
                    log.write(
                        "STEP",
                        step="wait_reachable",
                        status="waiting",
                        poll_s=f"{self.config.reachability_poll_interval:.2f}",
                        last_error=last_error,
                        target_ip=self.get_current_kef_ip(),
                    )
                if not self._interruptible_sleep(
                    min(self.config.reachability_poll_interval, remaining),
                    generation,
                    "wait_reachable",
                ):
                    return False

    def wake_kef(self, generation: int, reason: str, *, skip_if_already_on: bool = False) -> bool:
        outcome = "unknown"
        start_mono = self._log_action_begin("WAKE", generation, reason)
        c = self.config
        target_input = normalize_input_source(c.kef_input)
        log = self._action_log("WAKE", generation, reason)
        self._emit_power_action_started("WAKE", reason)

        try:
            if not target_input:
                # Waking works by setting the input source; without one there is
                # no request to send, so a "success" here would be a silent no-op.
                outcome = "skipped_no_input_configured"
                log.write(
                    "SKIP",
                    cause="no_input_configured",
                )
                return False

            if not self._is_configurable_input_source(target_input):
                outcome = "skipped_unsupported_input_source"
                log.write(
                    "SKIP",
                    target_input=target_input,
                    cause="unsupported_input_source",
                )
                return False

            if self._is_session_ending():
                outcome = "skipped_session_ending"
                log.write(
                    "SKIP",
                    cause="session_ending",
                )
                return False

            log.write(
                "STEP",
                step="set_input_source",
                target_input=target_input,
                target_ip=self.get_current_kef_ip(),
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

            if skip_if_already_on:
                # Display-on is a recovery action, not an instruction to take
                # over the speaker's current input.  A fresh source read is
                # authoritative: some firmware reports powerOn briefly after
                # entering standby, while source is already ``standby``.
                current_input = self.get_input_source(fresh=True)
                if current_input and current_input != "standby":
                    self._set_speaker_runtime_state(
                        input_source=current_input,
                        speaker_on=True,
                        trigger="display_on_idempotency_check",
                    )
                    outcome = "success_already_on"
                    log.write(
                        "STEP",
                        step="set_input_source",
                        status=outcome,
                        actual_input=current_input,
                    )
                    return True

            def execute_attempt(attempt: int) -> None:
                self._set_speaker_source(target_input, fresh=True)

                self.capture_identity_from_current_ip(reason=reason, trigger=f"wake_success_attempt_{attempt}")
                self.log_wifi_diagnostics(reason=reason, trigger=f"wake_success_attempt_{attempt}")
                log.write(
                    "STEP",
                    step="set_input_source",
                    attempt=attempt,
                    status="success",
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
