from __future__ import annotations

import json
import math
import os
from dataclasses import fields as dataclass_fields, is_dataclass
from typing import Any, Callable, ClassVar

from ..config import AppConfig
from ..config.user_settings import (
    USER_SETTINGS_FIELD_PATHS,
    USER_SETTINGS_FLAT_FIELD_NAMES,
    USER_SETTINGS_SECTION_NAMES,
)
from .json_file import write_json_atomic
from ..devices.speaker_models import INPUT_SOURCE_OPTIONS, normalize_input_source
from ..devices.speaker_models import normalize_mac


_USER_SETTINGS_FIELD_NAMES = USER_SETTINGS_FLAT_FIELD_NAMES
_USER_SETTINGS_SECTION_FIELDS = {
    section_name: tuple(
        field_name
        for field_name, path in USER_SETTINGS_FIELD_PATHS.items()
        if path[0] == section_name
    )
    for section_name in USER_SETTINGS_SECTION_NAMES
}
_CONFIGURABLE_INPUT_SOURCES = {value for _, value in INPUT_SOURCE_OPTIONS}
# Added 2026-05. Safe to remove after 2026-11 once released installs have auto-rewritten config.json.
_LEGACY_MAC_DISCOVERY_PROBE_TIMEOUT = 0.20
_DEFAULT_MAC_DISCOVERY_PROBE_TIMEOUT = 0.30
_LEGACY_PREWARMED_PERSIST_SOCKET = False
_LEGACY_PREWARMED_KEEPALIVE_INTERVAL_S = 20.0
_DEFAULT_PREWARMED_PERSIST_SOCKET = True
_DEFAULT_PREWARMED_KEEPALIVE_INTERVAL_S = 5.0


def _coerce_string(value: Any) -> str:
    return str(value or "")


def _coerce_startup_mode(value: Any) -> str:
    mode = str(value or "registry").strip().lower()
    if mode == "auto":
        return "task"
    if mode not in {"off", "task", "registry"}:
        raise ValueError(f"unsupported startup mode: {mode!r}")
    return mode


def _coerce_ui_language(value: Any) -> str:
    language = str(value or "zh").strip().lower()
    if language not in {"zh", "en"}:
        raise ValueError(f"unsupported UI language: {language!r}")
    return language


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


