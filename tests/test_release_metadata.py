from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ReleaseMetadataTests(unittest.TestCase):
    def test_installer_and_executable_versions_match(self) -> None:
        installer = (PROJECT_ROOT / "installer" / "KEF_Controller.iss").read_text(encoding="utf-8")
        version_info = (PROJECT_ROOT / "installer" / "version_info.txt").read_text(encoding="utf-8")

        match = re.search(r'#define AppVersion "([0-9]+(?:\.[0-9]+)+)"', installer)
        self.assertIsNotNone(match)
        version = match.group(1)
        version_tuple = ", ".join((*version.split("."), "0"))

        self.assertIn(f"filevers=({version_tuple})", version_info)
        self.assertIn(f"prodvers=({version_tuple})", version_info)
        self.assertIn(f"StringStruct('FileVersion', '{version}.0')", version_info)
        self.assertIn(f"StringStruct('ProductVersion', '{version}')", version_info)

    def test_current_build_documentation_describes_onedir_output(self) -> None:
        expected_executable = r"dist\KEF Controller\KEF Controller.exe"
        for name in ("README.en.md", "README.zh-CN.md"):
            contents = (PROJECT_ROOT / name).read_text(encoding="utf-8")
            self.assertIn(expected_executable, contents)
            self.assertNotIn("one-file", contents.lower())
            self.assertNotIn("单文件", contents)

        spec = (PROJECT_ROOT / "KEF Controller.spec").read_text(encoding="utf-8")
        self.assertIn("contents_directory='runtime'", spec)
        self.assertIn("COLLECT(", spec)

    def test_windows_system_icu_is_not_shadowed_by_build_environment(self) -> None:
        spec = (PROJECT_ROOT / "KEF Controller.spec").read_text(encoding="utf-8")
        self.assertIn('"icuuc.dll"', spec)
        self.assertIn('"icudt78.dll"', spec)

        installer = (PROJECT_ROOT / "installer" / "KEF_Controller.iss").read_text(encoding="utf-8")
        self.assertIn('[InstallDelete]', installer)
        self.assertIn('Type: filesandordirs; Name: "{app}\\runtime"', installer)


if __name__ == "__main__":
    unittest.main()
