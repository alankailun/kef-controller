from __future__ import annotations

from .identity_probe import ControllerIdentityProbeMixin
from .identity_helpers import (
    TargetValidationResult,
    can_use_cached_current_target,
    merge_identity_details,
    missing_identity_fields,
)
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


__all__ = [
    "ControllerDiscoveryMixin",
    "ControllerDiscoveryRecoveryMixin",
    "ControllerIdentityProbeMixin",
    "ControllerIdentityStateMixin",
    "ControllerManualTargetMixin",
    "TargetValidationResult",
    "can_use_cached_current_target",
    "merge_identity_details",
    "missing_identity_fields",
]
