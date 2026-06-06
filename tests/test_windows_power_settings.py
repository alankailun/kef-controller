from __future__ import annotations

import ctypes
import unittest
from unittest.mock import patch

from kef_app.platform.windows.api import (
    ERROR_NETWORK_UNREACHABLE,
    GUID_CONSOLE_DISPLAY_STATE,
    GUID_LIDSWITCH_STATE_CHANGE,
    LID_CLOSED,
    MONITOR_DISPLAY_OFF,
    MONITOR_DISPLAY_ON,
    POWERBROADCAST_SETTING,
    decode_power_setting_change,
    has_best_route_to_ipv4,
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

    def test_decode_console_display_state_power_setting(self):
        off_buffer = _power_setting_lparam(GUID_CONSOLE_DISPLAY_STATE, MONITOR_DISPLAY_OFF)
        off = decode_power_setting_change(ctypes.addressof(off_buffer))
        self.assertIsNotNone(off)
        self.assertEqual(off.name, "GUID_CONSOLE_DISPLAY_STATE")
        self.assertEqual(off.value, MONITOR_DISPLAY_OFF)
        self.assertEqual(off.label, "DisplayOff")

        on_buffer = _power_setting_lparam(GUID_CONSOLE_DISPLAY_STATE, MONITOR_DISPLAY_ON)
        on = decode_power_setting_change(ctypes.addressof(on_buffer))
        self.assertEqual(on.label, "DisplayOn")

    def test_best_route_preflight_reports_available_interface(self):
        def fake_get_best_interface(_address, interface_index):
            ctypes.cast(interface_index, ctypes.POINTER(ctypes.c_ulong)).contents.value = 11
            return 0

        with patch("kef_app.platform.windows.api.GetBestInterfaceEx", side_effect=fake_get_best_interface):
            self.assertTrue(has_best_route_to_ipv4("10.0.0.222"))

    def test_best_route_preflight_reports_explicitly_unreachable_route(self):
        with patch("kef_app.platform.windows.api.GetBestInterfaceEx", return_value=ERROR_NETWORK_UNREACHABLE):
            self.assertFalse(has_best_route_to_ipv4("10.0.0.222"))

    def test_best_route_preflight_fails_open_for_unknown_error(self):
        with patch("kef_app.platform.windows.api.GetBestInterfaceEx", return_value=87):
            self.assertIsNone(has_best_route_to_ipv4("10.0.0.222"))


if __name__ == "__main__":
    unittest.main()
