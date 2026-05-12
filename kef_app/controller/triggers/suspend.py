from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SuspendTrigger:
    name: str = "suspend"
    default_reason: str = "PBT_APMSUSPEND"

    def fire(self, controller: Any, reason: str | None = None) -> bool:
        reason = reason or self.default_reason
        generation = controller._new_generation("sleep", reason)
        if not controller.config.standby_on_sleep:
            controller._log_structured(
                "SKIP",
                action="STANDBY",
                reason=reason,
                cause="sleep_standby_disabled",
                mono=f"{controller.mono():.3f}",
            )
            return False
        if controller.config.suspend_fast_standby_enabled:
            return controller.standby_kef_fast_suspend(generation, reason)
        controller._start_controller_thread(lambda: controller.standby_kef(generation, reason), f"SuspendStandby-{generation}")
        return True


SUSPEND_TRIGGER = SuspendTrigger()

