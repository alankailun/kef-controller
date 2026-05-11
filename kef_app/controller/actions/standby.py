from __future__ import annotations

from .device_common import _STANDBY_VERIFY_TIMEOUT, StandbyVerificationError, _is_host_unreachable
from .raw_shutdown import FireAndForgetShutdownResult, fire_and_forget_standby


_FAST_STANDBY_FIRE_AND_FORGET_ATTEMPTS = 3
_FAST_STANDBY_FIRE_AND_FORGET_SOCKET_TIMEOUT = 0.18
_FAST_STANDBY_FIRE_AND_FORGET_JOIN_TIMEOUT = 0.25


class ControllerDeviceStandbyMixin:
    def _recently_lock_standby_ok(self) -> bool:
        with self._state_lock:
            last = self._last_lock_standby_ok_mono
            if last <= 0:
                return False
            return (self.mono() - last) < self.config.lock_standby_dedup_window

    def _perform_fast_shutdown(
        self,
        *,
        action: str,
        generation: int,
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

        if fire_and_forget and self._try_fire_and_forget_shutdown(
            action=action,
            generation=generation,
            reason=reason,
            current_ip=current_ip,
            mark_lock_success=mark_lock_success,
        ):
            return True, fire_and_forget_outcome or success_outcome

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
            self._request_shutdown(fresh=False, timeout=socket_timeout)
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
                mono=f"{self.mono():.3f}",
            )
            return True, outcome
        except Exception as exc:
            self.reset_speaker()
            if _is_host_unreachable(exc):
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
    ) -> bool:
        result = self._send_fire_and_forget_shutdown(current_ip)
        status = "sent" if result.success else "failed_fallback_standard"
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
        if extra_fields:
            fields.update(extra_fields)
        if result.errors:
            fields["errors"] = "; ".join(result.errors)

        self._log_structured("STEP", log_level="info", **fields)
        if result.success and mark_lock_success:
            self._mark_lock_prestandby_success()
        return result.success

    def standby_kef_preemptive(self, generation: int, reason: str) -> bool:
        outcome = "unknown"
        start_mono = self._log_action_begin("LOCK_PRE_STANDBY", generation, reason)
        c = self.config
        self._emit_power_action_started("LOCK_PRE_STANDBY", reason)

        try:
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

            success, outcome = self._perform_fast_shutdown(
                action="LOCK_PRE_STANDBY",
                generation=generation,
                reason=reason,
                begin_step="lock_fast_path",
                success_step="shutdown_request",
                lock_timeout=c.lock_standby_action_lock_timeout,
                lock_purpose="lock_pre_standby",
                socket_timeout=c.suspend_fast_standby_socket_timeout,
                success_outcome="success",
                host_unreachable_outcome="success_assumed_host_unreachable",
                host_unreachable_status="host_unreachable_assumed_standby",
                host_unreachable_cause="host_unreachable_assume_standby",
                failed_outcome="failed",
                mark_lock_success=True,
                fire_and_forget=True,
                fire_and_forget_outcome="success_fire_and_forget",
            )
            return success
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

            if self._try_fire_and_forget_shutdown(
                action="ENDSESSION_STANDBY",
                generation=None,
                reason=reason,
                current_ip=current_ip,
                mark_lock_success=False,
                extra_fields={"flags": flags},
            ):
                outcome = "success_fire_and_forget"
                return True

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

    def standby_kef_fast_suspend(self, generation: int, reason: str) -> bool:
        outcome = "unknown"
        start_mono = self._log_action_begin("STANDBY", generation, reason)
        c = self.config
        self._emit_power_action_started("STANDBY", reason)

        try:
            if not c.suspend_fast_standby_enabled:
                outcome = "disabled"
                self._log_structured(
                    "SKIP",
                    action="STANDBY",
                    gen=generation,
                    reason=reason,
                    cause="suspend_fast_standby_disabled",
                    mono=f"{self.mono():.3f}",
                )
                return False

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

            success, outcome = self._perform_fast_shutdown(
                action="STANDBY",
                generation=generation,
                reason=reason,
                begin_step="suspend_fast_path",
                success_step="suspend_fast_path",
                lock_timeout=c.suspend_fast_standby_action_lock_timeout,
                lock_purpose="standby",
                socket_timeout=c.suspend_fast_standby_socket_timeout,
                success_outcome="success_fast_suspend",
                host_unreachable_outcome="success_best_effort_host_unreachable",
                host_unreachable_status="host_unreachable_best_effort",
                host_unreachable_cause="suspend_network_or_speaker_unreachable",
                failed_outcome="failed_fast_suspend",
                failed_status="fast_suspend_failed",
                attempt=1,
                fire_and_forget=True,
                fire_and_forget_outcome="success_fast_suspend_fire_and_forget",
            )
            return success
        finally:
            self._emit_power_action_finished("STANDBY", reason, outcome)
            self._log_action_end("STANDBY", generation, reason, outcome, start_mono)

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
