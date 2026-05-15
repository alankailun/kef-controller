from __future__ import annotations

import time


class ControllerStateMixin:
    def _new_generation(self, desired_state: str, reason: str) -> int:
        with self._state_lock:
            self._generation += 1
            generation = self._generation
            self._log_structured("STATE", desired=desired_state, gen=generation, reason=reason, mono=f"{self.mono():.3f}")
            return generation

    def _current_generation(self) -> int:
        with self._state_lock:
            return self._generation

    def _clear_recent_early_standby_marker(self):
        with self._state_lock:
            self._early_standby_dedup.clear()

    def _mark_early_standby_success(self):
        with self._state_lock:
            self._early_standby_dedup.mark_success(self.mono())

    def _mark_early_standby_host_unreachable(self):
        with self._state_lock:
            self._early_standby_dedup.mark_host_unreachable(self.mono())

    def _recently_early_standby_host_unreachable(self) -> bool:
        with self._state_lock:
            return self._early_standby_dedup.is_recent_host_unreachable(
                self.mono(),
                self.config.early_standby_dedup_window,
            )

    def _set_session_ending(self, ending: bool):
        with self._state_lock:
            self._session_ending = ending
            if ending:
                self._early_standby_dedup.clear()

    def _is_session_ending(self) -> bool:
        with self._state_lock:
            return self._session_ending

    def _is_controller_power_action_active(self) -> bool:
        with self._state_lock:
            return self._controller_active_power_actions > 0

    def _should_abort_generation(self, generation: int) -> bool:
        return generation != self._current_generation()

    def _interruptible_sleep(self, seconds: float, generation: int, label: str, step: float = 0.05) -> bool:
        deadline = self.mono() + seconds
        while True:
            if self._should_abort_generation(generation):
                self._log_structured(
                    "ABORT",
                    step=label,
                    gen=generation,
                    current_gen=self._current_generation(),
                    reason="generation_changed_during_sleep",
                    mono=f"{self.mono():.3f}",
                )
                return False

            remaining = deadline - self.mono()
            if remaining <= 0:
                return True
            time.sleep(min(step, remaining))

    def _acquire_action_lock_interruptibly(
        self,
        timeout: float,
        generation: int,
        reason: str,
        purpose: str,
        *,
        log_timeout: bool = True,
    ) -> str:
        deadline = self.mono() + timeout
        while True:
            if self._should_abort_generation(generation):
                self._log_structured(
                    "ABORT",
                    action=purpose.upper(),
                    gen=generation,
                    reason=reason,
                    current_gen=self._current_generation(),
                    cause="generation_changed_while_waiting_action_lock",
                    mono=f"{self.mono():.3f}",
                )
                return "stale_generation"

            remaining = deadline - self.mono()
            if remaining <= 0:
                if log_timeout:
                    self._log_structured(
                        "SKIP",
                        action=purpose.upper(),
                        gen=generation,
                        reason=reason,
                        cause="action_lock_timeout",
                        timeout_s=f"{timeout:.2f}",
                        mono=f"{self.mono():.3f}",
                    )
                return "action_lock_timeout"

            if self._action_lock.acquire(timeout=min(0.1, remaining)):
                return "acquired"

    def _should_dedupe_resume_and_mark(self, reason: str) -> bool:
        with self._state_lock:
            now = self.mono()
            if (now - self._last_resume_event_mono) < self.config.resume_dedup_window:
                self._log_structured(
                    "SKIP",
                    action="WAKE",
                    reason=reason,
                    cause="resume_event_deduped",
                    window_s=f"{self.config.resume_dedup_window:.2f}",
                    mono=f"{now:.3f}",
                )
                return True
            self._last_resume_event_mono = now
            return False
