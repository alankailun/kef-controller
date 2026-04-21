from __future__ import annotations

import threading
import time

from ..platform_windows import ENDSESSION_CLOSEAPP, ENDSESSION_CRITICAL, ENDSESSION_LOGOFF, decode_query_end_session_flags


class ControllerSessionEventsMixin:
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

        threading.Thread(target=worker, daemon=True, name=f"{thread_name}-{generation}").start()

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

        self._log_structured(
            "STEP",
            action="WAKE",
            reason="startup",
            step="startup_delay",
            delay_s=f"{self.config.startup_delay:.2f}",
            mono=f"{self.mono():.3f}",
        )
        time.sleep(self.config.startup_delay)
        generation = self._new_generation("wake", "startup")
        self.wake_kef(generation, "startup")

    def on_suspend(self, reason: str):
        self._clear_pending_unlock_wake()
        if not self.config.standby_on_sleep:
            self._log_structured("SKIP", action="STANDBY", reason=reason, cause="sleep_standby_disabled", mono=f"{self.mono():.3f}")
            return
        generation = self._new_generation("sleep", reason)
        self.standby_kef(generation, reason)

    def on_lock(self, reason: str):
        if not self.config.standby_on_lock:
            return
        if self._is_session_ending():
            return
        self.standby_kef_preemptive(reason)

    def on_resume(self, reason: str):
        if self._should_dedupe_resume_and_mark(reason):
            return
        self._mark_resume_seen_since_lock()

        if self.config.wake_on_unlock_only:
            self._clear_pending_unlock_wake()
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

        self._clear_pending_unlock_wake()
        self._clear_lock_only_rewake_state()
        generation = self._new_generation("wake", reason)
        self._schedule_delayed_wake(generation, reason, self.config.unlock_wake_delay, "unlock_delay", "UnlockWake")

    def on_query_end_session(self, wparam: int, lparam: int) -> bool:
        self._clear_pending_unlock_wake()
        self._clear_lock_only_rewake_state()
        self._set_session_ending(True)
        self._new_generation("sleep", "WM_QUERYENDSESSION")
        flags = decode_query_end_session_flags(lparam)
        self._log_structured(
            "EVENT",
            kind="WINDOW",
            name="WM_QUERYENDSESSION",
            wparam=wparam,
            lparam=f"0x{lparam:08X}",
            flags=flags,
            mono=f"{self.mono():.3f}",
        )
        self._log_structured(
            "STEP",
            action="STANDBY",
            reason="WM_QUERYENDSESSION",
            step="generation_refresh",
            status="wake_threads_interrupted_wait_endsession",
            mono=f"{self.mono():.3f}",
        )

        is_rm_closeapp = bool(lparam & ENDSESSION_CLOSEAPP) and not bool(lparam & (ENDSESSION_LOGOFF | ENDSESSION_CRITICAL))
        if self.config.fast_exit_on_endsession and is_rm_closeapp:
            self._log_structured(
                "STEP",
                action="PROCESS_EXIT",
                reason="WM_QUERYENDSESSION",
                step="request_self_close",
                status="rm_closeapp_fast_exit",
                mono=f"{self.mono():.3f}",
            )
            return True

        standby_sent = self.standby_kef_end_session("WM_QUERYENDSESSION", flags)
        self._log_structured(
            "STEP",
            action="PROCESS_EXIT",
            reason="WM_QUERYENDSESSION",
            step="request_self_close",
            status="wait_for_wm_endsession_or_system_teardown",
            endsession_standby_sent=standby_sent,
            mono=f"{self.mono():.3f}",
        )
        return False

    def on_end_session(self, ending: bool, lparam: int):
        flags = decode_query_end_session_flags(lparam)
        self._set_session_ending(ending)
        self._log_structured(
            "EVENT",
            kind="WINDOW",
            name="WM_ENDSESSION",
            ending=ending,
            lparam=f"0x{lparam:08X}",
            flags=flags,
            mono=f"{self.mono():.3f}",
        )
        if ending:
            self._new_generation("sleep", "WM_ENDSESSION")
            self._log_structured(
                "STEP",
                action="PROCESS_EXIT",
                reason="WM_ENDSESSION",
                step="fast_exit",
                status=("skip_standby_to_avoid_app_hang" if self.config.fast_exit_on_endsession else "no_fast_exit"),
                mono=f"{self.mono():.3f}",
            )
