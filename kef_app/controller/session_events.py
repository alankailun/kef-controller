from __future__ import annotations

import threading
import traceback

from .triggers import get_trigger


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

    def _run_early_standby_trigger(self, trigger, reason: str) -> bool:
        return self._on_early_standby_signal(
            reason,
            enabled=bool(getattr(self.config, trigger.enabled_field)),
            disabled_cause=trigger.disabled_cause,
            action=trigger.action_name,
        )

    def _on_early_standby_signal(self, reason: str, *, enabled: bool, disabled_cause: str, action: str) -> bool:
        entry_mono = self.mono()
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

        with self._state_lock:
            event_name = self._last_windows_event_name
            event_mono = float(self._last_windows_event_mono or 0.0)
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

        generation = self._new_generation("sleep", reason)
        return self.standby_kef_preemptive(generation, reason)

    def on_lid_closed(self, reason: str = "POWER_LID_CLOSED") -> bool:
        return get_trigger("lid_closed").fire(self, reason)

    def on_resume(self, reason: str):
        if self._should_dedupe_resume_and_mark(reason):
            return

        if self.config.wake_on_unlock_only:
            self._log_structured("STEP", action="WAKE", reason=reason, step="resume", status="wait_for_any_unlock", mono=f"{self.mono():.3f}")
            return

        generation = self._new_generation("wake", reason)
        self._schedule_delayed_wake(generation, reason, self.config.resume_wake_delay, "resume_delay", "WakeWorker")

    def on_unlock(self, reason: str):
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
