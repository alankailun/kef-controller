from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .device_common import _STANDBY_VERIFY_TIMEOUT, StandbyVerificationError
from .fast_standby import ControllerFastStandbyMixin, _outcome_is_success
from ...devices.transport import is_host_unreachable


_FAST_STANDBY_STANDARD_HARD_TIMEOUT = 1.5

StandbyPolicyMode = Literal["fast_request", "verified_request", "end_session"]


@dataclass(frozen=True, slots=True)
class StandbyPolicy:
    action: str
    mode: StandbyPolicyMode
    begin_step: str
    success_step: str
    lock_purpose: str
    lock_timeout_field: str = ""
    socket_timeout_field: str = ""
    disabled_field: str = ""
    disabled_cause: str = "disabled"
    success_outcome: str = "success"
    failed_outcome: str = "failed"
    failed_status: str | None = None
    host_unreachable_outcome: str = "sent_skipped_host_unreachable"
    host_unreachable_status: str = "host_unreachable_assumed_standby"
    host_unreachable_cause: str = "host_unreachable_assume_standby"
    host_unreachable_is_success: bool = True
    fire_and_forget: bool = False
    fire_and_forget_outcome: str | None = None
    skip_if_session_ending: bool = False
    ensure_identity: bool = False
    identity_step: str = ""
    attempt: int | None = None


PREEMPTIVE_STANDBY_POLICY = StandbyPolicy(
    action="EARLY_STANDBY",
    mode="fast_request",
    begin_step="lock_fast_path",
    success_step="shutdown_request",
    lock_purpose="early_standby",
    lock_timeout_field="early_standby_action_lock_timeout",
    socket_timeout_field="suspend_fast_standby_socket_timeout",
    success_outcome="sent_unconfirmed_standard",
    failed_outcome="failed",
    host_unreachable_outcome="sent_skipped_host_unreachable",
    host_unreachable_status="local_network_unavailable_before_suspend",
    host_unreachable_cause="local_route_unavailable_before_suspend",
    host_unreachable_is_success=True,
    fire_and_forget=True,
    fire_and_forget_outcome="sent_unconfirmed_fire_and_forget",
    skip_if_session_ending=True,
)

FAST_SUSPEND_STANDBY_POLICY = StandbyPolicy(
    action="STANDBY",
    mode="fast_request",
    begin_step="suspend_fast_path",
    success_step="suspend_fast_path",
    lock_purpose="standby",
    lock_timeout_field="suspend_fast_standby_action_lock_timeout",
    socket_timeout_field="suspend_fast_standby_socket_timeout",
    disabled_field="suspend_fast_standby_enabled",
    disabled_cause="suspend_fast_standby_disabled",
    success_outcome="sent_unconfirmed_standard",
    failed_outcome="failed_fast_suspend",
    failed_status="fast_suspend_failed",
    host_unreachable_outcome="sent_skipped_host_unreachable",
    host_unreachable_status="host_unreachable_best_effort",
    host_unreachable_cause="suspend_network_or_speaker_unreachable",
    fire_and_forget=True,
    fire_and_forget_outcome="sent_unconfirmed_fire_and_forget",
    skip_if_session_ending=True,
    attempt=1,
)

STANDARD_STANDBY_POLICY = StandbyPolicy(
    action="STANDBY",
    mode="verified_request",
    begin_step="shutdown_request",
    success_step="shutdown_request",
    lock_purpose="standby",
    lock_timeout_field="suspend_action_lock_timeout",
    success_outcome="success_attempt_1",
    failed_outcome="failed_no_retry_before_suspend",
    failed_status="no_retry_before_suspend",
    skip_if_session_ending=True,
    ensure_identity=True,
    identity_step="standby_before_request",
    attempt=1,
)

ENDSESSION_STANDBY_POLICY = StandbyPolicy(
    action="ENDSESSION_STANDBY",
    mode="end_session",
    begin_step="shutdown_request",
    success_step="shutdown_request",
    lock_purpose="endsession_standby",
    lock_timeout_field="endsession_standby_action_lock_timeout",
    socket_timeout_field="endsession_standby_socket_timeout",
    disabled_field="endsession_standby_on_shutdown",
    success_outcome="sent_unconfirmed_standard",
    failed_outcome="failed",
    fire_and_forget=True,
    fire_and_forget_outcome="sent_unconfirmed_fire_and_forget",
    ensure_identity=True,
    identity_step="endsession_before_request",
)


