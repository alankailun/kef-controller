from __future__ import annotations

import time

class ControllerStateMixin:
    def _new_generation(self, desired_state: str, reason: str, *, mono: str | None = None) -> int:
        with self._state_lock:
            self._generation += 1
            generation = self._generation
            self._desired_state = desired_state
            self._desired_reason = reason
            self._log_structured("STATE", desired=desired_state, gen=generation, reason=reason, mono=mono or f"{self.mono():.3f}")
            return generation

    def _current_generation(self) -> int:
        with self._state_lock:
            return self._generation

    def _current_desired_state(self) -> tuple[str, str]:
        with self._state_lock:
            return self._desired_state, self._desired_reason

    def _set_session_ending(self, ending: bool):
        with self._state_lock:
            self._session_ending = ending

    def _is_session_ending(self) -> bool:
        with self._state_lock:
            return self._session_ending

    def _set_session_locked(self, locked: bool) -> None:
        with self._state_lock:
            self._session_locked = locked

    def _is_session_locked(self) -> bool:
        with self._state_lock:
            return self._session_locked

    def _is_controller_power_action_active(self) -> bool:
        with self._state_lock:
            return self._controller_active_power_actions > 0

    def _should_abort_generation(self, generation: int) -> bool:
        return generation != self._current_generation()

    def _bounded_standby_abort_reason(
        self,
        *,
        deadline_mono: float | None,
        generation: int | None,
        check_deadline: bool = True,
    ) -> str:
        if check_deadline and deadline_mono is not None and self.mono() >= deadline_mono:
            return "deadline_exceeded"
        if generation is not None and self._should_abort_generation(generation):
            return "stale_generation"
        return ""

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

    def _should_dedupe_wake_schedule_and_mark(self, reason: str) -> bool:
        with self._state_lock:
            now = self.mono()
            if (
                self._last_wake_schedule_mono > 0
                and (now - self._last_wake_schedule_mono) < self.config.resume_dedup_window
            ):
                self._log_structured(
                    "SKIP",
                    action="WAKE",
                    reason=reason,
                    cause="wake_event_deduped",
                    window_s=f"{self.config.resume_dedup_window:.2f}",
                    mono=f"{now:.3f}",
                )
                return True
            self._last_wake_schedule_mono = now
            return False

    def _mark_wake_scheduled(self) -> None:
        with self._state_lock:
            self._last_wake_schedule_mono = self.mono()
