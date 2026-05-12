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
        return controller._run_early_standby_trigger(self, reason or self.default_reason)
