from __future__ import annotations

import json
import os
from typing import Any

from .config import AppConfig
from .json_storage import write_json_atomic
from ..discovery import normalize_mac
from ..models import normalize_input_source, normalize_name


class UserConfigStore:
    USER_EDITABLE_FIELDS = (
        "backend_name",
        "kef_ip",
        "kef_mac",
        "expected_speaker_name",
        "expected_speaker_mac",
        "auto_discover_kef_ip_by_mac",
        "auto_discover_kef_ip_blind",
        "mac_discovery_subnet_prefix",
        "mac_discovery_extra_cidrs",
        "kef_input",
        "startup_registration_mode",
        "wake_on_startup",
        "startup_delay",
        "resume_wake_delay",
        "socket_timeout",
        "wake_on_unlock_only",
        "unlock_wake_delay",
        "reachability_wait_timeout",
        "reachability_poll_interval",
        "standby_attempt_delays",
        "wake_attempt_delays",
        "suspend_action_lock_timeout",
        "wake_action_lock_timeout",
        "resume_dedup_window",
        "standby_on_sleep",
        "standby_on_lock",
        "lock_standby_action_lock_timeout",
        "lock_standby_dedup_window",
        "log_backup_days",
        "persist_runtime_state",
        "enable_application_restart",
        "fast_exit_on_endsession",
        "endsession_standby_on_shutdown",
        "endsession_standby_action_lock_timeout",
        "endsession_standby_socket_timeout",
        "supported_w2_models",
    )

    def __init__(self, base_config: AppConfig):
        self._base_config = base_config
        self.path = base_config.config_file
        self._startup_messages: list[str] = []

    def load_or_create(self) -> AppConfig:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if not os.path.exists(self.path):
            self.save(self._base_config)
            self._startup_messages.append(
                f"Created a new default user config file | config_file={self.path}"
            )
            return self._base_config.clone()

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("config.json root must be an object")

            loaded = self._apply_to_config(self._base_config.clone(), data)
            if "log_dir" in data:
                self._startup_messages.append(
                    f"Ignored legacy custom log_dir and kept the default log folder | log_dir={loaded.log_dir}"
                )
            self._startup_messages.append(f"Loaded user config | config_file={self.path}")
            return loaded
        except Exception as exc:
            self._startup_messages.append(
                f"Failed to read user config, falling back to defaults | config_file={self.path} | {exc}"
            )
            return self._base_config.clone()

    def save(self, config: AppConfig) -> bool:
        try:
            write_json_atomic(self.path, self._to_user_dict(config), prefix="user_config_")
            return True
        except Exception:
            return False

    def drain_startup_messages(self) -> list[str]:
        messages = self._startup_messages[:]
        self._startup_messages.clear()
        return messages

    def _to_user_dict(self, config: AppConfig) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for field_name in self.USER_EDITABLE_FIELDS:
            value = getattr(config, field_name)
            if isinstance(value, tuple):
                data[field_name] = list(value)
            else:
                data[field_name] = value
        return data

    def _apply_to_config(self, config: AppConfig, data: dict[str, Any]) -> AppConfig:
        for key in self.USER_EDITABLE_FIELDS:
            if key not in data:
                continue

            value = data[key]
            try:
                if key in {"kef_ip", "backend_name"}:
                    setattr(config, key, str(value or ""))
                elif key == "kef_input":
                    setattr(config, key, normalize_input_source(str(value or "")))
                elif key == "startup_registration_mode":
                    mode = str(value or "registry").strip().lower()
                    setattr(config, key, mode if mode in {"auto", "task", "registry"} else "registry")
                elif key in {"kef_mac", "expected_speaker_mac"}:
                    setattr(config, key, normalize_mac(str(value or "")))
                elif key == "expected_speaker_name":
                    setattr(config, key, normalize_name(str(value or "")))
                elif key in {
                    "auto_discover_kef_ip_by_mac",
                    "auto_discover_kef_ip_blind",
                    "wake_on_startup",
                    "wake_on_unlock_only",
                    "standby_on_sleep",
                    "standby_on_lock",
                    "persist_runtime_state",
                    "enable_application_restart",
                    "fast_exit_on_endsession",
                    "endsession_standby_on_shutdown",
                }:
                    setattr(config, key, self._coerce_bool(value))
                elif key in {"mac_discovery_subnet_prefix", "log_backup_days"}:
                    setattr(config, key, int(value))
                elif key in {
                    "startup_delay",
                    "resume_wake_delay",
                    "socket_timeout",
                    "unlock_wake_delay",
                    "reachability_wait_timeout",
                    "reachability_poll_interval",
                    "suspend_action_lock_timeout",
                    "wake_action_lock_timeout",
                    "resume_dedup_window",
                    "lock_standby_action_lock_timeout",
                    "lock_standby_dedup_window",
                    "endsession_standby_action_lock_timeout",
                    "endsession_standby_socket_timeout",
                }:
                    setattr(config, key, float(value))
                elif key in {"mac_discovery_extra_cidrs", "standby_attempt_delays", "wake_attempt_delays"}:
                    if isinstance(value, list):
                        if key == "mac_discovery_extra_cidrs":
                            setattr(config, key, [str(v) for v in value])
                        else:
                            setattr(config, key, [float(v) for v in value])
                elif key == "supported_w2_models":
                    if isinstance(value, list):
                        setattr(config, key, tuple(str(v) for v in value))
            except Exception:
                pass
        return config

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off", ""}:
                return False
        return bool(value)
