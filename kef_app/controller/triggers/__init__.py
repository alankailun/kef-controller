from __future__ import annotations

from .base import ControllerTrigger, EarlyStandbyTrigger
from .endsession import END_SESSION_TRIGGER, QUERY_END_SESSION_TRIGGER
from .lid_closed import LID_CLOSED_TRIGGER
from .lock import LOCK_TRIGGER
from .sleep_countdown import SLEEP_COUNTDOWN_TRIGGER
from .suspend import SUSPEND_TRIGGER


TRIGGERS: dict[str, ControllerTrigger] = {
    trigger.name: trigger
    for trigger in (
        LOCK_TRIGGER,
        LID_CLOSED_TRIGGER,
        SLEEP_COUNTDOWN_TRIGGER,
        SUSPEND_TRIGGER,
        QUERY_END_SESSION_TRIGGER,
        END_SESSION_TRIGGER,
    )
}


def get_trigger(name: str) -> ControllerTrigger:
    return TRIGGERS[name]


__all__ = [
    "ControllerTrigger",
    "END_SESSION_TRIGGER",
    "EarlyStandbyTrigger",
    "LID_CLOSED_TRIGGER",
    "LOCK_TRIGGER",
    "QUERY_END_SESSION_TRIGGER",
    "SLEEP_COUNTDOWN_TRIGGER",
    "SUSPEND_TRIGGER",
    "TRIGGERS",
    "get_trigger",
]
