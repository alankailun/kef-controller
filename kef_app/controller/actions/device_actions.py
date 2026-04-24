from __future__ import annotations

from .device_common import ControllerDeviceCommonMixin, StandbyVerificationError
from .device_controls import ControllerDeviceControlsMixin
from .device_power import ControllerDevicePowerActionsMixin


class ControllerDeviceActionsMixin(
    ControllerDevicePowerActionsMixin,
    ControllerDeviceControlsMixin,
    ControllerDeviceCommonMixin,
):
    pass


__all__ = ["ControllerDeviceActionsMixin", "StandbyVerificationError"]
