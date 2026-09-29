from __future__ import annotations

import threading

from ..platform.windows.api import (
    ENDSESSION_CLOSEAPP,
    ENDSESSION_CRITICAL,
    ENDSESSION_LOGOFF,
    decode_query_end_session_flags,
)
from .event_dispatch import (
    _EARLY_STANDBY_EVENT_BUDGET_S,
    _EARLY_STANDBY_RULES,
    _PUMP_CALLBACK_SLOW_THRESHOLD_S,
    _SUSPEND_STANDBY_EVENT_BUDGET_S,
    ControllerEventDispatchMixin,
    _BoundedStandbyTask,
    _DisplayOffStandbyTask,
)

_STARTUP_PREBUILD_WAIT_S = 30.0
_STARTUP_PREBUILD_POLL_S = 0.1


class ControllerSessionEventsMixin(ControllerEventDispatchMixin):
    def _wait_for_startup_prebuild(self, ready: threading.Event, generation: int) -> bool:
        """Let the startup connection finish its target recovery before waking.

        Both paths recover the IP through non-blocking discovery locks; waking
        concurrently makes the loser skip the wake instead of waiting for the
        recovered target.  A lock or other generation change still cancels.
        """
        deadline = self.mono() + _STARTUP_PREBUILD_WAIT_S
        while not ready.is_set():
            remaining = deadline - self.mono()
            if remaining <= 0:
                self._log_structured(
                    "WARN",
                    action="WAKE",
                    reason="startup",
                    step="startup_prebuild_wait",
                    cause="prebuild_wait_timeout",
                    timeout_s=f"{_STARTUP_PREBUILD_WAIT_S:.0f}",
                )
                return True
            if not self._interruptible_sleep(min(_STARTUP_PREBUILD_POLL_S, remaining), generation, "startup_prebuild_wait"):
                return False
        return True

    def on_startup(self, ready: threading.Event | None = None) -> bool:
        if not self.config.wake_on_startup:
            self._log_structured(
                "SKIP",
                action="WAKE",
                reason="startup",
                cause="startup_wake_disabled",
            )
            return False

        generation, _ = self._claim_wake_generation("startup", dedupe=False)
        self._log_structured(
            "STEP",
            action="WAKE",
            reason="startup",
            step="startup_delay",
            delay_s=f"{self.config.startup_delay:.2f}",
        )
        if not self._interruptible_sleep(self.config.startup_delay, generation, "startup_delay"):
            return False
        if ready is not None and not self._wait_for_startup_prebuild(ready, generation):
            return False
        return self.wake_kef(generation, "startup")

    def on_suspend(
        self,
        reason: str,
        event_mono: float | None = None,
        *,
        callback_started_mono: float | None = None,
    ) -> bool:
        event_mono = self.mono() if event_mono is None else event_mono
        return self.dispatch_off_pump_standby(
            "suspend",
            reason,
            event_mono,
            callback_started_mono=event_mono if callback_started_mono is None else callback_started_mono,
            step="dispatch_suspend_standby",
        )

    def on_lock(
        self,
        reason: str,
        event_mono: float | None = None,
        *,
        callback_started_mono: float | None = None,
    ) -> bool:
        event_mono = self.mono() if event_mono is None else event_mono
        state_recorded_mono: float | None = None
        if reason == "WTS_SESSION_LOCK":
            self._set_session_locked(True)
            state_recorded_mono = self.mono()
        return self.dispatch_off_pump_standby(
            "lock",
            reason,
            event_mono,
            callback_started_mono=event_mono if callback_started_mono is None else callback_started_mono,
            state_recorded_mono=state_recorded_mono,
        )

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

        self._log_structured("STEP", **fields)
        if callback_duration_s > _PUMP_CALLBACK_SLOW_THRESHOLD_S:
            self._log_structured(
                "WARN",
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
        self._cancel_display_off_standby_intent(expected_trigger=None, advance_generation=False)
        generation = self._new_generation("sleep", reason, mono=f"{event_mono:.3f}")
        if not self.config.standby_on_sleep:
            self._log_structured(
                "SKIP",
                action="STANDBY",
                gen=generation,
                reason=reason,
                cause="sleep_standby_disabled",
            )
            return False

        deadline_mono = event_mono + _SUSPEND_STANDBY_EVENT_BUDGET_S
        fast_path_enabled = bool(self.config.suspend_fast_standby_enabled)
        self._log_structured(
            "STEP",
            action="STANDBY",
            gen=generation,
            reason=reason,
            step="schedule_suspend_dispatcher",
            event_mono=f"{event_mono:.3f}",
            deadline_mono=f"{deadline_mono:.3f}",
            budget_ms=int(_SUSPEND_STANDBY_EVENT_BUDGET_S * 1000),
            mode="fast_request" if fast_path_enabled else "verified_request",
        )

        self._enqueue_display_off_standby_task(
            _BoundedStandbyTask(
                trigger_name="suspend",
                generation=generation,
                reason=reason,
                event_mono=event_mono,
                deadline_mono=deadline_mono,
                fast_path_enabled=fast_path_enabled,
            )
        )
        return True

    def schedule_early_standby(self, trigger_name: str, reason: str, event_mono: float) -> bool:
        try:
            enabled_field, disabled_cause = _EARLY_STANDBY_RULES[trigger_name]
        except KeyError as exc:
            raise ValueError(f"Unknown early standby trigger: {trigger_name}") from exc
        action_name = "EARLY_STANDBY"
        if not bool(getattr(self.config, enabled_field)):
            self._log_structured(
                "SKIP",
                action=action_name,
                reason=reason,
                cause=disabled_cause,
            )
            return False
        if self._is_session_ending():
            self._log_structured(
                "SKIP",
                action=action_name,
                reason=reason,
                cause="session_ending",
            )
            return False

        if trigger_name in {"display_off", "lock"}:
            intent = self._begin_cancellable_standby_intent(trigger_name, reason, event_mono)
            task = _DisplayOffStandbyTask(
                generation=intent.generation,
                trigger_name=trigger_name,
                reason=reason,
                event_mono=event_mono,
                cancel_event=intent.cancel_event,
            )
            # Queue before logging: this is the shortest path from the Windows
            # event to the resident sender, while the intent remains fully
            # cancellable by a later DisplayOn.
            self._enqueue_display_off_standby_task(task)
            self._log_structured(
                "STATE",
                desired="sleep",
                gen=intent.generation,
                reason=reason,
                mono=f"{event_mono:.3f}",
            )
            self._log_structured(
                "STEP",
                action=action_name,
                gen=intent.generation,
                reason=reason,
                step="schedule_cancellable_dispatcher",
                trigger=trigger_name,
                event_mono=f"{event_mono:.3f}",
            )
            return True

        self._cancel_display_off_standby_intent(expected_trigger=None, advance_generation=False)
        generation = self._new_generation("sleep", reason, mono=f"{event_mono:.3f}")
        budget_s = _EARLY_STANDBY_EVENT_BUDGET_S
        deadline_mono = event_mono + budget_s
        self._log_structured(
            "STEP",
            action=action_name,
            gen=generation,
            reason=reason,
            step="schedule_bounded_dispatcher",
            trigger=trigger_name,
            event_mono=f"{event_mono:.3f}",
            deadline_mono=f"{deadline_mono:.3f}",
            budget_ms=int(budget_s * 1000),
        )

        self._enqueue_display_off_standby_task(
            _BoundedStandbyTask(
                trigger_name=trigger_name,
                generation=generation,
                reason=reason,
                event_mono=event_mono,
                deadline_mono=deadline_mono,
            )
        )
        return True

    def _run_early_standby_trigger(
        self,
        trigger_name: str,
        reason: str,
        *,
        generation: int,
        event_mono: float,
        deadline_mono: float,
    ) -> bool:
        enabled_field, disabled_cause = _EARLY_STANDBY_RULES[trigger_name]
        return self._on_early_standby_signal(
            reason,
            enabled=bool(getattr(self.config, enabled_field)),
            disabled_cause=disabled_cause,
            action="EARLY_STANDBY",
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
        generation: int,
        event_mono: float,
        deadline_mono: float,
    ) -> bool:
        entry_mono = self.mono()
        with self._state_lock:
            event_name = self._windows_events.last_event_name
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
            self._log_structured("STEP", **fields)

        if not enabled:
            self._log_structured(
                "SKIP",
                action=action,
                reason=reason,
                cause=disabled_cause,
            )
            return False
        if self._is_session_ending():
            self._log_structured(
                "SKIP",
                action=action,
                reason=reason,
                cause="session_ending",
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

        abort_reason = self._bounded_standby_abort_reason(
            deadline_mono=deadline_mono,
            generation=generation,
        )
        if abort_reason:
            self._log_structured(
                "ABORT",
                action=action,
                gen=generation,
                reason=reason,
                step="before_early_standby_worker_send",
                cause=abort_reason,
                deadline_mono=f"{deadline_mono:.3f}",
            )
            return False

        return self.standby_kef_preemptive(generation, reason, deadline_mono=deadline_mono)

    def on_lid_closed(
        self,
        reason: str = "POWER_LID_CLOSED",
        event_mono: float | None = None,
        *,
        callback_started_mono: float | None = None,
    ) -> bool:
        event_mono = self.mono() if event_mono is None else event_mono
        return self.dispatch_off_pump_standby(
            "lid_closed",
            reason,
            event_mono,
            callback_started_mono=event_mono if callback_started_mono is None else callback_started_mono,
        )

    def on_display_off(self, event_mono: float, reason: str = "DISPLAY_OFF") -> bool:
        # Modern Standby (Windows 11 S0 idle): the screen turning off is often the
        # only timely "user stepped away" signal we get. Pure display-off standby:
        # when the screen turns off, put the speaker into standby (gated only by
        # standby_on_display_off, like lock/lid). No playback check. The standby_on_
        # display_off gate is enforced downstream in schedule_early_standby.
        return self.dispatch_off_pump_standby(
            "display_off",
            reason,
            event_mono,
            callback_started_mono=event_mono,
            step="dispatch_display_off_standby",
        )

    def _log_display_on_wake_skip(
        self,
        reason: str,
        cause: str,
        *,
        event_mono: float,
        desired_state: str = "",
        desired_reason: str = "",
    ) -> None:
        fields: dict[str, object] = {
            "cause": cause,
            "event_mono": f"{event_mono:.3f}",
        }
        if desired_state or desired_reason:
            fields["desired_state"] = desired_state or "<empty>"
            fields["desired_reason"] = desired_reason or "<empty>"
        # Display events are sparse, and this branch explains why an enabled
        # wake rule did not run.  Keep it in the normal diagnostic log instead
        # of hiding the only useful evidence at DEBUG level.
        self._bind_log(action="WAKE", reason=reason).write("SKIP", **fields)

    def on_display_on(self, event_mono: float, reason: str = "DISPLAY_ON") -> bool:
        previous_intent = self._cancel_display_off_standby_intent(expected_trigger="display_off")
        if previous_intent is None:
            self._log_display_on_wake_skip(reason, "no_pending_display_off_intent", event_mono=event_mono)
            return False

        self._log_structured(
            "STATE",
            desired="display_off_cancelled",
            gen=self._current_generation(),
            reason=reason,
            cancelled_gen=previous_intent.generation,
            previous_status=previous_intent.send_status,
            since_event_ms=int(max(0.0, event_mono - previous_intent.event_mono) * 1000),
            mono=f"{event_mono:.3f}",
        )

        if not self.config.wake_on_display_on:
            self._log_display_on_wake_skip(reason, "display_on_wake_disabled", event_mono=event_mono)
            return False

        fence_generation = self._current_generation()
        deferred_status = self._defer_display_on_wake_if_locked(
            previous_intent,
            event_mono=event_mono,
            reason=reason,
            expected_generation=fence_generation,
        )
        if deferred_status == "deferred":
            self._log_display_on_wake_skip(
                reason,
                "deferred_until_unlock",
                event_mono=event_mono,
                desired_state=previous_intent.send_status,
                desired_reason=previous_intent.reason,
            )
            return True
        if deferred_status == "session_ending":
            self._log_display_on_wake_skip(
                reason,
                "session_ending",
                event_mono=event_mono,
                desired_state=previous_intent.send_status,
                desired_reason=previous_intent.reason,
            )
            return False
        if deferred_status == "stale_generation":
            self._log_display_on_wake_skip(
                reason,
                "generation_changed_before_wake",
                event_mono=event_mono,
                desired_state=previous_intent.send_status,
                desired_reason=previous_intent.reason,
            )
            return False
        generation, claim_status = self._claim_wake_generation(
            reason,
            expected_generation=fence_generation,
            mono=f"{event_mono:.3f}",
            dedupe=False,
        )
        if generation is None:
            self._log_display_on_wake_skip(
                reason,
                "generation_changed_before_wake" if claim_status == "stale_generation" else "wake_deduped",
                event_mono=event_mono,
                desired_state=previous_intent.send_status,
                desired_reason=previous_intent.reason,
            )
            return False
        self._log_structured(
            "STEP",
            action="WAKE",
            gen=generation,
            reason=reason,
            step="display_on_cancelled_display_off_standby",
            previous_send_status=previous_intent.send_status,
            event_mono=f"{event_mono:.3f}",
            delay_s=f"{self.config.display_on_wake_delay:.2f}",
        )
        self._schedule_delayed_wake(
            generation,
            reason,
            self.config.display_on_wake_delay,
            "display_on_delay",
            "DisplayOnWake",
            skip_if_already_on=True,
        )
        return True

    def on_resume(self, reason: str):
        if self._should_dedupe_resume_and_mark(reason):
            return

        if self.config.wake_on_unlock_only:
            self._log_structured("STEP", action="WAKE", reason=reason, step="resume", status="wait_for_any_unlock")
            return

        generation, _ = self._claim_wake_generation(reason, dedupe=False)
        self._schedule_delayed_wake(generation, reason, self.config.resume_wake_delay, "resume_delay", "WakeWorker")

    def on_unlock(self, reason: str):
        self._set_session_locked(False)
        previous_intent = self._cancel_display_off_standby_intent(expected_trigger="lock")
        if previous_intent is not None:
            self._log_structured(
                "STATE",
                desired="lock_standby_cancelled",
                gen=self._current_generation(),
                reason=reason,
                cancelled_gen=previous_intent.generation,
                previous_status=previous_intent.send_status,
                since_event_ms=int(max(0.0, self.mono() - previous_intent.event_mono) * 1000),
            )
        deferred, deferred_status = self._take_deferred_display_on_wake()
        if self._is_session_ending():
            self._log_structured("SKIP", action="WAKE", reason=reason, cause="session_ending")
            return

        if deferred is not None and deferred_status != "current":
            self._log_structured(
                "SKIP",
                action="WAKE",
                reason=reason,
                cause="deferred_display_on_stale",
                source_gen=deferred.source_generation,
                fence_gen=deferred.fence_generation,
                current_gen=self._current_generation(),
                deferred_cause=deferred_status,
            )

        normal_unlock_wake = bool(self.config.wake_on_unlock_only)
        deferred_display_on_wake = deferred if deferred_status == "current" else None
        if not normal_unlock_wake and deferred_display_on_wake is None:
            self._log_structured("SKIP", action="WAKE", reason=reason, cause="unlock_wake_disabled")
            return
        wake_reason = reason if normal_unlock_wake else "DISPLAY_ON_DEFERRED_TO_UNLOCK"
        wake_delay = self.config.unlock_wake_delay if normal_unlock_wake else self.config.display_on_wake_delay
        expected_generation = deferred_display_on_wake.fence_generation if deferred_display_on_wake else None
        generation, claim_status = self._claim_wake_generation(
            wake_reason,
            expected_generation=expected_generation,
        )
        if generation is None:
            if claim_status == "stale_generation":
                self._log_structured(
                    "SKIP",
                    action="WAKE",
                    reason=wake_reason,
                    cause="deferred_display_on_stale_before_claim",
                    source_gen=deferred_display_on_wake.source_generation if deferred_display_on_wake else None,
                    fence_gen=expected_generation,
                    current_gen=self._current_generation(),
                )
            return
        fields: dict[str, object] = {}
        if deferred_display_on_wake is not None:
            fields = {
                "step": "deferred_display_on_consumed",
                "source_gen": deferred_display_on_wake.source_generation,
                "fence_gen": deferred_display_on_wake.fence_generation,
                "previous_send_status": deferred_display_on_wake.previous_send_status,
            }
        if fields:
            self._log_structured("STEP", action="WAKE", reason=wake_reason, gen=generation, **fields)
        self._schedule_delayed_wake(
            generation,
            wake_reason,
            wake_delay,
            "unlock_delay" if normal_unlock_wake else "deferred_display_on_delay",
            "UnlockWake" if normal_unlock_wake else "DeferredDisplayOnWake",
            skip_if_already_on=True,
        )

    def on_query_end_session(self, wparam: int, lparam: int) -> bool:
        reason = "WM_QUERYENDSESSION"
        self._set_session_ending(True)
        self._new_generation("sleep", reason)
        flags = decode_query_end_session_flags(lparam)
        self._log_structured(
            "EVENT",
            action="WINDOW_SESSION_EVENT",
            kind="WINDOW",
            name=reason,
            wparam=wparam,
            lparam=f"0x{lparam:08X}",
            flags=flags,
        )
        self._log_structured(
            "STEP",
            action="STANDBY",
            reason=reason,
            step="generation_refresh",
            status="wake_threads_interrupted_wait_endsession",
        )

        is_rm_closeapp = bool(lparam & ENDSESSION_CLOSEAPP) and not bool(
            lparam & (ENDSESSION_LOGOFF | ENDSESSION_CRITICAL)
        )
        if self.config.fast_exit_on_endsession and is_rm_closeapp:
            self._log_structured(
                "STEP",
                action="PROCESS_EXIT",
                reason=reason,
                step="request_self_close",
                status="rm_closeapp_fast_exit",
            )
            return True

        standby_sent = self.standby_kef_end_session(reason, flags)
        self._log_structured(
            "STEP",
            action="PROCESS_EXIT",
            reason=reason,
            step="request_self_close",
            status="wait_for_wm_endsession_or_system_teardown",
            endsession_standby_sent=standby_sent,
        )
        return False

    def on_end_session(self, ending: bool, lparam: int) -> None:
        reason = "WM_ENDSESSION"
        flags = decode_query_end_session_flags(lparam)
        self._set_session_ending(ending)
        self._log_structured(
            "EVENT",
            action="WINDOW_SESSION_EVENT",
            kind="WINDOW",
            name=reason,
            ending=ending,
            lparam=f"0x{lparam:08X}",
            flags=flags,
        )
        if ending:
            self._new_generation("sleep", reason)
            self._log_structured(
                "STEP",
                action="PROCESS_EXIT",
                reason=reason,
                step="fast_exit",
                status=("skip_standby_to_avoid_app_hang" if self.config.fast_exit_on_endsession else "no_fast_exit"),
            )
