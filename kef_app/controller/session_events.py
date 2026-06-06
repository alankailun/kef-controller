from __future__ import annotations

import threading
import traceback

from .triggers import get_trigger


_EARLY_STANDBY_EVENT_BUDGET_S = 0.30
_DISPLAY_OFF_STANDBY_EVENT_BUDGET_S = 1.50
_SUSPEND_STANDBY_EVENT_BUDGET_S = 0.30
_PUMP_CALLBACK_SLOW_THRESHOLD_S = 0.020


class ControllerSessionEventsMixin:
    @staticmethod
    def _early_standby_event_matches_reason(event_name: str, reason: str) -> bool:
        if event_name == reason:
            return True
        if event_name == "GUID_LIDSWITCH_STATE_CHANGE" and reason == "POWER_LID_CLOSED":
            return True
        return event_name == "GUID_CONSOLE_DISPLAY_STATE" and reason == "DISPLAY_OFF"

    def _start_controller_thread(self, target, thread_name: str):
        def guarded():
            try:
                target()
            except Exception:
                self.log.error("%s hit an unhandled exception:\n%s", thread_name, traceback.format_exc())

        threading.Thread(target=guarded, daemon=True, name=thread_name).start()

    def _schedule_delayed_wake(self, generation: int, reason: str, delay: float, step_label: str, thread_name: str):
        def worker():
            self._log_structured(
                "STEP",
                action="WAKE",
                gen=generation,
                reason=reason,
                step=step_label,
                delay_s=f"{delay:.2f}",
                mono=f"{self.mono():.3f}",
            )
            if not self._interruptible_sleep(delay, generation, step_label):
                return
            self.wake_kef(generation, reason)

        self._start_controller_thread(worker, f"{thread_name}-{generation}")

    def on_startup(self):
        if not self.config.wake_on_startup:
            self._log_structured(
                "SKIP",
                action="WAKE",
                reason="startup",
                cause="startup_wake_disabled",
                mono=f"{self.mono():.3f}",
            )
            return

        generation = self._new_generation("wake", "startup")
        self._log_structured(
            "STEP",
            action="WAKE",
            reason="startup",
            step="startup_delay",
            delay_s=f"{self.config.startup_delay:.2f}",
            mono=f"{self.mono():.3f}",
        )
        if not self._interruptible_sleep(self.config.startup_delay, generation, "startup_delay"):
            return
        self.wake_kef(generation, "startup")

    def on_suspend(self, reason: str):
        return get_trigger("suspend").fire(self, reason)

    def on_lock(self, reason: str):
        return get_trigger("lock").fire(self, reason)

    def dispatch_off_pump_standby(
        self,
        trigger_name: str,
        reason: str,
        event_mono: float,
        *,
        callback_started_mono: float | None = None,
        state_recorded_mono: float | None = None,
        step: str = "dispatch_off_pump_standby",
    ) -> bool:
        dispatch_started_mono = self.mono()
        scheduled = self.schedule_off_pump_standby(trigger_name, reason, event_mono)
        finished_mono = self.mono()
        callback_started_mono = event_mono if callback_started_mono is None else callback_started_mono
        callback_duration_s = max(0.0, finished_mono - callback_started_mono)
        callback_duration_ms = int(callback_duration_s * 1000)

        fields: dict[str, object] = {
            "action": "WINDOW_MESSAGE",
            "reason": reason,
            "step": step,
            "trigger": trigger_name,
            "scheduled": scheduled,
            "dispatch_duration_ms": int(max(0.0, finished_mono - dispatch_started_mono) * 1000),
            "callback_duration_ms": callback_duration_ms,
            "mono": f"{finished_mono:.3f}",
        }
        if state_recorded_mono is not None:
            fields["state_recorded_ms"] = int(max(0.0, state_recorded_mono - callback_started_mono) * 1000)
            fields["schedule_duration_ms"] = int(max(0.0, finished_mono - state_recorded_mono) * 1000)

        self._log_structured("STEP", log_level="info", **fields)
        if callback_duration_s > _PUMP_CALLBACK_SLOW_THRESHOLD_S:
            self._log_structured(
                "WARN",
                log_level="info",
                action="WINDOW_MESSAGE",
                reason=reason,
                step="pump_callback_slow",
                trigger=trigger_name,
                callback_duration_ms=callback_duration_ms,
                threshold_ms=int(_PUMP_CALLBACK_SLOW_THRESHOLD_S * 1000),
                mono=f"{finished_mono:.3f}",
            )
        return scheduled

    def schedule_off_pump_standby(self, trigger_name: str, reason: str, event_mono: float) -> bool:
        if trigger_name == "suspend":
            return self.schedule_suspend_standby(reason, event_mono)
        if trigger_name in {"lock", "lid_closed", "display_off"}:
            return self.schedule_early_standby(trigger_name, reason, event_mono)
        raise ValueError(f"Unknown off-pump standby trigger: {trigger_name}")

    def schedule_suspend_standby(self, reason: str, event_mono: float) -> bool:
        generation = self._new_generation("sleep", reason, mono=f"{event_mono:.3f}")
        if not self.config.standby_on_sleep:
            self._log_structured(
                "SKIP",
                action="STANDBY",
                gen=generation,
                reason=reason,
                cause="sleep_standby_disabled",
                mono=f"{self.mono():.3f}",
            )
            return False

        deadline_mono = event_mono + _SUSPEND_STANDBY_EVENT_BUDGET_S
        fast_path_enabled = bool(self.config.suspend_fast_standby_enabled)
        self._log_structured(
            "STEP",
            log_level="info",
            action="STANDBY",
            gen=generation,
            reason=reason,
            step="schedule_suspend_worker",
            event_mono=f"{event_mono:.3f}",
            deadline_mono=f"{deadline_mono:.3f}",
            budget_ms=int(_SUSPEND_STANDBY_EVENT_BUDGET_S * 1000),
            mode="fast_request" if fast_path_enabled else "verified_request",
            mono=f"{self.mono():.3f}",
        )

        def worker() -> None:
            abort_reason = self._bounded_standby_abort_reason(
                deadline_mono=deadline_mono,
                generation=generation,
            )
            if abort_reason:
                self._log_structured(
                    "ABORT",
                    action="STANDBY",
                    gen=generation,
                    reason=reason,
                    step="before_suspend_worker_send",
                    cause=abort_reason,
                    mono=f"{self.mono():.3f}",
                )
                return
            if fast_path_enabled:
                self.standby_kef_fast_suspend(generation, reason, deadline_mono=deadline_mono)
                return
            self.standby_kef(generation, reason)

        self._start_controller_thread(worker, f"SuspendStandby-{generation}")
        return True

    @staticmethod
    def _early_standby_event_budget_s(trigger_name: str) -> float:
        if trigger_name == "display_off":
            return _DISPLAY_OFF_STANDBY_EVENT_BUDGET_S
        return _EARLY_STANDBY_EVENT_BUDGET_S

    def schedule_early_standby(self, trigger_name: str, reason: str, event_mono: float) -> bool:
        trigger = get_trigger(trigger_name)
        if not bool(getattr(self.config, trigger.enabled_field)):
            self._log_structured(
                "SKIP",
                action=trigger.action_name,
                reason=reason,
                cause=trigger.disabled_cause,
                mono=f"{self.mono():.3f}",
            )
            return False
        if self._is_session_ending():
            self._log_structured(
                "SKIP",
                action=trigger.action_name,
                reason=reason,
                cause="session_ending",
                mono=f"{self.mono():.3f}",
            )
            return False

        generation = self._new_generation("sleep", reason, mono=f"{event_mono:.3f}")
        budget_s = self._early_standby_event_budget_s(trigger_name)
        deadline_mono = event_mono + budget_s
        self._log_structured(
            "STEP",
            log_level="info",
            action=trigger.action_name,
            gen=generation,
            reason=reason,
            step="schedule_bounded_worker",
            trigger=trigger_name,
            event_mono=f"{event_mono:.3f}",
            deadline_mono=f"{deadline_mono:.3f}",
            budget_ms=int(budget_s * 1000),
            mono=f"{self.mono():.3f}",
        )

        def worker():
            if trigger_name == "lock" and self.try_handle_cached_lock_fast_path(
                reason,
                event_mono,
                generation=generation,
                deadline_mono=deadline_mono,
            ):
                return
            if trigger_name == "display_off":
                abort_reason = self._bounded_standby_abort_reason(
                    deadline_mono=deadline_mono,
                    generation=generation,
                )
                if abort_reason:
                    self._log_structured(
                        "ABORT",
                        action=trigger.action_name,
                        gen=generation,
                        reason=reason,
                        step="before_display_off_play_state_probe",
                        cause=abort_reason,
                        deadline_mono=f"{deadline_mono:.3f}",
                        mono=f"{self.mono():.3f}",
                    )
                    return

                playing = self.speaker_is_probably_playing()
                if playing is not True:
                    remaining_s = deadline_mono - self.mono()
                    if remaining_s <= 0.05:
                        self._log_structured(
                            "ABORT",
                            action=trigger.action_name,
                            gen=generation,
                            reason=reason,
                            step="before_display_off_play_state_probe",
                            cause="deadline_exceeded",
                            deadline_mono=f"{deadline_mono:.3f}",
                            mono=f"{self.mono():.3f}",
                        )
                        return
                    playing = self.read_speaker_playing_live(
                        reason,
                        trigger_name,
                        timeout=min(float(self.config.socket_timeout), remaining_s),
                    )
                if playing is not False:
                    self._log_structured(
                        "ABORT",
                        action=trigger.action_name,
                        gen=generation,
                        reason=reason,
                        step="before_display_off_worker_send",
                        cause="speaker_playing" if playing else "play_state_unknown",
                        mono=f"{self.mono():.3f}",
                    )
                    return
            self._run_early_standby_trigger(
                trigger,
                reason,
                generation=generation,
                event_mono=event_mono,
                deadline_mono=deadline_mono,
            )

        self._start_controller_thread(worker, f"EarlyStandby-{trigger_name}-{generation}")
        return True

    def try_handle_cached_lock_fast_path(
        self,
        reason: str,
        event_mono: float,
        *,
        generation: int | None = None,
        deadline_mono: float | None = None,
    ) -> bool:
        if not self.config.standby_on_lock:
            self._log_cached_lock_fast_path_skip(reason, event_mono, "lock_standby_disabled")
            return False
        if self._session_ending:
            self._log_cached_lock_fast_path_skip(reason, event_mono, "session_ending")
            return False

        if deadline_mono is None and generation is None:
            result = self.try_send_cached_prewarmed_standby()
        else:
            result = self.try_send_cached_prewarmed_standby(
                deadline_mono=deadline_mono,
                generation=generation,
            )
        if not result.success:
            self._log_cached_lock_fast_path_result(reason, event_mono, result)
            return False

        if generation is None:
            # Direct callers do not pre-record the Windows event or generation.
            self._record_session_event_state(reason, event_mono)
            generation = self._new_generation("sleep", reason, mono=f"{event_mono:.3f}")
        self._mark_early_standby_sent_unconfirmed()
        self._log_cached_lock_fast_path_success(reason, event_mono, generation, result)
        return True

    def _log_cached_lock_fast_path_skip(self, reason: str, event_mono: float, skip_reason: str) -> None:
        now = self.mono()
        self._log_structured(
            "STEP",
            log_level="info",
            action="EARLY_STANDBY",
            reason=reason,
            step="cached_lock_fast_path",
            fast_path_used=False,
            fast_path_skip_reason=skip_reason,
            since_event_ms=int(max(0.0, now - event_mono) * 1000),
            mono=f"{now:.3f}",
        )

    def _log_cached_lock_fast_path_result(self, reason: str, event_mono: float, result) -> None:
        finished = result.finished_mono or self.mono()
        fields = {
            "action": "EARLY_STANDBY",
            "reason": reason,
            "step": "cached_lock_fast_path",
            "status": result.status or "skipped",
            "fast_path_used": False,
            "fast_path_skip_reason": result.fast_path_skip_reason or "unknown",
            "target_ip": result.target_ip or "<empty>",
            "target_mac": result.target_mac or "<empty>",
            "duration_ms": result.duration_ms,
            "since_event_ms": int(max(0.0, (result.started_mono or finished) - event_mono) * 1000),
            "cache_version": result.cache_version,
            "cache_age_ms": result.cache_age_ms,
            "mono": f"{finished:.3f}",
        }
        if result.error:
            fields["error"] = result.error
        if result.so_error is not None:
            fields["so_error"] = result.so_error
        if result.host_unreachable:
            fields["host_unreachable"] = True
        self._log_structured("WARN" if result.error else "STEP", log_level="info", **fields)

    def _log_cached_lock_fast_path_success(self, reason: str, event_mono: float, generation: int, result) -> None:
        started = result.started_mono
        finished = result.finished_mono
        since_event_ms = int(max(0.0, started - event_mono) * 1000)
        common = {
            "fast_path_used": True,
            "cache_version": result.cache_version,
            "cache_age_ms": result.cache_age_ms,
            "fast_path_duration_ms": result.duration_ms,
        }
        self._log_structured(
            "STEP",
            log_level="info",
            action="EARLY_STANDBY",
            reason=reason,
            step="early_standby_trigger_entry",
            event=reason,
            since_event_ms=since_event_ms,
            **common,
            mono=f"{started:.3f}",
        )
        self._log_structured("BEGIN", action="EARLY_STANDBY", gen=generation, reason=reason, mono=f"{started:.3f}")
        self._log_structured(
            "STEP",
            log_level="info",
            action="EARLY_STANDBY",
            gen=generation,
            reason=reason,
            step="lock_fast_path",
            status="begin",
            target_ip=result.target_ip,
            target_mac=result.target_mac or "<empty>",
            identity_check="cached_snapshot_only",
            verify_standby=False,
            **common,
            mono=f"{started:.3f}",
        )
        self._log_structured(
            "STEP",
            log_level="info",
            action="PREWARMED_STANDBY_SOCKET",
            reason=reason,
            step="send_enter",
            target_ip=result.target_ip,
            mode=result.mode,
            deadline_s=f"{self.config.prewarmed_send_deadline_s:.2f}",
            since_windows_event_ms=since_event_ms,
            **common,
            mono=f"{started:.3f}",
        )
        self._log_structured(
            "STEP",
            log_level="info",
            action="EARLY_STANDBY",
            gen=generation,
            reason=reason,
            step="prewarmed_standby_send",
            status=result.status,
            target_ip=result.target_ip,
            duration_ms=result.duration_ms,
            mode=result.mode,
            deadline_s=f"{self.config.prewarmed_send_deadline_s:.2f}",
            bypass_action_lock=True,
            read_response=False,
            so_error=result.so_error,
            **common,
            mono=f"{finished:.3f}",
        )
        self._log_structured(
            "END",
            action="EARLY_STANDBY",
            gen=generation,
            reason=reason,
            outcome="sent_unconfirmed_prewarmed",
            duration_ms=int(max(0.0, finished - started) * 1000),
            **common,
            mono=f"{finished:.3f}",
        )

    def _run_early_standby_trigger(
        self,
        trigger,
        reason: str,
        *,
        generation: int | None = None,
        event_mono: float | None = None,
        deadline_mono: float | None = None,
    ) -> bool:
        return self._on_early_standby_signal(
            reason,
            enabled=bool(getattr(self.config, trigger.enabled_field)),
            disabled_cause=trigger.disabled_cause,
            action=trigger.action_name,
            generation=generation,
            event_mono=event_mono,
            deadline_mono=deadline_mono,
        )

    def _on_early_standby_signal(
        self,
        reason: str,
        *,
        enabled: bool,
        disabled_cause: str,
        action: str,
        generation: int | None = None,
        event_mono: float | None = None,
        deadline_mono: float | None = None,
    ) -> bool:
        entry_mono = self.mono()
        with self._state_lock:
            event_name = self._last_windows_event_name
            recorded_event_mono = float(self._last_windows_event_mono or 0.0)
        event_mono = recorded_event_mono if event_mono is None else event_mono
        if self._early_standby_event_matches_reason(event_name, reason):
            fields = {
                "action": action,
                "reason": reason,
                "step": "early_standby_trigger_entry",
                "event": event_name,
                "mono": f"{entry_mono:.3f}",
            }
            if event_mono > 0:
                fields["since_event_ms"] = int(max(0.0, entry_mono - event_mono) * 1000)
            self._log_structured("STEP", log_level="info", **fields)

        if not enabled:
            self._log_structured(
                "SKIP",
                action=action,
                reason=reason,
                cause=disabled_cause,
                mono=f"{self.mono():.3f}",
            )
            return False
        if self._is_session_ending():
            self._log_structured(
                "SKIP",
                action=action,
                reason=reason,
                cause="session_ending",
                mono=f"{self.mono():.3f}",
            )
            return False

        if (
            event_mono > 0
            and self._early_standby_event_matches_reason(event_name, reason)
            and entry_mono - event_mono > 5.0
        ):
            self._log_structured(
                "WARN",
                action=action,
                reason=reason,
                cause="thread_frozen_before_trigger_entry",
                event=event_name,
                frozen_s=f"{entry_mono - event_mono:.1f}",
                note="modern_standby_likely_froze_message_pump",
                mono=f"{entry_mono:.3f}",
            )

        if generation is None:
            generation = self._new_generation("sleep", reason)

        abort_reason = self._bounded_standby_abort_reason(
            deadline_mono=deadline_mono,
            generation=generation if deadline_mono is not None else None,
        )
        if abort_reason:
            self._log_structured(
                "ABORT",
                action=action,
                gen=generation,
                reason=reason,
                step="before_early_standby_worker_send",
                cause=abort_reason,
                deadline_mono=f"{deadline_mono:.3f}" if deadline_mono is not None else None,
                mono=f"{self.mono():.3f}",
            )
            return False

        if deadline_mono is None:
            return self.standby_kef_preemptive(generation, reason)
        return self.standby_kef_preemptive(generation, reason, deadline_mono=deadline_mono)

    def on_lid_closed(self, reason: str = "POWER_LID_CLOSED") -> bool:
        return get_trigger("lid_closed").fire(self, reason)

    def on_display_off(self, event_mono: float, reason: str = "DISPLAY_OFF") -> bool:
        # Modern Standby (Windows 11 S0 idle): the screen turning off is often the
        # only timely "user stepped away" signal we get. Reuse the suspend standby
        # behavior, but first use the cached playback state as a cheap fast skip.
        # The worker does the bounded live read before any standby packet is sent.
        if not self.config.standby_on_sleep:
            self._log_structured(
                "SKIP",
                action="EARLY_STANDBY",
                reason=reason,
                cause="sleep_standby_disabled",
                mono=f"{self.mono():.3f}",
            )
            return False

        playing = self.speaker_is_probably_playing()
        if playing is True:
            self._log_structured(
                "SKIP",
                action="EARLY_STANDBY",
                reason=reason,
                cause="speaker_playing",
                mono=f"{self.mono():.3f}",
            )
            return False

        return self.dispatch_off_pump_standby(
            "display_off",
            reason,
            event_mono,
            callback_started_mono=event_mono,
            step="dispatch_display_off_standby",
        )

    def on_resume(self, reason: str):
        self._clear_early_standby_state()
        if self._should_dedupe_resume_and_mark(reason):
            return

        if self.config.wake_on_unlock_only:
            self._log_structured("STEP", action="WAKE", reason=reason, step="resume", status="wait_for_any_unlock", mono=f"{self.mono():.3f}")
            return

        generation = self._new_generation("wake", reason)
        self._schedule_delayed_wake(generation, reason, self.config.resume_wake_delay, "resume_delay", "WakeWorker")

    def on_unlock(self, reason: str):
        self._clear_early_standby_state()
        if self._is_session_ending():
            self._log_structured("SKIP", action="WAKE", reason=reason, cause="session_ending", mono=f"{self.mono():.3f}")
            return

        if not self.config.wake_on_unlock_only:
            self._log_structured("SKIP", action="WAKE", reason=reason, cause="unlock_wake_disabled", mono=f"{self.mono():.3f}")
            return

        generation = self._new_generation("wake", reason)
        self._schedule_delayed_wake(generation, reason, self.config.unlock_wake_delay, "unlock_delay", "UnlockWake")

    def on_query_end_session(self, wparam: int, lparam: int) -> bool:
        return get_trigger("query_end_session").fire(self, wparam, lparam)

    def on_end_session(self, ending: bool, lparam: int):
        return get_trigger("end_session").fire(self, ending, lparam)
