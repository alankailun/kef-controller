from __future__ import annotations

import json
import math
import os
from dataclasses import fields as dataclass_fields
from typing import Any, Callable

from ..config import AppConfig, UserSettings
from .json_file import write_json_atomic
from ..devices.speaker_models import INPUT_SOURCE_OPTIONS, normalize_input_source
from ..devices.speaker_models import normalize_mac


_USER_SETTINGS_FIELD_NAMES = tuple(field.name for field in dataclass_fields(UserSettings))
_CONFIGURABLE_INPUT_SOURCES = {value for _, value in INPUT_SOURCE_OPTIONS}
_LEGACY_MAC_DISCOVERY_PROBE_TIMEOUT = 0.20
_DEFAULT_MAC_DISCOVERY_PROBE_TIMEOUT = 0.50
_LEGACY_LOCK_STANDBY_DEDUP_WINDOW = 8.0
_DEFAULT_LOCK_STANDBY_DEDUP_WINDOW = 30.0


def _coerce_string(value: Any) -> str:
    return str(value or "")


def _coerce_startup_mode(value: Any) -> str:
    mode = str(value or "registry").strip().lower()
    if mode == "auto":
        return "task"
    if mode not in {"off", "task", "registry"}:
        raise ValueError(f"unsupported startup mode: {mode!r}")
    return mode


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
    if not math.isfinite(result) or result <= 0:
        raise ValueError("expected a positive number")
    return result


