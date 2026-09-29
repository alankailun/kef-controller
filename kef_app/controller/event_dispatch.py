from __future__ import annotations

import queue
import threading
import traceback
from dataclasses import dataclass

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


class ControllerEventDispatchMixin:
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

