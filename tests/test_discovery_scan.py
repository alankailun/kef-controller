from __future__ import annotations

import ipaddress
import logging
import unittest
from unittest.mock import Mock, patch

from kef_app.config import AppConfig
from kef_app.devices.scan.network import build_candidate_networks
from kef_app.devices.scan.scan import discover_ip_by_mac, discover_kef_device_blind, discover_kef_devices
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

    def test_arp_recovery_uses_existing_cache_without_network_sweep(self):
        config = AppConfig()

        with (
            patch("kef_app.devices.scan.scan.read_arp_table", return_value={"10.0.0.222": "84171517AC77"}),
            patch("kef_app.devices.scan.scan.build_candidate_networks") as build_networks,
            patch("kef_app.devices.scan.scan.probe_ip_port") as probe,
        ):
            ip = discover_ip_by_mac("84:17:15:17:AC:77", "10.0.0.222", config, self.make_logger())

        self.assertEqual(ip, "10.0.0.222")
        build_networks.assert_not_called()
        probe.assert_not_called()

    def test_arp_recovery_miss_falls_back_without_network_sweep(self):
        config = AppConfig()

        with (
            patch("kef_app.devices.scan.scan.read_arp_table", return_value={"10.0.0.222": "841715175722"}),
            patch("kef_app.devices.scan.scan.build_candidate_networks") as build_networks,
            patch("kef_app.devices.scan.scan.probe_ip_port") as probe,
        ):
            ip = discover_ip_by_mac("84:17:15:17:AC:77", "10.0.0.222", config, self.make_logger())

        self.assertIsNone(ip)
        build_networks.assert_not_called()
        probe.assert_not_called()

    def test_manual_scan_identifies_seed_without_tcp_probe(self):
        config = AppConfig()
        seed = "10.0.0.222"
        identity = self.make_identity(seed)
        candidates: list[SpeakerIdentity] = []

        with (
            patch(
                "kef_app.devices.scan.scan.build_candidate_networks",
                return_value=[ipaddress.IPv4Network("10.0.0.220/30")],
            ),
            patch("kef_app.devices.scan.scan.probe_ip_port", return_value=False),
            patch("kef_app.devices.scan.scan.identify_kef_device", return_value=identity) as identify,
        ):
            devices = discover_kef_devices(seed, config, self.make_logger(), on_candidate=candidates.append)

        self.assertEqual([device.ip for device in devices], [seed])
        self.assertEqual([device.ip for device in candidates], [seed])
        identify.assert_called_once_with(seed, config, timeout=1.5)

    def test_manual_scan_can_cancel_after_seed_candidate_before_network_sweep(self):
        config = AppConfig()
        seed = "10.0.0.222"
        identity = self.make_identity(seed)
        scanning = {"continue": True}

        def on_candidate(_speaker: SpeakerIdentity) -> None:
            scanning["continue"] = False

        with (
            patch(
                "kef_app.devices.scan.scan.build_candidate_networks",
                return_value=[ipaddress.IPv4Network("10.0.0.220/30")],
            ),
            patch("kef_app.devices.scan.scan.ThreadPoolExecutor") as executor_cls,
            patch("kef_app.devices.scan.scan.probe_ip_port") as probe,
            patch("kef_app.devices.scan.scan.identify_kef_device", return_value=identity) as identify,
        ):
            devices = discover_kef_devices(
                seed,
                config,
                self.make_logger(),
                on_candidate=on_candidate,
                should_continue=lambda: scanning["continue"],
            )

        self.assertEqual([device.ip for device in devices], [seed])
        identify.assert_called_once_with(seed, config, timeout=1.5)
        executor_cls.assert_not_called()
        probe.assert_not_called()

    def test_manual_scan_retries_seed_after_network_miss(self):
        config = AppConfig()
        seed = "10.0.0.222"
        identity = self.make_identity(seed)

        with (
            patch(
                "kef_app.devices.scan.scan.build_candidate_networks",
                return_value=[ipaddress.IPv4Network("10.0.0.220/30")],
            ),
            patch("kef_app.devices.scan.scan.probe_ip_port", return_value=False),
            patch("kef_app.devices.scan.scan.identify_kef_device", side_effect=[None, identity]) as identify,
            patch("kef_app.devices.scan.scan.time.sleep") as sleep,
        ):
            devices = discover_kef_devices(seed, config, self.make_logger())

        self.assertEqual([device.ip for device in devices], [seed])
        self.assertEqual(identify.call_count, 2)
        sleep.assert_called_once_with(0.35)

    def test_candidate_networks_use_default_route_before_seed_and_extra_networks(self):
        config = AppConfig().with_updates(mac_discovery_extra_cidrs=["172.16.10.0/24"])

        with patch(
            "kef_app.devices.scan.network.get_local_ipv4_candidates",
            return_value=["10.0.0.5"],
        ):
            networks = build_candidate_networks("192.168.50.222", config, self.make_logger())

        self.assertEqual(
            [str(network) for network in networks],
            ["10.0.0.0/24", "192.168.50.0/24", "172.16.10.0/24"],
        )

    def test_candidate_networks_fall_back_to_seed_route_when_default_route_is_missing(self):
        config = AppConfig()

        def route_source(target: str) -> str | None:
            return "192.168.50.10" if target == "192.168.50.222" else None

        with patch("kef_app.devices.scan.network._source_ip_for_route", side_effect=route_source):
            networks = build_candidate_networks("192.168.50.222", config, self.make_logger())

        self.assertEqual([str(network) for network in networks], ["192.168.50.0/24"])

    def test_manual_scan_probes_candidate_networks_in_priority_order(self):
        config = AppConfig()
        executor = Mock()

        with (
            patch(
                "kef_app.devices.scan.scan.build_candidate_networks",
                return_value=[
                    ipaddress.IPv4Network("10.0.0.0/30"),
                    ipaddress.IPv4Network("10.0.1.0/30"),
                ],
            ),
            patch("kef_app.devices.scan.scan.ThreadPoolExecutor") as executor_cls,
            patch("kef_app.devices.scan.scan._reachable_hosts", return_value=[]) as reachable,
            patch("kef_app.devices.scan.scan._identify_hosts") as identify,
        ):
            executor_cls.return_value.__enter__.return_value = executor
            devices = discover_kef_devices(None, config, self.make_logger())

        self.assertEqual(devices, [])
        self.assertEqual(executor_cls.call_count, 2)
        self.assertEqual([call.kwargs for call in executor_cls.call_args_list], [{"max_workers": 2}, {"max_workers": 2}])
        self.assertEqual(
            [call.args[1] for call in reachable.call_args_list],
            [["10.0.0.1", "10.0.0.2"], ["10.0.1.1", "10.0.1.2"]],
        )
        for call in reachable.call_args_list:
            self.assertIs(call.args[0], executor)
            self.assertIs(call.args[2], config)
            self.assertIsNone(call.kwargs.get("should_continue"))
        identify.assert_not_called()

    def test_full_scan_matches_seed_before_broad_scan(self):
        config = AppConfig()
        seed = "10.0.0.222"
        identity = self.make_identity(seed)

        with (
            patch(
                "kef_app.devices.scan.scan.build_candidate_networks",
                return_value=[ipaddress.IPv4Network("10.0.0.220/30")],
            ),
            patch("kef_app.devices.scan.scan.probe_ip_port") as probe,
            patch("kef_app.devices.scan.scan.identify_kef_device", return_value=identity),
        ):
            found = discover_kef_device_blind(identity.mac, seed, config, self.make_logger())

        self.assertIsNotNone(found)
        self.assertEqual(found.ip, seed)
        self.assertEqual(found.matched_by, "target_mac")
        probe.assert_not_called()

    def test_full_scan_probes_candidate_networks_in_priority_order_until_target_matches(self):
        config = AppConfig()
        identity = self.make_identity("10.0.1.2")
        executor = Mock()

        with (
            patch(
                "kef_app.devices.scan.scan.build_candidate_networks",
                return_value=[
                    ipaddress.IPv4Network("10.0.0.0/30"),
                    ipaddress.IPv4Network("10.0.1.0/30"),
                ],
            ),
            patch("kef_app.devices.scan.scan.ThreadPoolExecutor") as executor_cls,
            patch("kef_app.devices.scan.scan._reachable_hosts", side_effect=[[], ["10.0.1.2"]]) as reachable,
            patch("kef_app.devices.scan.scan._identify_hosts", return_value=[identity]) as identify,
        ):
            executor_cls.return_value.__enter__.return_value = executor
            found = discover_kef_device_blind(identity.mac, None, config, self.make_logger())

        self.assertIsNotNone(found)
        self.assertEqual(found.ip, "10.0.1.2")
        self.assertEqual(executor_cls.call_count, 2)
        self.assertEqual([call.kwargs for call in executor_cls.call_args_list], [{"max_workers": 2}, {"max_workers": 2}])
        self.assertEqual(
            [call.args[1] for call in reachable.call_args_list],
            [["10.0.0.1", "10.0.0.2"], ["10.0.1.1", "10.0.1.2"]],
        )
        identify.assert_called_once_with(executor, ["10.0.1.2"], config, should_continue=None)


if __name__ == "__main__":
    unittest.main()
