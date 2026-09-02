from __future__ import annotations

from .base import EarlyStandbyTrigger

LOCK_TRIGGER = EarlyStandbyTrigger(
    name="lock",
    default_reason="WTS_SESSION_LOCK",
    enabled_field="standby_on_lock",
    disabled_cause="lock_standby_disabled",
)

