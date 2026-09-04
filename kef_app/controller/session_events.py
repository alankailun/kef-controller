from __future__ import annotations

import queue
import threading
import traceback
from dataclasses import dataclass

from ..platform.windows.api import (
    ENDSESSION_CLOSEAPP,
    ENDSESSION_CRITICAL,
    ENDSESSION_LOGOFF,
    decode_query_end_session_flags,
)

_EARLY_STANDBY_EVENT_BUDGET_S = 0.30
_SUSPEND_STANDBY_EVENT_BUDGET_S = 0.30
_PUMP_CALLBACK_SLOW_THRESHOLD_S = 0.020
_DISPLAY_OFF_FAST_RETRY_DELAY_S = 0.50
_DISPLAY_OFF_VERIFIED_RETRY_DELAY_S = 1.50

_EARLY_STANDBY_RULES: dict[str, tuple[str, str]] = {
    "display_off": ("standby_on_display_off", "display_off_standby_disabled"),
    "lid_closed": ("standby_on_lid_close", "lid_close_standby_disabled"),
    "lock": ("standby_on_lock", "lock_standby_disabled"),
}


@dataclass(frozen=True, slots=True)
class _DisplayOffStandbyTask:
    generation: int
    trigger_name: str
    reason: str
    event_mono: float
    cancel_event: threading.Event


@dataclass(frozen=True, slots=True)
class _BoundedStandbyTask:
    trigger_name: str
    generation: int
    reason: str
    event_mono: float
    deadline_mono: float
    fast_path_enabled: bool = True


