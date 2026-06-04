from __future__ import annotations

import json
import logging
import tempfile
import unittest
from dataclasses import fields as dataclass_fields
from unittest.mock import patch

from kef_app.config import AppConfig, SystemConfig, UserSettings
from kef_app.config.user_settings import USER_SETTINGS_FLAT_FIELD_NAMES, USER_SETTINGS_SECTION_NAMES
from kef_app.devices.speaker_models import SpeakerIdentity
from kef_app.storage import SpeakerStateStore, UserConfigStore


class UserConfigStoreTests(unittest.TestCase):
    def make_config(self, directory: str) -> AppConfig:
        return AppConfig(
            system=SystemConfig(
                app_data_dir=directory,
                log_dir=directory,
            )
        )

    def test_every_user_setting_is_persisted_and_loaded(self):
        self.assertEqual(tuple(UserConfigStore.USER_EDITABLE_FIELDS), USER_SETTINGS_FLAT_FIELD_NAMES)
        saved = UserConfigStore(AppConfig())._to_user_dict(AppConfig())
        self.assertEqual(tuple(saved), USER_SETTINGS_SECTION_NAMES)
        self.assertEqual([field.name for field in dataclass_fields(UserSettings)], list(USER_SETTINGS_SECTION_NAMES))

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
            self.assertIn("blind_discovery_http_timeout", saved["discovery"])

    def test_legacy_discovery_probe_timeout_is_raised_to_current_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_config = self.make_config(temp_dir)
            path = base_config.config_file
            data = UserConfigStore(base_config)._to_user_dict(base_config)
            data["mac_discovery_probe_timeout"] = 0.20
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle)

            loaded = UserConfigStore(base_config).load_or_create()

            self.assertEqual(loaded.mac_discovery_probe_timeout, 0.30)
            with open(path, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertEqual(saved["discovery"]["mac_discovery_probe_timeout"], 0.30)

    def test_legacy_prewarmed_standby_tuning_is_raised_to_current_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_config = self.make_config(temp_dir)
            path = base_config.config_file
            data = UserConfigStore(base_config)._to_user_dict(base_config)
            data["standby_tuning"]["prewarmed_persist_socket"] = False
            data["standby_tuning"]["prewarmed_keepalive_interval_s"] = 20.0
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(data, handle)

            loaded = UserConfigStore(base_config).load_or_create()

            self.assertTrue(loaded.prewarmed_persist_socket)
            self.assertEqual(loaded.prewarmed_keepalive_interval_s, 5.0)
            with open(path, "r", encoding="utf-8") as handle:
                saved = json.load(handle)
            self.assertTrue(saved["standby_tuning"]["prewarmed_persist_socket"])
            self.assertEqual(saved["standby_tuning"]["prewarmed_keepalive_interval_s"], 5.0)

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

    def test_legacy_expected_mac_migrates_to_target_speaker_mac(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_config = self.make_config(temp_dir)
            data = UserConfigStore(base_config)._to_user_dict(base_config)
            data["kef_mac"] = ""
            data["expected_speaker_mac"] = "AA:BB:CC:DD:EE:FF"
            data["expected_speaker_name"] = "Office Speaker"
            with open(base_config.config_file, "w", encoding="utf-8") as handle:
                json.dump(data, handle)

            loaded = UserConfigStore(base_config).load_or_create()
            saved = UserConfigStore(loaded)._to_user_dict(loaded)

            self.assertEqual(loaded.kef_mac, "AABBCCDDEEFF")
            self.assertNotIn("expected_speaker_mac", saved)
            self.assertNotIn("expected_speaker_name", saved)
            self.assertNotIn("auto_discover_kef_ip_by_mac", saved)
            self.assertNotIn("auto_discover_kef_ip_blind", saved)

    def test_speaker_state_store_skips_unchanged_identity_write(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = self.make_config(temp_dir)
            store = SpeakerStateStore(config, logging.getLogger("tests.speaker_state_store"))
            identity = SpeakerIdentity(
                ip="192.168.1.20",
                mac="AABBCCDDEE01",
                speaker_name="Office Speaker",
                speaker_model="LS50 Wireless II",
                backend="w2",
                matched_by="target_mac",
            )

            with patch("kef_app.storage.speaker_state_store.write_json_atomic") as write:
                self.assertTrue(store.save(identity, source="unit_test_first"))
                self.assertFalse(store.save(identity, source="unit_test_second"))

            self.assertEqual(write.call_count, 1)


if __name__ == "__main__":
    unittest.main()