class ControllerDeviceStandbyMixin(ControllerFastStandbyMixin):
    def _config_value(self, field_name: str):
        return getattr(self.config, field_name)

    def _standby_log_fields(self, policy: StandbyPolicy, generation: int | None, reason: str) -> dict[str, object]:
        return {"action": policy.action, "gen": generation, "reason": reason}

    def _log_standby(
        self,
        tag: str,
        policy: StandbyPolicy,
        generation: int | None,
        reason: str,
        *,
        log_level: object = None,
        **fields: object,
    ) -> None:
        self._log_structured(
            tag,
            log_level=log_level,
            **self._standby_log_fields(policy, generation, reason),
            **fields,
        )

    def _standby_skip_session_ending(self, policy: StandbyPolicy, generation: int | None, reason: str) -> tuple[bool, str]:
        if not policy.skip_if_session_ending or not self._is_session_ending():
            return False, ""
        outcome = "skipped_session_ending"
        self._log_standby(
            "SKIP",
            policy,
            generation,
            reason,
            cause="session_ending",
        )
        return True, outcome

    def _standby_skip_disabled(
        self,
        policy: StandbyPolicy,
        generation: int | None,
        reason: str,
        *,
        extra_fields: dict[str, object] | None = None,
    ) -> tuple[bool, str]:
        if not policy.disabled_field or bool(self._config_value(policy.disabled_field)):
            return False, ""
        outcome = "disabled"
        self._log_standby(
            "SKIP",
            policy,
            generation,
            reason,
            cause=policy.disabled_cause,
            **(extra_fields or {}),
        )
        return True, outcome

    def _execute_standby_policy(
        self,
        policy: StandbyPolicy,
        *,
        generation: int | None,
        reason: str,
        flags: str = "",
    ) -> bool:
        outcome = "unknown"
        start_mono = self._log_action_begin(policy.action, generation, reason)
        self._emit_power_action_started(policy.action, reason)

        try:
            skipped, outcome = self._standby_skip_disabled(
                policy,
                generation,
                reason,
                extra_fields={"flags": flags} if flags else None,
            )
            if skipped:
                return False

            if policy.mode == "end_session":
                success, outcome = self._execute_end_session_policy(policy, reason, flags)
                return success
            if policy.mode == "verified_request":
                success, outcome = self._execute_verified_standby_policy(policy, generation, reason)
                return success

            success, outcome = self._execute_fast_request_policy(policy, generation, reason)
            return success
        finally:
            self._emit_power_action_finished(policy.action, reason, outcome)
            self._log_action_end(policy.action, generation, reason, outcome, start_mono)

    def _execute_fast_request_policy(
        self,
        policy: StandbyPolicy,
        generation: int | None,
        reason: str,
    ) -> tuple[bool, str]:
        skipped, outcome = self._standby_skip_session_ending(policy, generation, reason)
        if skipped:
            return False, outcome

        return self._perform_fast_shutdown(
            policy,
            generation=generation,
            reason=reason,
        )

    def _execute_verified_standby_policy(
        self,
        policy: StandbyPolicy,
        generation: int | None,
        reason: str,
    ) -> tuple[bool, str]:
        self._log_standby(
            "STEP",
            policy,
            generation,
            reason,
            step=policy.begin_step,
        )

        skipped, outcome = self._standby_skip_session_ending(policy, generation, reason)
        if skipped:
            return False, outcome
        if policy.ensure_identity and not self._ensure_target_identity(policy.action, reason, policy.identity_step):
            return False, "skipped_target_identity_not_verified"

        if generation is None:
            return False, "skipped_missing_generation"

        outcome = self._acquire_generation_action_lock(
            action=policy.action,
            generation=generation,
            reason=reason,
            lock_timeout=float(self._config_value(policy.lock_timeout_field)),
            purpose=policy.lock_purpose,
        )
        if outcome is not None:
            return False, outcome

        try:
            try:
                self._perform_standby_request(
                    action=policy.action,
                    generation=generation,
                    reason=reason,
                    attempt=policy.attempt or 1,
                    fresh=False,
                    verify_timeout=_STANDBY_VERIFY_TIMEOUT,
                )
                return True, policy.success_outcome
            except Exception as exc:
                self.reset_speaker()
                self._log_standby(
                    "WARN",
                    policy,
                    generation,
                    reason,
                    attempt=policy.attempt or 1,
                    cause=("standby_not_verified" if isinstance(exc, StandbyVerificationError) else "shutdown_failed"),
                    status=policy.failed_status,
                    error=repr(exc),
                )
                return False, policy.failed_outcome
        finally:
            self._action_lock.release()

    def _execute_end_session_policy(self, policy: StandbyPolicy, reason: str, flags: str) -> tuple[bool, str]:
        if policy.ensure_identity and not self._ensure_target_identity(policy.action, reason, policy.identity_step):
            return False, "skipped_target_identity_not_verified"

        current_ip = self.get_current_kef_ip()
        if not current_ip:
            self._log_standby(
                "SKIP",
                policy,
                None,
                reason,
                cause="no_current_ip",
                flags=flags,
            )
            return False, "skipped_no_current_ip"

        if policy.fire_and_forget:
            fast_send_outcome = self._try_fast_standby_send(
                action=policy.action,
                generation=None,
                reason=reason,
                current_ip=current_ip,
                mark_early_standby_sent_unconfirmed=False,
                extra_fields={"flags": flags},
                success_outcome=policy.fire_and_forget_outcome or policy.success_outcome,
            )
            if fast_send_outcome is not None:
                return True, fast_send_outcome

        lock_timeout = float(self._config_value(policy.lock_timeout_field))
        if not self._action_lock.acquire(timeout=lock_timeout):
            self._log_standby(
                "SKIP",
                policy,
                None,
                reason,
                cause="action_lock_busy",
                flags=flags,
                timeout_s=f"{lock_timeout:.2f}",
            )
            return False, "skipped_action_lock_busy"

        try:
            self._request_shutdown(fresh=False, timeout=float(self._config_value(policy.socket_timeout_field)))
            self._log_standby(
                "STEP",
                policy,
                None,
                reason,
                step=policy.success_step,
                status="sent",
                flags=flags,
                target_ip=current_ip,
            )
            return True, policy.success_outcome
        except Exception as exc:
            self.reset_speaker()
            self._log_standby(
                "RETRY",
                policy,
                None,
                reason,
                attempt=1,
                cause="shutdown_failed",
                flags=flags,
                error=repr(exc),
                target_ip=current_ip,
            )
            return False, policy.failed_outcome
        finally:
            self._action_lock.release()

    def _perform_fast_shutdown(
        self,
        policy: StandbyPolicy,
        *,
        generation: int | None,
        reason: str,
    ) -> tuple[bool, str]:
        current_ip = self.get_current_kef_ip()
        socket_timeout = float(self._config_value(policy.socket_timeout_field))
        self._log_standby(
            "STEP",
            policy,
            generation,
            reason,
            log_level="info",
            step=policy.begin_step,
            status="begin",
            target_ip=current_ip or "<empty>",
            target_mac=self.get_effective_target_mac() or "<empty>",
            identity_check="cached_target_only",
            verify_standby=False,
        )
        if not current_ip:
            outcome = "skipped_no_current_ip"
            self._log_standby(
                "SKIP",
                policy,
                generation,
                reason,
                cause="no_current_ip",
            )
            return False, outcome

        if policy.fire_and_forget:
            fast_send_result = self._try_fast_standby_send(
                action=policy.action,
                generation=generation,
                reason=reason,
                current_ip=current_ip,
                mark_early_standby_sent_unconfirmed=policy.action == "EARLY_STANDBY",
                success_outcome=policy.fire_and_forget_outcome or policy.success_outcome,
                host_unreachable_outcome=policy.host_unreachable_outcome,
                host_unreachable_status=policy.host_unreachable_status,
                host_unreachable_cause=policy.host_unreachable_cause,
            )
            if fast_send_result is not None:
                return _outcome_is_success(fast_send_result), fast_send_result

        if generation is None:
            return False, "skipped_missing_generation"

        outcome = self._acquire_generation_action_lock(
            action=policy.action,
            generation=generation,
            reason=reason,
            lock_timeout=float(self._config_value(policy.lock_timeout_field)),
            purpose=policy.lock_purpose,
        )
        if outcome is not None:
            return False, outcome

        try:
            request_start_mono = self.mono()
            self._request_shutdown(fresh=False, timeout=socket_timeout)
            request_finished_mono = self.mono()
            request_duration_ms = int(max(0.0, request_finished_mono - request_start_mono) * 1000)
            if request_duration_ms > int(_FAST_STANDBY_STANDARD_HARD_TIMEOUT * 1000):
                outcome = "failed_standard_request_deadline_exceeded"
                self._log_standby(
                    "WARN",
                    policy,
                    generation,
                    reason,
                    step=policy.success_step,
                    status="late_success_ignored",
                    cause="standard_fallback_deadline_exceeded",
                    target_ip=current_ip,
                    duration_ms=request_duration_ms,
                    deadline_s=f"{_FAST_STANDBY_STANDARD_HARD_TIMEOUT:.2f}",
                    mono=f"{request_finished_mono:.3f}",
                )
                return False, outcome
            if policy.action == "EARLY_STANDBY":
                self._mark_early_standby_sent_unconfirmed()
            outcome = policy.success_outcome
            self._log_standby(
                "STEP",
                policy,
                generation,
                reason,
                step=policy.success_step,
                status="sent",
                target_ip=current_ip,
                timeout_s=f"{socket_timeout:.2f}",
                duration_ms=request_duration_ms,
                mono=f"{request_finished_mono:.3f}",
            )
            return True, outcome
        except Exception as exc:
            request_finished_mono = self.mono()
            request_duration_ms = int(max(0.0, request_finished_mono - request_start_mono) * 1000)
            self.reset_speaker()
            if request_duration_ms > int(_FAST_STANDBY_STANDARD_HARD_TIMEOUT * 1000):
                outcome = "failed_standard_request_deadline_exceeded"
                fields = {
                    "step": policy.success_step,
                    "status": "late_error_ignored",
                    "cause": "standard_fallback_deadline_exceeded",
                    "error": repr(exc),
                    "target_ip": current_ip,
                    "duration_ms": request_duration_ms,
                    "deadline_s": f"{_FAST_STANDBY_STANDARD_HARD_TIMEOUT:.2f}",
                    "mono": f"{request_finished_mono:.3f}",
                }
                if policy.attempt is not None:
                    fields["attempt"] = policy.attempt
                self._log_standby("WARN", policy, generation, reason, **fields)
                return False, outcome

            if is_host_unreachable(exc):
                if policy.action == "EARLY_STANDBY":
                    self.log_wifi_diagnostics(
                        reason=reason,
                        trigger="early_standby_host_unreachable",
                        fresh=True,
                        timeout=0.15,
                    )
                outcome = policy.host_unreachable_outcome
                fields = {
                    "step": policy.success_step,
                    "status": policy.host_unreachable_status,
                    "cause": policy.host_unreachable_cause,
                    "error": repr(exc),
                    "target_ip": current_ip,
                }
                if policy.attempt is not None:
                    fields["attempt"] = policy.attempt
                self._log_standby(
                    "STEP" if policy.host_unreachable_is_success else "WARN",
                    policy,
                    generation,
                    reason,
                    **fields,
                )
                return policy.host_unreachable_is_success, outcome

            outcome = policy.failed_outcome
            fields = {
                "cause": "shutdown_failed",
                "error": repr(exc),
                "target_ip": current_ip,
            }
            if policy.attempt is not None:
                fields["attempt"] = policy.attempt
            if policy.failed_status is not None:
                fields["status"] = policy.failed_status
            self._log_standby("WARN", policy, generation, reason, **fields)
            return False, outcome
        finally:
            self._action_lock.release()

    def standby_kef_preemptive(self, generation: int, reason: str) -> bool:
        return self._execute_standby_policy(PREEMPTIVE_STANDBY_POLICY, generation=generation, reason=reason)

    def standby_kef_end_session(self, reason: str, flags: str) -> bool:
        return self._execute_standby_policy(ENDSESSION_STANDBY_POLICY, generation=None, reason=reason, flags=flags)

    def standby_kef_fast_suspend(self, generation: int, reason: str) -> bool:
        return self._execute_standby_policy(FAST_SUSPEND_STANDBY_POLICY, generation=generation, reason=reason)

    def standby_kef(self, generation: int, reason: str) -> bool:
        return self._execute_standby_policy(STANDARD_STANDBY_POLICY, generation=generation, reason=reason)
