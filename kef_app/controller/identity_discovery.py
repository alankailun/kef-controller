from __future__ import annotations

from ..devices.discovery import (
    discover_ip_by_mac,
    discover_kef_device_blind,
    discover_kef_devices,
    identify_kef_device,
    is_routable_ipv4,
    probe_ip_port,
)
from .discovery import (
    ControllerDiscoveryRecoveryMixin,
    ControllerIdentityProbeMixin,
    ControllerIdentityStateMixin,
    ControllerManualTargetMixin,
)


class ControllerDiscoveryMixin(
    ControllerDiscoveryRecoveryMixin,
    ControllerManualTargetMixin,
    ControllerIdentityProbeMixin,
    ControllerIdentityStateMixin,
):
    pass


__all__ = [
    "ControllerDiscoveryMixin",
    "discover_ip_by_mac",
    "discover_kef_device_blind",
    "discover_kef_devices",
    "identify_kef_device",
    "is_routable_ipv4",
    "probe_ip_port",
]
