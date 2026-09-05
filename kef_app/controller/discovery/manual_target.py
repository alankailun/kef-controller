from __future__ import annotations

import re

from ...devices.scan.network import is_routable_ipv4, probe_ip_port
from ...devices.scan.scan import discover_ip_by_mac, discover_kef_device_blind
from ...devices.speaker_models import SpeakerIdentity, normalize_mac
from .identity_helpers import TargetValidationResult


class ControllerManualTargetMixin:
    def validate_manual_target(self, ip: str, target_mac: str, reason: str, trigger: str) -> TargetValidationResult:
        requested_ip = str(ip or "").strip()
        requested_mac = normalize_mac(target_mac)

        if requested_ip and not is_routable_ipv4(requested_ip):
            return TargetValidationResult("invalid_ip", requested_ip=requested_ip, requested_mac=requested_mac)
        if target_mac and (len(requested_mac) != 12 or not re.fullmatch(r"[0-9A-Fa-f:.\s-]+", target_mac)):
            return TargetValidationResult("invalid_mac", requested_ip=requested_ip, requested_mac=requested_mac)
        if not requested_ip and not requested_mac:
            return TargetValidationResult("empty", requested_ip="", requested_mac="")

        if requested_ip:
            info = self.inspect_kef_identity_at_ip(requested_ip, reason=reason, trigger=f"{trigger}_ip")
            if info:
                if requested_mac:
                    if info.mac and info.mac != requested_mac:
                        return TargetValidationResult(
                            "mac_mismatch",
                            requested_ip=requested_ip,
                            requested_mac=requested_mac,
                            identity=info,
                        )
                    if not info.mac:
                        return TargetValidationResult(
                            "mac_unverified",
                            requested_ip=requested_ip,
                            requested_mac=requested_mac,
                            identity=info,
                        )
                return TargetValidationResult(
                    "verified",
                    requested_ip=requested_ip,
                    requested_mac=requested_mac,
                    identity=info,
                )

            if probe_ip_port(requested_ip, self.config.mac_discovery_tcp_port, self.config.mac_discovery_probe_timeout):
                return TargetValidationResult("not_kef", requested_ip=requested_ip, requested_mac=requested_mac)

        if requested_mac:
            recovered = self._recover_identity_for_manual_target(
                requested_mac,
                seed_ip=requested_ip,
                reason=reason,
                trigger=f"{trigger}_recover",
            )
            if recovered:
                return TargetValidationResult(
                    "recovered",
                    requested_ip=requested_ip,
                    requested_mac=requested_mac,
                    identity=recovered,
                )
            return TargetValidationResult("mac_not_found", requested_ip=requested_ip, requested_mac=requested_mac)

        return TargetValidationResult("unreachable", requested_ip=requested_ip, requested_mac=requested_mac)

    def _recover_identity_for_manual_target(
        self,
        target_mac: str,
        seed_ip: str,
        reason: str,
        trigger: str,
    ) -> SpeakerIdentity | None:
        discovered_ip = discover_ip_by_mac(target_mac, seed_ip, self.config, self.log)
        if discovered_ip:
            info = self.inspect_kef_identity_at_ip(discovered_ip, reason=reason, trigger=f"{trigger}_mac_ip")
            if info and info.mac == target_mac:
                return info.with_match("target_mac")

        identity = discover_kef_device_blind(target_mac, seed_ip, self.config, self.log)
        if identity and identity.mac == target_mac:
            return identity

        return None
