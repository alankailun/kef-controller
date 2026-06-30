from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class ControllerTrigger(Protocol):
    name: str

    def fire(self, controller: Any, *args: Any, **kwargs: Any) -> Any:
        ...


@dataclass(frozen=True, slots=True)
class EarlyStandbyTrigger:
    name: str
    default_reason: str
    enabled_field: str
    disabled_cause: str
    action_name: str = "EARLY_STANDBY"

    def fire(self, controller: Any, reason: str | None = None) -> bool:
        event_mono = controller.mono()
        return controller.dispatch_off_pump_standby(
            self.name,
            reason or self.default_reason,
            event_mono,
            callback_started_mono=event_mono,
        )
