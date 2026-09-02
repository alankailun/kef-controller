"""Preflight checks for the WebView2 engine required by the web UI.

pywebview silently falls back to the legacy MSHTML engine when its WebView2
prerequisites are absent.  The bundled UI is intentionally modern HTML/CSS/JS,
so that fallback produces a blank window rather than a usable application.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

WEBVIEW2_RUNTIME_KEY = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
MIN_WEBVIEW2_VERSION = (86, 0, 622, 0)
MIN_DOTNET_RELEASE = 394802  # .NET Framework 4.6.2


@dataclass(frozen=True, slots=True)
class WebView2Readiness:
    runtime_version: str | None
    dotnet_release: int | None

    @property
    def ready(self) -> bool:
        return self.runtime_version is not None and (self.dotnet_release or 0) >= MIN_DOTNET_RELEASE

    @property
    def missing_runtime(self) -> bool:
        return self.runtime_version is None

    @property
    def missing_dotnet(self) -> bool:
        return (self.dotnet_release or 0) < MIN_DOTNET_RELEASE


def _version_tuple(value: object) -> tuple[int, ...] | None:
    parts = str(value or "").strip().split(".")
    if not parts or any(not part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts)


def _at_least(version: tuple[int, ...], minimum: tuple[int, ...]) -> bool:
    padded = version + (0,) * max(0, len(minimum) - len(version))
    return padded >= minimum


def _read_registry_value(registry: Any, root: Any, path: str, name: str) -> object | None:
    try:
        with registry.OpenKey(root, path) as key:
            value, _value_type = registry.QueryValueEx(key, name)
            return value
    except OSError:
        return None


def _runtime_registry_paths(is_64_bit: bool) -> tuple[str, str]:
    machine_path = r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients" if is_64_bit else r"SOFTWARE\Microsoft\EdgeUpdate\Clients"
    user_path = r"Software\Microsoft\EdgeUpdate\Clients"
    return (
        machine_path + "\\" + WEBVIEW2_RUNTIME_KEY,
        user_path + "\\" + WEBVIEW2_RUNTIME_KEY,
    )


def check_webview2_readiness(
    *,
    registry: Any | None = None,
    is_64_bit: bool | None = None,
) -> WebView2Readiness:
    """Return whether this machine meets pywebview's EdgeChromium prerequisites."""
    if sys.platform != "win32":
        return WebView2Readiness(runtime_version="non-windows", dotnet_release=MIN_DOTNET_RELEASE)

    if registry is None:
        import winreg as registry  # type: ignore[no-redef]

    if is_64_bit is None:
        is_64_bit = sys.maxsize > 2**32

    machine_path, user_path = _runtime_registry_paths(is_64_bit)
    runtime_version: str | None = None
    for root, path in ((registry.HKEY_LOCAL_MACHINE, machine_path), (registry.HKEY_CURRENT_USER, user_path)):
        value = _read_registry_value(registry, root, path, "pv")
        parsed = _version_tuple(value)
        if parsed is not None and _at_least(parsed, MIN_WEBVIEW2_VERSION):
            runtime_version = str(value)
            break

    dotnet_value = _read_registry_value(
        registry,
        registry.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full",
        "Release",
    )
    try:
        dotnet_release = int(dotnet_value) if dotnet_value is not None else None
    except (TypeError, ValueError):
        dotnet_release = None

    return WebView2Readiness(runtime_version=runtime_version, dotnet_release=dotnet_release)
