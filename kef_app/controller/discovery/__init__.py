from __future__ import annotations

from .identity_probe import ControllerIdentityProbeMixin
from .manual_target import ControllerManualTargetMixin
from .recovery import ControllerDiscoveryRecoveryMixin
from .state import ControllerIdentityStateMixin


class ControllerDiscoveryMixin(
    ControllerDiscoveryRecoveryMixin,
    ControllerManualTargetMixin,
    ControllerIdentityProbeMixin,
    ControllerIdentityStateMixin,
):
    pass


__all__ = ["ControllerDiscoveryMixin"]
