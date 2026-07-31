from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .device_common import _STANDBY_VERIFY_TIMEOUT, StandbyVerificationError
from .fast_standby import ControllerFastStandbyMixin
from ..power_state import power_action_outcome_is_success


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

_STANDARD_STANDBY_ACTION = "STANDBY"
_ENDSESSION_STANDBY_ACTION = "ENDSESSION_STANDBY"


class ControllerDeviceStandbyMixin(ControllerFastStandbyMixin):
    def _log_standby(
        self,
        tag: str,
        action: str | FastStandbyPolicy,
        generation: int | None,
        reason: str,
        *,
        log_level: object = None,
        **fields: object,
    ) -> None:
        action_name = action.action if isinstance(action, FastStandbyPolicy) else action
        self._action_log(action_name, generation, reason).write(tag, log_level=log_level, **fields)

    def _standby_skip_session_ending(self, action: str | FastStandbyPolicy, generation: int | None, reason: str) -> tuple[bool, str]:
        if not self._is_session_ending():
            return False, ""
        outcome = "skipped_session_ending"
        self._log_standby(
            "SKIP",
            action,
            generation,
            reason,
            cause="session_ending",
        )
        return True, outcome

    def _standby_skip_disabled(
        self,
        action: str | FastStandbyPolicy,
        generation: int | None,
        reason: str,
        *,
        extra_fields: dict[str, object] | None = None,
    ) -> tuple[bool, str]:
        field_name = action.disabled_field if isinstance(action, FastStandbyPolicy) else "endsession_standby_on_shutdown"
        disabled_cause = action.disabled_cause if isinstance(action, FastStandbyPolicy) else "disabled"
        if not field_name or bool(getattr(self.config, field_name)):
            return False, ""
        outcome = "disabled"
        self._log_standby(
            "SKIP",
            action,
            generation,
            reason,
            cause=disabled_cause,
            **(extra_fields or {}),
        )
        return True, outcome

    def _run_standby_action(
        self,
        action: str,
        *,
        generation: int | None,
        reason: str,
        run: Callable[[], tuple[bool, str]],
        defer_started_event: bool = False,
    ) -> bool:
        outcome = "unknown"
        start_mono = self._log_action_begin(action, generation, reason)
        if defer_started_event:
            self._mark_power_action_started()
        else:
            self._emit_power_action_started(action, reason)

        try:
            success, outcome = run()
            return success
        finally:
            if defer_started_event:
                self._emit_power_action_started_event(action, reason)
            self._emit_power_action_finished(action, reason, outcome)
            self._log_action_end(action, generation, reason, outcome, start_mono)

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

    def _execute_verified_standby(self, generation: int | None, reason: str) -> tuple[bool, str]:
        self._log_standby(
            "STEP",
            _STANDARD_STANDBY_ACTION,
            generation,
            reason,
            step="shutdown_request",
        )

        skipped, outcome = self._standby_skip_session_ending(_STANDARD_STANDBY_ACTION, generation, reason)
        if skipped:
            return False, outcome
        if not self._ensure_target_identity(_STANDARD_STANDBY_ACTION, reason, "standby_before_request"):
            return False, "skipped_target_identity_not_verified"

        if generation is None:
            return False, "skipped_missing_generation"

        outcome = self._acquire_generation_action_lock(
            action=_STANDARD_STANDBY_ACTION,
            generation=generation,
            reason=reason,
            lock_timeout=float(self.config.suspend_action_lock_timeout),
            purpose="standby",
        )
        if outcome is not None:
            return power_action_outcome_is_success(outcome), outcome

        try:
            try:
                self._perform_standby_request(
                    action=_STANDARD_STANDBY_ACTION,
                    generation=generation,
                    reason=reason,
                    attempt=1,
                    fresh=False,
                    verify_timeout=_STANDBY_VERIFY_TIMEOUT,
                )
                return True, "success_attempt_1"
            except Exception as exc:
                self.reset_speaker()
                self._log_standby(
                    "WARN",
                    _STANDARD_STANDBY_ACTION,
                    generation,
                    reason,
                    attempt=1,
                    cause=("standby_not_verified" if isinstance(exc, StandbyVerificationError) else "shutdown_failed"),
                    status="no_retry_before_suspend",
                    error=repr(exc),
                )
                return False, "failed_no_retry_before_suspend"
        finally:
            self._action_lock.release()

    def _execute_end_session(self, reason: str, flags: str) -> tuple[bool, str]:
        deadline_mono = self.mono() + _ENDSESSION_FAST_STANDBY_BUDGET_S
        current_ip = self.get_current_kef_ip()
        self._log_standby(
            "STEP",
            _ENDSESSION_STANDBY_ACTION,
            None,
            reason,
            log_level="info",
            step="shutdown_request",
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
                _ENDSESSION_STANDBY_ACTION,
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
                _ENDSESSION_STANDBY_ACTION,
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
            action=_ENDSESSION_STANDBY_ACTION,
            generation=None,
            reason=reason,
            current_ip=current_ip,
            extra_fields=extra_fields,
            success_outcome="sent_unconfirmed_fire_and_forget",
            host_unreachable_status="host_unreachable_best_effort",
            host_unreachable_cause="endsession_network_or_speaker_unreachable",
            deadline_mono=deadline_mono,
        )
        if fast_send_outcome is not None:
            return power_action_outcome_is_success(fast_send_outcome), fast_send_outcome

        self._log_standby(
            "WARN",
            _ENDSESSION_STANDBY_ACTION,
            None,
            reason,
            step="shutdown_request",
            status="standard_fallback_disabled",
            cause="endsession_fast_send_failed",
            flags=flags,
            target_ip=current_ip,
            deadline_mono=f"{deadline_mono:.3f}",
            budget_ms=_ENDSESSION_FAST_STANDBY_BUDGET_MS,
        )
        return False, "failed_fast_endsession"

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
            return power_action_outcome_is_success(outcome), outcome

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
            return power_action_outcome_is_success(fast_send_result), fast_send_result

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
            policy.action,
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
                _ENDSESSION_STANDBY_ACTION,
                None,
                reason,
                extra_fields={"flags": flags},
            )
            if skipped:
                return False, outcome
            return self._execute_end_session(reason, flags)

        return self._run_standby_action(
            _ENDSESSION_STANDBY_ACTION,
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
            _STANDARD_STANDBY_ACTION,
            generation=generation,
            reason=reason,
            run=lambda: self._execute_verified_standby(generation, reason),
        )
