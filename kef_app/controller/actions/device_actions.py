from __future__ import annotations

from .device_common import ControllerDeviceCommonMixin
from .device_controls import ControllerDeviceControlsMixin
from .standby import ControllerDeviceStandbyMixin
from .wake import ControllerDeviceWakeMixin


class ControllerDeviceActionsMixin(
    ControllerDeviceWakeMixin,
    ControllerDeviceStandbyMixin,
    ControllerDeviceControlsMixin,
    ControllerDeviceCommonMixin,
):
    pass


__all__ = ["ControllerDeviceActionsMixin"]
