from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SuspendTrigger:
    name: str = "suspend"
    default_reason: str = "PBT_APMSUSPEND"

    def fire(self, controller: Any, reason: str | None = None) -> bool:
        event_mono = controller.mono()
        return controller.dispatch_off_pump_standby(
            self.name,
            reason or self.default_reason,
            event_mono,
            callback_started_mono=event_mono,
            step="dispatch_suspend_standby",
        )


SUSPEND_TRIGGER = SuspendTrigger()
