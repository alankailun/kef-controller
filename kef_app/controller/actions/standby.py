from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .device_common import _STANDBY_VERIFY_TIMEOUT, StandbyVerificationError
from ...devices.transport import FireAndForgetShutdownResult, fire_and_forget_standby, is_host_unreachable


_FAST_STANDBY_FIRE_AND_FORGET_ATTEMPTS = 3
_FAST_STANDBY_FIRE_AND_FORGET_SOCKET_TIMEOUT = 0.18
_FAST_STANDBY_FIRE_AND_FORGET_JOIN_TIMEOUT = 0.25
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
    host_unreachable_outcome: str = "success_assumed_host_unreachable"
    host_unreachable_status: str = "host_unreachable_assumed_standby"
    host_unreachable_cause: str = "host_unreachable_assume_standby"
    fire_and_forget: bool = False
    fire_and_forget_outcome: str | None = None
    mark_lock_success: bool = False
    skip_if_recent_lock: bool = False
    skip_if_session_ending: bool = False
    ensure_identity: bool = False
    identity_step: str = ""
    attempt: int | None = None


PREEMPTIVE_STANDBY_POLICY = StandbyPolicy(
    action="EARLY_STANDBY",
    mode="fast_request",
    begin_step="lock_fast_path",
    success_step="shutdown_request",
    lock_purpose="lock_pre_standby",
    lock_timeout_field="lock_standby_action_lock_timeout",
    socket_timeout_field="suspend_fast_standby_socket_timeout",
    success_outcome="success",
    failed_outcome="failed",
    fire_and_forget=True,
    fire_and_forget_outcome="success_fire_and_forget",
    mark_lock_success=True,
    skip_if_recent_lock=True,
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
    success_outcome="success_fast_suspend",
    failed_outcome="failed_fast_suspend",
    failed_status="fast_suspend_failed",
    host_unreachable_outcome="success_best_effort_host_unreachable",
    host_unreachable_status="host_unreachable_best_effort",
    host_unreachable_cause="suspend_network_or_speaker_unreachable",
    fire_and_forget=True,
    fire_and_forget_outcome="success_fast_suspend_fire_and_forget",
    skip_if_recent_lock=True,
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
    skip_if_recent_lock=True,
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
    success_outcome="success",
    failed_outcome="failed",
    fire_and_forget=True,
    fire_and_forget_outcome="success_fire_and_forget",
    ensure_identity=True,
    identity_step="endsession_before_request",
)


