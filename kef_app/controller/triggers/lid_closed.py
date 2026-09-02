from __future__ import annotations

from .base import EarlyStandbyTrigger

LID_CLOSED_TRIGGER = EarlyStandbyTrigger(
    name="lid_closed",
    default_reason="POWER_LID_CLOSED",
    enabled_field="standby_on_lid_close",
    disabled_cause="lid_close_standby_disabled",
)

