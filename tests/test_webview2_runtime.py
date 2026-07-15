from __future__ import annotations

import unittest

from kef_app.platform.webview2_runtime import (
    MIN_DOTNET_RELEASE,
    WEBVIEW2_RUNTIME_KEY,
    check_webview2_readiness,
)


class _Key:
    def __init__(self, value: object) -> None:
        self.value = value

    def __enter__(self) -> _Key:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class _Registry:
    HKEY_LOCAL_MACHINE = "HKLM"
    HKEY_CURRENT_USER = "HKCU"

    def __init__(self, values: dict[tuple[str, str, str], object]) -> None:
        self.values = values

    def OpenKey(self, root: str, path: str) -> _Key:
        matches = [value for (candidate_root, candidate_path, _name), value in self.values.items() if (candidate_root, candidate_path) == (root, path)]
        if not matches:
            raise OSError("key not found")
        return _Key(path)

    def QueryValueEx(self, key: _Key, name: str) -> tuple[object, int]:
        for (_root, path, candidate_name), value in self.values.items():
            if path == key.value and candidate_name == name:
                return value, 1
        raise OSError("value not found")


class WebView2RuntimeTests(unittest.TestCase):
    def test_detects_machine_runtime_and_dotnet(self) -> None:
        runtime_path = rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_RUNTIME_KEY}"
        dotnet_path = r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full"
        registry = _Registry({
            ("HKLM", runtime_path, "pv"): "150.0.4078.65",
            ("HKLM", dotnet_path, "Release"): MIN_DOTNET_RELEASE,
        })

        readiness = check_webview2_readiness(registry=registry, is_64_bit=True)

        self.assertTrue(readiness.ready)
        self.assertEqual(readiness.runtime_version, "150.0.4078.65")

    def test_rejects_missing_or_too_old_runtime(self) -> None:
        runtime_path = rf"Software\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_RUNTIME_KEY}"
        dotnet_path = r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full"
        registry = _Registry({
            ("HKCU", runtime_path, "pv"): "85.0.0.0",
            ("HKLM", dotnet_path, "Release"): MIN_DOTNET_RELEASE - 1,
        })

        readiness = check_webview2_readiness(registry=registry, is_64_bit=True)

        self.assertFalse(readiness.ready)
        self.assertTrue(readiness.missing_runtime)
        self.assertTrue(readiness.missing_dotnet)


if __name__ == "__main__":
    unittest.main()