class ControllerDeviceStandbyMixin:
    def _recently_lock_standby_ok(self) -> bool:
        with self._state_lock:
            return self._lock_standby_dedup.is_recent(self.mono(), self.config.lock_standby_dedup_window)

    def _config_value(self, field_name: str):
        return getattr(self.config, field_name)

    def _standby_log_fields(self, policy: StandbyPolicy, generation: int | None, reason: str) -> dict[str, object]:
        return {"action": policy.action, "gen": generation, "reason": reason}

    def _standby_skip_recent_lock(self, policy: StandbyPolicy, generation: int | None, reason: str) -> tuple[bool, str]:
        if not policy.skip_if_recent_lock or not self._recently_lock_standby_ok():
            return False, ""
        outcome = "skipped_recent_lock_pre_standby_ok"
        self._log_structured(
            "SKIP",
            **self._standby_log_fields(policy, generation, reason),
            cause="recent_lock_pre_standby_ok",
            window_s=f"{self.config.lock_standby_dedup_window:.2f}",
            mono=f"{self.mono():.3f}",
        )
        return True, outcome

    def _standby_skip_session_ending(self, policy: StandbyPolicy, generation: int | None, reason: str) -> tuple[bool, str]:
        if not policy.skip_if_session_ending or not self._is_session_ending():
            return False, ""
        outcome = "skipped_session_ending"
        self._log_structured(
            "SKIP",
            **self._standby_log_fields(policy, generation, reason),
            cause="session_ending",
            mono=f"{self.mono():.3f}",
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
        fields = self._standby_log_fields(policy, generation, reason)
        if extra_fields:
            fields.update(extra_fields)
        self._log_structured(
            "SKIP",
            **fields,
            cause=policy.disabled_cause,
            mono=f"{self.mono():.3f}",
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
        skipped, outcome = self._standby_skip_recent_lock(policy, generation, reason)
        if skipped:
            return True, outcome

        return self._perform_fast_shutdown(
            action=policy.action,
            generation=generation,
            reason=reason,
            begin_step=policy.begin_step,
            success_step=policy.success_step,
            lock_timeout=float(self._config_value(policy.lock_timeout_field)),
            lock_purpose=policy.lock_purpose,
            socket_timeout=float(self._config_value(policy.socket_timeout_field)),
            success_outcome=policy.success_outcome,
            host_unreachable_outcome=policy.host_unreachable_outcome,
            host_unreachable_status=policy.host_unreachable_status,
            host_unreachable_cause=policy.host_unreachable_cause,
            failed_outcome=policy.failed_outcome,
            failed_status=policy.failed_status,
            attempt=policy.attempt,
            mark_lock_success=policy.mark_lock_success,
            fire_and_forget=policy.fire_and_forget,
            fire_and_forget_outcome=policy.fire_and_forget_outcome,
        )

    def _execute_verified_standby_policy(
        self,
        policy: StandbyPolicy,
        generation: int | None,
        reason: str,
    ) -> tuple[bool, str]:
        self._log_structured(
            "STEP",
            **self._standby_log_fields(policy, generation, reason),
            step=policy.begin_step,
            mono=f"{self.mono():.3f}",
        )

        skipped, outcome = self._standby_skip_recent_lock(policy, generation, reason)
        if skipped:
            return True, outcome
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
                self._log_structured(
                    "WARN",
                    action=policy.action,
                    gen=generation,
                    reason=reason,
                    attempt=policy.attempt or 1,
                    cause=("standby_not_verified" if isinstance(exc, StandbyVerificationError) else "shutdown_failed"),
                    status=policy.failed_status,
                    error=repr(exc),
                    mono=f"{self.mono():.3f}",
                )
                return False, policy.failed_outcome
        finally:
            self._action_lock.release()

    def _execute_end_session_policy(self, policy: StandbyPolicy, reason: str, flags: str) -> tuple[bool, str]:
        if policy.ensure_identity and not self._ensure_target_identity(policy.action, reason, policy.identity_step):
            return False, "skipped_target_identity_not_verified"

        current_ip = self.get_current_kef_ip()
        if not current_ip:
            self._log_structured(
                "SKIP",
                action=policy.action,
                reason=reason,
                cause="no_current_ip",
                flags=flags,
                mono=f"{self.mono():.3f}",
            )
            return False, "skipped_no_current_ip"

        if policy.fire_and_forget:
            fire_and_forget_outcome = self._try_fire_and_forget_shutdown(
                action=policy.action,
                generation=None,
                reason=reason,
                current_ip=current_ip,
                mark_lock_success=False,
                extra_fields={"flags": flags},
                success_outcome=policy.fire_and_forget_outcome or policy.success_outcome,
            )
            if fire_and_forget_outcome is not None:
                return True, fire_and_forget_outcome

        lock_timeout = float(self._config_value(policy.lock_timeout_field))
        if not self._action_lock.acquire(timeout=lock_timeout):
            self._log_structured(
                "SKIP",
                action=policy.action,
                reason=reason,
                cause="action_lock_busy",
                flags=flags,
                timeout_s=f"{lock_timeout:.2f}",
                mono=f"{self.mono():.3f}",
            )
            return False, "skipped_action_lock_busy"

        try:
            self._request_shutdown(fresh=False, timeout=float(self._config_value(policy.socket_timeout_field)))
            self._log_structured(
                "STEP",
                action=policy.action,
                reason=reason,
                step=policy.success_step,
                status="success",
                flags=flags,
                target_ip=current_ip,
                mono=f"{self.mono():.3f}",
            )
            return True, policy.success_outcome
        except Exception as exc:
            self.reset_speaker()
            self._log_structured(
                "RETRY",
                action=policy.action,
                reason=reason,
                attempt=1,
                cause="shutdown_failed",
                flags=flags,
                error=repr(exc),
                target_ip=current_ip,
                mono=f"{self.mono():.3f}",
            )
            return False, policy.failed_outcome
        finally:
            self._action_lock.release()

    def _perform_fast_shutdown(
        self,
        *,
        action: str,
        generation: int | None,
        reason: str,
        begin_step: str,
        success_step: str,
        lock_timeout: float,
        lock_purpose: str,
        socket_timeout: float,
        success_outcome: str,
        host_unreachable_outcome: str,
        host_unreachable_status: str,
        host_unreachable_cause: str,
        failed_outcome: str,
        failed_status: str | None = None,
        attempt: int | None = None,
        mark_lock_success: bool = False,
        fire_and_forget: bool = False,
        fire_and_forget_outcome: str | None = None,
    ) -> tuple[bool, str]:
        current_ip = self.get_current_kef_ip()
        self._log_structured(
            "STEP",
            action=action,
            gen=generation,
            reason=reason,
            step=begin_step,
            status="begin",
            target_ip=current_ip or "<empty>",
            target_mac=self.get_effective_target_mac() or "<empty>",
            identity_check="cached_target_only",
            verify_standby=False,
            mono=f"{self.mono():.3f}",
        )
        if not current_ip:
            outcome = "skipped_no_current_ip"
            self._log_structured(
                "SKIP",
                action=action,
                gen=generation,
                reason=reason,
                cause="no_current_ip",
                mono=f"{self.mono():.3f}",
            )
            return False, outcome

        if fire_and_forget:
            fire_and_forget_result = self._try_fire_and_forget_shutdown(
                action=action,
                generation=generation,
                reason=reason,
                current_ip=current_ip,
                mark_lock_success=mark_lock_success,
                success_outcome=fire_and_forget_outcome or success_outcome,
                host_unreachable_outcome=host_unreachable_outcome,
                host_unreachable_status=host_unreachable_status,
                host_unreachable_cause=host_unreachable_cause,
            )
            if fire_and_forget_result is not None:
                return True, fire_and_forget_result

        if generation is None:
            return False, "skipped_missing_generation"

        outcome = self._acquire_generation_action_lock(
            action=action,
            generation=generation,
            reason=reason,
            lock_timeout=lock_timeout,
            purpose=lock_purpose,
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
                self._log_structured(
                    "WARN",
                    action=action,
                    gen=generation,
                    reason=reason,
                    step=success_step,
                    status="late_success_ignored",
                    cause="standard_fallback_deadline_exceeded",
                    target_ip=current_ip,
                    duration_ms=request_duration_ms,
                    deadline_s=f"{_FAST_STANDBY_STANDARD_HARD_TIMEOUT:.2f}",
                    mono=f"{request_finished_mono:.3f}",
                )
                return False, outcome
            if mark_lock_success:
                self._mark_lock_prestandby_success()
            outcome = success_outcome
            self._log_structured(
                "STEP",
                action=action,
                gen=generation,
                reason=reason,
                step=success_step,
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
                    "action": action,
                    "gen": generation,
                    "reason": reason,
                    "step": success_step,
                    "status": "late_error_ignored",
                    "cause": "standard_fallback_deadline_exceeded",
                    "error": repr(exc),
                    "target_ip": current_ip,
                    "duration_ms": request_duration_ms,
                    "deadline_s": f"{_FAST_STANDBY_STANDARD_HARD_TIMEOUT:.2f}",
                    "mono": f"{request_finished_mono:.3f}",
                }
                if attempt is not None:
                    fields["attempt"] = attempt
                self._log_structured("WARN", **fields)
                return False, outcome

            if is_host_unreachable(exc):
                if mark_lock_success:
                    self._mark_lock_prestandby_success()
                outcome = host_unreachable_outcome
                fields = {
                    "action": action,
                    "gen": generation,
                    "reason": reason,
                    "step": success_step,
                    "status": host_unreachable_status,
                    "cause": host_unreachable_cause,
                    "error": repr(exc),
                    "target_ip": current_ip,
                    "mono": f"{self.mono():.3f}",
                }
                if attempt is not None:
                    fields["attempt"] = attempt
                self._log_structured("STEP", **fields)
                return True, outcome

            outcome = failed_outcome
            fields = {
                "action": action,
                "gen": generation,
                "reason": reason,
                "cause": "shutdown_failed",
                "error": repr(exc),
                "target_ip": current_ip,
                "mono": f"{self.mono():.3f}",
            }
            if attempt is not None:
                fields["attempt"] = attempt
            if failed_status is not None:
                fields["status"] = failed_status
            self._log_structured("WARN", **fields)
            return False, outcome
        finally:
            self._action_lock.release()

    def _send_fire_and_forget_shutdown(self, current_ip: str) -> FireAndForgetShutdownResult:
        return fire_and_forget_standby(
            current_ip,
            port=self.config.mac_discovery_tcp_port,
            attempts=_FAST_STANDBY_FIRE_AND_FORGET_ATTEMPTS,
            socket_timeout=_FAST_STANDBY_FIRE_AND_FORGET_SOCKET_TIMEOUT,
            join_timeout=_FAST_STANDBY_FIRE_AND_FORGET_JOIN_TIMEOUT,
        )

    def _try_fire_and_forget_shutdown(
        self,
        *,
        action: str,
        generation: int | None,
        reason: str,
        current_ip: str,
        mark_lock_success: bool,
        extra_fields: dict[str, object] | None = None,
        success_outcome: str = "success_fire_and_forget",
        host_unreachable_outcome: str = "success_assumed_host_unreachable",
        host_unreachable_status: str = "host_unreachable_assumed_standby",
        host_unreachable_cause: str = "fire_and_forget_host_unreachable",
    ) -> str | None:
        result = self._send_fire_and_forget_shutdown(current_ip)
        if result.success:
            status = "sent"
            outcome = success_outcome
        elif result.all_host_unreachable:
            status = host_unreachable_status
            outcome = host_unreachable_outcome
        else:
            status = "failed_fallback_standard"
            outcome = None
        fields = {
            "action": action,
            "gen": generation,
            "reason": reason,
            "step": "fire_and_forget_shutdown",
            "status": status,
            "target_ip": current_ip,
            "attempts": result.attempts,
            "completed": result.completed,
            "pending": result.pending,
            "duration_ms": result.duration_ms,
            "bypass_action_lock": True,
            "read_response": False,
            "mono": f"{self.mono():.3f}",
        }
        if result.all_host_unreachable:
            fields["cause"] = host_unreachable_cause
            fields["all_host_unreachable"] = True
        if extra_fields:
            fields.update(extra_fields)
        if result.errors:
            fields["errors"] = "; ".join(result.errors)

        self._log_structured("STEP", log_level="info", **fields)
        if outcome is not None and mark_lock_success:
            self._mark_lock_prestandby_success()
        return outcome

    def standby_kef_preemptive(self, generation: int, reason: str) -> bool:
        return self._execute_standby_policy(PREEMPTIVE_STANDBY_POLICY, generation=generation, reason=reason)

    def standby_kef_end_session(self, reason: str, flags: str) -> bool:
        return self._execute_standby_policy(ENDSESSION_STANDBY_POLICY, generation=None, reason=reason, flags=flags)

    def standby_kef_fast_suspend(self, generation: int, reason: str) -> bool:
        return self._execute_standby_policy(FAST_SUSPEND_STANDBY_POLICY, generation=generation, reason=reason)

    def standby_kef(self, generation: int, reason: str) -> bool:
        return self._execute_standby_policy(STANDARD_STANDBY_POLICY, generation=generation, reason=reason)
