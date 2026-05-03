from __future__ import annotations

from .device_common import _STANDBY_VERIFY_TIMEOUT, StandbyVerificationError


class ControllerDeviceStandbyMixin:
    def _recently_lock_standby_ok(self) -> bool:
        with self._state_lock:
            last = self._last_lock_standby_ok_mono
            if last <= 0:
                return False
            return (self.mono() - last) < self.config.lock_standby_dedup_window

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

            current_ip = self.get_current_kef_ip()
            self._log_structured(
                "STEP",
                action="LOCK_PRE_STANDBY",
                gen=generation,
                reason=reason,
                step="lock_fast_path",
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
                    action="LOCK_PRE_STANDBY",
                    gen=generation,
                    reason=reason,
                    cause="no_current_ip",
                    mono=f"{self.mono():.3f}",
                )
                return False

            outcome = self._acquire_generation_action_lock(
                action="LOCK_PRE_STANDBY",
                generation=generation,
                reason=reason,
                lock_timeout=c.lock_standby_action_lock_timeout,
                purpose="lock_pre_standby",
            )
            if outcome is not None:
                return False

            try:
                self._request_shutdown(fresh=False, timeout=c.suspend_fast_standby_socket_timeout)
                self._mark_lock_prestandby_success()
                outcome = "success"
                self._log_structured(
                    "STEP",
                    action="LOCK_PRE_STANDBY",
                    gen=generation,
                    reason=reason,
                    step="shutdown_request",
                    status="sent",
                    target_ip=current_ip,
                    timeout_s=f"{c.suspend_fast_standby_socket_timeout:.2f}",
                    mono=f"{self.mono():.3f}",
                )
                return True
            except Exception as exc:
                self.reset_speaker()
                outcome = "failed"
                self._log_structured(
                    "WARN",
                    action="LOCK_PRE_STANDBY",
                    gen=generation,
                    reason=reason,
                    cause="shutdown_failed",
                    error=repr(exc),
                    target_ip=current_ip,
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

            current_ip = self.get_current_kef_ip()
            self._log_structured(
                "STEP",
                action="STANDBY",
                gen=generation,
                reason=reason,
                step="suspend_fast_path",
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
                    action="STANDBY",
                    gen=generation,
                    reason=reason,
                    cause="no_current_ip",
                    mono=f"{self.mono():.3f}",
                )
                return False

            outcome = self._acquire_generation_action_lock(
                action="STANDBY",
                generation=generation,
                reason=reason,
                lock_timeout=c.suspend_fast_standby_action_lock_timeout,
                purpose="standby",
            )
            if outcome is not None:
                return False

            try:
                self._request_shutdown(fresh=False, timeout=c.suspend_fast_standby_socket_timeout)
                outcome = "success_fast_suspend"
                self._log_structured(
                    "STEP",
                    action="STANDBY",
                    gen=generation,
                    reason=reason,
                    step="suspend_fast_path",
                    status="sent",
                    target_ip=current_ip,
                    timeout_s=f"{c.suspend_fast_standby_socket_timeout:.2f}",
                    mono=f"{self.mono():.3f}",
                )
                return True
            except Exception as exc:
                self.reset_speaker()
                outcome = "failed_fast_suspend"
                self._log_structured(
                    "WARN",
                    action="STANDBY",
                    gen=generation,
                    reason=reason,
                    attempt=1,
                    cause="shutdown_failed",
                    status="fast_suspend_failed",
                    error=repr(exc),
                    target_ip=current_ip,
                    mono=f"{self.mono():.3f}",
                )
                return False
            finally:
                self._action_lock.release()
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
