from __future__ import annotations

from .base import EarlyStandbyTrigger

# Pure display-off standby, parallel to lid_closed: when the screen turns off,
# put the speaker into standby, gated only by its own standby_on_display_off
# toggle. No playback/audio check.
DISPLAY_OFF_TRIGGER = EarlyStandbyTrigger(
    name="display_off",
    default_reason="DISPLAY_OFF",
    enabled_field="standby_on_display_off",
    disabled_cause="display_off_standby_disabled",
)
