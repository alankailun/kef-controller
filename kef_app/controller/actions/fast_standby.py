from __future__ import annotations

from collections.abc import Callable

from ..fast_standby_sender import FastStandbySender, FastStandbySendResult
from ...devices.transport import FireAndForgetShutdownResult, fire_and_forget_standby
from ...platform.windows import temporary_system_required_request


_FAST_STANDBY_FIRE_AND_FORGET_ATTEMPTS = 3
_FAST_STANDBY_FIRE_AND_FORGET_SOCKET_TIMEOUT = 0.18
_FAST_STANDBY_FIRE_AND_FORGET_JOIN_TIMEOUT = 0.25


def _outcome_is_success(outcome: str) -> bool:
    return outcome.startswith("success") or outcome == "skipped_recent_early_standby_ok"


class ControllerFastStandbyMixin:
    def _send_fire_and_forget_shutdown(self, current_ip: str) -> FireAndForgetShutdownResult:
        return fire_and_forget_standby(
            current_ip,
            port=self.config.mac_discovery_tcp_port,
            attempts=_FAST_STANDBY_FIRE_AND_FORGET_ATTEMPTS,
            socket_timeout=_FAST_STANDBY_FIRE_AND_FORGET_SOCKET_TIMEOUT,
            join_timeout=_FAST_STANDBY_FIRE_AND_FORGET_JOIN_TIMEOUT,
        )

    def _fire_and_forget_sender(
        self,
        *,
        hold_fire_and_forget: bool,
        action: str,
        generation: int | None,
        reason: str,
    ) -> Callable[[str], FireAndForgetShutdownResult]:
        if not hold_fire_and_forget:
            return self._send_fire_and_forget_shutdown
        return lambda current_ip: self._send_fire_and_forget_with_system_required_hold(
            current_ip,
            action=action,
            generation=generation,
            reason=reason,
        )

    def _send_fire_and_forget_with_system_required_hold(
        self,
        current_ip: str,
        *,
        action: str,
        generation: int | None,
        reason: str,
    ) -> FireAndForgetShutdownResult:
        with temporary_system_required_request("KEF Controller early standby") as hold:
            fields: dict[str, object] = {
                "action": action,
                "gen": generation,
                "reason": reason,
                "step": "system_required_hold",
                "status": "active" if hold.active else "unavailable",
                "api": "PowerCreateRequest",
                "scope": "fire_and_forget_shutdown",
                "mono": f"{self.mono():.3f}",
            }
            if hold.error:
                fields["error"] = hold.error
            self._log_structured(
                "STEP" if hold.active else "WARN",
                log_level="info",
                **fields,
            )
            return self._send_fire_and_forget_shutdown(current_ip)

    def _send_fast_standby(
        self,
        current_ip: str,
        *,
        hold_fire_and_forget: bool = False,
        action: str = "",
        generation: int | None = None,
        reason: str = "",
    ) -> FastStandbySendResult:
        return FastStandbySender(
            self.try_send_prewarmed_standby,
            self._fire_and_forget_sender(
                hold_fire_and_forget=hold_fire_and_forget,
                action=action,
                generation=generation,
                reason=reason,
            ),
        ).send(current_ip)

    def _log_prewarmed_fast_send(
        self,
        fast_result: FastStandbySendResult,
        *,
        action: str,
        generation: int | None,
        reason: str,
        current_ip: str,
        host_unreachable_cause: str,
        extra_fields: dict[str, object] | None,
    ) -> None:
        prewarmed_result = fast_result.prewarmed
        if prewarmed_result is None:
            return

        fields = {
            "action": action,
            "gen": generation,
            "reason": reason,
            "step": "prewarmed_standby_send",
            "status": prewarmed_result.status,
            "target_ip": current_ip,
            "duration_ms": prewarmed_result.duration_ms,
            "mode": prewarmed_result.mode,
            "deadline_s": f"{self.config.prewarmed_send_deadline_s:.2f}",
            "bypass_action_lock": True,
            "read_response": False,
            "mono": f"{self.mono():.3f}",
        }
        if extra_fields:
            fields.update(extra_fields)
        if prewarmed_result.error:
            fields["error"] = prewarmed_result.error
        if prewarmed_result.so_error is not None:
            fields["so_error"] = prewarmed_result.so_error
        if prewarmed_result.frozen_s:
            fields["cause"] = "prewarmed_send_deadline_exceeded"
            fields["frozen_s"] = prewarmed_result.frozen_s
        if prewarmed_result.host_unreachable:
            fields["cause"] = host_unreachable_cause
            fields["host_unreachable"] = True

        self._log_structured(
            "STEP" if prewarmed_result.success else "WARN",
            log_level="info",
            **fields,
        )

    def _log_fire_and_forget_fast_send(
        self,
        fast_result: FastStandbySendResult,
        *,
        action: str,
        generation: int | None,
        reason: str,
        current_ip: str,
        host_unreachable_outcome: str,
        host_unreachable_status: str,
        host_unreachable_cause: str,
        extra_fields: dict[str, object] | None,
    ) -> None:
        result = fast_result.fire_and_forget
        if result is None:
            return

        if fast_result.success:
            status = "sent"
            outcome = "success"
        elif fast_result.host_unreachable:
            status = host_unreachable_status
            outcome = host_unreachable_outcome
        else:
            status = "failed_fallback_standard"
            outcome = ""

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

        log_tag = "WARN" if outcome and not _outcome_is_success(outcome) else "STEP"
        self._log_structured(log_tag, log_level="info", **fields)

    def _try_fast_standby_send(
        self,
        *,
        action: str,
        generation: int | None,
        reason: str,
        current_ip: str,
        mark_early_standby_success: bool,
        extra_fields: dict[str, object] | None = None,
        success_outcome: str = "success_fire_and_forget",
        host_unreachable_outcome: str = "success_assumed_host_unreachable",
        host_unreachable_status: str = "host_unreachable_assumed_standby",
        host_unreachable_cause: str = "fire_and_forget_host_unreachable",
        hold_fire_and_forget: bool = False,
    ) -> str | None:
        fast_result = self._send_fast_standby(
            current_ip,
            hold_fire_and_forget=hold_fire_and_forget,
            action=action,
            generation=generation,
            reason=reason,
        )
        self._log_prewarmed_fast_send(
            fast_result,
            action=action,
            generation=generation,
            reason=reason,
            current_ip=current_ip,
            host_unreachable_cause=host_unreachable_cause,
            extra_fields=extra_fields,
        )
        self._log_fire_and_forget_fast_send(
            fast_result,
            action=action,
            generation=generation,
            reason=reason,
            current_ip=current_ip,
            host_unreachable_outcome=host_unreachable_outcome,
            host_unreachable_status=host_unreachable_status,
            host_unreachable_cause=host_unreachable_cause,
            extra_fields=extra_fields,
        )

        if fast_result.success and mark_early_standby_success:
            self._mark_early_standby_success()
        if fast_result.success:
            return "success_prewarmed_send" if fast_result.source == "prewarmed" else success_outcome
        if fast_result.host_unreachable:
            if mark_early_standby_success:
                self._mark_early_standby_host_unreachable()
                if action == "EARLY_STANDBY":
                    self.log_wifi_diagnostics(
                        reason=reason,
                        trigger="early_standby_host_unreachable",
                        fresh=True,
                        timeout=0.15,
                    )
            return host_unreachable_outcome
        return None
