from __future__ import annotations

import json
import os
from typing import Any, Callable

from .config import AppConfig
from .json_storage import write_json_atomic
from ..discovery import normalize_mac
from ..models import normalize_input_source, normalize_name


def _coerce_string(value: Any) -> str:
    return str(value or "")


def _coerce_startup_mode(value: Any) -> str:
    mode = str(value or "registry").strip().lower()
    if mode not in {"auto", "task", "registry"}:
        raise ValueError(f"unsupported startup mode: {mode!r}")
    return mode


def _coerce_float_list(value: Any) -> list[float]:
    if not isinstance(value, list):
        raise TypeError("expected a list")
    return [float(item) for item in value]


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        raise TypeError("expected a list")
    return [str(item) for item in value]


def _coerce_model_list(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise TypeError("expected a list")
    return tuple(str(item) for item in value)


def _coerce_positive_float(value: Any) -> float:
    result = float(value)
    if result <= 0:
        raise ValueError("expected a positive number")
    return result


def _coerce_positive_int(value: Any) -> int:
    result = int(value)
    if result < 1:
        raise ValueError("expected an integer >= 1")
    return result


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
        "home_external_poll_interval",
        "tray_identity_poll_interval",
        "identity_probe_failure_threshold",
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
        "diagnostic_logging",
        "persist_runtime_state",
        "enable_application_restart",
        "fast_exit_on_endsession",
        "endsession_standby_on_shutdown",
        "endsession_standby_action_lock_timeout",
        "endsession_standby_socket_timeout",
        "supported_w2_models",
    )
    FIELD_COERCERS: dict[str, Callable[[Any], Any]] = {
        "backend_name": _coerce_string,
        "kef_ip": _coerce_string,
        "kef_mac": lambda value: normalize_mac(_coerce_string(value)),
        "expected_speaker_name": lambda value: normalize_name(_coerce_string(value)),
        "expected_speaker_mac": lambda value: normalize_mac(_coerce_string(value)),
        "auto_discover_kef_ip_by_mac": lambda value: UserConfigStore._coerce_bool(value),
        "auto_discover_kef_ip_blind": lambda value: UserConfigStore._coerce_bool(value),
        "mac_discovery_subnet_prefix": int,
        "mac_discovery_extra_cidrs": _coerce_string_list,
        "kef_input": lambda value: normalize_input_source(_coerce_string(value)),
        "startup_registration_mode": _coerce_startup_mode,
        "wake_on_startup": lambda value: UserConfigStore._coerce_bool(value),
        "startup_delay": float,
        "resume_wake_delay": float,
        "socket_timeout": float,
        "wake_on_unlock_only": lambda value: UserConfigStore._coerce_bool(value),
        "unlock_wake_delay": float,
        "reachability_wait_timeout": float,
        "reachability_poll_interval": float,
        "home_external_poll_interval": _coerce_positive_float,
        "tray_identity_poll_interval": _coerce_positive_float,
        "identity_probe_failure_threshold": _coerce_positive_int,
        "standby_attempt_delays": _coerce_float_list,
        "wake_attempt_delays": _coerce_float_list,
        "suspend_action_lock_timeout": float,
        "wake_action_lock_timeout": float,
        "resume_dedup_window": float,
        "standby_on_sleep": lambda value: UserConfigStore._coerce_bool(value),
        "standby_on_lock": lambda value: UserConfigStore._coerce_bool(value),
        "lock_standby_action_lock_timeout": float,
        "lock_standby_dedup_window": float,
        "log_backup_days": int,
        "diagnostic_logging": lambda value: UserConfigStore._coerce_bool(value),
        "persist_runtime_state": lambda value: UserConfigStore._coerce_bool(value),
        "enable_application_restart": lambda value: UserConfigStore._coerce_bool(value),
        "fast_exit_on_endsession": lambda value: UserConfigStore._coerce_bool(value),
        "endsession_standby_on_shutdown": lambda value: UserConfigStore._coerce_bool(value),
        "endsession_standby_action_lock_timeout": float,
        "endsession_standby_socket_timeout": float,
        "supported_w2_models": _coerce_model_list,
    }

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

            coerce = self.FIELD_COERCERS.get(key)
            if coerce is None:
                continue

            try:
                setattr(config, key, coerce(data[key]))
            except Exception as exc:
                self._startup_messages.append(
                    "Ignored invalid user config field | "
                    f"field={key} value={self._format_value_for_log(data[key])} | {exc}"
                )
        return config

    @staticmethod
    def _format_value_for_log(value: Any, limit: int = 120) -> str:
        text = repr(value)
        if len(text) > limit:
            return text[: limit - 3] + "..."
        return text

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
