from __future__ import annotations

import ctypes
import unittest

from kef_app.platform.windows.api import (
    GUID_SESSION_DISPLAY_STATUS,
    GUID_SESSION_USER_PRESENCE,
    POWERBROADCAST_SETTING,
    POWER_MONITOR_OFF,
    POWER_USER_INACTIVE,
    decode_power_setting_change,
)


def _power_setting_lparam(guid, value: int) -> ctypes.Array[ctypes.c_char]:
    size = POWERBROADCAST_SETTING.Data.offset + 4
    buffer = ctypes.create_string_buffer(size)
    setting = ctypes.cast(ctypes.addressof(buffer), ctypes.POINTER(POWERBROADCAST_SETTING)).contents
    setting.PowerSetting = guid
    setting.DataLength = 4
    ctypes.memmove(ctypes.addressof(buffer) + POWERBROADCAST_SETTING.Data.offset, value.to_bytes(4, "little"), 4)
    return buffer


class WindowsPowerSettingsTests(unittest.TestCase):
    def test_decode_user_inactive_power_setting(self):
        buffer = _power_setting_lparam(GUID_SESSION_USER_PRESENCE, POWER_USER_INACTIVE)

        change = decode_power_setting_change(ctypes.addressof(buffer))

        self.assertIsNotNone(change)
        self.assertEqual(change.name, "GUID_SESSION_USER_PRESENCE")
        self.assertEqual(change.value, POWER_USER_INACTIVE)
        self.assertEqual(change.label, "PowerUserInactive")

    def test_decode_display_off_power_setting(self):
        buffer = _power_setting_lparam(GUID_SESSION_DISPLAY_STATUS, POWER_MONITOR_OFF)

        change = decode_power_setting_change(ctypes.addressof(buffer))

        self.assertIsNotNone(change)
        self.assertEqual(change.name, "GUID_SESSION_DISPLAY_STATUS")
        self.assertEqual(change.value, POWER_MONITOR_OFF)
        self.assertEqual(change.label, "PowerMonitorOff")


if __name__ == "__main__":
    unittest.main()
