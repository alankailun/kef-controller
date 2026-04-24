from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import fields as dataclass_fields

from kef_app.config import AppConfig, SystemConfig, UserSettings
from kef_app.storage import UserConfigStore


class UserConfigStoreTests(unittest.TestCase):
    def make_config(self, directory: str) -> AppConfig:
        return AppConfig(
            system=SystemConfig(
                app_data_dir=directory,
                log_dir=directory,
            )
        )

    def test_every_user_setting_is_persisted_and_loaded(self):
        missing = [
            field.name
            for field in dataclass_fields(UserSettings)
            if field.name not in UserConfigStore.USER_EDITABLE_FIELDS
        ]
        self.assertEqual(missing, [])

    def test_every_persisted_user_setting_has_a_coercer(self):
        missing = [
            field_name
            for field_name in UserConfigStore.USER_EDITABLE_FIELDS
            if field_name not in UserConfigStore.FIELD_COERCERS
        ]
        self.assertEqual(missing, [])

    def test_discovery_tuning_fields_round_trip_through_user_config(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_config = self.make_config(temp_dir)
            path = base_config.config_file
            data = UserConfigStore(base_config)._to_user_dict(base_config)
            data.update(
                {
                    "mac_discovery_tcp_port": 8080,
                    "mac_discovery_probe_timeout": 0.35,
                    "mac_discovery_max_workers": 12,
                    "mac_discovery_cooldown": 0,
                    "mac_discovery_max_hosts_per_network": 128,
                    "blind_discovery_http_timeout": 1.25,
                    "blind_discovery_cooldown": 0,
                    "blind_discovery_max_workers": 4,
                }
            )
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle)

            loaded = UserConfigStore(base_config).load_or_create()
            store = UserConfigStore(loaded)
            saved = store._to_user_dict(loaded)

            self.assertEqual(loaded.mac_discovery_tcp_port, 8080)
            self.assertEqual(loaded.mac_discovery_probe_timeout, 0.35)
            self.assertEqual(loaded.mac_discovery_max_workers, 12)
            self.assertEqual(loaded.mac_discovery_cooldown, 0)
            self.assertEqual(loaded.mac_discovery_max_hosts_per_network, 128)
            self.assertEqual(loaded.blind_discovery_http_timeout, 1.25)
            self.assertEqual(loaded.blind_discovery_cooldown, 0)
            self.assertEqual(loaded.blind_discovery_max_workers, 4)
            self.assertIn("blind_discovery_http_timeout", saved)

    def test_invalid_input_source_is_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_config = self.make_config(temp_dir)
            data = UserConfigStore(base_config)._to_user_dict(base_config)
            data["kef_input"] = "not-a-speaker-input"
            with open(base_config.config_file, "w", encoding="utf-8") as handle:
                json.dump(data, handle)

            loaded = UserConfigStore(base_config).load_or_create()

            self.assertEqual(loaded.kef_input, base_config.kef_input)

    def test_invalid_numeric_values_are_ignored(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_config = self.make_config(temp_dir)
            data = UserConfigStore(base_config)._to_user_dict(base_config)
            data["socket_timeout"] = -1
            data["mac_discovery_tcp_port"] = 70000
            with open(base_config.config_file, "w", encoding="utf-8") as handle:
                json.dump(data, handle)

            loaded = UserConfigStore(base_config).load_or_create()

            self.assertEqual(loaded.socket_timeout, base_config.socket_timeout)
            self.assertEqual(loaded.mac_discovery_tcp_port, base_config.mac_discovery_tcp_port)


if __name__ == "__main__":
    unittest.main()
