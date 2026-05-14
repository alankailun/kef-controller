from __future__ import annotations

import logging
import unittest
from unittest.mock import Mock, patch

from kef_app.config import AppConfig
from kef_app.controller import KefPowerController
from kef_app.controller.actions.standby import (
    ENDSESSION_STANDBY_POLICY,
    FAST_SUSPEND_STANDBY_POLICY,
    PREEMPTIVE_STANDBY_POLICY,
    STANDARD_STANDBY_POLICY,
)
from kef_app.controller.prewarmed_standby_socket import PrewarmedStandbySendResult
from kef_app.controller.triggers import TRIGGERS
from kef_app.devices.transport import FireAndForgetShutdownResult
from kef_app.devices.transport import is_host_unreachable
from kef_app.devices.speaker_models import SpeakerIdentity
from kef_app.platform.windows import ENDSESSION_CLOSEAPP


class PowerEventLogicTests(unittest.TestCase):
    def make_controller(self, **config_updates) -> KefPowerController:
        config = AppConfig().with_updates(**config_updates)
        logger = logging.getLogger(f"tests.power_event_logic.{self._testMethodName}")
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        return KefPowerController(config, logger)

    @staticmethod
    def host_unreachable_error() -> OSError:
        return OSError("[WinError 10065] A socket operation was attempted to an unreachable host")

    @staticmethod
    def fire_and_forget_result(success: bool, *, all_host_unreachable: bool = False) -> FireAndForgetShutdownResult:
        return FireAndForgetShutdownResult(
            success=success,
            attempts=3,
            completed=3,
            pending=0,
            duration_ms=2,
            errors=() if success else (
                "OSError(10065, 'A socket operation was attempted to an unreachable host')"
                if all_host_unreachable
                else "TimeoutError('timed out')",
            ),
            all_host_unreachable=all_host_unreachable,
        )

    def capture_events(self, controller: KefPowerController) -> list[tuple[str, dict[str, object]]]:
        events: list[tuple[str, dict[str, object]]] = []
        controller.add_event_listener(lambda name, payload: events.append((name, payload)))
        return events

    @staticmethod
    def emitted_outcome(events: list[tuple[str, dict[str, object]]], outcome: str) -> bool:
        return any(
            name == "power_action_finished" and payload.get("success") is True and payload.get("outcome") == outcome
            for name, payload in events
        )

    def test_trigger_registry_contains_power_standby_triggers(self):
        self.assertEqual(
            set(TRIGGERS),
            {
                "lock",
                "lid_closed",
                "suspend",
                "query_end_session",
                "end_session",
            },
        )

    def test_standby_policies_capture_distinct_action_modes(self):
        self.assertEqual(PREEMPTIVE_STANDBY_POLICY.mode, "fast_request")
        self.assertEqual(PREEMPTIVE_STANDBY_POLICY.action, "EARLY_STANDBY")
        self.assertTrue(PREEMPTIVE_STANDBY_POLICY.mark_early_standby_success)
        self.assertFalse(PREEMPTIVE_STANDBY_POLICY.host_unreachable_is_success)
        self.assertEqual(FAST_SUSPEND_STANDBY_POLICY.host_unreachable_outcome, "success_best_effort_host_unreachable")
        self.assertEqual(STANDARD_STANDBY_POLICY.mode, "verified_request")
        self.assertEqual(ENDSESSION_STANDBY_POLICY.mode, "end_session")

    def test_host_unreachable_classifier_does_not_match_connection_refused(self):
        self.assertTrue(is_host_unreachable(self.host_unreachable_error()))
        self.assertFalse(is_host_unreachable(ConnectionRefusedError(10061, "Connection refused")))

    def test_wait_for_input_source_reuses_connector_after_first_poll(self):
        controller = self.make_controller(kef_ip="192.168.1.10")
        controller.get_input_source = Mock(side_effect=[None, "coaxial"])

        with patch("kef_app.controller.actions.device_common.time.sleep"):
            observed = controller._wait_for_input_source("coaxial", timeout=0.5)

        self.assertEqual(observed, "coaxial")
        self.assertEqual(
            [call.kwargs["fresh"] for call in controller.get_input_source.mock_calls],
            [True, False],
        )

    def test_on_startup_delay_can_abort_before_wake(self):
        controller = self.make_controller(wake_on_startup=True, startup_delay=0.5)
        controller._interruptible_sleep = Mock(return_value=False)
        controller.wake_kef = Mock(return_value=True)

        controller.on_startup()

        controller._interruptible_sleep.assert_called_once_with(0.5, 1, "startup_delay")
        controller.wake_kef.assert_not_called()
        self.assertEqual(controller._current_generation(), 1)

    def test_on_startup_skips_when_startup_wake_disabled(self):
        controller = self.make_controller(wake_on_startup=False)
        controller._interruptible_sleep = Mock(return_value=True)
        controller.wake_kef = Mock(return_value=True)

        controller.on_startup()

        controller._interruptible_sleep.assert_not_called()
        controller.wake_kef.assert_not_called()
        self.assertEqual(controller._current_generation(), 0)

    def test_on_startup_wakes_after_configured_delay(self):
        controller = self.make_controller(wake_on_startup=True, startup_delay=0.25)
        controller._interruptible_sleep = Mock(return_value=True)
        controller.wake_kef = Mock(return_value=True)

        controller.on_startup()

        controller._interruptible_sleep.assert_called_once_with(0.25, 1, "startup_delay")
        controller.wake_kef.assert_called_once_with(1, "startup")
        self.assertEqual(controller._current_generation(), 1)

    def test_on_suspend_uses_fast_standby_when_sleep_standby_enabled(self):
        controller = self.make_controller(standby_on_sleep=True)
        controller._start_controller_thread = Mock()
        controller.standby_kef_fast_suspend = Mock(return_value=True)
        controller.standby_kef = Mock(return_value=True)

        controller.on_suspend("PBT_APMSUSPEND")

        controller.standby_kef_fast_suspend.assert_called_once_with(1, "PBT_APMSUSPEND")
        controller.standby_kef.assert_not_called()
        controller._start_controller_thread.assert_not_called()
        self.assertEqual(controller._current_generation(), 1)

    def test_on_suspend_can_use_full_standby_when_fast_path_disabled(self):
        controller = self.make_controller(standby_on_sleep=True, suspend_fast_standby_enabled=False)
        controller._start_controller_thread = lambda target, _thread_name: target()
        controller.standby_kef_fast_suspend = Mock(return_value=True)
        controller.standby_kef = Mock(return_value=True)

        controller.on_suspend("PBT_APMSUSPEND")

        controller.standby_kef_fast_suspend.assert_not_called()
        controller.standby_kef.assert_called_once_with(1, "PBT_APMSUSPEND")
        self.assertEqual(controller._current_generation(), 1)

    def test_on_suspend_refreshes_generation_even_when_sleep_standby_disabled(self):
        controller = self.make_controller(standby_on_sleep=False)
        controller.standby_kef_fast_suspend = Mock(return_value=True)
        controller.standby_kef = Mock(return_value=True)

        controller.on_suspend("PBT_APMSUSPEND")

        controller.standby_kef_fast_suspend.assert_not_called()
        controller.standby_kef.assert_not_called()
        self.assertEqual(controller._current_generation(), 1)

    def test_on_lock_creates_sleep_generation_before_preemptive_standby(self):
        controller = self.make_controller(standby_on_lock=True)
        seen: dict[str, object] = {}

        def fake_preemptive(generation: int, reason: str) -> bool:
            seen["generation"] = generation
            seen["reason"] = reason
            return True

        controller.standby_kef_preemptive = fake_preemptive

        controller.on_lock("WTS_SESSION_LOCK")

        self.assertEqual(seen, {"generation": 1, "reason": "WTS_SESSION_LOCK"})
        self.assertEqual(controller._current_generation(), 1)

    def test_on_lock_skips_when_lock_standby_disabled(self):
        controller = self.make_controller(standby_on_lock=False)
        controller.standby_kef_preemptive = Mock(return_value=True)

        controller.on_lock("WTS_SESSION_LOCK")

        controller.standby_kef_preemptive.assert_not_called()
        self.assertEqual(controller._current_generation(), 0)

    def test_on_lock_skips_when_session_is_ending(self):
        controller = self.make_controller(standby_on_lock=True)
        controller._set_session_ending(True)
        controller.standby_kef_preemptive = Mock(return_value=True)

        controller.on_lock("WTS_SESSION_LOCK")

        controller.standby_kef_preemptive.assert_not_called()
        self.assertEqual(controller._current_generation(), 0)

    def test_on_lid_closed_triggers_preemptive_standby(self):
        controller = self.make_controller(standby_on_lid_close=True)
        controller.standby_kef_preemptive = Mock(return_value=True)

        result = controller.on_lid_closed("POWER_LID_CLOSED")

        self.assertTrue(result)
        controller.standby_kef_preemptive.assert_called_once_with(1, "POWER_LID_CLOSED")
        self.assertEqual(controller._current_generation(), 1)

    def test_on_resume_waits_for_unlock_when_unlock_wake_enabled(self):
        controller = self.make_controller(wake_on_unlock_only=True)
        controller._schedule_delayed_wake = Mock()

        controller.on_resume("PBT_APMRESUMEAUTOMATIC")

        controller._schedule_delayed_wake.assert_not_called()
        self.assertEqual(controller._current_generation(), 0)

    def test_on_resume_schedules_wake_when_unlock_wake_disabled(self):
        controller = self.make_controller(wake_on_unlock_only=False, resume_wake_delay=0.75)
        controller._schedule_delayed_wake = Mock()

        controller.on_resume("PBT_APMRESUMESUSPEND")

        controller._schedule_delayed_wake.assert_called_once_with(
            1,
            "PBT_APMRESUMESUSPEND",
            0.75,
            "resume_delay",
            "WakeWorker",
        )
        self.assertEqual(controller._current_generation(), 1)

    def test_on_unlock_schedules_wake_when_unlock_wake_enabled(self):
        controller = self.make_controller(wake_on_unlock_only=True, unlock_wake_delay=0.35)
        controller._schedule_delayed_wake = Mock()

        controller.on_unlock("WTS_SESSION_UNLOCK")

        controller._schedule_delayed_wake.assert_called_once_with(
            1,
            "WTS_SESSION_UNLOCK",
            0.35,
            "unlock_delay",
            "UnlockWake",
        )
        self.assertEqual(controller._current_generation(), 1)

    def test_on_unlock_skips_when_unlock_wake_disabled(self):
        controller = self.make_controller(wake_on_unlock_only=False)
        controller._schedule_delayed_wake = Mock()

        controller.on_unlock("WTS_SESSION_UNLOCK")

        controller._schedule_delayed_wake.assert_not_called()
        self.assertEqual(controller._current_generation(), 0)

    def test_on_unlock_skips_when_session_is_ending(self):
        controller = self.make_controller(wake_on_unlock_only=True)
        controller._set_session_ending(True)
        controller._schedule_delayed_wake = Mock()

        controller.on_unlock("WTS_SESSION_UNLOCK")

        controller._schedule_delayed_wake.assert_not_called()
        self.assertEqual(controller._current_generation(), 0)

    def test_end_session_standby_skips_when_shutdown_standby_disabled(self):
        controller = self.make_controller(kef_ip="192.168.1.10", endsession_standby_on_shutdown=False)
        controller.resolve_target = Mock(return_value=True)
        controller._request_shutdown = Mock()

        result = controller.standby_kef_end_session("WM_QUERYENDSESSION", "NONE")

        self.assertFalse(result)
        controller.resolve_target.assert_not_called()
        controller._request_shutdown.assert_not_called()

    def test_end_session_standby_uses_fire_and_forget_when_target_verified(self):
        controller = self.make_controller(kef_ip="192.168.1.10", endsession_standby_on_shutdown=True)
        controller.resolve_target = Mock(return_value=True)
        controller._request_shutdown = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(True))

        result = controller.standby_kef_end_session("WM_QUERYENDSESSION", "NONE")

        self.assertTrue(result)
        controller.resolve_target.assert_called_once()
        controller._send_fire_and_forget_shutdown.assert_called_once_with("192.168.1.10")
        controller._request_shutdown.assert_not_called()

    def test_end_session_standby_falls_back_to_standard_request_when_fire_and_forget_fails(self):
        controller = self.make_controller(kef_ip="192.168.1.10", endsession_standby_on_shutdown=True)
        controller.resolve_target = Mock(return_value=True)
        controller._request_shutdown = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(False))

        result = controller.standby_kef_end_session("WM_QUERYENDSESSION", "NONE")

        self.assertTrue(result)
        controller.resolve_target.assert_called_once()
        controller._send_fire_and_forget_shutdown.assert_called_once_with("192.168.1.10")
        controller._request_shutdown.assert_called_once_with(
            fresh=False,
            timeout=controller.config.endsession_standby_socket_timeout,
        )

    def test_end_session_standby_fire_and_forget_bypasses_busy_action_lock(self):
        controller = self.make_controller(kef_ip="192.168.1.10", endsession_standby_on_shutdown=True)
        controller.resolve_target = Mock(return_value=True)
        controller._request_shutdown = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(True))

        self.assertTrue(controller._action_lock.acquire(blocking=False))
        try:
            result = controller.standby_kef_end_session("WM_QUERYENDSESSION", "NONE")
        finally:
            controller._action_lock.release()

        self.assertTrue(result)
        controller._send_fire_and_forget_shutdown.assert_called_once_with("192.168.1.10")
        controller._request_shutdown.assert_not_called()

    def test_end_session_standby_skips_when_action_lock_is_busy_after_fire_and_forget_fails(self):
        controller = self.make_controller(kef_ip="192.168.1.10", endsession_standby_on_shutdown=True)
        controller.resolve_target = Mock(return_value=True)
        controller._request_shutdown = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(False))

        self.assertTrue(controller._action_lock.acquire(blocking=False))
        try:
            result = controller.standby_kef_end_session("WM_QUERYENDSESSION", "NONE")
        finally:
            controller._action_lock.release()

        self.assertFalse(result)
        controller._send_fire_and_forget_shutdown.assert_called_once_with("192.168.1.10")
        controller._request_shutdown.assert_not_called()

    def test_query_end_session_fast_closeapp_does_not_send_standby(self):
        controller = self.make_controller(fast_exit_on_endsession=True)
        controller.standby_kef_end_session = Mock(return_value=True)

        result = controller.on_query_end_session(1, ENDSESSION_CLOSEAPP)

        self.assertTrue(result)
        controller.standby_kef_end_session.assert_not_called()
        self.assertTrue(controller._is_session_ending())
        self.assertEqual(controller._current_generation(), 1)

    def test_query_end_session_normal_shutdown_sends_standby_and_waits_for_end_session(self):
        controller = self.make_controller(fast_exit_on_endsession=True)
        controller.standby_kef_end_session = Mock(return_value=True)

        result = controller.on_query_end_session(1, 0)

        self.assertFalse(result)
        controller.standby_kef_end_session.assert_called_once_with("WM_QUERYENDSESSION", "NONE")
        self.assertTrue(controller._is_session_ending())
        self.assertEqual(controller._current_generation(), 1)

    def test_preemptive_standby_uses_cached_ip_without_identity_probe(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
            suspend_fast_standby_socket_timeout=0.25,
        )
        generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        controller.resolve_target = Mock(return_value=False)
        controller._ensure_target_identity = Mock(return_value=False)
        controller._perform_standby_request = Mock()
        controller._request_shutdown = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(True))

        result = controller.standby_kef_preemptive(generation, "WTS_SESSION_LOCK")

        self.assertTrue(result)
        controller.resolve_target.assert_not_called()
        controller._ensure_target_identity.assert_not_called()
        controller._perform_standby_request.assert_not_called()
        controller._send_fire_and_forget_shutdown.assert_called_once_with("192.168.1.10")
        controller._request_shutdown.assert_not_called()
        self.assertTrue(controller._recently_early_standby_ok())

    def test_preemptive_standby_uses_prewarmed_send_before_fire_and_forget(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
        )
        events = self.capture_events(controller)
        generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        controller.try_send_prewarmed_standby = Mock(
            return_value=PrewarmedStandbySendResult(
                attempted=True,
                success=True,
                status="sent",
                duration_ms=4,
                target_ip="192.168.1.10",
                mode="short_connection",
            )
        )
        controller._send_fire_and_forget_shutdown = Mock()
        controller._request_shutdown = Mock()

        result = controller.standby_kef_preemptive(generation, "WTS_SESSION_LOCK")

        self.assertTrue(result)
        controller.try_send_prewarmed_standby.assert_called_once_with("192.168.1.10")
        controller._send_fire_and_forget_shutdown.assert_not_called()
        controller._request_shutdown.assert_not_called()
        self.assertTrue(controller._recently_early_standby_ok())
        self.assertTrue(self.emitted_outcome(events, "success_prewarmed_send"))

    def test_preemptive_standby_falls_back_when_prewarmed_send_was_frozen(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
        )
        generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        controller._log_structured = Mock()
        controller.try_send_prewarmed_standby = Mock(
            return_value=PrewarmedStandbySendResult(
                attempted=True,
                success=False,
                status="frozen_during_send",
                duration_ms=420,
                target_ip="192.168.1.10",
                mode="short_connection",
                frozen_s="0.420",
            )
        )
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(True))
        controller._request_shutdown = Mock()

        result = controller.standby_kef_preemptive(generation, "WTS_SESSION_LOCK")

        self.assertTrue(result)
        controller.try_send_prewarmed_standby.assert_called_once_with("192.168.1.10")
        controller._send_fire_and_forget_shutdown.assert_called_once_with("192.168.1.10")
        controller._request_shutdown.assert_not_called()
        self.assertTrue(
            any(
                call.args[:1] == ("WARN",)
                and call.kwargs.get("action") == "EARLY_STANDBY"
                and call.kwargs.get("step") == "prewarmed_standby_send"
                and call.kwargs.get("status") == "frozen_during_send"
                and call.kwargs.get("cause") == "prewarmed_send_deadline_exceeded"
                for call in controller._log_structured.mock_calls
            )
        )

    def test_preemptive_standby_short_circuits_prewarmed_host_unreachable(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
            suspend_fast_standby_socket_timeout=0.25,
        )
        events = self.capture_events(controller)
        generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        controller._log_structured = Mock()
        controller.try_send_prewarmed_standby = Mock(
            return_value=PrewarmedStandbySendResult(
                attempted=True,
                success=False,
                status="send_failed:OSError",
                duration_ms=18,
                target_ip="192.168.1.10",
                mode="short_connection",
                error="OSError(10065, 'A socket operation was attempted to an unreachable host')",
                host_unreachable=True,
            )
        )
        controller._send_fire_and_forget_shutdown = Mock()
        controller._request_shutdown = Mock()

        result = controller.standby_kef_preemptive(generation, "WTS_SESSION_LOCK")

        self.assertFalse(result)
        controller.try_send_prewarmed_standby.assert_called_once_with("192.168.1.10")
        controller._send_fire_and_forget_shutdown.assert_not_called()
        controller._request_shutdown.assert_not_called()
        self.assertFalse(controller._recently_early_standby_ok())
        self.assertTrue(
            any(
                name == "power_action_finished"
                and payload.get("success") is False
                and payload.get("outcome") == "failed_local_network_unavailable"
                for name, payload in events
            )
        )
        self.assertTrue(
            any(
                call.args[:1] == ("WARN",)
                and call.kwargs.get("action") == "EARLY_STANDBY"
                and call.kwargs.get("step") == "prewarmed_standby_send"
                and call.kwargs.get("cause") == "local_route_unavailable_before_suspend"
                and call.kwargs.get("host_unreachable") is True
                for call in controller._log_structured.mock_calls
            )
        )

    def test_preemptive_standby_fire_and_forget_bypasses_busy_action_lock(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
        )
        generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(True))
        controller._request_shutdown = Mock()
        self.assertTrue(controller._action_lock.acquire(blocking=False))
        try:
            result = controller.standby_kef_preemptive(generation, "WTS_SESSION_LOCK")
        finally:
            controller._action_lock.release()

        self.assertTrue(result)
        controller._send_fire_and_forget_shutdown.assert_called_once_with("192.168.1.10")
        controller._request_shutdown.assert_not_called()
        self.assertTrue(controller._recently_early_standby_ok())

    def test_preemptive_standby_treats_host_unreachable_as_local_network_failure(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
            suspend_fast_standby_socket_timeout=0.25,
        )
        events = self.capture_events(controller)
        generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        controller._log_structured = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(False))
        controller._request_shutdown = Mock(side_effect=self.host_unreachable_error())

        result = controller.standby_kef_preemptive(generation, "WTS_SESSION_LOCK")

        self.assertFalse(result)
        controller._send_fire_and_forget_shutdown.assert_called_once_with("192.168.1.10")
        controller._request_shutdown.assert_called_once_with(fresh=False, timeout=0.25)
        self.assertFalse(controller._recently_early_standby_ok())
        self.assertTrue(
            any(
                name == "power_action_finished"
                and payload.get("success") is False
                and payload.get("outcome") == "failed_local_network_unavailable"
                for name, payload in events
            )
        )
        self.assertTrue(
            any(
                call.args[:1] == ("WARN",)
                and call.kwargs.get("action") == "EARLY_STANDBY"
                and call.kwargs.get("status") == "local_network_unavailable_before_suspend"
                for call in controller._log_structured.mock_calls
            )
        )

    def test_preemptive_standby_fire_and_forget_host_unreachable_fails_fast(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
            suspend_fast_standby_socket_timeout=0.25,
        )
        events = self.capture_events(controller)
        generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        controller._log_structured = Mock()
        controller._send_fire_and_forget_shutdown = Mock(
            return_value=self.fire_and_forget_result(False, all_host_unreachable=True)
        )
        controller._request_shutdown = Mock()

        result = controller.standby_kef_preemptive(generation, "WTS_SESSION_LOCK")

        self.assertFalse(result)
        controller._send_fire_and_forget_shutdown.assert_called_once_with("192.168.1.10")
        controller._request_shutdown.assert_not_called()
        self.assertFalse(controller._recently_early_standby_ok())
        self.assertTrue(
            any(
                name == "power_action_finished"
                and payload.get("success") is False
                and payload.get("outcome") == "failed_local_network_unavailable"
                for name, payload in events
            )
        )
        self.assertTrue(
            any(
                call.args[:1] == ("WARN",)
                and call.kwargs.get("action") == "EARLY_STANDBY"
                and call.kwargs.get("status") == "local_network_unavailable_before_suspend"
                and call.kwargs.get("all_host_unreachable") is True
                for call in controller._log_structured.mock_calls
            )
        )

    def test_preemptive_standby_skips_when_recent_early_standby_already_succeeded(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
        )
        events = self.capture_events(controller)
        generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        controller._log_structured = Mock()
        controller._send_fire_and_forget_shutdown = Mock()
        controller._request_shutdown = Mock()
        controller._mark_early_standby_success()

        result = controller.standby_kef_preemptive(generation, "WTS_SESSION_LOCK")

        self.assertTrue(result)
        controller._send_fire_and_forget_shutdown.assert_not_called()
        controller._request_shutdown.assert_not_called()
        self.assertTrue(self.emitted_outcome(events, "skipped_recent_early_standby_ok"))
        self.assertTrue(
            any(
                call.args[:1] == ("SKIP",)
                and call.kwargs.get("action") == "EARLY_STANDBY"
                and call.kwargs.get("cause") == "recent_early_standby_ok"
                for call in controller._log_structured.mock_calls
            )
        )

    def test_preemptive_standby_skips_without_current_ip(self):
        controller = self.make_controller(kef_ip="")
        generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        controller._request_shutdown = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(True))

        result = controller.standby_kef_preemptive(generation, "WTS_SESSION_LOCK")

        self.assertFalse(result)
        controller._send_fire_and_forget_shutdown.assert_not_called()
        controller._request_shutdown.assert_not_called()
        self.assertFalse(controller._recently_early_standby_ok())

    def test_fast_suspend_standby_uses_cached_ip_without_identity_probe(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
            suspend_fast_standby_socket_timeout=0.25,
        )
        generation = controller._new_generation("sleep", "PBT_APMSUSPEND")
        controller.resolve_target = Mock(return_value=False)
        controller._ensure_target_identity = Mock(return_value=False)
        controller._perform_standby_request = Mock()
        controller._request_shutdown = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(True))

        result = controller.standby_kef_fast_suspend(generation, "PBT_APMSUSPEND")

        self.assertTrue(result)
        controller.resolve_target.assert_not_called()
        controller._ensure_target_identity.assert_not_called()
        controller._perform_standby_request.assert_not_called()
        controller._send_fire_and_forget_shutdown.assert_called_once_with("192.168.1.10")
        controller._request_shutdown.assert_not_called()

    def test_fast_suspend_standby_treats_host_unreachable_as_best_effort_success(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
            suspend_fast_standby_socket_timeout=0.25,
        )
        events = self.capture_events(controller)
        generation = controller._new_generation("sleep", "PBT_APMSUSPEND")
        controller._log_structured = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(False))
        controller._request_shutdown = Mock(side_effect=self.host_unreachable_error())

        result = controller.standby_kef_fast_suspend(generation, "PBT_APMSUSPEND")

        self.assertTrue(result)
        controller._send_fire_and_forget_shutdown.assert_called_once_with("192.168.1.10")
        controller._request_shutdown.assert_called_once_with(fresh=False, timeout=0.25)
        self.assertTrue(self.emitted_outcome(events, "success_best_effort_host_unreachable"))
        self.assertFalse(
            any(
                call.args[:1] == ("WARN",) and call.kwargs.get("action") == "STANDBY"
                for call in controller._log_structured.mock_calls
            )
        )

    def test_fast_suspend_standby_assumes_fire_and_forget_host_unreachable_without_standard_fallback(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
            suspend_fast_standby_socket_timeout=0.25,
        )
        events = self.capture_events(controller)
        generation = controller._new_generation("sleep", "PBT_APMSUSPEND")
        controller._log_structured = Mock()
        controller._send_fire_and_forget_shutdown = Mock(
            return_value=self.fire_and_forget_result(False, all_host_unreachable=True)
        )
        controller._request_shutdown = Mock()

        result = controller.standby_kef_fast_suspend(generation, "PBT_APMSUSPEND")

        self.assertTrue(result)
        controller._send_fire_and_forget_shutdown.assert_called_once_with("192.168.1.10")
        controller._request_shutdown.assert_not_called()
        self.assertTrue(self.emitted_outcome(events, "success_best_effort_host_unreachable"))

    def test_fast_suspend_standby_skips_without_current_ip(self):
        controller = self.make_controller(kef_ip="")
        generation = controller._new_generation("sleep", "PBT_APMSUSPEND")
        controller._request_shutdown = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(True))

        result = controller.standby_kef_fast_suspend(generation, "PBT_APMSUSPEND")

        self.assertFalse(result)
        controller._send_fire_and_forget_shutdown.assert_not_called()
        controller._request_shutdown.assert_not_called()

    def test_end_session_standby_skips_when_target_identity_is_not_verified(self):
        controller = self.make_controller(kef_ip="192.168.1.10")
        controller.resolve_target = Mock(return_value=False)
        controller._request_shutdown = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(True))

        result = controller.standby_kef_end_session("unit_test", "flags")

        self.assertFalse(result)
        controller._send_fire_and_forget_shutdown.assert_not_called()
        controller._request_shutdown.assert_not_called()

    def test_apply_configured_device_target_updates_runtime_ip_and_mac(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")

        controller.config.kef_ip = "192.168.1.20"
        controller.config.kef_mac = "AA:BB:CC:DD:EE:02"

        changed = controller.apply_configured_device_target(source="unit_test")

        self.assertTrue(changed)
        self.assertEqual(controller.get_current_kef_ip(), "192.168.1.20")
        self.assertEqual(controller.get_target_kef_mac(), "AABBCCDDEE02")

    def test_apply_configured_device_target_ignores_invalid_ip(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")

        controller.config.kef_ip = "not-an-ip"
        controller.config.kef_mac = "AA:BB:CC:DD:EE:02"

        changed = controller.apply_configured_device_target(source="unit_test")

        self.assertTrue(changed)
        self.assertEqual(controller.get_current_kef_ip(), "192.168.1.10")
        self.assertEqual(controller.get_target_kef_mac(), "AABBCCDDEE02")

    def test_apply_configured_device_target_clears_target_mac(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")

        controller.config.kef_mac = ""

        changed = controller.apply_configured_device_target(source="unit_test")

        self.assertTrue(changed)
        self.assertEqual(controller.get_target_kef_mac(), "")

    def test_apply_configured_device_target_clears_current_ip(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")

        controller.config.kef_ip = ""

        changed = controller.apply_configured_device_target(source="unit_test")

        self.assertTrue(changed)
        self.assertEqual(controller.get_current_kef_ip(), "")

    def test_resolve_target_recovers_ip_when_only_target_mac_is_known(self):
        controller = self.make_controller(kef_ip="", kef_mac="AA:BB:CC:DD:EE:01")
        identity = SpeakerIdentity(
            ip="192.168.1.20",
            mac="AABBCCDDEE01",
            speaker_name="Office Speaker",
            speaker_model="LS50 Wireless II",
        )
        controller._backend.capture_identity = Mock(return_value=identity)

        with (
            patch("kef_app.controller.discovery.recovery.discover_ip_by_mac", return_value="192.168.1.20"),
            patch("kef_app.controller.discovery.identity_probe.identify_kef_device", return_value=identity),
        ):
            resolved = controller.resolve_target("unit_test", "unit_test", force_recovery=True)

        self.assertTrue(resolved)
        self.assertEqual(controller.get_current_kef_ip(), "192.168.1.20")

    def test_resolve_target_supplements_partial_identity_with_http_mac(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
        partial_identity = SpeakerIdentity(
            ip="192.168.1.10",
            speaker_model="LS50 Wireless II",
        )
        full_identity = SpeakerIdentity(
            ip="192.168.1.10",
            mac="AABBCCDDEE01",
            mac_display="AA:BB:CC:DD:EE:01",
            speaker_name="Office Speaker",
            speaker_model="LS50 Wireless II",
        )
        controller.get_speaker = Mock(return_value=object())
        controller._backend.capture_identity = Mock(return_value=partial_identity)

        with patch("kef_app.controller.discovery.identity_probe.identify_kef_device", return_value=full_identity) as identify:
            resolved = controller.resolve_target("unit_test", "unit_test", force_recovery=True)

        self.assertTrue(resolved)
        identify.assert_called_once_with("192.168.1.10", controller.config)

    def test_urgent_standby_target_can_use_cached_identity_when_live_mac_is_missing(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
        with controller._ip_lock:
            controller._speaker_model = "LS50 Wireless II"
        partial_identity = SpeakerIdentity(
            ip="192.168.1.10",
            speaker_model="LS50 Wireless II",
        )
        controller.get_speaker = Mock(return_value=object())
        controller._backend.capture_identity = Mock(return_value=partial_identity)

        with patch("kef_app.controller.discovery.identity_probe.identify_kef_device") as identify:
            resolved = controller.resolve_target(
                "TRAY_STANDBY",
                "standby_before_request",
                force_recovery=True,
            )

        self.assertTrue(resolved)
        identify.assert_not_called()

    def test_urgent_standby_target_uses_cached_identity_without_probe(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
        with controller._ip_lock:
            controller._speaker_model = "LS50 Wireless II"
        controller.get_speaker = Mock()
        controller._backend.capture_identity = Mock()

        with patch("kef_app.controller.discovery.identity_probe.identify_kef_device") as identify:
            resolved = controller.resolve_target(
                "TRAY_STANDBY",
                "standby_before_request",
                force_recovery=True,
            )

        self.assertTrue(resolved)
        controller.get_speaker.assert_not_called()
        controller._backend.capture_identity.assert_not_called()
        identify.assert_not_called()

    def test_urgent_standby_target_can_use_cached_identity_when_live_mac_and_model_are_missing(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
        with controller._ip_lock:
            controller._speaker_model = "LS50 Wireless II"
        partial_identity = SpeakerIdentity(ip="192.168.1.10")
        controller.get_speaker = Mock(return_value=object())
        controller._backend.capture_identity = Mock(return_value=partial_identity)

        with patch("kef_app.controller.discovery.identity_probe.identify_kef_device") as identify:
            resolved = controller.resolve_target(
                "TRAY_STANDBY",
                "standby_before_request",
                force_recovery=True,
            )

        self.assertTrue(resolved)
        identify.assert_not_called()

    def test_nonurgent_target_does_not_use_cached_identity_when_live_mac_and_model_are_missing(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
        with controller._ip_lock:
            controller._speaker_model = "LS50 Wireless II"
        partial_identity = SpeakerIdentity(ip="192.168.1.10")
        controller.get_speaker = Mock(return_value=object())
        controller._backend.capture_identity = Mock(return_value=partial_identity)

        with (
            patch("kef_app.controller.discovery.identity_probe.identify_kef_device", return_value=None) as identify,
            patch("kef_app.controller.discovery.recovery.discover_ip_by_mac", return_value=None),
            patch("kef_app.controller.discovery.recovery.discover_kef_device_blind", return_value=None),
        ):
            resolved = controller.resolve_target("unit_test", "unit_test", force_recovery=True)

        self.assertFalse(resolved)
        identify.assert_called_once_with("192.168.1.10", controller.config)

    def test_refresh_ip_requires_consecutive_mac_misses_before_blind_scan(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
        identity = SpeakerIdentity(
            ip="192.168.1.20",
            mac="AABBCCDDEE01",
            speaker_name="Office Speaker",
            speaker_model="LS50 Wireless II",
        )

        with (
            patch("kef_app.controller.discovery.recovery.discover_ip_by_mac", return_value=None) as discover_by_mac,
            patch("kef_app.controller.discovery.recovery.discover_kef_device_blind", return_value=identity) as blind,
        ):
            first = controller.maybe_refresh_kef_ip("unit_test", "unit_test", force=True)
            second = controller.maybe_refresh_kef_ip("unit_test", "unit_test", force=True)

        self.assertFalse(first)
        self.assertTrue(second)
        self.assertEqual(discover_by_mac.call_count, 2)
        blind.assert_called_once()
        self.assertEqual(controller.get_current_kef_ip(), "192.168.1.20")

    def test_refresh_ip_resets_blind_gate_after_full_scan_attempt(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")

        with (
            patch("kef_app.controller.discovery.recovery.discover_ip_by_mac", return_value=None),
            patch("kef_app.controller.discovery.recovery.discover_kef_device_blind", return_value=None) as blind,
        ):
            self.assertFalse(controller.maybe_refresh_kef_ip("unit_test", "unit_test", force=True))
            self.assertFalse(controller.maybe_refresh_kef_ip("unit_test", "unit_test", force=True))
            self.assertFalse(controller.maybe_refresh_kef_ip("unit_test", "unit_test", force=True))

        self.assertEqual(blind.call_count, 1)

    def test_startup_http_identity_snapshot_does_not_mutate_target(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
        other_identity = SpeakerIdentity(
            ip="192.168.1.10",
            mac="AABBCCDDEE02",
            mac_display="AA:BB:CC:DD:EE:02",
            speaker_name="Other Speaker",
            speaker_model="LS50 Wireless II",
        )

        with patch("kef_app.controller.discovery.identity_probe.identify_kef_device", return_value=other_identity):
            matched = controller.log_current_http_identity_snapshot("startup_prebuild", "unit_test")

        self.assertFalse(matched)
        self.assertEqual(controller.get_current_kef_ip(), "192.168.1.10")
        self.assertEqual(controller.get_target_kef_mac(), "AABBCCDDEE01")

    def test_blind_recovery_without_target_mac_does_not_replace_existing_target(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="")

        with patch("kef_app.controller.discovery.recovery.discover_kef_device_blind") as discover:
            refreshed = controller.maybe_refresh_kef_ip_by_blind("unit_test", "unit_test", force=True)

        self.assertFalse(refreshed)
        discover.assert_not_called()
        self.assertEqual(controller.get_current_kef_ip(), "192.168.1.10")

    def test_blind_recovery_without_target_mac_requires_manual_selection(self):
        controller = self.make_controller(kef_ip="", kef_mac="")
        identity = SpeakerIdentity(
            ip="192.168.1.20",
            mac="AABBCCDDEE01",
            speaker_name="Office Speaker",
            speaker_model="LS50 Wireless II",
        )

        with patch("kef_app.controller.discovery.recovery.discover_kef_device_blind", return_value=identity) as discover:
            refreshed = controller.maybe_refresh_kef_ip_by_blind("unit_test", "unit_test", force=True)

        self.assertFalse(refreshed)
        discover.assert_not_called()
        self.assertEqual(controller.get_current_kef_ip(), "")

    def test_scan_kef_devices_does_not_change_current_target(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
        found = [
            SpeakerIdentity(
                ip="192.168.1.20",
                mac="AABBCCDDEE02",
                speaker_name="Other Speaker",
                speaker_model="LSX II",
            )
        ]

        with patch("kef_app.controller.discovery.recovery.discover_kef_devices", return_value=found):
            result = controller.scan_kef_devices()

        self.assertEqual(result, found)
        self.assertEqual(controller.get_current_kef_ip(), "192.168.1.10")
        self.assertEqual(controller.get_target_kef_mac(), "AABBCCDDEE01")

    def test_select_kef_device_updates_current_target(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
        identity = SpeakerIdentity(
            ip="192.168.1.20",
            mac="AABBCCDDEE02",
            speaker_name="Office Speaker",
            speaker_model="LS50 Wireless II",
        )

        changed = controller.select_kef_device(identity, source="unit_test")

        self.assertTrue(changed)
        self.assertEqual(controller.get_current_kef_ip(), "192.168.1.20")
        self.assertEqual(controller.get_target_kef_mac(), "AABBCCDDEE02")

    def test_select_kef_device_without_mac_clears_previous_target_mac(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
        identity = SpeakerIdentity(
            ip="192.168.1.20",
            speaker_name="Office Speaker",
            speaker_model="LS50 Wireless II",
        )

        changed = controller.select_kef_device(identity, source="unit_test")

        self.assertTrue(changed)
        self.assertEqual(controller.get_current_kef_ip(), "192.168.1.20")
        self.assertEqual(controller.get_target_kef_mac(), "")

    def test_validate_manual_target_rejects_mac_mismatch(self):
        controller = self.make_controller()
        identity = SpeakerIdentity(
            ip="192.168.1.20",
            mac="AABBCCDDEE02",
            speaker_name="Office Speaker",
            speaker_model="LS50 Wireless II",
        )
        controller.inspect_kef_identity_at_ip = Mock(return_value=identity)

        result = controller.validate_manual_target(
            "192.168.1.20",
            "AA:BB:CC:DD:EE:01",
            reason="unit_test",
            trigger="unit_test",
        )

        self.assertEqual(result.status, "mac_mismatch")
        self.assertEqual(controller.get_current_kef_ip(), "")

    def test_validate_manual_target_allows_unreachable_ip_only(self):
        controller = self.make_controller()
        controller.inspect_kef_identity_at_ip = Mock(return_value=None)

        with patch("kef_app.controller.discovery.manual_target.probe_ip_port", return_value=False):
            result = controller.validate_manual_target(
                "192.168.1.20",
                "",
                reason="unit_test",
                trigger="unit_test",
            )

        self.assertEqual(result.status, "unreachable")
        self.assertEqual(result.requested_ip, "192.168.1.20")

    def test_validate_manual_target_recovers_mac_only(self):
        controller = self.make_controller()
        identity = SpeakerIdentity(
            ip="192.168.1.20",
            mac="AABBCCDDEE01",
            speaker_name="Office Speaker",
            speaker_model="LS50 Wireless II",
        )
        controller.inspect_kef_identity_at_ip = Mock(return_value=identity)

        with patch("kef_app.controller.discovery.manual_target.discover_ip_by_mac", return_value="192.168.1.20"):
            result = controller.validate_manual_target(
                "",
                "AA:BB:CC:DD:EE:01",
                reason="unit_test",
                trigger="unit_test",
            )

        self.assertEqual(result.status, "recovered")
        self.assertEqual(result.identity.ip, "192.168.1.20")
        self.assertEqual(controller.get_current_kef_ip(), "")

    def test_live_input_rejects_empty_or_unsupported_source(self):
        controller = self.make_controller(kef_ip="192.168.1.10")

        self.assertFalse(controller.change_input_live(""))
        self.assertFalse(controller.change_input_live("standby"))

    def test_change_input_confirms_selected_input(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
        controller._ensure_target_identity = Mock(return_value=True)
        controller.get_input_source = Mock(side_effect=["wifi", "coaxial"])
        controller._set_speaker_source = Mock()

        result = controller.change_input_live("coaxial")

        self.assertTrue(result)
        controller._set_speaker_source.assert_called_once_with("coaxial", fresh=True)

    def test_set_volume_rejects_non_numeric_level_without_connector_call(self):
        controller = self.make_controller(kef_ip="192.168.1.10")

        self.assertFalse(controller.set_volume("loud"))  # type: ignore[arg-type]

    def test_speaker_event_poll_returns_normalized_ui_state(self):
        controller = self.make_controller(kef_ip="192.168.1.10")
        speaker = Mock()
        speaker.poll_speaker.return_value = {
            "source": "wifi",
            "volume": 31,
            "speaker_status": "powerOn",
        }
        controller.get_speaker = Mock(return_value=speaker)

        result = controller.poll_speaker_event_state("unit_test", "unit_test", timeout=7.5)

        self.assertEqual(result, ("wifi", 31, True))
        speaker.poll_speaker.assert_called_once_with(timeout=7)

    def test_speaker_event_poll_ignores_non_ui_events(self):
        controller = self.make_controller(kef_ip="192.168.1.10")
        speaker = Mock()
        speaker.poll_speaker.return_value = {"device_name": "Office Speaker"}
        controller.get_speaker = Mock(return_value=speaker)

        result = controller.poll_speaker_event_state("unit_test", "unit_test", timeout=7.5)

        self.assertEqual(result, (None, None, None))

    def test_speaker_event_poll_first_failure_resets_subscription_only(self):
        controller = self.make_controller(kef_ip="192.168.1.10")
        speaker = Mock()
        speaker.polling_queue = "stale-queue"
        speaker.last_polled = 123.0
        speaker._previous_poll_song_status = True
        speaker.poll_speaker.side_effect = RuntimeError("poll failed")
        controller.get_speaker = Mock(return_value=speaker)
        controller.reset_speaker = Mock()

        result = controller.poll_speaker_event_state("unit_test", "unit_test", timeout=7.5)

        self.assertEqual(result, (None, None, None))
        self.assertIsNone(speaker.polling_queue)
        self.assertIsNone(speaker.last_polled)
        self.assertFalse(speaker._previous_poll_song_status)
        controller.reset_speaker.assert_not_called()

    def test_speaker_event_poll_single_failure_logs_as_transient_step(self):
        controller = self.make_controller(kef_ip="192.168.1.10")
        speaker = Mock()
        speaker.polling_queue = "stale-queue"
        speaker.last_polled = 123.0
        speaker._previous_poll_song_status = False
        speaker.poll_speaker.side_effect = RuntimeError("poll failed")
        controller.get_speaker = Mock(return_value=speaker)
        controller._log_structured = Mock()

        controller.poll_speaker_event_state("unit_test", "unit_test", timeout=7.5)

        transient_logs = [
            call for call in controller._log_structured.mock_calls
            if call.kwargs.get("status") == "transient_failure"
        ]
        self.assertEqual(len(transient_logs), 1)
        self.assertEqual(transient_logs[0].args[0], "STEP")
        self.assertEqual(transient_logs[0].kwargs.get("log_level"), "info")

    def test_speaker_event_poll_recovers_ip_after_consecutive_failures(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
            speaker_event_recovery_failure_threshold=2,
        )
        speaker = Mock()
        speaker.poll_speaker.side_effect = RuntimeError("poll failed")
        controller.get_speaker = Mock(return_value=speaker)
        controller.maybe_refresh_kef_ip = Mock(return_value=True)

        controller.poll_speaker_event_state("unit_test", "unit_test", timeout=7.5)
        controller.poll_speaker_event_state("unit_test", "unit_test", timeout=7.5)

        controller.maybe_refresh_kef_ip.assert_called_once_with(
            reason="unit_test",
            trigger="unit_test_event_recover",
            force=True,
        )

    def test_wifi_diagnostics_logs_available_radio_info(self):
        controller = self.make_controller(kef_ip="192.168.1.10")
        speaker = Mock()
        speaker.get_wifi_information.return_value = {
            "signalLevel": -48,
            "ssid": "Office",
            "frequency": 5180,
            "bssid": "AA:BB:CC:DD:EE:FF",
        }
        controller.get_speaker = Mock(return_value=speaker)
        controller._log_structured = Mock()

        info = controller.log_wifi_diagnostics("unit_test", "unit_test")

        self.assertEqual(info["signalLevel"], -48)
        self.assertTrue(
            any(
                call.kwargs.get("action") == "WIFI_DIAGNOSTICS"
                and call.kwargs.get("status") == "available"
                and call.kwargs.get("signal_level") == -48
                for call in controller._log_structured.mock_calls
            )
        )

    def test_wake_rejects_unsupported_configured_input(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_input="standby")
        controller.wait_until_reachable = Mock(return_value=True)

        self.assertFalse(controller.wake_kef(0, "unit_test"))
        controller.wait_until_reachable.assert_not_called()


if __name__ == "__main__":
    unittest.main()
