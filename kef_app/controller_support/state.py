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

    def _is_generation_current(self, generation: int) -> bool:
        return generation == self._current_generation()

    def _clear_pending_unlock_wake(self):
        with self._state_lock:
            self._pending_unlock_wake = False
            self._pending_unlock_deadline_mono = 0.0

    def _clear_lock_only_rewake_state(self):
        with self._state_lock:
            self._lock_prestandby_pending_rewake = False
            self._resume_seen_since_lock = False

    def _clear_recent_lock_standby_marker(self):
        with self._state_lock:
            self._last_lock_standby_ok_mono = 0.0

    def _mark_lock_prestandby_success(self):
        with self._state_lock:
            now = self.mono()
            self._last_lock_standby_ok_mono = now
            self._lock_prestandby_pending_rewake = True
            self._resume_seen_since_lock = False

    def _mark_resume_seen_since_lock(self):
        with self._state_lock:
            self._resume_seen_since_lock = True
            self._lock_prestandby_pending_rewake = False

    def _set_session_ending(self, ending: bool):
        with self._state_lock:
            self._session_ending = ending
            if ending:
                self._pending_unlock_wake = False
                self._pending_unlock_deadline_mono = 0.0
                self._lock_prestandby_pending_rewake = False
                self._resume_seen_since_lock = False
                self._last_lock_standby_ok_mono = 0.0

    def _is_session_ending(self) -> bool:
        with self._state_lock:
            return self._session_ending

    def _should_abort_generation(self, generation: int) -> bool:
        return not self._is_generation_current(generation)

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

    def _acquire_action_lock_interruptibly(self, timeout: float, generation: int, reason: str, purpose: str) -> bool:
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
                return False

            remaining = deadline - self.mono()
            if remaining <= 0:
                self._log_structured(
                    "SKIP",
                    action=purpose.upper(),
                    gen=generation,
                    reason=reason,
                    cause="action_lock_timeout",
                    timeout_s=f"{timeout:.2f}",
                    mono=f"{self.mono():.3f}",
                )
                return False

            if self._action_lock.acquire(timeout=min(0.1, remaining)):
                return True

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
