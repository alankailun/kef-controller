from __future__ import annotations

from .base import ControllerTrigger, EarlyStandbyTrigger
from .display_off import DISPLAY_OFF_TRIGGER
from .endsession import END_SESSION_TRIGGER, QUERY_END_SESSION_TRIGGER
from .lid_closed import LID_CLOSED_TRIGGER
from .lock import LOCK_TRIGGER
from .suspend import SUSPEND_TRIGGER
from .user_inactive import USER_INACTIVE_TRIGGER


TRIGGERS: dict[str, ControllerTrigger] = {
    trigger.name: trigger
    for trigger in (
        LOCK_TRIGGER,
        USER_INACTIVE_TRIGGER,
        DISPLAY_OFF_TRIGGER,
        LID_CLOSED_TRIGGER,
        SUSPEND_TRIGGER,
        QUERY_END_SESSION_TRIGGER,
        END_SESSION_TRIGGER,
    )
}


def get_trigger(name: str) -> ControllerTrigger:
    return TRIGGERS[name]


__all__ = [
    "ControllerTrigger",
    "DISPLAY_OFF_TRIGGER",
    "END_SESSION_TRIGGER",
    "EarlyStandbyTrigger",
    "LID_CLOSED_TRIGGER",
    "LOCK_TRIGGER",
    "QUERY_END_SESSION_TRIGGER",
    "SUSPEND_TRIGGER",
    "TRIGGERS",
    "USER_INACTIVE_TRIGGER",
    "get_trigger",
]

