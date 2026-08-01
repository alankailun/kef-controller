from __future__ import annotations

import ast
import logging
import unittest
from pathlib import Path

from kef_app.structured_logging import STRUCTURED_LOG_TAGS, log_structured


_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_APP_ROOT = _PROJECT_ROOT / "kef_app"
_RAW_LOG_METHODS = {"debug", "info", "warning", "error", "critical", "exception"}
_STRUCTURED_HELPERS = {"_log_structured", "_log_standby", "write"}
_DEPRECATED_FIELD_ALIASES = {
    "source",
    "old_ip",
    "new_ip",
    "old_mac",
    "new_mac",
    "input",
    "input_source",
    "new_input",
    "normalized_input",
    "known_mac",
    "mac",
}


def _is_structured_call(node: ast.Call) -> bool:
    if isinstance(node.func, ast.Name):
        return node.func.id == "log_structured"
    if not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr in {"_log_structured", "_log_standby"}:
        return True
    # BoundStructuredLogger.write always receives its tag as the first literal
    # argument.  This deliberately excludes normal file-like ``.write`` calls.
    return (
        node.func.attr == "write"
        and bool(node.args)
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and node.args[0].value in STRUCTURED_LOG_TAGS
    )


class StructuredLoggingContractTests(unittest.TestCase):
    def test_step_and_skip_are_visible_at_info(self) -> None:
        logger = logging.getLogger("tests.structured_logging.visibility")
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(logging.INFO)

        captured: list[logging.LogRecord] = []

        class Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(record)

        handler = Capture()
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)

        log_structured(logger, "STEP", action="TEST", step="visible", status="ok")
        log_structured(logger, "SKIP", action="TEST", cause="visible")

        self.assertEqual([record.levelno for record in captured], [logging.INFO, logging.INFO])

    def test_application_code_uses_one_structured_log_wire_format(self) -> None:
        raw_log_calls: list[str] = []
        deprecated_fields: list[str] = []
        redundant_info_overrides: list[str] = []
        invalid_tags: list[str] = []
        missing_action: list[str] = []

        for path in _APP_ROOT.rglob("*.py"):
            if path.name == "structured_logging.py":
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Attribute) and node.func.attr in _RAW_LOG_METHODS:
                    raw_log_calls.append(f"{path.relative_to(_PROJECT_ROOT)}:{node.lineno}")
                if not _is_structured_call(node):
                    continue

                if isinstance(node.func, ast.Name) and node.func.id == "log_structured":
                    if "action" not in {keyword.arg for keyword in node.keywords if keyword.arg}:
                        missing_action.append(f"{path.relative_to(_PROJECT_ROOT)}:{node.lineno}")

                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    if node.args[0].value not in STRUCTURED_LOG_TAGS:
                        invalid_tags.append(f"{path.relative_to(_PROJECT_ROOT)}:{node.lineno}")
                for keyword in node.keywords:
                    if keyword.arg in _DEPRECATED_FIELD_ALIASES:
                        deprecated_fields.append(
                            f"{path.relative_to(_PROJECT_ROOT)}:{node.lineno}:{keyword.arg}"
                        )
                    if (
                        keyword.arg == "log_level"
                        and isinstance(keyword.value, ast.Constant)
                        and keyword.value.value == "info"
                    ):
                        redundant_info_overrides.append(f"{path.relative_to(_PROJECT_ROOT)}:{node.lineno}")

        self.assertEqual(raw_log_calls, [])
        self.assertEqual(deprecated_fields, [])
        self.assertEqual(redundant_info_overrides, [])
        self.assertEqual(invalid_tags, [])
        self.assertEqual(missing_action, [])


if __name__ == "__main__":
    unittest.main()
