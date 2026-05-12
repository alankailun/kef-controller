from __future__ import annotations

import ctypes
import unittest

from kef_app.platform.windows.api import (
    GUID_LIDSWITCH_STATE_CHANGE,
    LID_CLOSED,
    POWERBROADCAST_SETTING,
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
    def test_decode_lid_closed_power_setting(self):
        buffer = _power_setting_lparam(GUID_LIDSWITCH_STATE_CHANGE, LID_CLOSED)

        change = decode_power_setting_change(ctypes.addressof(buffer))

        self.assertIsNotNone(change)
        self.assertEqual(change.name, "GUID_LIDSWITCH_STATE_CHANGE")
        self.assertEqual(change.value, LID_CLOSED)
        self.assertEqual(change.label, "LidClosed")


if __name__ == "__main__":
    unittest.main()