class ControllerSessionEventsMixin:
    @staticmethod
    def _early_standby_event_matches_reason(event_name: str, reason: str) -> bool:
        if event_name == reason:
            return True
        return event_name == "GUID_LIDSWITCH_STATE_CHANGE" and reason == "POWER_LID_CLOSED"

    def _start_controller_thread(self, target, thread_name: str):
        def guarded():
            try:
                target()
            except Exception:
                self._log_structured(
                    "ERROR",
                    action="CONTROLLER_THREAD",
                    trigger=thread_name,
                    cause="unhandled_exception",
                    error=traceback.format_exc(),
                )

        threading.Thread(target=guarded, daemon=True, name=thread_name).start()

    def start_display_off_standby_dispatcher(self) -> bool:
        """Start the resident worker used by off-pump standby fast paths."""
        with self._display_off_dispatcher.lock:
            thread = self._display_off_dispatcher.thread
            if thread is not None and thread.is_alive():
                return False
            self._display_off_dispatcher.stop.clear()
            self._display_off_dispatcher.queue = queue.SimpleQueue()
            thread = threading.Thread(
                target=self._run_display_off_standby_dispatcher,
                daemon=True,
                name="DisplayOffStandbyDispatcher",
            )
            self._display_off_dispatcher.thread = thread
            thread.start()
            return True

    def stop_display_off_standby_dispatcher(self) -> None:
        self._display_off_dispatcher.stop.set()
        with self._display_off_dispatcher.lock:
            self._display_off_dispatcher.queue.put(None)

    def _enqueue_display_off_standby_task(self, task: _DisplayOffStandbyTask | _BoundedStandbyTask) -> None:
        if self._display_off_dispatcher.stop.is_set():
            # stop_display_off_standby_dispatcher is a terminal cleanup action.
            # Never append work to a live-but-exiting worker's queue.
            return
        self.start_display_off_standby_dispatcher()
        with self._display_off_dispatcher.lock:
            if self._display_off_dispatcher.stop.is_set():
                return
            self._display_off_dispatcher.queue.put(task)

    def _run_display_off_standby_dispatcher(self) -> None:
        """Keep one task failure from taking down future display-off work."""
        while not self._display_off_dispatcher.stop.is_set():
            task = self._display_off_dispatcher.queue.get()
            if task is None:
                continue
            try:
                if isinstance(task, _DisplayOffStandbyTask):
                    self._process_display_off_standby_task(task)
                else:
                    self._process_bounded_standby_task(task)
            except Exception:
                if isinstance(task, _DisplayOffStandbyTask):
                    self._update_display_off_standby_intent(task.generation, "failed")
                self._log_structured(
                    "ERROR",
                    action="DISPLAY_OFF_STANDBY",
                    trigger="standby_dispatcher",
                    cause="task_failed",
                    error=traceback.format_exc(),
                )

    def _log_cancellable_retry_stop(
        self,
        task: _DisplayOffStandbyTask,
        *,
        step: str,
        cause: str,
        include_current_state: bool = False,
    ) -> None:
        fields = {
            "step": step,
            "cause": cause,
        }
        if include_current_state:
            desired_state, desired_reason = self._current_desired_state()
            fields.update(
                current_gen=self._current_generation(),
                current_desired=desired_state or "<empty>",
                current_reason=desired_reason or "<empty>",
            )
        self._action_log("EARLY_STANDBY", task.generation, task.reason).write("STEP", **fields)

    def _wait_for_display_off_retry(self, task: _DisplayOffStandbyTask, delay_s: float, step: str) -> bool:
        if task.cancel_event.wait(delay_s):
            self._log_cancellable_retry_stop(
                task,
                step="cancellable_retry_cancelled",
                cause="cancel_event",
                include_current_state=True,
            )
            return False
        if self._display_off_dispatcher.stop.is_set():
            self._log_cancellable_retry_stop(
                task,
                step="cancellable_retry_cancelled",
                cause="dispatcher_stopped",
            )
            return False
        if not self._display_off_intent_is_active(task.generation):
            self._log_cancellable_retry_stop(
                task,
                step="cancellable_retry_superseded",
                cause="generation_or_intent_changed",
                include_current_state=True,
            )
            return False
        self._log_structured(
            "STEP",
            action="EARLY_STANDBY",
            gen=task.generation,
            reason=task.reason,
            step=step,
            delay_s=f"{delay_s:.2f}",
        )
        return True

    def _complete_display_off_fast_send(
        self,
        task: _DisplayOffStandbyTask,
        *,
        attempt: int,
        outcome: str,
    ) -> bool:
        if not self._update_display_off_standby_intent(task.generation, "sent"):
            return False
        # The send has already completed.  UI/tray listeners are arbitrary
        # user-facing code and must not hold the dispatcher that carries later
        # deadline-bound lid/suspend work.  Keep the start/finish pair ordered
        # in one guarded, short-lived notifier instead.
        def notify_completed_send() -> None:
            self._mark_power_action_started()
            self._emit_power_action_started_event("EARLY_STANDBY", task.reason)
            self._emit_power_action_finished("EARLY_STANDBY", task.reason, outcome)

        self._start_controller_thread(
            notify_completed_send,
            f"CancellableStandbyNotify-{task.trigger_name}-{task.generation}-{attempt}",
        )
        self._log_structured(
            "END",
            action="EARLY_STANDBY",
            gen=task.generation,
            reason=task.reason,
            outcome=outcome,
            attempt=attempt,
            since_event_ms=int(max(0.0, self.mono() - task.event_mono) * 1000),
        )
        return True

    def _send_display_off_fast_attempt(self, task: _DisplayOffStandbyTask, attempt: int) -> bool:
        if not self._update_display_off_standby_intent(task.generation, "sending"):
            return False

        # The cached request avoids JSON/header construction and logging before
        # the first byte.  Its pool helper tries both persistent holders.
        cached_result = self.try_send_cached_prewarmed_standby(generation=task.generation)
        if cached_result.success:
            self._log_structured(
                "STEP",
                action="EARLY_STANDBY",
                gen=task.generation,
                reason=task.reason,
                step="cached_cancellable_send",
                status=cached_result.status,
                target_ip=cached_result.target_ip,
                attempt=attempt,
                cache_version=cached_result.cache_version,
                cache_age_ms=cached_result.cache_age_ms,
                duration_ms=cached_result.duration_ms,
            )
            return self._complete_display_off_fast_send(
                task,
                attempt=attempt,
                outcome="sent_unconfirmed_prewarmed",
            )

        current_ip = self.get_current_kef_ip()
        if not current_ip:
            self._log_structured(
                "WARN",
                action="EARLY_STANDBY",
                gen=task.generation,
                reason=task.reason,
                step="cancellable_fast_send",
                attempt=attempt,
                status="skipped_no_current_ip",
            )
            self._update_display_off_standby_intent(task.generation, "retry_waiting")
            return False

        fast_result = self._send_fast_standby(
            current_ip,
            generation=task.generation,
            reason=task.reason,
            fire_and_forget_attempts=1,
            skip_prewarmed=True,
        )
        fields = {"attempt": attempt, "display_off_dispatcher": True}
        self._log_prewarmed_fast_send(
            fast_result,
            action="EARLY_STANDBY",
            generation=task.generation,
            reason=task.reason,
            current_ip=current_ip,
            host_unreachable_cause="display_off_host_unreachable",
            extra_fields=fields,
        )
        self._log_fire_and_forget_fast_send(
            fast_result,
            action="EARLY_STANDBY",
            generation=task.generation,
            reason=task.reason,
            current_ip=current_ip,
            host_unreachable_outcome="failed_host_unreachable",
            host_unreachable_status="host_unreachable_retrying",
            host_unreachable_cause="display_off_host_unreachable",
            extra_fields=fields,
        )

        if fast_result.success:
            return self._complete_display_off_fast_send(
                task,
                attempt=attempt,
                outcome=("sent_unconfirmed_prewarmed" if fast_result.source == "prewarmed" else "sent_unconfirmed_fire_and_forget"),
            )

        self._update_display_off_standby_intent(task.generation, "retry_waiting")
        return False

    def _process_display_off_standby_task(self, task: _DisplayOffStandbyTask) -> None:
        """Run a cancellable fast-fast-verified early-standby sequence."""
        if self._send_display_off_fast_attempt(task, attempt=1):
            return
        if not self._wait_for_display_off_retry(task, _DISPLAY_OFF_FAST_RETRY_DELAY_S, "display_off_fast_retry"):
            return
        if self._send_display_off_fast_attempt(task, attempt=2):
            return
        if not self._wait_for_display_off_retry(task, _DISPLAY_OFF_VERIFIED_RETRY_DELAY_S, "display_off_verified_retry"):
            return
        if not self._update_display_off_standby_intent(task.generation, "sending"):
            return

        # Verified standby can take seconds (identity, action lock, HTTP
        # verification).  It must never occupy the shared dispatcher, which
        # also carries deadline-bound lid and suspend sends.
        def verified_fallback() -> None:
            success = self.standby_kef(task.generation, task.reason)
            self._update_display_off_standby_intent(
                task.generation,
                "confirmed" if success else "failed",
            )

        self._start_controller_thread(
            verified_fallback,
            f"CancellableStandbyVerify-{task.trigger_name}-{task.generation}",
        )

    def _process_bounded_standby_task(self, task: _BoundedStandbyTask) -> None:
        """Run one deadline-bound fast send without blocking the dispatcher."""
        abort_reason = self._bounded_standby_abort_reason(
            deadline_mono=task.deadline_mono,
            generation=task.generation,
        )
        if abort_reason:
            self._log_structured(
                "ABORT",
                action="STANDBY" if task.trigger_name == "suspend" else "EARLY_STANDBY",
                gen=task.generation,
                reason=task.reason,
                step=f"before_{task.trigger_name}_dispatcher_send",
                cause=abort_reason,
                deadline_mono=f"{task.deadline_mono:.3f}",
            )
            return

        if task.trigger_name == "suspend":
            if task.fast_path_enabled:
                self.standby_kef_fast_suspend(
                    task.generation,
                    task.reason,
                    deadline_mono=task.deadline_mono,
                )
                return
            # Preserve the existing full-standby fallback without allowing it
            # to hold up the resident fast-path dispatcher.
            self._start_controller_thread(
                lambda: self.standby_kef(task.generation, task.reason),
                f"SuspendVerifiedStandby-{task.generation}",
            )
            return

        self._run_early_standby_trigger(
            task.trigger_name,
            task.reason,
            generation=task.generation,
            event_mono=task.event_mono,
            deadline_mono=task.deadline_mono,
        )

    def _schedule_delayed_wake(
        self,
        generation: int,
        reason: str,
        delay: float,
        step_label: str,
        thread_name: str,
        *,
        skip_if_already_on: bool = False,
    ):
        def worker():
            self._log_structured(
                "STEP",
                action="WAKE",
                gen=generation,
                reason=reason,
                step=step_label,
                delay_s=f"{delay:.2f}",
            )
            if not self._interruptible_sleep(delay, generation, step_label):
                return
            if skip_if_already_on:
                self.wake_kef(generation, reason, skip_if_already_on=True)
            else:
                self.wake_kef(generation, reason)

        self._start_controller_thread(worker, f"{thread_name}-{generation}")

    def on_startup(self) -> bool:
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
