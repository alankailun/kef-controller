from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field, fields as dataclass_fields

from .system_config import SystemConfig
from .user_settings import UserSettings


_SYSTEM_CONFIG_FIELD_NAMES = {field.name for field in dataclass_fields(SystemConfig)}
_USER_SETTINGS_FIELD_NAMES = {field.name for field in dataclass_fields(UserSettings)}


@dataclass(slots=True)
class AppConfig:
    system: SystemConfig = field(default_factory=SystemConfig)
    user: UserSettings = field(default_factory=UserSettings)

    def __getattr__(self, name: str):
        if name in _SYSTEM_CONFIG_FIELD_NAMES:
            return getattr(self.system, name)
        if name in _USER_SETTINGS_FIELD_NAMES:
            return getattr(self.user, name)
        raise AttributeError(f"{type(self).__name__!s} has no attribute {name!r}")

    def __setattr__(self, name: str, value):
        if name in {"system", "user"}:
            object.__setattr__(self, name, value)
            return
        if name in _SYSTEM_CONFIG_FIELD_NAMES:
            setattr(self.system, name, value)
            return
        if name in _USER_SETTINGS_FIELD_NAMES:
            setattr(self.user, name, value)
            return
        object.__setattr__(self, name, value)

    def clone(self) -> "AppConfig":
        return copy.deepcopy(self)

    def with_updates(self, **changes) -> "AppConfig":
        updated = self.clone()
        for key, value in changes.items():
            if key not in _SYSTEM_CONFIG_FIELD_NAMES and key not in _USER_SETTINGS_FIELD_NAMES:
                raise AttributeError(f"Unknown config field: {key}")
            setattr(updated, key, value)
        return updated

    @property
    def application_restart_flags(self) -> int:
        return self.system.restart_no_crash | self.system.restart_no_hang

    @property
    def log_file(self) -> str:
        return os.path.join(self.system.log_dir, self.system.log_file_name)

    @property
    def config_file(self) -> str:
        return os.path.join(self.system.app_data_dir, self.system.config_file_name)

    @property
    def state_file(self) -> str:
        return os.path.join(self.system.app_data_dir, self.system.state_file_name)


__all__ = ["AppConfig", "SystemConfig", "UserSettings"]
