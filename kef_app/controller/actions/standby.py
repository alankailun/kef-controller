from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .device_common import _STANDBY_VERIFY_TIMEOUT, StandbyVerificationError
from .fast_standby import ControllerFastStandbyMixin, _outcome_is_success


_ENDSESSION_FAST_STANDBY_BUDGET_S = 2.00
_ENDSESSION_FAST_STANDBY_BUDGET_MS = int(_ENDSESSION_FAST_STANDBY_BUDGET_S * 1000)


@dataclass(frozen=True, slots=True)
class FastStandbyPolicy:
    action: str
    begin_step: str
    failed_outcome: str
    fire_and_forget_outcome: str
    disabled_field: str = ""
    disabled_cause: str = "disabled"
    host_unreachable_outcome: str = "sent_skipped_host_unreachable"
    host_unreachable_status: str = "host_unreachable_assumed_standby"
    host_unreachable_cause: str = "host_unreachable_assume_standby"
    host_unreachable_is_success: bool = True
    skip_if_session_ending: bool = False


@dataclass(frozen=True, slots=True)
class VerifiedStandbyPolicy:
    action: str
    begin_step: str
    success_step: str
    lock_purpose: str
    lock_timeout_field: str
    identity_step: str
    success_outcome: str
    failed_outcome: str
    failed_status: str | None = None
    skip_if_session_ending: bool = False
    attempt: int | None = None


@dataclass(frozen=True, slots=True)
class EndSessionStandbyPolicy:
    action: str
    begin_step: str
    success_step: str
    disabled_field: str
    disabled_cause: str
    failed_outcome: str
    fire_and_forget_outcome: str


StandbyLogPolicy = FastStandbyPolicy | VerifiedStandbyPolicy | EndSessionStandbyPolicy


PREEMPTIVE_STANDBY_POLICY = FastStandbyPolicy(
    action="EARLY_STANDBY",
    begin_step="lock_fast_path",
    failed_outcome="failed",
    fire_and_forget_outcome="sent_unconfirmed_fire_and_forget",
    host_unreachable_outcome="sent_skipped_host_unreachable",
    host_unreachable_status="local_network_unavailable_before_suspend",
    host_unreachable_cause="local_route_unavailable_before_suspend",
    host_unreachable_is_success=True,
    skip_if_session_ending=True,
)

FAST_SUSPEND_STANDBY_POLICY = FastStandbyPolicy(
    action="STANDBY",
    begin_step="suspend_fast_path",
    disabled_field="suspend_fast_standby_enabled",
    disabled_cause="suspend_fast_standby_disabled",
    failed_outcome="failed_fast_suspend",
    fire_and_forget_outcome="sent_unconfirmed_fire_and_forget",
    host_unreachable_outcome="sent_skipped_host_unreachable",
    host_unreachable_status="host_unreachable_best_effort",
    host_unreachable_cause="suspend_network_or_speaker_unreachable",
    skip_if_session_ending=True,
)

STANDARD_STANDBY_POLICY = VerifiedStandbyPolicy(
    action="STANDBY",
    begin_step="shutdown_request",
    success_step="shutdown_request",
    lock_purpose="standby",
    lock_timeout_field="suspend_action_lock_timeout",
    identity_step="standby_before_request",
    success_outcome="success_attempt_1",
    failed_outcome="failed_no_retry_before_suspend",
    failed_status="no_retry_before_suspend",
    skip_if_session_ending=True,
    attempt=1,
)

ENDSESSION_STANDBY_POLICY = EndSessionStandbyPolicy(
    action="ENDSESSION_STANDBY",
    begin_step="shutdown_request",
    success_step="shutdown_request",
    disabled_field="endsession_standby_on_shutdown",
    disabled_cause="disabled",
    failed_outcome="failed_fast_endsession",
    fire_and_forget_outcome="sent_unconfirmed_fire_and_forget",
)


