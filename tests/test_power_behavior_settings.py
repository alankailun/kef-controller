from __future__ import annotations

import tempfile
import unittest

from kef_app.config import AppConfig, SystemConfig
from kef_app.storage import UserConfigStore
from kef_app.ui.settings.settings_service import (
    SPEAKER_POWER_OPTIONS,
    get_speaker_power_disabled_reason,
    log_power_behavior_state_message,
)


EXPECTED_POWER_BEHAVIOR_KEYS = (
    "wake_on_startup",
    "standby_on_display_off",
    "wake_on_unlock_only",
    "standby_on_lock",
    "standby_on_sleep",
    "endsession_standby_on_shutdown",
)


class PowerBehaviorSettingsTests(unittest.TestCase):
    def make_config(self, directory: str) -> AppConfig:
        return AppConfig(
            system=SystemConfig(
                app_data_dir=directory,
                log_dir=directory,
            )
        )

    def test_power_behavior_options_cover_every_visible_toggle_in_order(self):
        self.assertEqual(tuple(option.key for option in SPEAKER_POWER_OPTIONS), EXPECTED_POWER_BEHAVIOR_KEYS)
        for option in SPEAKER_POWER_OPTIONS:
            self.assertTrue(hasattr(AppConfig(), option.key), option.key)
            self.assertIn(option.key, UserConfigStore.USER_EDITABLE_FIELDS)
            self.assertIn(option.key, UserConfigStore.FIELD_COERCERS)

    def test_power_behavior_flags_round_trip_through_user_config(self):
        values = {
            "wake_on_startup": False,
            "endsession_standby_on_shutdown": False,
            "standby_on_lock": False,
            "wake_on_unlock_only": True,
            "standby_on_sleep": False,
            "standby_on_display_off": True,
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            config = self.make_config(temp_dir).with_updates(**values)
            store = UserConfigStore(config)

            self.assertTrue(store.save(config))

            loaded = UserConfigStore(self.make_config(temp_dir)).load_or_create()

        for key, expected in values.items():
            self.assertIs(getattr(loaded, key), expected)

    def test_disabled_reason_uses_the_matching_toggle_title(self):
        for option in SPEAKER_POWER_OPTIONS:
            self.assertEqual(get_speaker_power_disabled_reason(option.key), f"{option.title} is currently off.")

    def test_power_behavior_log_message_includes_every_toggle_state(self):
        config = AppConfig().with_updates(
            wake_on_startup=False,
            endsession_standby_on_shutdown=True,
            standby_on_lock=False,
            wake_on_unlock_only=True,
            standby_on_sleep=False,
            standby_on_display_off=True,
        )

        message = log_power_behavior_state_message(config)

        self.assertIn("wake_on_startup=False", message)
        self.assertIn("shutdown_standby=True", message)
        self.assertIn("lock_standby=False", message)
        self.assertIn("wake_after_unlock=True", message)
        self.assertIn("sleep_standby=False", message)
        self.assertIn("display_off_standby=True", message)


if __name__ == "__main__":
    unittest.main()
