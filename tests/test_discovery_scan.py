from __future__ import annotations

import ipaddress
import logging
import unittest
from unittest.mock import patch

from kef_app.config import AppConfig
from kef_app.devices.discovery.scan import discover_kef_device_blind, discover_kef_devices
from kef_app.devices.speaker_models import SpeakerIdentity


class DiscoveryScanTests(unittest.TestCase):
    def make_identity(self, ip: str = "10.0.0.222", mac: str = "84171517AC77") -> SpeakerIdentity:
        return SpeakerIdentity(
            ip=ip,
            mac=mac,
            mac_display="84:17:15:17:AC:77",
            speaker_name="LS50 Wireless II-17ac77",
            speaker_model="LS50 Wireless II",
            firmware_version="LS50 Wireless II",
            backend="w2",
        )

    def make_logger(self):
        logger = logging.getLogger(f"tests.discovery_scan.{self._testMethodName}")
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        return logger

    def test_manual_scan_identifies_seed_without_tcp_probe(self):
        config = AppConfig()
        seed = "10.0.0.222"
        identity = self.make_identity(seed)

        with (
            patch(
                "kef_app.devices.discovery.scan.build_candidate_networks",
                return_value=[ipaddress.IPv4Network("10.0.0.220/30")],
            ),
            patch("kef_app.devices.discovery.scan.probe_ip_port", return_value=False),
            patch("kef_app.devices.discovery.scan.identify_kef_device", return_value=identity) as identify,
        ):
            devices = discover_kef_devices(seed, config, self.make_logger())

        self.assertEqual([device.ip for device in devices], [seed])
        identify.assert_called_once_with(seed, config, timeout=1.5)

    def test_manual_scan_retries_seed_after_network_miss(self):
        config = AppConfig()
        seed = "10.0.0.222"
        identity = self.make_identity(seed)

        with (
            patch(
                "kef_app.devices.discovery.scan.build_candidate_networks",
                return_value=[ipaddress.IPv4Network("10.0.0.220/30")],
            ),
            patch("kef_app.devices.discovery.scan.probe_ip_port", return_value=False),
            patch("kef_app.devices.discovery.scan.identify_kef_device", side_effect=[None, identity]) as identify,
            patch("kef_app.devices.discovery.scan.time.sleep") as sleep,
        ):
            devices = discover_kef_devices(seed, config, self.make_logger())

        self.assertEqual([device.ip for device in devices], [seed])
        self.assertEqual(identify.call_count, 2)
        sleep.assert_called_once_with(0.35)

    def test_full_scan_matches_seed_before_broad_scan(self):
        config = AppConfig()
        seed = "10.0.0.222"
        identity = self.make_identity(seed)

        with (
            patch(
                "kef_app.devices.discovery.scan.build_candidate_networks",
                return_value=[ipaddress.IPv4Network("10.0.0.220/30")],
            ),
            patch("kef_app.devices.discovery.scan.probe_ip_port") as probe,
            patch("kef_app.devices.discovery.scan.identify_kef_device", return_value=identity),
        ):
            found = discover_kef_device_blind(identity.mac, seed, config, self.make_logger())

        self.assertIsNotNone(found)
        self.assertEqual(found.ip, seed)
        self.assertEqual(found.matched_by, "target_mac")
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
