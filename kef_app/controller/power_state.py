from __future__ import annotations

import threading
import time
from dataclasses import dataclass


@dataclass(slots=True)
class DisplayOffStandbyIntent:
    """Generation-scoped, cancellable early-standby work.

    ``cancelled`` deliberately lives beside, rather than inside, send_status:
    a display-on event must preserve whether a send could already have reached
    the speaker while still invalidating all later work for the generation.
    """

    generation: int
    trigger_name: str
    reason: str
    event_mono: float
    cancel_event: threading.Event
    send_status: str = "pending"
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class DisplayOffStandbyIntentSnapshot:
    generation: int
    trigger_name: str
    reason: str
    event_mono: float
    cancel_event: threading.Event
    send_status: str


@dataclass(frozen=True, slots=True)
class DeferredDisplayOnWake:
    source_generation: int
    fence_generation: int
    event_mono: float
    reason: str
    previous_send_status: str


_CANCELLABLE_STANDBY_STATUS_TRANSITIONS = {
    "pending": frozenset({"sending", "failed"}),
    "sending": frozenset({"retry_waiting", "sent", "confirmed", "failed"}),
    "retry_waiting": frozenset({"sending", "failed"}),
    "sent": frozenset({"confirmed"}),
}


_UNCONFIRMED_STANDBY_OUTCOMES = frozenset({
    "sent_unconfirmed_prewarmed",
    "sent_unconfirmed_fire_and_forget",
    "sent_skipped_host_unreachable",
})


def standby_outcome_is_confirmed(outcome: str) -> bool:
    return power_action_outcome_is_success(outcome) and outcome not in _UNCONFIRMED_STANDBY_OUTCOMES


def power_action_outcome_is_success(outcome: str) -> bool:
    return outcome.startswith("success") or outcome.startswith("sent_")


