from __future__ import annotations

from .base import EarlyStandbyTrigger


USER_INACTIVE_TRIGGER = EarlyStandbyTrigger(
    name="user_inactive",
    default_reason="POWER_USER_INACTIVE",
    enabled_field="standby_on_user_inactive",
    disabled_cause="user_inactive_standby_disabled",
)