class ControllerDeviceStandbyMixin(ControllerFastStandbyMixin):
    def _config_value(self, field_name: str):
        return getattr(self.config, field_name)

    def _standby_log_fields(self, policy: StandbyLogPolicy, generation: int | None, reason: str) -> dict[str, object]:
        return {"action": policy.action, "gen": generation, "reason": reason}

    def _log_standby(
        self,
        tag: str,
        policy: StandbyLogPolicy,
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

    def _standby_skip_session_ending(self, policy: FastStandbyPolicy | VerifiedStandbyPolicy, generation: int | None, reason: str) -> tuple[bool, str]:
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
        policy: FastStandbyPolicy | EndSessionStandbyPolicy,
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

    def _run_standby_action(
        self,
        policy: StandbyLogPolicy,
        *,
        generation: int | None,
        reason: str,
        run: Callable[[], tuple[bool, str]],
        defer_started_event: bool = False,
    ) -> bool:
        outcome = "unknown"
        start_mono = self._log_action_begin(policy.action, generation, reason)
        if defer_started_event:
            self._mark_power_action_started()
        else:
            self._emit_power_action_started(policy.action, reason)

        try:
            success, outcome = run()
            return success
        finally:
            if defer_started_event:
                self._emit_power_action_started_event(policy.action, reason)
            self._emit_power_action_finished(policy.action, reason, outcome)
            self._log_action_end(policy.action, generation, reason, outcome, start_mono)

    def _abort_bounded_standby_if_needed(
        self,
        policy: FastStandbyPolicy,
        generation: int | None,
        reason: str,
        *,
        deadline_mono: float | None,
        step: str,
        target_ip: str = "",
    ) -> str | None:
        if deadline_mono is None:
            return None

        abort_reason = self._bounded_standby_abort_reason(
            deadline_mono=deadline_mono,
            generation=generation,
        )
        if not abort_reason:
            return None

        outcome = f"aborted_bounded_{abort_reason}"
        self._log_standby(
            "ABORT",
            policy,
            generation,
            reason,
            step=step,
            cause=abort_reason,
            deadline_mono=f"{deadline_mono:.3f}",
        )
        return outcome

    def _execute_verified_standby_policy(
        self,
        policy: VerifiedStandbyPolicy,
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
        if not self._ensure_target_identity(policy.action, reason, policy.identity_step):
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
            return _outcome_is_success(outcome), outcome

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

    def _execute_end_session_policy(self, policy: EndSessionStandbyPolicy, reason: str, flags: str) -> tuple[bool, str]:
        deadline_mono = self.mono() + _ENDSESSION_FAST_STANDBY_BUDGET_S
        current_ip = self.get_current_kef_ip()
        self._log_standby(
            "STEP",
            policy,
            None,
            reason,
            log_level="info",
            step=policy.begin_step,
            status="begin",
            flags=flags,
            target_ip=current_ip or "<empty>",
            target_mac=self.get_effective_target_mac() or "<empty>",
            identity_check="cached_target_only",
            verify_standby=False,
            deadline_mono=f"{deadline_mono:.3f}",
            budget_ms=_ENDSESSION_FAST_STANDBY_BUDGET_MS,
        )
        if not current_ip:
            self._log_standby(
                "SKIP",
                policy,
                None,
                reason,
                cause="no_current_ip",
                flags=flags,
                identity_check="cached_target_only",
                verify_standby=False,
            )
            return False, "skipped_no_current_ip"

        abort_reason = self._bounded_standby_abort_reason(
            deadline_mono=deadline_mono,
            generation=None,
        )
        if abort_reason:
            outcome = f"aborted_bounded_{abort_reason}"
            self._log_standby(
                "ABORT",
                policy,
                None,
                reason,
                step="before_fast_send",
                cause=abort_reason,
                flags=flags,
                target_ip=current_ip,
                deadline_mono=f"{deadline_mono:.3f}",
                budget_ms=_ENDSESSION_FAST_STANDBY_BUDGET_MS,
            )
            return False, outcome

        extra_fields = {
            "flags": flags,
            "deadline_mono": f"{deadline_mono:.3f}",
            "budget_ms": _ENDSESSION_FAST_STANDBY_BUDGET_MS,
        }
        fast_send_outcome = self._try_fast_standby_send(
            action=policy.action,
            generation=None,
            reason=reason,
            current_ip=current_ip,
            extra_fields=extra_fields,
            success_outcome=policy.fire_and_forget_outcome,
            host_unreachable_status="host_unreachable_best_effort",
            host_unreachable_cause="endsession_network_or_speaker_unreachable",
            deadline_mono=deadline_mono,
        )
        if fast_send_outcome is not None:
            return _outcome_is_success(fast_send_outcome), fast_send_outcome

        self._log_standby(
            "WARN",
            policy,
            None,
            reason,
            step=policy.success_step,
            status="standard_fallback_disabled",
            cause="endsession_fast_send_failed",
            flags=flags,
            target_ip=current_ip,
            deadline_mono=f"{deadline_mono:.3f}",
            budget_ms=_ENDSESSION_FAST_STANDBY_BUDGET_MS,
        )
        return False, policy.failed_outcome

    def _perform_fast_shutdown(
        self,
        policy: FastStandbyPolicy,
        *,
        generation: int | None,
        reason: str,
        deadline_mono: float,
    ) -> tuple[bool, str]:
        current_ip = self.get_current_kef_ip()
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

        outcome = self._abort_bounded_standby_if_needed(
            policy,
            generation,
            reason,
            deadline_mono=deadline_mono,
            step="before_fast_send",
            target_ip=current_ip,
        )
        if outcome is not None:
            return _outcome_is_success(outcome), outcome

        fast_send_result = self._try_fast_standby_send(
            action=policy.action,
            generation=generation,
            reason=reason,
            current_ip=current_ip,
            success_outcome=policy.fire_and_forget_outcome,
            host_unreachable_outcome=policy.host_unreachable_outcome,
            host_unreachable_status=policy.host_unreachable_status,
            host_unreachable_cause=policy.host_unreachable_cause,
            deadline_mono=deadline_mono,
        )
        if fast_send_result is not None:
            return _outcome_is_success(fast_send_result), fast_send_result

        fields: dict[str, object] = {
            "cause": "fast_standby_send_failed",
            "deadline_mono": f"{deadline_mono:.3f}",
            "standard_fallback": "disabled_for_bounded_path",
        }
        self._log_standby("SKIP", policy, generation, reason, **fields)
        return False, policy.failed_outcome

    def _execute_fast_standby_policy(
        self,
        policy: FastStandbyPolicy,
        *,
        generation: int,
        reason: str,
        deadline_mono: float,
    ) -> bool:
        def run() -> tuple[bool, str]:
            skipped, outcome = self._standby_skip_disabled(policy, generation, reason)
            if skipped:
                return False, outcome
            skipped, outcome = self._standby_skip_session_ending(policy, generation, reason)
            if skipped:
                return False, outcome
            return self._perform_fast_shutdown(
                policy,
                generation=generation,
                reason=reason,
                deadline_mono=deadline_mono,
            )

        return self._run_standby_action(
            policy,
            generation=generation,
            reason=reason,
            run=run,
            defer_started_event=True,
        )

    def standby_kef_preemptive(
        self,
        generation: int,
        reason: str,
        *,
        deadline_mono: float,
    ) -> bool:
        return self._execute_fast_standby_policy(
            PREEMPTIVE_STANDBY_POLICY,
            generation=generation,
            reason=reason,
            deadline_mono=deadline_mono,
        )

    def standby_kef_end_session(self, reason: str, flags: str) -> bool:
        def run() -> tuple[bool, str]:
            skipped, outcome = self._standby_skip_disabled(
                ENDSESSION_STANDBY_POLICY,
                None,
                reason,
                extra_fields={"flags": flags},
            )
            if skipped:
                return False, outcome
            return self._execute_end_session_policy(ENDSESSION_STANDBY_POLICY, reason, flags)

        return self._run_standby_action(
            ENDSESSION_STANDBY_POLICY,
            generation=None,
            reason=reason,
            run=run,
        )

    def standby_kef_fast_suspend(
        self,
        generation: int,
        reason: str,
        *,
        deadline_mono: float,
    ) -> bool:
        return self._execute_fast_standby_policy(
            FAST_SUSPEND_STANDBY_POLICY,
            generation=generation,
            reason=reason,
            deadline_mono=deadline_mono,
        )

    def standby_kef(self, generation: int, reason: str) -> bool:
        return self._run_standby_action(
            STANDARD_STANDBY_POLICY,
            generation=generation,
            reason=reason,
            run=lambda: self._execute_verified_standby_policy(STANDARD_STANDBY_POLICY, generation, reason),
        )
