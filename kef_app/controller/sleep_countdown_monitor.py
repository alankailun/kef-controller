from __future__ import annotations

import threading

from ..platform.windows import read_system_idle_info
from .triggers import get_trigger

_MIN_SLEEP_COUNTDOWN_POLL_INTERVAL_S = 0.25
_NO_SLEEP_COUNTDOWN_REMAINING_S = 0xFFFFFFFF


class SleepCountdownMonitorMixin:
    def start_sleep_countdown_monitor(self, reason: str = "runtime") -> bool:
        if not self.config.standby_on_sleep_countdown:
            return False

        with self._sleep_countdown_monitor_lock:
            if self._sleep_countdown_monitor_running:
                if self._sleep_countdown_monitor_stop.is_set():
                    self._sleep_countdown_monitor_restart_pending = True
                    self._log_structured(
                        "STEP",
                        action="SLEEP_COUNTDOWN_MONITOR",
                        reason=reason,
                        step="monitor",
                        status="restart_pending",
                        mono=f"{self.mono():.3f}",
                    )
                    return True
                return False
            self._sleep_countdown_monitor_running = True
            self._sleep_countdown_monitor_restart_pending = False
            self._sleep_countdown_monitor_stop.clear()

        self._spawn_sleep_countdown_monitor_thread(reason)
        return True

    def _spawn_sleep_countdown_monitor_thread(self, reason: str) -> None:
        thread = threading.Thread(
            target=lambda: self._run_sleep_countdown_monitor(reason),
            daemon=True,
            name="SleepCountdownMonitor",
        )
        with self._sleep_countdown_monitor_lock:
            self._sleep_countdown_monitor_thread = thread
        thread.start()

    def stop_sleep_countdown_monitor(self) -> None:
        self._sleep_countdown_monitor_stop.set()

    def _finish_sleep_countdown_monitor(self) -> str | None:
        restart_reason = None
        with self._sleep_countdown_monitor_lock:
            self._sleep_countdown_monitor_running = False
            self._sleep_countdown_monitor_thread = None
            if self._sleep_countdown_monitor_restart_pending and self.config.standby_on_sleep_countdown:
                self._sleep_countdown_monitor_restart_pending = False
                self._sleep_countdown_monitor_running = True
                self._sleep_countdown_monitor_stop.clear()
                restart_reason = "restart_pending"
        return restart_reason

    def _run_sleep_countdown_monitor(self, reason: str) -> None:
        fired_for_this_countdown = False
        self._log_structured(
            "STEP",
            action="SLEEP_COUNTDOWN_MONITOR",
            reason=reason,
            step="monitor",
            status="started",
            threshold_s=f"{self.config.sleep_countdown_threshold_s:.1f}",
            poll_interval_s=f"{self.config.sleep_countdown_poll_interval_s:.1f}",
            mono=f"{self.mono():.3f}",
        )
        try:
            while not self._sleep_countdown_monitor_stop.is_set():
                fired_for_this_countdown = self._poll_sleep_countdown_once(
                    fired_for_this_countdown,
                    reason=reason,
                )
                poll_interval = max(
                    _MIN_SLEEP_COUNTDOWN_POLL_INTERVAL_S,
                    float(self.config.sleep_countdown_poll_interval_s),
                )
                if (
                    self._sleep_countdown_monitor_stop.wait(poll_interval)
                    and self._sleep_countdown_monitor_stop.is_set()
                ):
                    return
        finally:
            restart_reason = self._finish_sleep_countdown_monitor()
            self._log_structured(
                "STEP",
                action="SLEEP_COUNTDOWN_MONITOR",
                reason=reason,
                step="monitor",
                status="stopped",
                mono=f"{self.mono():.3f}",
            )
            if restart_reason is not None:
                self._spawn_sleep_countdown_monitor_thread(restart_reason)

    def _poll_sleep_countdown_once(self, fired_for_this_countdown: bool, *, reason: str) -> bool:
        threshold = max(0.0, float(self.config.sleep_countdown_threshold_s))
        if not self.config.standby_on_sleep_countdown or threshold <= 0:
            return False
        if self._is_session_ending() or self._is_controller_power_action_active():
            return fired_for_this_countdown

        info = read_system_idle_info()
        if info is None:
            return fired_for_this_countdown

        time_remaining_s = int(info.TimeRemaining)
        if time_remaining_s == 0 or time_remaining_s >= _NO_SLEEP_COUNTDOWN_REMAINING_S:
            return False

        reset_threshold = threshold * 3.0
        if time_remaining_s > reset_threshold:
            return False

        if 0 < time_remaining_s <= threshold and not fired_for_this_countdown:
            self._log_structured(
                "EVENT",
                kind="SLEEP_COUNTDOWN",
                reason=reason,
                time_remaining_s=time_remaining_s,
                threshold_s=f"{threshold:.1f}",
                max_idleness_allowed=int(info.MaxIdlenessAllowed),
                idleness=int(info.Idleness),
                mono=f"{self.mono():.3f}",
            )
            get_trigger("sleep_countdown").fire(self, time_remaining_s)
            return True

        return fired_for_this_countdown
