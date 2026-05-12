from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SleepCountdownTrigger:
    name: str = "sleep_countdown"
    default_reason: str = "SLEEP_COUNTDOWN"
    action_name: str = "EARLY_STANDBY"

    def fire(self, controller: Any, time_remaining_s: int | None = None) -> bool:
        if not controller.config.standby_on_sleep_countdown:
            controller._log_structured(
                "SKIP",
                action=self.action_name,
                reason=self.default_reason,
                cause="sleep_countdown_standby_disabled",
                time_remaining_s=time_remaining_s,
                mono=f"{controller.mono():.3f}",
            )
            return False
        if controller._is_session_ending():
            controller._log_structured(
                "SKIP",
                action=self.action_name,
                reason=self.default_reason,
                cause="session_ending",
                time_remaining_s=time_remaining_s,
                mono=f"{controller.mono():.3f}",
            )
            return False
        if controller._recently_early_standby_ok():
            controller._log_structured(
                "SKIP",
                action=self.action_name,
                reason=self.default_reason,
                cause="recent_early_standby_ok",
                time_remaining_s=time_remaining_s,
                window_s=f"{controller.config.early_standby_dedup_window:.2f}",
                mono=f"{controller.mono():.3f}",
            )
            return True

        generation = controller._new_generation("sleep", self.default_reason)
        return controller.standby_kef_preemptive(generation, self.default_reason)


SLEEP_COUNTDOWN_TRIGGER = SleepCountdownTrigger()
