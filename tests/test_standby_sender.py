from __future__ import annotations

import unittest
from unittest.mock import Mock

from kef_app.controller.standby import PrewarmedStandbySendResult, send_fast_standby
from kef_app.devices.transport.standby_request import FireAndForgetShutdownResult


def prewarmed_result(
    *,
    attempted: bool,
    success: bool = False,
    host_unreachable: bool = False,
) -> PrewarmedStandbySendResult:
    return PrewarmedStandbySendResult(
        attempted=attempted,
        success=success,
        status="sent" if success else "send_failed:OSError",
        host_unreachable=host_unreachable,
    )


def fire_and_forget_result(
    *,
    success: bool,
    all_host_unreachable: bool = False,
) -> FireAndForgetShutdownResult:
    return FireAndForgetShutdownResult(
        success=success,
        attempts=3,
        completed=3,
        pending=0,
        duration_ms=2,
        all_host_unreachable=all_host_unreachable,
    )


class FastStandbySendTests(unittest.TestCase):
    def test_uses_prewarmed_success_without_fire_and_forget(self):
        send_fire_and_forget = Mock()

        result = send_fast_standby(
            "192.168.1.10",
            Mock(return_value=prewarmed_result(attempted=True, success=True)),
            send_fire_and_forget,
        )

        self.assertEqual(result.status, "sent")
        self.assertEqual(result.source, "prewarmed")
        send_fire_and_forget.assert_not_called()

    def test_short_circuits_prewarmed_host_unreachable(self):
        send_fire_and_forget = Mock()

        result = send_fast_standby(
            "192.168.1.10",
            Mock(return_value=prewarmed_result(attempted=True, host_unreachable=True)),
            send_fire_and_forget,
        )

        self.assertEqual(result.status, "host_unreachable")
        self.assertEqual(result.source, "prewarmed")
        send_fire_and_forget.assert_not_called()

    def test_falls_through_to_fire_and_forget_for_other_prewarmed_failure(self):
        result = send_fast_standby(
            "192.168.1.10",
            Mock(return_value=prewarmed_result(attempted=True)),
            Mock(return_value=fire_and_forget_result(success=True)),
        )

        self.assertEqual(result.status, "sent")
        self.assertEqual(result.source, "fire_and_forget")
        self.assertIsNotNone(result.prewarmed)
        self.assertIsNotNone(result.fire_and_forget)

    def test_reports_fire_and_forget_host_unreachable(self):
        result = send_fast_standby(
            "192.168.1.10",
            Mock(return_value=prewarmed_result(attempted=False)),
            Mock(return_value=fire_and_forget_result(success=False, all_host_unreachable=True)),
        )

        self.assertEqual(result.status, "host_unreachable")
        self.assertEqual(result.source, "fire_and_forget")
        self.assertIsNotNone(result.prewarmed)
        self.assertFalse(result.prewarmed.attempted)

    def test_does_not_fall_through_after_send_gate_closes(self):
        send_fire_and_forget = Mock()
        result = send_fast_standby(
            "192.168.1.10",
            Mock(return_value=prewarmed_result(attempted=True)),
            send_fire_and_forget,
            should_continue=Mock(return_value=False),
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.source, "prewarmed")
        send_fire_and_forget.assert_not_called()


if __name__ == "__main__":
    unittest.main()
