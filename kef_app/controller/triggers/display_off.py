from __future__ import annotations

from typing import Any

from .base import EarlyStandbyTrigger


# Independent toggle (`standby_on_display_off`), decoupled from `standby_on_sleep`:
# display-off standby is a more eager, input-dependent behavior, so users can
# enable/disable it without touching sleep standby. The playback gate (only fire
# when the speaker is known to be not playing) lives in
# ControllerSessionEventsMixin.on_display_off.
class DisplayOffTrigger(EarlyStandbyTrigger):
    def fire(self, controller: Any, event_mono: float | str | None = None, reason: str | None = None) -> bool:
        if isinstance(event_mono, str):
            if reason is None:
                reason = event_mono
            event_mono = None
        return controller.on_display_off(
            controller.mono() if event_mono is None else float(event_mono),
            reason or self.default_reason,
        )


DISPLAY_OFF_TRIGGER = DisplayOffTrigger(
    name="display_off",
    default_reason="DISPLAY_OFF",
    enabled_field="standby_on_display_off",
    disabled_cause="display_off_standby_disabled",
)