class ControllerStateMixin:
    def _advance_generation_locked(self, desired_state: str, reason: str, *, mono: str | None = None) -> int:
        """Advance the desired power generation while ``_state_lock`` is held."""
        self._power.generation += 1
        generation = self._power.generation
        self._power.desired_state = desired_state
        self._power.desired_reason = reason
        self._log_structured(
            "STATE",
            action="POWER_GENERATION",
            desired=desired_state,
            gen=generation,
            reason=reason,
            mono=mono,
        )
        return generation

    def _new_generation(self, desired_state: str, reason: str, *, mono: str | None = None) -> int:
        with self._state_lock:
            return self._advance_generation_locked(desired_state, reason, mono=mono)

    def _current_generation(self) -> int:
        with self._state_lock:
            return self._power.generation

    def _current_desired_state(self) -> tuple[str, str]:
        with self._state_lock:
            return self._power.desired_state, self._power.desired_reason

    def is_system_sleep_pending(self) -> bool:
        """Return only the Windows sleep fence, not prewarm health."""
        with self._state_lock:
            return bool(self._windows_events.system_sleep_pending)

    @staticmethod
    def _display_off_intent_snapshot(intent: DisplayOffStandbyIntent) -> DisplayOffStandbyIntentSnapshot:
        return DisplayOffStandbyIntentSnapshot(
            generation=intent.generation,
            trigger_name=intent.trigger_name,
            reason=intent.reason,
            event_mono=intent.event_mono,
            cancel_event=intent.cancel_event,
            send_status=intent.send_status,
        )

    def _begin_cancellable_standby_intent(
        self,
        trigger_name: str,
        reason: str,
        event_mono: float,
    ) -> DisplayOffStandbyIntentSnapshot:
        """Record minimum state before queueing cancellable early standby.

        This intentionally does not log.  The display-off hot path must signal
        the resident dispatcher before doing diagnostic bookkeeping.
        """
        with self._state_lock:
            previous = self._power.display_off_standby_intent
            if previous is not None and not previous.cancelled:
                previous.cancelled = True
                previous.cancel_event.set()

            self._power.generation += 1
            self._power.desired_state = "sleep"
            self._power.desired_reason = reason
            intent = DisplayOffStandbyIntent(
                generation=self._power.generation,
                trigger_name=trigger_name,
                reason=reason,
                event_mono=event_mono,
                cancel_event=threading.Event(),
            )
            self._power.display_off_standby_intent = intent
            return self._display_off_intent_snapshot(intent)

    def _cancel_display_off_standby_intent(
        self,
        *,
        expected_trigger: str | None = "display_off",
        advance_generation: bool = True,
    ) -> DisplayOffStandbyIntentSnapshot | None:
        """Invalidate an outstanding display-off task without losing send state."""
        with self._state_lock:
            intent = self._power.display_off_standby_intent
            if (
                intent is None
                or intent.cancelled
                or (expected_trigger is not None and intent.trigger_name != expected_trigger)
            ):
                return None

            snapshot = self._display_off_intent_snapshot(intent)
            intent.cancelled = True
            intent.cancel_event.set()
            # This is the cancellation fence used by every socket send and
            # retry.  Do not call _new_generation here: display-on decides its
            # desired state only after it has observed the old send status.
            if advance_generation:
                self._power.generation += 1
            return snapshot

    def _defer_display_on_wake_if_locked(
        self,
        previous_intent: DisplayOffStandbyIntentSnapshot,
        *,
        event_mono: float,
        reason: str,
        expected_generation: int,
    ) -> str:
        """Atomically defer a DisplayOn wake only while its fence is current."""
        with self._state_lock:
            if self._power.generation != expected_generation:
                return "stale_generation"
            if self._power.session_ending:
                return "session_ending"
            if not self._power.session_locked:
                return "session_unlocked"
            self._power.deferred_display_on_wake = DeferredDisplayOnWake(
                source_generation=previous_intent.generation,
                fence_generation=expected_generation,
                event_mono=event_mono,
                reason=reason,
                previous_send_status=previous_intent.send_status,
            )
            return "deferred"

    def _take_deferred_display_on_wake(self) -> tuple[DeferredDisplayOnWake | None, str]:
        """Consume a deferred request and report whether its generation survived."""
        with self._state_lock:
            deferred = self._power.deferred_display_on_wake
            self._power.deferred_display_on_wake = None
            if deferred is None:
                return None, "none"
            if deferred.fence_generation != self._power.generation:
                return deferred, "stale_generation"
            if self._power.session_ending:
                return deferred, "session_ending"
            return deferred, "current"

    def _claim_wake_generation(
        self,
        reason: str,
        *,
        expected_generation: int | None = None,
        mono: str | None = None,
        dedupe: bool = True,
    ) -> tuple[int | None, str]:
        """Deduplicate and create one wake generation under the state lock."""
        with self._state_lock:
            if expected_generation is not None and self._power.generation != expected_generation:
                return None, "stale_generation"
            now = self.mono()
            if dedupe and (
                self._power.last_wake_schedule_mono > 0
                and (now - self._power.last_wake_schedule_mono) < self.config.resume_dedup_window
            ):
                self._log_structured(
                    "SKIP",
                    action="WAKE",
                    reason=reason,
                    cause="wake_event_deduped",
                    window_s=f"{self.config.resume_dedup_window:.2f}",
                    mono=f"{now:.3f}",
                )
                return None, "deduped"
            self._power.last_wake_schedule_mono = now
            generation = self._advance_generation_locked("wake", reason, mono=mono)
            return generation, "claimed"

    def _display_off_intent_is_active(self, generation: int) -> bool:
        with self._state_lock:
            intent = self._power.display_off_standby_intent
            return bool(
                intent is not None
                and intent.generation == generation
                and not intent.cancelled
                and self._power.generation == generation
            )

    def _update_display_off_standby_intent(self, generation: int, send_status: str) -> bool:
        """CAS-style, monotonic state transition for a display-off task."""
        with self._state_lock:
            intent = self._power.display_off_standby_intent
            if (
                intent is None
                or intent.generation != generation
                or intent.cancelled
                or self._power.generation != generation
            ):
                return False
            if send_status not in _CANCELLABLE_STANDBY_STATUS_TRANSITIONS.get(intent.send_status, ()):
                return False
            intent.send_status = send_status
            return True

    def _set_session_ending(self, ending: bool):
        with self._state_lock:
            self._power.session_ending = ending

    def _is_session_ending(self) -> bool:
        with self._state_lock:
            return self._power.session_ending

    def _set_session_locked(self, locked: bool) -> None:
        with self._state_lock:
            self._power.session_locked = locked

    def _is_controller_power_action_active(self) -> bool:
        with self._state_lock:
            return self._power.active_actions > 0

    def is_power_action_active(self) -> bool:
        """Return whether any controller-owned wake or standby action is running."""
        return self._is_controller_power_action_active()

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
                    action="INTERRUPTIBLE_SLEEP",
                    step=label,
                    gen=generation,
                    current_gen=self._current_generation(),
                    reason="generation_changed_during_sleep",
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
                    )
                return "action_lock_timeout"

            if self._action_lock.acquire(timeout=min(0.1, remaining)):
                return "acquired"

    def _should_dedupe_resume_and_mark(self, reason: str) -> bool:
        with self._state_lock:
            now = self.mono()
            if (now - self._power.last_resume_event_mono) < self.config.resume_dedup_window:
                self._log_structured(
                    "SKIP",
                    action="WAKE",
                    reason=reason,
                    cause="resume_event_deduped",
                    window_s=f"{self.config.resume_dedup_window:.2f}",
                    mono=f"{now:.3f}",
                )
                return True
            self._power.last_resume_event_mono = now
            return False