def _coerce_log_level(value: Any) -> str:
    level = str(value or "INFO").strip().upper()
    if level not in {"DEBUG", "INFO", "WARNING", "ERROR"}:
        raise ValueError(f"unsupported log level: {level!r}")
    return level


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
    FIELD_COERCERS: ClassVar[dict[str, Callable[[Any], Any]]] = {
        "ui_language": _coerce_ui_language,
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
        "wake_on_display_on": lambda value: UserConfigStore._coerce_bool(value),
        "display_on_wake_delay": _coerce_non_negative_float,
        "reachability_wait_timeout": _coerce_non_negative_float,
        "reachability_poll_interval": _coerce_positive_float,
        "home_external_poll_interval": _coerce_positive_float,
        "home_event_poll_enabled": lambda value: UserConfigStore._coerce_bool(value),
        "home_event_poll_timeout": _coerce_positive_float,
        "speaker_event_recovery_failure_threshold": _coerce_positive_int,
        "tray_identity_poll_interval": _coerce_positive_float,
        "identity_probe_failure_threshold": _coerce_positive_int,
        "wake_attempt_delays": _coerce_non_negative_float_list,
        "suspend_action_lock_timeout": _coerce_non_negative_float,
        "wake_action_lock_timeout": _coerce_non_negative_float,
        "resume_dedup_window": _coerce_non_negative_float,
        "standby_on_sleep": lambda value: UserConfigStore._coerce_bool(value),
        "suspend_fast_standby_enabled": lambda value: UserConfigStore._coerce_bool(value),
        "standby_on_lock": lambda value: UserConfigStore._coerce_bool(value),
        "standby_on_lid_close": lambda value: UserConfigStore._coerce_bool(value),
        "standby_on_display_off": lambda value: UserConfigStore._coerce_bool(value),
        "prewarmed_standby_enabled": lambda value: UserConfigStore._coerce_bool(value),
        "prewarmed_persist_socket": lambda value: UserConfigStore._coerce_bool(value),
        "prewarmed_keepalive_interval_s": _coerce_positive_float,
        "prewarmed_socket_timeout_s": _coerce_positive_float,
        "prewarmed_send_deadline_s": _coerce_positive_float,
        "prewarmed_frozen_send_multiplier": _coerce_positive_float,
        "log_level": _coerce_log_level,
        "log_backup_days": _coerce_log_backup_days,
        "persist_runtime_state": lambda value: UserConfigStore._coerce_bool(value),
        "fast_exit_on_endsession": lambda value: UserConfigStore._coerce_bool(value),
        "endsession_standby_on_shutdown": lambda value: UserConfigStore._coerce_bool(value),
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
            if self._migrate_legacy_prewarmed_standby_tuning(loaded, data):
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
        for section_name in USER_SETTINGS_SECTION_NAMES:
            section = getattr(config.user, section_name)
            section_data: dict[str, Any] = {}
            for field in dataclass_fields(section):
                section_data[field.name] = self._json_value(getattr(section, field.name))
            data[section_name] = section_data
        return data

    def _apply_to_config(self, config: AppConfig, data: dict[str, Any]) -> AppConfig:
        flat_data = self._flatten_user_data(data)
        for key in self.USER_EDITABLE_FIELDS:
            if key not in flat_data:
                continue

            coerce = self.FIELD_COERCERS.get(key)
            if coerce is None:
                continue

            try:
                setattr(config, key, coerce(flat_data[key]))
            except Exception as exc:
                self._startup_messages.append(
                    "Ignored invalid user config field | "
                    f"field={key} value={self._format_value_for_log(flat_data[key])} | {exc}"
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
        raw_value = self._raw_user_value(data, "mac_discovery_probe_timeout")
        if raw_value is None:
            return False
        try:
            probe_timeout = float(raw_value)
        except Exception:
            return False
        if not math.isclose(probe_timeout, _LEGACY_MAC_DISCOVERY_PROBE_TIMEOUT, rel_tol=0.0, abs_tol=1e-9):
            return False

        config.mac_discovery_probe_timeout = _DEFAULT_MAC_DISCOVERY_PROBE_TIMEOUT
        self._startup_messages.append(
            "Raised legacy MAC discovery probe timeout from 0.20s to 0.30s"
        )
        return True

    def _migrate_legacy_prewarmed_standby_tuning(self, config: AppConfig, data: dict[str, Any]) -> bool:
        raw_persist_socket = self._raw_user_value(data, "prewarmed_persist_socket")
        raw_keepalive_interval = self._raw_user_value(data, "prewarmed_keepalive_interval_s")
        if raw_persist_socket is None or raw_keepalive_interval is None:
            return False

        try:
            persist_socket = self._coerce_bool(raw_persist_socket)
            keepalive_interval = float(raw_keepalive_interval)
        except Exception:
            return False

        if persist_socket != _LEGACY_PREWARMED_PERSIST_SOCKET:
            return False
        if not math.isclose(
            keepalive_interval,
            _LEGACY_PREWARMED_KEEPALIVE_INTERVAL_S,
            rel_tol=0.0,
            abs_tol=1e-9,
        ):
            return False

        config.prewarmed_persist_socket = _DEFAULT_PREWARMED_PERSIST_SOCKET
        config.prewarmed_keepalive_interval_s = _DEFAULT_PREWARMED_KEEPALIVE_INTERVAL_S
        self._startup_messages.append(
            "Raised legacy prewarmed standby tuning to persistent sockets with a 5.0s keepalive interval"
        )
        return True

    @classmethod
    def _flatten_user_data(cls, data: dict[str, Any]) -> dict[str, Any]:
        flat: dict[str, Any] = {}
        for section_name, field_names in _USER_SETTINGS_SECTION_FIELDS.items():
            section_data = data.get(section_name)
            if not isinstance(section_data, dict):
                continue
            for field_name in field_names:
                if field_name in section_data:
                    flat[field_name] = section_data[field_name]
        flat.update(
            {
                key: value
                for key, value in data.items()
                if key in USER_SETTINGS_FIELD_PATHS
            }
        )
        return flat

    @classmethod
    def _raw_user_value(cls, data: dict[str, Any], field_name: str) -> Any:
        path = USER_SETTINGS_FIELD_PATHS.get(field_name)
        if path is None:
            return None
        section_name, nested_field_name = path
        return cls._raw_field_value(data, section_name, nested_field_name)

    @classmethod
    def _raw_field_value(cls, data: dict[str, Any], section_name: str, field_name: str) -> Any:
        if field_name in data:
            return data[field_name]
        section_data = data.get(section_name)
        if isinstance(section_data, dict):
            return section_data.get(field_name)
        return None

    @classmethod
    def _json_value(cls, value: Any) -> Any:
        if isinstance(value, tuple):
            return list(value)
        if is_dataclass(value):
            return {
                field.name: cls._json_value(getattr(value, field.name))
                for field in dataclass_fields(value)
            }
        return value

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
