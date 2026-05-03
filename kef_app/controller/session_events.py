from __future__ import annotations

import threading
import traceback

from ..platform.windows import ENDSESSION_CLOSEAPP, ENDSESSION_CRITICAL, ENDSESSION_LOGOFF, decode_query_end_session_flags


class ControllerSessionEventsMixin:
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
        generation = self._new_generation("sleep", reason)
        if not self.config.standby_on_sleep:
            self._log_structured("SKIP", action="STANDBY", reason=reason, cause="sleep_standby_disabled", mono=f"{self.mono():.3f}")
            return
        if self.config.suspend_fast_standby_enabled:
            self.standby_kef_fast_suspend(generation, reason)
            return
        self._start_controller_thread(lambda: self.standby_kef(generation, reason), f"SuspendStandby-{generation}")

    def on_lock(self, reason: str):
        if not self.config.standby_on_lock:
            return
        if self._is_session_ending():
            return
        generation = self._new_generation("sleep", reason)
        # Run synchronously on the message-pump thread so the HTTP shutdown
        # fires before Windows can deliver PBT_APMSUSPEND and tear down
        # networking — by suspend time the route is already gone.
        self.standby_kef_preemptive(generation, reason)

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
