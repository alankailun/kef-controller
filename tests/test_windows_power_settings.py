from __future__ import annotations

import ctypes
import unittest
from unittest.mock import patch

from kef_app.platform.windows.api import (
    GUID_LIDSWITCH_STATE_CHANGE,
    LID_CLOSED,
    POWERBROADCAST_SETTING,
    SYSTEM_POWER_INFORMATION,
    SystemPowerInformation,
    decode_power_setting_change,
    read_system_idle_info,
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

    def test_read_system_idle_info_returns_none_on_error(self):
        with patch("kef_app.platform.windows.api.CallNtPowerInformation", return_value=1):
            self.assertIsNone(read_system_idle_info())

    def test_read_system_idle_info_calls_system_power_information(self):
        def fake_call(info_level, _input, _input_size, output, output_size):
            self.assertEqual(info_level, SystemPowerInformation)
            self.assertEqual(output_size, ctypes.sizeof(SYSTEM_POWER_INFORMATION))
            info = ctypes.cast(output, ctypes.POINTER(SYSTEM_POWER_INFORMATION)).contents
            info.MaxIdlenessAllowed = 80
            info.Idleness = 90
            info.TimeRemaining = 4
            info.CoolingMode = 0
            return 0

        with patch("kef_app.platform.windows.api.CallNtPowerInformation", side_effect=fake_call):
            info = read_system_idle_info()

        self.assertIsNotNone(info)
        self.assertEqual(info.TimeRemaining, 4)
        self.assertEqual(info.Idleness, 90)


if __name__ == "__main__":
    unittest.main()