def _coerce_non_negative_float(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("expected a non-negative number")
    return result


def _coerce_non_negative_float_list(value: Any) -> list[float]:
    if not isinstance(value, list):
        raise TypeError("expected a list")
    return [_coerce_non_negative_float(item) for item in value]


def _coerce_positive_int(value: Any) -> int:
    result = int(value)
    if result < 1:
        raise ValueError("expected an integer >= 1")
    return result


def _coerce_log_backup_days(value: Any) -> int:
    result = int(value)
    if result < 0:
        raise ValueError("expected an integer >= 0")
    return result


def _coerce_tcp_port(value: Any) -> int:
    result = _coerce_positive_int(value)
    if result > 65535:
        raise ValueError("expected a TCP port <= 65535")
    return result


def _coerce_ipv4_prefix(value: Any) -> int:
    result = int(value)
    if result < 0 or result > 32:
        raise ValueError("expected an IPv4 prefix length between 0 and 32")
    return result


def _coerce_input_source(value: Any) -> str:
    result = normalize_input_source(_coerce_string(value))
    if result and result not in _CONFIGURABLE_INPUT_SOURCES:
        raise ValueError(f"unsupported input source: {result!r}")
    return result


class UserConfigStore:
    USER_EDITABLE_FIELDS = _USER_SETTINGS_FIELD_NAMES
    FIELD_COERCERS: dict[str, Callable[[Any], Any]] = {
        "backend_name": _coerce_string,
        "kef_ip": _coerce_string,
        "kef_mac": lambda value: normalize_mac(_coerce_string(value)),
        "supported_w2_models": _coerce_model_list,
        "mac_discovery_subnet_prefix": _coerce_ipv4_prefix,
        "mac_discovery_extra_cidrs": _coerce_string_list,
        "mac_discovery_tcp_port": _coerce_tcp_port,
        "mac_discovery_probe_timeout": _coerce_positive_float,
        "mac_discovery_max_workers": _coerce_positive_int,
        "mac_discovery_cooldown": _coerce_non_negative_float,
        "mac_discovery_max_hosts_per_network": _coerce_positive_int,
        "blind_discovery_http_timeout": _coerce_positive_float,
        "blind_discovery_cooldown": _coerce_non_negative_float,
        "blind_discovery_max_workers": _coerce_positive_int,
        "kef_input": _coerce_input_source,
        "startup_registration_mode": _coerce_startup_mode,
        "wake_on_startup": lambda value: UserConfigStore._coerce_bool(value),
        "startup_delay": _coerce_non_negative_float,
        "resume_wake_delay": _coerce_non_negative_float,
        "socket_timeout": _coerce_positive_float,
        "wake_on_unlock_only": lambda value: UserConfigStore._coerce_bool(value),
        "unlock_wake_delay": _coerce_non_negative_float,
        "reachability_wait_timeout": _coerce_non_negative_float,
        "reachability_poll_interval": _coerce_positive_float,
        "home_external_poll_interval": _coerce_positive_float,
        "home_event_poll_enabled": lambda value: UserConfigStore._coerce_bool(value),
        "home_event_poll_timeout": _coerce_positive_float,
        "home_event_reconcile_interval": _coerce_positive_float,
        "home_volume_debounce_ms": _coerce_positive_int,
        "speaker_event_recovery_failure_threshold": _coerce_positive_int,
        "tray_identity_poll_interval": _coerce_positive_float,
        "identity_probe_failure_threshold": _coerce_positive_int,
        "wake_attempt_delays": _coerce_non_negative_float_list,
        "suspend_action_lock_timeout": _coerce_non_negative_float,
        "wake_action_lock_timeout": _coerce_non_negative_float,
        "resume_dedup_window": _coerce_non_negative_float,
        "standby_on_sleep": lambda value: UserConfigStore._coerce_bool(value),
        "suspend_fast_standby_enabled": lambda value: UserConfigStore._coerce_bool(value),
        "suspend_fast_standby_action_lock_timeout": _coerce_non_negative_float,
        "suspend_fast_standby_socket_timeout": _coerce_positive_float,
        "standby_on_lock": lambda value: UserConfigStore._coerce_bool(value),
        "standby_on_user_inactive": lambda value: UserConfigStore._coerce_bool(value),
        "standby_on_display_off": lambda value: UserConfigStore._coerce_bool(value),
        "standby_on_lid_close": lambda value: UserConfigStore._coerce_bool(value),
        "lock_standby_action_lock_timeout": _coerce_non_negative_float,
        "lock_standby_dedup_window": _coerce_non_negative_float,
        "log_backup_days": _coerce_log_backup_days,
        "diagnostic_logging": lambda value: UserConfigStore._coerce_bool(value),
        "persist_runtime_state": lambda value: UserConfigStore._coerce_bool(value),
        "fast_exit_on_endsession": lambda value: UserConfigStore._coerce_bool(value),
        "endsession_standby_on_shutdown": lambda value: UserConfigStore._coerce_bool(value),
        "endsession_standby_action_lock_timeout": _coerce_non_negative_float,
        "endsession_standby_socket_timeout": _coerce_positive_float,
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
            loaded = self._migrate_legacy_device_target(loaded, data)
            migrated = False
            if self._migrate_legacy_probe_timeout(loaded, data):
                migrated = True
            if self._migrate_legacy_lock_standby_dedup_window(loaded, data):
                migrated = True
            if migrated:
                self.save(loaded)
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

    def _migrate_legacy_device_target(self, config: AppConfig, data: dict[str, Any]) -> AppConfig:
        legacy_expected_mac = normalize_mac(_coerce_string(data.get("expected_speaker_mac")))
        if legacy_expected_mac and not normalize_mac(config.kef_mac):
            config.kef_mac = legacy_expected_mac
            self._startup_messages.append(
                "Migrated legacy Expected MAC to Target Speaker MAC"
            )

        if data.get("expected_speaker_name") or data.get("expected_speaker_mac"):
            self._startup_messages.append(
                "Cleared legacy Expected Device Name and Expected MAC fields"
            )

        return config

    def _migrate_legacy_probe_timeout(self, config: AppConfig, data: dict[str, Any]) -> bool:
        if "mac_discovery_probe_timeout" not in data:
            return False
        try:
            probe_timeout = float(data["mac_discovery_probe_timeout"])
        except Exception:
            return False
        if not math.isclose(probe_timeout, _LEGACY_MAC_DISCOVERY_PROBE_TIMEOUT, rel_tol=0.0, abs_tol=1e-9):
            return False

        config.mac_discovery_probe_timeout = _DEFAULT_MAC_DISCOVERY_PROBE_TIMEOUT
        self._startup_messages.append(
            "Raised legacy MAC discovery probe timeout from 0.20s to 0.50s"
        )
        return True

    def _migrate_legacy_lock_standby_dedup_window(self, config: AppConfig, data: dict[str, Any]) -> bool:
        if "lock_standby_dedup_window" not in data:
            return False
        try:
            window = float(data["lock_standby_dedup_window"])
        except Exception:
            return False
        if not math.isclose(window, _LEGACY_LOCK_STANDBY_DEDUP_WINDOW, rel_tol=0.0, abs_tol=1e-9):
            return False

        config.lock_standby_dedup_window = _DEFAULT_LOCK_STANDBY_DEDUP_WINDOW
        self._startup_messages.append(
            "Raised legacy lock standby dedup window from 8.00s to 30.00s"
        )
        return True

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
