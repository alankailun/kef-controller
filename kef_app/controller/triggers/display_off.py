from __future__ import annotations

from .base import EarlyStandbyTrigger


DISPLAY_OFF_TRIGGER = EarlyStandbyTrigger(
    name="display_off",
    default_reason="POWER_DISPLAY_OFF",
    enabled_field="standby_on_display_off",
    disabled_cause="display_off_standby_disabled",
)

