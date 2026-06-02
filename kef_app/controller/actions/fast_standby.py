from __future__ import annotations

from typing import Callable

from ..fast_standby_sender import FastStandbySender, FastStandbySendResult
from ...devices.transport import FireAndForgetShutdownResult, fire_and_forget_standby


_FAST_STANDBY_FIRE_AND_FORGET_ATTEMPTS = 3
_FAST_STANDBY_FIRE_AND_FORGET_SOCKET_TIMEOUT = 0.18
_FAST_STANDBY_FIRE_AND_FORGET_JOIN_TIMEOUT = 0.25


def _outcome_is_success(outcome: str) -> bool:
    return outcome.startswith("success") or outcome.startswith("sent_")


class ControllerFastStandbyMixin:
    def _send_fire_and_forget_shutdown(
        self,
        current_ip: str,
        *,
        deadline_mono: float | None = None,
        should_send: Callable[[], bool] | None = None,
    ) -> FireAndForgetShutdownResult:
        return fire_and_forget_standby(
            current_ip,
            port=self.config.mac_discovery_tcp_port,
            attempts=_FAST_STANDBY_FIRE_AND_FORGET_ATTEMPTS,
            socket_timeout=_FAST_STANDBY_FIRE_AND_FORGET_SOCKET_TIMEOUT,
            join_timeout=_FAST_STANDBY_FIRE_AND_FORGET_JOIN_TIMEOUT,
            deadline_mono=deadline_mono,
            should_send=should_send,
        )

    def _send_fast_standby(
        self,
        current_ip: str,
        *,
        deadline_mono: float | None = None,
        generation: int | None = None,
    ) -> FastStandbySendResult:
        if deadline_mono is None and generation is None:
            return FastStandbySender(
                self.try_send_prewarmed_standby,
                self._send_fire_and_forget_shutdown,
            ).send(current_ip)

        def should_send() -> bool:
            return not self._bounded_standby_abort_reason(
                deadline_mono=deadline_mono,
                generation=generation,
                target_ip=current_ip,
            )

        return FastStandbySender(
            lambda ip: self.try_send_prewarmed_standby(
                ip,
                deadline_mono=deadline_mono,
                generation=generation,
            ),
            lambda ip: self._send_fire_and_forget_shutdown(
                ip,
                deadline_mono=deadline_mono,
                should_send=should_send,
            ),
            should_continue=should_send,
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
        if result.abort_reason:
            fields["cause"] = f"bounded_send_{result.abort_reason}"

        log_tag = "WARN" if outcome and not _outcome_is_success(outcome) else "STEP"
        self._log_structured(log_tag, log_level="info", **fields)

    def _try_fast_standby_send(
        self,
        *,
        action: str,
        generation: int | None,
        reason: str,
        current_ip: str,
        mark_early_standby_sent_unconfirmed: bool,
        extra_fields: dict[str, object] | None = None,
        success_outcome: str = "sent_unconfirmed_fire_and_forget",
        host_unreachable_outcome: str = "sent_skipped_host_unreachable",
        host_unreachable_status: str = "host_unreachable_assumed_standby",
        host_unreachable_cause: str = "fire_and_forget_host_unreachable",
        deadline_mono: float | None = None,
    ) -> str | None:
        fast_result = self._send_fast_standby(
            current_ip,
            deadline_mono=deadline_mono,
            generation=generation if deadline_mono is not None else None,
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

        if fast_result.success and mark_early_standby_sent_unconfirmed:
            self._mark_early_standby_sent_unconfirmed()
        if fast_result.success:
            return "sent_unconfirmed_prewarmed" if fast_result.source == "prewarmed" else success_outcome
        if fast_result.host_unreachable:
            if action == "EARLY_STANDBY":
                self.log_wifi_diagnostics(
                    reason=reason,
                    trigger="early_standby_host_unreachable",
                    fresh=True,
                    timeout=0.15,
                )
            return host_unreachable_outcome
        return None
