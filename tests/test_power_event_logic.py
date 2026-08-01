from __future__ import annotations

import logging
import threading
import unittest
from unittest.mock import Mock, patch

from kef_app.config import AppConfig
from kef_app.controller import KefPowerController
from kef_app.controller.actions.standby import (
    FAST_SUSPEND_STANDBY_POLICY,
    FastStandbyPolicy,
    PREEMPTIVE_STANDBY_POLICY,
)
from kef_app.controller.session_events import _DisplayOffStandbyTask
from kef_app.controller.standby import CachedPrewarmedStandbySendResult, PrewarmedStandbySendResult
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

    @staticmethod
    def begin_display_off_intent(controller: KefPowerController, status: str = "sent"):
        intent = controller._begin_cancellable_standby_intent("display_off", "DISPLAY_OFF", controller.mono())
        if status == "pending":
            return intent
        controller._update_display_off_standby_intent(intent.generation, "sending")
        if status != "sending":
            controller._update_display_off_standby_intent(intent.generation, status)
        return intent

    def test_trigger_registry_contains_power_standby_triggers(self):
        self.assertEqual(
            set(TRIGGERS),
            {
                "lock",
                "lid_closed",
                "display_off",
                "suspend",
                "query_end_session",
                "end_session",
            },
        )

    def test_user_power_action_keeps_generation_management_inside_controller(self):
        controller = self.make_controller()
        controller._new_generation = Mock(return_value=9)
        controller.wake_kef = Mock(return_value=True)
        controller.standby_kef = Mock(return_value=True)

        self.assertTrue(controller.run_user_power_action("wake", "ui_test"))
        controller._new_generation.assert_called_once_with("wake", "ui_test")
        controller.wake_kef.assert_called_once_with(9, "ui_test")

        controller._new_generation.reset_mock()
        self.assertTrue(controller.run_user_power_action("standby", "ui_test"))
        controller._new_generation.assert_called_once_with("sleep", "ui_test")
        controller.standby_kef.assert_called_once_with(9, "ui_test")

    def test_fast_standby_policies_capture_distinct_path_shapes(self):
        self.assertIsInstance(PREEMPTIVE_STANDBY_POLICY, FastStandbyPolicy)
        self.assertEqual(PREEMPTIVE_STANDBY_POLICY.action, "EARLY_STANDBY")
        self.assertEqual(PREEMPTIVE_STANDBY_POLICY.fire_and_forget_outcome, "sent_unconfirmed_fire_and_forget")

        self.assertIsInstance(FAST_SUSPEND_STANDBY_POLICY, FastStandbyPolicy)
        self.assertEqual(FAST_SUSPEND_STANDBY_POLICY.host_unreachable_outcome, "sent_skipped_host_unreachable")
        self.assertEqual(FAST_SUSPEND_STANDBY_POLICY.disabled_field, "suspend_fast_standby_enabled")

    def test_bounded_standby_abort_log_includes_current_ip(self):
        controller = self.make_controller()
        controller._log_structured = Mock()

        outcome = controller._abort_bounded_standby_if_needed(
            PREEMPTIVE_STANDBY_POLICY,
            generation=None,
            reason="unit_test",
            deadline_mono=controller.mono() - 1.0,
            step="before_fast_send",
            current_ip="192.168.1.10",
        )

        self.assertEqual(outcome, "aborted_bounded_deadline_exceeded")
        self.assertEqual(controller._log_structured.call_args.kwargs["current_ip"], "192.168.1.10")

    def test_network_parameter_notifications_are_deduped_with_summary(self):
        controller = self.make_controller(kef_ip="192.168.1.10")
        controller._log_structured = Mock()

        with patch("kef_app.controller.logging_mixin.threading.Timer") as timer_factory:
            first = controller._should_suppress_network_interface_event(
                notification="ParameterNotification",
                interface="Ethernet",
                interface_index=11,
                if_state="down",
                family=2,
                metric=0,
                nl_mtu=0,
                current_ip="192.168.1.10",
                event_mono=100.0,
            )
            second = controller._should_suppress_network_interface_event(
                notification="ParameterNotification",
                interface="Ethernet",
                interface_index=11,
                if_state="down",
                family=23,
                metric=0,
                nl_mtu=0,
                current_ip="192.168.1.10",
                event_mono=100.1,
            )

        self.assertFalse(first)
        self.assertTrue(second)
        timer_factory.assert_called_once()
        controller._flush_network_interface_dedup_due(now_mono=100.3)
        self.assertTrue(
            any(
                call.args[:1] == ("EVENT",)
                and call.kwargs.get("name") == "INTERFACE_CHANGE_DEDUP"
                and call.kwargs.get("repeats") == 1
                and call.kwargs.get("families") == "2,23"
                for call in controller._log_structured.mock_calls
            )
        )

    def test_off_pump_dispatch_routes_time_sensitive_standby_events(self):
        controller = self.make_controller()
        controller.schedule_early_standby = Mock(return_value=True)
        controller.schedule_suspend_standby = Mock(return_value=True)
        controller._log_structured = Mock()

        self.assertTrue(controller.dispatch_off_pump_standby("lock", "WTS_SESSION_LOCK", 100.0))
        self.assertTrue(controller.dispatch_off_pump_standby("lid_closed", "POWER_LID_CLOSED", 101.0))
        self.assertTrue(controller.dispatch_off_pump_standby("suspend", "PBT_APMSUSPEND", 102.0))

        controller.schedule_early_standby.assert_any_call("lock", "WTS_SESSION_LOCK", 100.0)
        controller.schedule_early_standby.assert_any_call("lid_closed", "POWER_LID_CLOSED", 101.0)
        controller.schedule_suspend_standby.assert_called_once_with("PBT_APMSUSPEND", 102.0)

    def test_off_pump_dispatch_warns_when_pump_callback_is_slow(self):
        controller = self.make_controller()
        controller.schedule_off_pump_standby = Mock(return_value=True)
        controller._log_structured = Mock()
        controller.mono = Mock(side_effect=[100.001, 100.030])

        scheduled = controller.dispatch_off_pump_standby(
            "lock",
            "WTS_SESSION_LOCK",
            100.0,
            callback_started_mono=100.0,
            step="unit_dispatch",
        )

        self.assertTrue(scheduled)
        self.assertTrue(
            any(
                call.args[:1] == ("WARN",)
                and call.kwargs.get("step") == "pump_callback_slow"
                and call.kwargs.get("callback_duration_ms") == 30
                for call in controller._log_structured.mock_calls
            )
        )

    def test_event_monitor_records_restart_request_while_stopping(self):
        controller = self.make_controller(home_event_poll_enabled=True)
        with controller._speaker_events.lock:
            controller._speaker_events.running = True
            controller._speaker_events.stop.set()

        self.assertFalse(controller.start_speaker_event_monitor("PBT_APMRESUMEAUTOMATIC"))
        self.assertEqual(controller._finish_speaker_event_monitor(), "PBT_APMRESUMEAUTOMATIC")

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

    def test_on_suspend_dispatches_off_pump_standby(self):
        controller = self.make_controller(standby_on_sleep=True)
        controller.dispatch_off_pump_standby = Mock(return_value=True)
        controller.mono = Mock(return_value=123.0)

        result = controller.on_suspend("PBT_APMSUSPEND")

        self.assertTrue(result)
        controller.dispatch_off_pump_standby.assert_called_once_with(
            "suspend",
            "PBT_APMSUSPEND",
            123.0,
            callback_started_mono=123.0,
            step="dispatch_suspend_standby",
        )

    def test_scheduled_suspend_standby_uses_event_anchored_deadline_in_dispatcher(self):
        controller = self.make_controller(standby_on_sleep=True)
        controller._enqueue_display_off_standby_task = Mock()
        controller._log_structured = Mock()
        controller.standby_kef_fast_suspend = Mock(return_value=True)
        event_mono = controller.mono()

        scheduled = controller.schedule_suspend_standby("PBT_APMSUSPEND", event_mono)

        self.assertTrue(scheduled)
        self.assertTrue(
            any(
                call.args[:1] == ("STEP",)
                and "log_level" not in call.kwargs
                and call.kwargs.get("step") == "schedule_suspend_dispatcher"
                for call in controller._log_structured.mock_calls
            )
        )
        task = controller._enqueue_display_off_standby_task.call_args.args[0]
        self.assertEqual(task.deadline_mono, event_mono + 0.30)
        controller._process_bounded_standby_task(task)
        controller.standby_kef_fast_suspend.assert_called_once_with(
            1,
            "PBT_APMSUSPEND",
            deadline_mono=event_mono + 0.30,
        )

    def test_scheduled_suspend_standby_does_not_send_after_event_deadline(self):
        controller = self.make_controller(standby_on_sleep=True)
        controller._enqueue_display_off_standby_task = Mock()
        controller.standby_kef_fast_suspend = Mock(return_value=True)
        controller._log_structured = Mock()

        self.assertTrue(controller.schedule_suspend_standby("PBT_APMSUSPEND", controller.mono() - 1.0))
        controller._process_bounded_standby_task(controller._enqueue_display_off_standby_task.call_args.args[0])

        controller.standby_kef_fast_suspend.assert_not_called()
        self.assertTrue(
            any(
                call.args[:1] == ("ABORT",)
                and call.kwargs.get("step") == "before_suspend_dispatcher_send"
                and call.kwargs.get("cause") == "deadline_exceeded"
                for call in controller._log_structured.mock_calls
            )
        )

    def test_scheduled_suspend_standby_does_not_send_after_generation_changes(self):
        controller = self.make_controller(standby_on_sleep=True)
        controller._enqueue_display_off_standby_task = Mock()
        controller.standby_kef_fast_suspend = Mock(return_value=True)
        controller._log_structured = Mock()
        event_mono = controller.mono()

        self.assertTrue(controller.schedule_suspend_standby("PBT_APMSUSPEND", event_mono))
        controller._new_generation("wake", "PBT_APMRESUMEAUTOMATIC")
        controller._process_bounded_standby_task(controller._enqueue_display_off_standby_task.call_args.args[0])

        controller.standby_kef_fast_suspend.assert_not_called()
        self.assertTrue(
            any(
                call.args[:1] == ("ABORT",)
                and call.kwargs.get("step") == "before_suspend_dispatcher_send"
                and call.kwargs.get("cause") == "stale_generation"
                for call in controller._log_structured.mock_calls
            )
        )

    def test_scheduled_suspend_can_use_full_standby_when_fast_path_disabled(self):
        controller = self.make_controller(standby_on_sleep=True, suspend_fast_standby_enabled=False)
        captured: dict[str, object] = {}
        controller._enqueue_display_off_standby_task = Mock()
        controller._start_controller_thread = lambda target, thread_name: captured.update(
            target=target,
            thread_name=thread_name,
        )
        controller.standby_kef_fast_suspend = Mock(return_value=True)
        controller.standby_kef = Mock(return_value=True)

        self.assertTrue(controller.schedule_suspend_standby("PBT_APMSUSPEND", controller.mono()))
        controller._process_bounded_standby_task(controller._enqueue_display_off_standby_task.call_args.args[0])
        self.assertEqual(captured["thread_name"], "SuspendVerifiedStandby-1")
        captured["target"]()

        controller.standby_kef_fast_suspend.assert_not_called()
        controller.standby_kef.assert_called_once_with(1, "PBT_APMSUSPEND")

    def test_on_lock_dispatches_off_pump_standby(self):
        controller = self.make_controller(standby_on_lock=True)
        controller.dispatch_off_pump_standby = Mock(return_value=True)
        controller.mono = Mock(return_value=124.0)

        result = controller.on_lock("WTS_SESSION_LOCK")

        self.assertTrue(result)
        controller.dispatch_off_pump_standby.assert_called_once_with(
            "lock",
            "WTS_SESSION_LOCK",
            124.0,
            callback_started_mono=124.0,
        )

    def test_ui_test_lock_does_not_leave_session_locked(self):
        controller = self.make_controller(standby_on_lock=True)
        controller.dispatch_off_pump_standby = Mock(return_value=True)

        result = controller.on_lock("UI_TEST_LOCK")

        self.assertTrue(result)
        self.assertFalse(controller._is_session_locked())

    def test_on_lid_closed_dispatches_off_pump_standby(self):
        controller = self.make_controller(standby_on_lid_close=True)
        controller.dispatch_off_pump_standby = Mock(return_value=True)
        controller.mono = Mock(return_value=125.0)

        result = controller.on_lid_closed("POWER_LID_CLOSED")

        self.assertTrue(result)
        controller.dispatch_off_pump_standby.assert_called_once_with(
            "lid_closed",
            "POWER_LID_CLOSED",
            125.0,
            callback_started_mono=125.0,
        )

    def test_on_display_off_dispatches_standby(self):
        controller = self.make_controller(standby_on_display_off=True)
        controller.dispatch_off_pump_standby = Mock(return_value=True)

        result = controller.on_display_off(100.0)

        self.assertTrue(result)
        controller.dispatch_off_pump_standby.assert_called_once_with(
            "display_off",
            "DISPLAY_OFF",
            100.0,
            callback_started_mono=100.0,
            step="dispatch_display_off_standby",
        )

    def test_lid_event_matches_reason_for_early_standby_diagnostics(self):
        controller = self.make_controller()

        self.assertTrue(
            controller._early_standby_event_matches_reason(
                "GUID_LIDSWITCH_STATE_CHANGE",
                "POWER_LID_CLOSED",
            )
        )

    def test_schedule_display_off_skips_when_disabled(self):
        controller = self.make_controller(standby_on_display_off=False)
        captured: dict[str, object] = {}
        controller._start_controller_thread = lambda target, thread_name: captured.update(target=target)
        controller._run_early_standby_trigger = Mock(return_value=True)

        scheduled = controller.schedule_early_standby("display_off", "DISPLAY_OFF", controller.mono())

        self.assertFalse(scheduled)
        self.assertNotIn("target", captured)
        controller._run_early_standby_trigger.assert_not_called()

    def test_scheduled_display_off_queues_resident_dispatcher_without_event_deadline(self):
        controller = self.make_controller(standby_on_display_off=True)
        controller._enqueue_display_off_standby_task = Mock()
        event_mono = controller.mono()

        scheduled = controller.schedule_early_standby("display_off", "DISPLAY_OFF", event_mono)

        self.assertTrue(scheduled)
        task = controller._enqueue_display_off_standby_task.call_args.args[0]
        self.assertEqual(task.generation, 1)
        self.assertEqual(task.reason, "DISPLAY_OFF")
        self.assertEqual(task.event_mono, event_mono)

    def test_display_on_wakes_only_after_display_off_standby(self):
        controller = self.make_controller(
            standby_on_display_off=True,
            wake_on_display_on=True,
            display_on_wake_delay=0.45,
        )
        controller._schedule_delayed_wake = Mock()

        self.begin_display_off_intent(controller)
        result = controller.on_display_on(controller.mono())

        self.assertTrue(result)
        controller._schedule_delayed_wake.assert_called_once_with(
            3,
            "DISPLAY_ON",
            0.45,
            "display_on_delay",
            "DisplayOnWake",
            skip_if_already_on=True,
        )
        self.assertEqual(controller._current_generation(), 3)

    def test_display_on_skips_when_disabled(self):
        controller = self.make_controller(wake_on_display_on=False)
        self.begin_display_off_intent(controller)
        controller._schedule_delayed_wake = Mock()
        controller._log_structured = Mock()

        result = controller.on_display_on(controller.mono())

        self.assertFalse(result)
        controller._schedule_delayed_wake.assert_not_called()
        self.assertTrue(
            any(
                call.args[:1] == ("SKIP",)
                and call.kwargs.get("cause") == "display_on_wake_disabled"
                and call.kwargs.get("log_level") is None
                for call in controller._log_structured.mock_calls
            )
        )

    def test_display_on_cancels_pending_intent_even_when_wake_is_disabled(self):
        controller = self.make_controller(wake_on_display_on=False)
        intent = self.begin_display_off_intent(controller, "pending")
        controller._schedule_delayed_wake = Mock()

        self.assertFalse(controller.on_display_on(controller.mono()))

        self.assertTrue(intent.cancel_event.is_set())
        self.assertFalse(controller._display_off_intent_is_active(intent.generation))
        controller._schedule_delayed_wake.assert_not_called()

    def test_display_on_cancels_pending_intent_while_session_is_locked(self):
        controller = self.make_controller(wake_on_display_on=True)
        intent = self.begin_display_off_intent(controller, "pending")
        controller._set_session_locked(True)
        controller._schedule_delayed_wake = Mock()

        self.assertFalse(controller.on_display_on(controller.mono()))

        self.assertTrue(intent.cancel_event.is_set())
        self.assertFalse(controller._display_off_intent_is_active(intent.generation))
        controller._schedule_delayed_wake.assert_not_called()

    def test_display_on_wakes_after_display_off_send_failed(self):
        controller = self.make_controller(wake_on_display_on=True)
        self.begin_display_off_intent(controller, "failed")
        controller._schedule_delayed_wake = Mock()

        self.assertTrue(controller.on_display_on(controller.mono()))

        controller._schedule_delayed_wake.assert_called_once_with(
            3,
            "DISPLAY_ON",
            controller.config.display_on_wake_delay,
            "display_on_delay",
            "DisplayOnWake",
            skip_if_already_on=True,
        )

    def test_display_off_processor_uses_cached_send_without_event_deadline(self):
        controller = self.make_controller(kef_ip="192.168.1.10")
        intent = self.begin_display_off_intent(controller, "pending")
        task = type("Task", (), {
            "generation": intent.generation,
            "trigger_name": "display_off",
            "reason": intent.reason,
            "event_mono": intent.event_mono,
            "cancel_event": intent.cancel_event,
        })()
        controller.try_send_cached_prewarmed_standby = Mock(
            return_value=CachedPrewarmedStandbySendResult(
                success=True,
                fast_path_used=True,
                status="sent",
                target_ip="192.168.1.10",
                cache_version=1,
                cache_age_ms=0,
            )
        )
        controller._send_fast_standby = Mock()

        controller._process_display_off_standby_task(task)

        controller.try_send_cached_prewarmed_standby.assert_called_once_with(generation=intent.generation)
        controller._send_fast_standby.assert_not_called()

    def test_generation_only_fast_send_uses_one_synchronous_cold_attempt(self):
        controller = self.make_controller(kef_ip="192.168.1.10")
        generation = controller._new_generation("sleep", "DISPLAY_OFF")
        controller.try_send_prewarmed_standby = Mock(
            return_value=PrewarmedStandbySendResult(False, False, "no_recent_keepalive")
        )
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(True))

        result = controller._send_fast_standby(
            "192.168.1.10",
            generation=generation,
            fire_and_forget_attempts=1,
        )

        self.assertTrue(result.success)
        kwargs = controller._send_fire_and_forget_shutdown.call_args.kwargs
        self.assertEqual(kwargs["attempts"], 1)
        self.assertTrue(kwargs["should_send"]())

    def test_dispatcher_survives_one_task_exception(self):
        controller = self.make_controller()
        first = _DisplayOffStandbyTask(0, "display_off", "DISPLAY_OFF", controller.mono(), threading.Event())
        second = _DisplayOffStandbyTask(0, "display_off", "DISPLAY_OFF", controller.mono(), threading.Event())
        second_processed = threading.Event()

        def process(task):
            if task is first:
                raise RuntimeError("synthetic task failure")
            second_processed.set()
            controller.stop_display_off_standby_dispatcher()

        controller._process_display_off_standby_task = Mock(side_effect=process)
        controller.start_display_off_standby_dispatcher()
        controller._display_off_dispatcher.queue.put(first)
        controller._display_off_dispatcher.queue.put(second)

        self.assertTrue(second_processed.wait(1.0))
        self.assertEqual(controller._process_display_off_standby_task.call_count, 2)

    def test_unlock_cancels_lock_intent_even_when_unlock_wake_is_disabled(self):
        controller = self.make_controller(wake_on_unlock_only=False)
        intent = controller._begin_cancellable_standby_intent("lock", "WTS_SESSION_LOCK", controller.mono())

        controller.on_unlock("WTS_SESSION_UNLOCK")

        self.assertTrue(intent.cancel_event.is_set())
        self.assertFalse(controller._display_off_intent_is_active(intent.generation))

    def test_cancellable_verified_fallback_does_not_hold_dispatcher(self):
        controller = self.make_controller()
        intent = self.begin_display_off_intent(controller, "pending")
        task = _DisplayOffStandbyTask(
            intent.generation,
            "display_off",
            intent.reason,
            intent.event_mono,
            intent.cancel_event,
        )
        controller._send_display_off_fast_attempt = Mock(return_value=False)
        controller._wait_for_display_off_retry = Mock(return_value=True)
        controller._start_controller_thread = Mock()

        controller._process_display_off_standby_task(task)

        self.assertEqual(controller._start_controller_thread.call_count, 1)
        self.assertIn("CancellableStandbyVerify", controller._start_controller_thread.call_args.args[1])

    def test_cancellable_verified_success_can_reach_confirmed(self):
        controller = self.make_controller()
        intent = self.begin_display_off_intent(controller, "sending")

        self.assertTrue(controller._update_display_off_standby_intent(intent.generation, "confirmed"))

    def test_cancellable_retry_logs_when_cancelled(self):
        controller = self.make_controller()
        intent = self.begin_display_off_intent(controller, "retry_waiting")
        task = _DisplayOffStandbyTask(
            intent.generation,
            "display_off",
            intent.reason,
            intent.event_mono,
            intent.cancel_event,
        )
        controller._log_structured = Mock()
        task.cancel_event.set()

        self.assertFalse(controller._wait_for_display_off_retry(task, 0.0, "retry"))

        self.assertTrue(
            any(
                call.kwargs.get("step") == "cancellable_retry_cancelled"
                and call.kwargs.get("cause") == "cancel_event"
                for call in controller._log_structured.mock_calls
            )
        )

    def test_display_on_skips_after_non_display_off_sleep(self):
        controller = self.make_controller(wake_on_display_on=True)
        controller._new_generation("sleep", "PBT_APMSUSPEND")
        controller._schedule_delayed_wake = Mock()
        controller._log_structured = Mock()

        result = controller.on_display_on(controller.mono())

        self.assertFalse(result)
        controller._schedule_delayed_wake.assert_not_called()
        self.assertTrue(
            any(
                call.args[:1] == ("SKIP",)
                and call.kwargs.get("cause") == "no_pending_display_off_intent"
                for call in controller._log_structured.mock_calls
            )
        )

    def test_display_on_skips_while_session_locked(self):
        controller = self.make_controller(wake_on_display_on=True)
        self.begin_display_off_intent(controller)
        controller._set_session_locked(True)
        controller._schedule_delayed_wake = Mock()
        controller._log_structured = Mock()

        result = controller.on_display_on(controller.mono())

        self.assertFalse(result)
        controller._schedule_delayed_wake.assert_not_called()
        self.assertTrue(
            any(
                call.args[:1] == ("SKIP",)
                and call.kwargs.get("cause") == "session_locked"
                for call in controller._log_structured.mock_calls
            )
        )

    def test_display_on_wake_dedupes_following_unlock(self):
        controller = self.make_controller(wake_on_display_on=True, wake_on_unlock_only=True)
        self.begin_display_off_intent(controller)
        controller._schedule_delayed_wake = Mock()

        self.assertTrue(controller.on_display_on(controller.mono()))
        controller.on_unlock("WTS_SESSION_UNLOCK")

        controller._schedule_delayed_wake.assert_called_once_with(
            3,
            "DISPLAY_ON",
            controller.config.display_on_wake_delay,
            "display_on_delay",
            "DisplayOnWake",
            skip_if_already_on=True,
        )

    def test_display_on_is_not_blocked_by_previous_unlock_wake_after_new_display_off(self):
        controller = self.make_controller(wake_on_display_on=True, wake_on_unlock_only=True)
        controller._schedule_delayed_wake = Mock()

        controller.on_unlock("WTS_SESSION_UNLOCK")
        self.begin_display_off_intent(controller)
        result = controller.on_display_on(controller.mono())

        self.assertTrue(result)
        self.assertEqual(controller._schedule_delayed_wake.call_count, 2)
        controller._schedule_delayed_wake.assert_any_call(
            4,
            "DISPLAY_ON",
            controller.config.display_on_wake_delay,
            "display_on_delay",
            "DisplayOnWake",
            skip_if_already_on=True,
        )

    def test_scheduled_lock_standby_uses_cancellable_dispatcher_without_deadline(self):
        controller = self.make_controller(standby_on_lock=True)
        controller._enqueue_display_off_standby_task = Mock()
        event_mono = controller.mono()

        scheduled = controller.schedule_early_standby("lock", "WTS_SESSION_LOCK", event_mono)

        self.assertTrue(scheduled)
        task = controller._enqueue_display_off_standby_task.call_args.args[0]
        self.assertEqual(task.trigger_name, "lock")
        self.assertEqual(task.generation, 1)

    def test_scheduled_lid_standby_reuses_bounded_dispatcher_without_lock_fast_path(self):
        controller = self.make_controller(standby_on_lid_close=True)
        controller._enqueue_display_off_standby_task = Mock()
        controller._run_early_standby_trigger = Mock(return_value=True)
        event_mono = controller.mono()

        scheduled = controller.schedule_early_standby("lid_closed", "POWER_LID_CLOSED", event_mono)

        self.assertTrue(scheduled)
        task = controller._enqueue_display_off_standby_task.call_args.args[0]
        self.assertEqual(task.deadline_mono, event_mono + 0.30)
        controller._process_bounded_standby_task(task)
        controller._run_early_standby_trigger.assert_called_once()
        self.assertEqual(controller._run_early_standby_trigger.call_args.kwargs["generation"], 1)
        self.assertEqual(controller._run_early_standby_trigger.call_args.kwargs["event_mono"], event_mono)
        self.assertEqual(controller._run_early_standby_trigger.call_args.kwargs["deadline_mono"], event_mono + 0.30)

    def test_scheduled_lid_standby_does_not_send_after_event_deadline(self):
        controller = self.make_controller(standby_on_lid_close=True)
        controller._enqueue_display_off_standby_task = Mock()
        controller.standby_kef_preemptive = Mock(return_value=True)
        controller._log_structured = Mock()

        self.assertTrue(
            controller.schedule_early_standby(
                "lid_closed",
                "POWER_LID_CLOSED",
                controller.mono() - 1.0,
            )
        )
        controller._process_bounded_standby_task(controller._enqueue_display_off_standby_task.call_args.args[0])

        controller.standby_kef_preemptive.assert_not_called()
        self.assertTrue(
            any(
                call.args[:1] == ("ABORT",)
                and call.kwargs.get("step") == "before_lid_closed_dispatcher_send"
                and call.kwargs.get("cause") == "deadline_exceeded"
                for call in controller._log_structured.mock_calls
            )
        )

    def test_scheduled_lid_standby_does_not_send_after_generation_changes(self):
        controller = self.make_controller(standby_on_lid_close=True)
        controller._enqueue_display_off_standby_task = Mock()
        controller.standby_kef_preemptive = Mock(return_value=True)
        controller._log_structured = Mock()
        event_mono = controller.mono()

        self.assertTrue(controller.schedule_early_standby("lid_closed", "POWER_LID_CLOSED", event_mono))
        controller._new_generation("wake", "WTS_SESSION_UNLOCK")
        controller._process_bounded_standby_task(controller._enqueue_display_off_standby_task.call_args.args[0])

        controller.standby_kef_preemptive.assert_not_called()
        self.assertTrue(
            any(
                call.args[:1] == ("ABORT",)
                and call.kwargs.get("step") == "before_lid_closed_dispatcher_send"
                and call.kwargs.get("cause") == "stale_generation"
                for call in controller._log_structured.mock_calls
            )
        )

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
            skip_if_already_on=True,
        )
        self.assertEqual(controller._current_generation(), 1)

    def test_on_unlock_safely_wakes_after_lock_standby_failed(self):
        controller = self.make_controller(wake_on_unlock_only=True)
        intent = controller._begin_cancellable_standby_intent("lock", "WTS_SESSION_LOCK", controller.mono())
        self.assertTrue(controller._update_display_off_standby_intent(intent.generation, "failed"))
        controller._schedule_delayed_wake = Mock()

        controller.on_unlock("WTS_SESSION_UNLOCK")

        controller._schedule_delayed_wake.assert_called_once_with(
            3,
            "WTS_SESSION_UNLOCK",
            controller.config.unlock_wake_delay,
            "unlock_delay",
            "UnlockWake",
            skip_if_already_on=True,
        )

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

    def test_end_session_standby_uses_bounded_fire_and_forget_without_identity_probe(self):
        controller = self.make_controller(kef_ip="192.168.1.10", endsession_standby_on_shutdown=True)
        controller.resolve_target = Mock(return_value=True)
        controller._ensure_target_identity = Mock(return_value=True)
        controller._request_shutdown = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(True))

        started_mono = controller.mono()
        result = controller.standby_kef_end_session("WM_QUERYENDSESSION", "NONE")

        self.assertTrue(result)
        controller.resolve_target.assert_not_called()
        controller._ensure_target_identity.assert_not_called()
        controller._send_fire_and_forget_shutdown.assert_called_once()
        args, kwargs = controller._send_fire_and_forget_shutdown.call_args
        self.assertEqual(args, ("192.168.1.10",))
        self.assertIn("deadline_mono", kwargs)
        self.assertIsNotNone(kwargs["deadline_mono"])
        self.assertGreaterEqual(kwargs["deadline_mono"], started_mono + 1.9)
        self.assertIn("should_send", kwargs)
        controller._request_shutdown.assert_not_called()

    def test_end_session_standby_does_not_use_standard_fallback_when_fast_send_fails(self):
        controller = self.make_controller(kef_ip="192.168.1.10", endsession_standby_on_shutdown=True)
        controller.resolve_target = Mock(return_value=True)
        controller._ensure_target_identity = Mock(return_value=True)
        controller._request_shutdown = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(False))

        result = controller.standby_kef_end_session("WM_QUERYENDSESSION", "NONE")

        self.assertFalse(result)
        controller.resolve_target.assert_not_called()
        controller._ensure_target_identity.assert_not_called()
        controller._send_fire_and_forget_shutdown.assert_called_once()
        controller._request_shutdown.assert_not_called()

    def test_end_session_standby_skips_blocking_route_preflight_and_uses_transport_result(self):
        controller = self.make_controller(kef_ip="192.168.1.10", endsession_standby_on_shutdown=True)
        events = self.capture_events(controller)
        controller.resolve_target = Mock(return_value=True)
        controller._ensure_target_identity = Mock(return_value=True)
        controller._request_shutdown = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(True))

        with patch("kef_app.platform.windows.api.has_best_route_to_ipv4", return_value=False) as route_check:
            result = controller.standby_kef_end_session("WM_QUERYENDSESSION", "NONE")

        self.assertTrue(result)
        controller.resolve_target.assert_not_called()
        controller._ensure_target_identity.assert_not_called()
        route_check.assert_not_called()
        controller._send_fire_and_forget_shutdown.assert_called_once()
        controller._request_shutdown.assert_not_called()
        self.assertTrue(self.emitted_outcome(events, "sent_unconfirmed_fire_and_forget"))

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
        controller._send_fire_and_forget_shutdown.assert_called_once()
        controller._request_shutdown.assert_not_called()

    def test_end_session_standby_ignores_busy_action_lock_when_fast_send_fails(self):
        controller = self.make_controller(kef_ip="192.168.1.10", endsession_standby_on_shutdown=True)
        controller.resolve_target = Mock(return_value=True)
        controller._ensure_target_identity = Mock(return_value=True)
        controller._request_shutdown = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(False))

        self.assertTrue(controller._action_lock.acquire(blocking=False))
        try:
            result = controller.standby_kef_end_session("WM_QUERYENDSESSION", "NONE")
        finally:
            controller._action_lock.release()

        self.assertFalse(result)
        controller.resolve_target.assert_not_called()
        controller._ensure_target_identity.assert_not_called()
        controller._send_fire_and_forget_shutdown.assert_called_once()
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
        )
        generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        controller.resolve_target = Mock(return_value=False)
        controller._ensure_target_identity = Mock(return_value=False)
        controller._perform_standby_request = Mock()
        controller._request_shutdown = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(True))

        result = controller.standby_kef_preemptive(generation, "WTS_SESSION_LOCK", deadline_mono=controller.mono() + 1.0)

        self.assertTrue(result)
        controller.resolve_target.assert_not_called()
        controller._ensure_target_identity.assert_not_called()
        controller._perform_standby_request.assert_not_called()
        controller._send_fire_and_forget_shutdown.assert_called_once()
        controller._request_shutdown.assert_not_called()

    def test_bounded_preemptive_standby_does_not_send_after_generation_changes(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
        )
        sleep_generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        controller._new_generation("wake", "WTS_SESSION_UNLOCK")
        controller.try_send_prewarmed_standby = Mock()
        controller._send_fire_and_forget_shutdown = Mock()
        controller._request_shutdown = Mock()

        result = controller.standby_kef_preemptive(
            sleep_generation,
            "WTS_SESSION_LOCK",
            deadline_mono=controller.mono() + 1.0,
        )

        self.assertFalse(result)
        controller.try_send_prewarmed_standby.assert_not_called()
        controller._send_fire_and_forget_shutdown.assert_not_called()
        controller._request_shutdown.assert_not_called()

    def test_fast_send_gate_leaves_deadline_to_raw_transport(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
        )
        generation = controller._new_generation("sleep", "PBT_APMSUSPEND")
        deadline_mono = controller.mono() + 1.0
        check_deadline_values: list[bool] = []
        controller.try_send_prewarmed_standby = Mock(
            return_value=PrewarmedStandbySendResult(
                attempted=True,
                success=False,
                status="no_socket",
                target_ip="192.168.1.10",
            )
        )

        def fake_abort_reason(**kwargs) -> str:
            check_deadline_values.append(kwargs.get("check_deadline", True))
            return ""

        def fake_fire_and_forget(_ip: str, **kwargs) -> FireAndForgetShutdownResult:
            self.assertEqual(kwargs["deadline_mono"], deadline_mono)
            self.assertTrue(kwargs["should_send"]())
            return self.fire_and_forget_result(True)

        controller._bounded_standby_abort_reason = fake_abort_reason
        controller._send_fire_and_forget_shutdown = fake_fire_and_forget

        result = controller._send_fast_standby(
            "192.168.1.10",
            deadline_mono=deadline_mono,
            generation=generation,
        )

        self.assertTrue(result.success)
        self.assertTrue(check_deadline_values)
        self.assertTrue(all(value is False for value in check_deadline_values))

    def test_bounded_preemptive_standby_skips_blocking_route_preflight(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
        )
        generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        controller.try_send_prewarmed_standby = Mock(
            return_value=PrewarmedStandbySendResult(False, False, "no_recent_keepalive")
        )
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(True))
        controller._request_shutdown = Mock()
        controller._log_structured = Mock()

        with patch("kef_app.platform.windows.api.has_best_route_to_ipv4", return_value=False) as route_check:
            result = controller.standby_kef_preemptive(
                generation,
                "WTS_SESSION_LOCK",
                deadline_mono=controller.mono() + 1.0,
            )

        self.assertTrue(result)
        route_check.assert_not_called()
        controller.try_send_prewarmed_standby.assert_called_once()
        controller._send_fire_and_forget_shutdown.assert_called_once()
        controller._request_shutdown.assert_not_called()

    def test_bounded_fast_send_reserves_time_for_fire_and_forget_after_slow_prewarm(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
        )
        clock = {"now": 100.0}
        controller.mono = lambda: clock["now"]
        generation = controller._new_generation("sleep", "PBT_APMSUSPEND")

        def slow_prewarm(_ip: str, **kwargs) -> PrewarmedStandbySendResult:
            self.assertEqual(kwargs["deadline_mono"], 100.0)
            clock["now"] = 100.30
            return PrewarmedStandbySendResult(
                attempted=True,
                success=False,
                status="frozen_during_send",
                duration_ms=300,
                target_ip="192.168.1.10",
                frozen_s="0.300",
            )

        def fire_and_forget(_ip: str, **kwargs) -> FireAndForgetShutdownResult:
            self.assertGreaterEqual(kwargs["deadline_mono"], 100.45)
            self.assertTrue(kwargs["should_send"]())
            return self.fire_and_forget_result(True)

        controller.try_send_prewarmed_standby = Mock(side_effect=slow_prewarm)
        controller._send_fire_and_forget_shutdown = Mock(side_effect=fire_and_forget)

        result = controller._send_fast_standby(
            "192.168.1.10",
            deadline_mono=100.10,
            generation=generation,
            reason="PBT_APMSUSPEND",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.source, "fire_and_forget")

    def test_bounded_preemptive_standby_does_not_use_standard_fallback(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
        )
        generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        controller.try_send_prewarmed_standby = Mock(
            return_value=PrewarmedStandbySendResult(False, False, "no_recent_keepalive")
        )
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(False))
        controller._request_shutdown = Mock()

        result = controller.standby_kef_preemptive(
            generation,
            "WTS_SESSION_LOCK",
            deadline_mono=controller.mono() + 1.0,
        )

        self.assertFalse(result)
        controller.try_send_prewarmed_standby.assert_called_once()
        controller._send_fire_and_forget_shutdown.assert_called_once()
        controller._request_shutdown.assert_not_called()

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

        result = controller.standby_kef_preemptive(generation, "WTS_SESSION_LOCK", deadline_mono=controller.mono() + 1.0)

        self.assertTrue(result)
        controller.try_send_prewarmed_standby.assert_called_once()
        controller._send_fire_and_forget_shutdown.assert_not_called()
        controller._request_shutdown.assert_not_called()
        self.assertTrue(self.emitted_outcome(events, "sent_unconfirmed_prewarmed"))

    def test_fast_suspend_standby_sends_after_early_standby(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
        )
        events = self.capture_events(controller)
        early_generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        controller.try_send_prewarmed_standby = Mock(
            return_value=PrewarmedStandbySendResult(
                attempted=True,
                success=True,
                status="sent",
                duration_ms=1,
                target_ip="192.168.1.10",
                mode="persistent_socket",
                so_error=0,
            )
        )
        controller._send_fire_and_forget_shutdown = Mock()
        controller._request_shutdown = Mock()

        self.assertTrue(
            controller.standby_kef_preemptive(
                early_generation,
                "WTS_SESSION_LOCK",
                deadline_mono=controller.mono() + 1.0,
            )
        )

        suspend_generation = controller._new_generation("sleep", "PBT_APMSUSPEND")
        controller.try_send_prewarmed_standby = Mock(
            return_value=PrewarmedStandbySendResult(
                attempted=True,
                success=True,
                status="sent",
                duration_ms=1,
                target_ip="192.168.1.10",
                mode="persistent_socket",
                so_error=0,
            )
        )
        controller._send_fire_and_forget_shutdown = Mock()
        controller._request_shutdown = Mock()

        result = controller.standby_kef_fast_suspend(
            suspend_generation,
            "PBT_APMSUSPEND",
            deadline_mono=controller.mono() + 1.0,
        )

        self.assertTrue(result)
        controller.try_send_prewarmed_standby.assert_called_once()
        controller._send_fire_and_forget_shutdown.assert_not_called()
        controller._request_shutdown.assert_not_called()
        self.assertTrue(self.emitted_outcome(events, "sent_unconfirmed_prewarmed"))

    def test_fast_suspend_defers_power_started_event_until_after_fast_send(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
        )
        order: list[str] = []
        controller.add_event_listener(
            lambda name, _payload: order.append(name)
            if name in {"power_action_started", "power_action_finished"}
            else None
        )
        generation = controller._new_generation("sleep", "PBT_APMSUSPEND")
        controller.try_send_prewarmed_standby = Mock(
            side_effect=lambda _ip, **_kwargs: order.append("prewarmed_send")
            or PrewarmedStandbySendResult(
                attempted=True,
                success=True,
                status="sent",
                duration_ms=1,
                target_ip="192.168.1.10",
                mode="persistent_socket",
                so_error=0,
            )
        )
        controller._send_fire_and_forget_shutdown = Mock()
        controller._request_shutdown = Mock()

        result = controller.standby_kef_fast_suspend(
            generation,
            "PBT_APMSUSPEND",
            deadline_mono=controller.mono() + 1.0,
        )

        self.assertTrue(result)
        self.assertEqual(order, ["prewarmed_send", "power_action_started", "power_action_finished"])

    def test_preemptive_standby_falls_back_when_prewarmed_send_was_frozen(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
        )
        generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        controller._log_structured = Mock()
        controller.log_wifi_diagnostics = Mock()
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

        result = controller.standby_kef_preemptive(generation, "WTS_SESSION_LOCK", deadline_mono=controller.mono() + 1.0)

        self.assertTrue(result)
        controller.try_send_prewarmed_standby.assert_called_once()
        controller._send_fire_and_forget_shutdown.assert_called_once()
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

    def test_preemptive_standby_falls_back_to_fire_and_forget_when_prewarmed_send_freezes(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
        )
        order: list[str] = []
        generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        controller._log_structured = Mock()
        controller.try_send_prewarmed_standby = Mock(
            side_effect=lambda _ip, **_kwargs: order.append("prewarmed")
            or PrewarmedStandbySendResult(
                attempted=True,
                success=False,
                status="frozen_during_send",
                duration_ms=420,
                target_ip="192.168.1.10",
                mode="short_connection",
                frozen_s="0.420",
            )
        )
        controller._send_fire_and_forget_shutdown = Mock(
            side_effect=lambda _ip, **_kwargs: order.append("fire_and_forget") or self.fire_and_forget_result(True)
        )
        controller._request_shutdown = Mock()

        result = controller.standby_kef_preemptive(generation, "WTS_SESSION_LOCK", deadline_mono=controller.mono() + 1.0)

        self.assertTrue(result)
        self.assertEqual(order, ["prewarmed", "fire_and_forget"])
        controller._request_shutdown.assert_not_called()

    def test_preemptive_standby_short_circuits_prewarmed_host_unreachable(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
        )
        events = self.capture_events(controller)
        generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        controller._log_structured = Mock()
        controller.log_wifi_diagnostics = Mock()
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

        result = controller.standby_kef_preemptive(generation, "WTS_SESSION_LOCK", deadline_mono=controller.mono() + 1.0)

        self.assertTrue(result)
        controller.try_send_prewarmed_standby.assert_called_once()
        controller._send_fire_and_forget_shutdown.assert_not_called()
        controller._request_shutdown.assert_not_called()
        controller.log_wifi_diagnostics.assert_called_once_with(
            reason="WTS_SESSION_LOCK",
            trigger="early_standby_host_unreachable",
            fresh=True,
            timeout=0.15,
        )
        self.assertIsNone(controller._runtime_speaker.power_on)
        self.assertTrue(
            any(
                name == "power_action_finished"
                and payload.get("success") is True
                and payload.get("outcome") == "sent_skipped_host_unreachable"
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
            result = controller.standby_kef_preemptive(
                generation,
                "WTS_SESSION_LOCK",
                deadline_mono=controller.mono() + 1.0,
            )
        finally:
            controller._action_lock.release()

        self.assertTrue(result)
        controller._send_fire_and_forget_shutdown.assert_called_once()
        controller._request_shutdown.assert_not_called()

    def test_preemptive_standby_generic_fire_and_forget_failure_does_not_use_standard_fallback(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
        )
        generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        controller._log_structured = Mock()
        controller.log_wifi_diagnostics = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(False))
        controller._request_shutdown = Mock()

        result = controller.standby_kef_preemptive(generation, "WTS_SESSION_LOCK", deadline_mono=controller.mono() + 1.0)

        self.assertFalse(result)
        controller._send_fire_and_forget_shutdown.assert_called_once()
        controller._request_shutdown.assert_not_called()
        controller.log_wifi_diagnostics.assert_not_called()
        self.assertTrue(
            any(
                call.args[:1] == ("SKIP",)
                and call.kwargs.get("action") == "EARLY_STANDBY"
                and call.kwargs.get("cause") == "fast_standby_send_failed"
                and call.kwargs.get("standard_fallback") == "disabled_for_bounded_path"
                for call in controller._log_structured.mock_calls
            )
        )

    def test_preemptive_standby_fire_and_forget_host_unreachable_fails_fast(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
        )
        events = self.capture_events(controller)
        generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        controller._log_structured = Mock()
        controller.log_wifi_diagnostics = Mock()
        controller._send_fire_and_forget_shutdown = Mock(
            return_value=self.fire_and_forget_result(False, all_host_unreachable=True)
        )
        controller._request_shutdown = Mock()

        result = controller.standby_kef_preemptive(generation, "WTS_SESSION_LOCK", deadline_mono=controller.mono() + 1.0)

        self.assertTrue(result)
        controller._send_fire_and_forget_shutdown.assert_called_once()
        controller._request_shutdown.assert_not_called()
        controller.log_wifi_diagnostics.assert_called_once_with(
            reason="WTS_SESSION_LOCK",
            trigger="early_standby_host_unreachable",
            fresh=True,
            timeout=0.15,
        )
        self.assertIsNone(controller._runtime_speaker.power_on)
        self.assertTrue(
            any(
                name == "power_action_finished"
                and payload.get("success") is True
                and payload.get("outcome") == "sent_skipped_host_unreachable"
                for name, payload in events
            )
        )
        self.assertTrue(
            any(
                call.args[:1] == ("STEP",)
                and call.kwargs.get("action") == "EARLY_STANDBY"
                and call.kwargs.get("status") == "local_network_unavailable_before_suspend"
                and call.kwargs.get("all_host_unreachable") is True
                for call in controller._log_structured.mock_calls
            )
        )

    def test_preemptive_standby_does_not_skip_fire_and_forget_without_dedup_state(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
        )
        events = self.capture_events(controller)
        generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        controller._log_structured = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(True))
        controller._request_shutdown = Mock()

        result = controller.standby_kef_preemptive(generation, "WTS_SESSION_LOCK", deadline_mono=controller.mono() + 1.0)

        self.assertTrue(result)
        controller._send_fire_and_forget_shutdown.assert_called_once()
        controller._request_shutdown.assert_not_called()
        self.assertTrue(self.emitted_outcome(events, "sent_unconfirmed_fire_and_forget"))
        self.assertFalse(
            any(
                call.args[:1] == ("SKIP",)
                and call.kwargs.get("action") == "EARLY_STANDBY"
                for call in controller._log_structured.mock_calls
            )
        )

    def test_preemptive_standby_skips_without_current_ip(self):
        controller = self.make_controller(kef_ip="")
        generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
        controller._request_shutdown = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(True))

        result = controller.standby_kef_preemptive(generation, "WTS_SESSION_LOCK", deadline_mono=controller.mono() + 1.0)

        self.assertFalse(result)
        controller._send_fire_and_forget_shutdown.assert_not_called()
        controller._request_shutdown.assert_not_called()

    def test_fast_suspend_standby_uses_cached_ip_without_identity_probe(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
        )
        generation = controller._new_generation("sleep", "PBT_APMSUSPEND")
        controller.resolve_target = Mock(return_value=False)
        controller._ensure_target_identity = Mock(return_value=False)
        controller._perform_standby_request = Mock()
        controller._request_shutdown = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(True))

        result = controller.standby_kef_fast_suspend(
            generation,
            "PBT_APMSUSPEND",
            deadline_mono=controller.mono() + 1.0,
        )

        self.assertTrue(result)
        controller.resolve_target.assert_not_called()
        controller._ensure_target_identity.assert_not_called()
        controller._perform_standby_request.assert_not_called()
        controller._send_fire_and_forget_shutdown.assert_called_once()
        controller._request_shutdown.assert_not_called()

    def test_fast_suspend_standby_generic_fire_and_forget_failure_does_not_use_standard_fallback(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
        )
        generation = controller._new_generation("sleep", "PBT_APMSUSPEND")
        controller._log_structured = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(False))
        controller._request_shutdown = Mock()

        result = controller.standby_kef_fast_suspend(
            generation,
            "PBT_APMSUSPEND",
            deadline_mono=controller.mono() + 1.0,
        )

        self.assertFalse(result)
        controller._send_fire_and_forget_shutdown.assert_called_once()
        controller._request_shutdown.assert_not_called()
        self.assertTrue(
            any(
                call.args[:1] == ("SKIP",)
                and call.kwargs.get("action") == "STANDBY"
                and call.kwargs.get("cause") == "fast_standby_send_failed"
                and call.kwargs.get("standard_fallback") == "disabled_for_bounded_path"
                for call in controller._log_structured.mock_calls
            )
        )

    def test_fast_suspend_standby_does_not_inherit_early_local_network_unavailable(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
        )
        controller._log_structured = Mock()
        controller.log_wifi_diagnostics = Mock()
        early_generation = controller._new_generation("sleep", "WTS_SESSION_LOCK")
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

        self.assertTrue(
            controller.standby_kef_preemptive(
                early_generation,
                "WTS_SESSION_LOCK",
                deadline_mono=controller.mono() + 1.0,
            )
        )

        suspend_generation = controller._new_generation("sleep", "PBT_APMSUSPEND")
        controller.try_send_prewarmed_standby = Mock(
            return_value=PrewarmedStandbySendResult(False, False, "no_recent_keepalive", target_ip="192.168.1.10")
        )
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(True))
        controller._request_shutdown = Mock()

        result = controller.standby_kef_fast_suspend(
            suspend_generation,
            "PBT_APMSUSPEND",
            deadline_mono=controller.mono() + 1.0,
        )

        self.assertTrue(result)
        controller._send_fire_and_forget_shutdown.assert_called_once()
        controller._request_shutdown.assert_not_called()

    def test_fast_suspend_standby_assumes_fire_and_forget_host_unreachable_without_standard_fallback(self):
        controller = self.make_controller(
            kef_ip="192.168.1.10",
            kef_mac="AA:BB:CC:DD:EE:01",
        )
        events = self.capture_events(controller)
        generation = controller._new_generation("sleep", "PBT_APMSUSPEND")
        controller._log_structured = Mock()
        controller._send_fire_and_forget_shutdown = Mock(
            return_value=self.fire_and_forget_result(False, all_host_unreachable=True)
        )
        controller._request_shutdown = Mock()

        result = controller.standby_kef_fast_suspend(
            generation,
            "PBT_APMSUSPEND",
            deadline_mono=controller.mono() + 1.0,
        )

        self.assertTrue(result)
        controller._send_fire_and_forget_shutdown.assert_called_once()
        controller._request_shutdown.assert_not_called()
        self.assertTrue(self.emitted_outcome(events, "sent_skipped_host_unreachable"))

    def test_fast_suspend_standby_skips_without_current_ip(self):
        controller = self.make_controller(kef_ip="")
        generation = controller._new_generation("sleep", "PBT_APMSUSPEND")
        controller._request_shutdown = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(True))

        result = controller.standby_kef_fast_suspend(
            generation,
            "PBT_APMSUSPEND",
            deadline_mono=controller.mono() + 1.0,
        )

        self.assertFalse(result)
        controller._send_fire_and_forget_shutdown.assert_not_called()
        controller._request_shutdown.assert_not_called()

    def test_end_session_standby_uses_cached_ip_even_when_identity_probe_would_fail(self):
        controller = self.make_controller(kef_ip="192.168.1.10")
        controller.resolve_target = Mock(return_value=False)
        controller._ensure_target_identity = Mock(return_value=False)
        controller._request_shutdown = Mock()
        controller._send_fire_and_forget_shutdown = Mock(return_value=self.fire_and_forget_result(True))

        result = controller.standby_kef_end_session("unit_test", "flags")

        self.assertTrue(result)
        controller.resolve_target.assert_not_called()
        controller._ensure_target_identity.assert_not_called()
        controller._send_fire_and_forget_shutdown.assert_called_once()
        controller._request_shutdown.assert_not_called()

    def test_apply_configured_device_target_updates_runtime_ip_and_mac(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")

        controller.config.kef_ip = "192.168.1.20"
        controller.config.kef_mac = "AA:BB:CC:DD:EE:02"

        changed = controller.apply_configured_device_target(trigger="unit_test")

        self.assertTrue(changed)
        self.assertEqual(controller.get_current_kef_ip(), "192.168.1.20")
        self.assertEqual(controller.get_target_kef_mac(), "AABBCCDDEE02")

    def test_apply_configured_device_target_ignores_invalid_ip(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")

        controller.config.kef_ip = "not-an-ip"
        controller.config.kef_mac = "AA:BB:CC:DD:EE:02"

        changed = controller.apply_configured_device_target(trigger="unit_test")

        self.assertTrue(changed)
        self.assertEqual(controller.get_current_kef_ip(), "192.168.1.10")
        self.assertEqual(controller.get_target_kef_mac(), "AABBCCDDEE02")

    def test_apply_configured_device_target_clears_target_mac(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")

        controller.config.kef_mac = ""

        changed = controller.apply_configured_device_target(trigger="unit_test")

        self.assertTrue(changed)
        self.assertEqual(controller.get_target_kef_mac(), "")

    def test_apply_configured_device_target_clears_current_ip(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")

        controller.config.kef_ip = ""

        changed = controller.apply_configured_device_target(trigger="unit_test")

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
            controller._identity.speaker_model = "LS50 Wireless II"
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
            controller._identity.speaker_model = "LS50 Wireless II"
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
            controller._identity.speaker_model = "LS50 Wireless II"
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
            controller._identity.speaker_model = "LS50 Wireless II"
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

    def test_refresh_ip_runs_blind_scan_after_arp_cache_miss(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
        identity = SpeakerIdentity(
            ip="192.168.1.20",
            mac="AABBCCDDEE01",
            speaker_name="Office Speaker",
            speaker_model="LS50 Wireless II",
        )

        with (
            patch("kef_app.controller.discovery.recovery.has_best_route_to_ipv4", return_value=True),
            patch("kef_app.controller.discovery.recovery.discover_ip_by_mac", return_value=None) as discover_by_mac,
            patch("kef_app.controller.discovery.recovery.discover_kef_device_blind", return_value=identity) as blind,
        ):
            refreshed = controller.maybe_refresh_kef_ip("unit_test", "unit_test", force=True)

        self.assertTrue(refreshed)
        discover_by_mac.assert_called_once()
        blind.assert_called_once()
        self.assertEqual(controller.get_current_kef_ip(), "192.168.1.20")

    def test_refresh_ip_does_not_repeat_blind_scan_during_blind_cooldown(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")

        with (
            patch("kef_app.controller.discovery.recovery.has_best_route_to_ipv4", return_value=True),
            patch("kef_app.controller.discovery.recovery.discover_ip_by_mac", return_value=None),
            patch("kef_app.controller.discovery.recovery.discover_kef_device_blind", return_value=None) as blind,
        ):
            self.assertFalse(controller.maybe_refresh_kef_ip("unit_test", "unit_test"))
            self.assertFalse(controller.maybe_refresh_kef_ip("unit_test", "unit_test"))

        self.assertEqual(blind.call_count, 1)

    def test_refresh_ip_skips_discovery_when_local_route_is_unavailable(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
        controller._log_structured = Mock()
        controller.maybe_refresh_kef_ip_by_mac = Mock(return_value=True)
        controller.maybe_refresh_kef_ip_by_blind = Mock(return_value=True)

        with patch("kef_app.controller.discovery.recovery.has_best_route_to_ipv4", return_value=False) as route:
            refreshed = controller.maybe_refresh_kef_ip("unit_test", "unit_test", force=True)

        self.assertFalse(refreshed)
        route.assert_called_once_with("192.168.1.10")
        controller.maybe_refresh_kef_ip_by_mac.assert_not_called()
        controller.maybe_refresh_kef_ip_by_blind.assert_not_called()
        self.assertTrue(
            any(
                call.args == ("SKIP",)
                and call.kwargs.get("action") == "DISCOVER_IP"
                and call.kwargs.get("cause") == "no_local_route"
                and call.kwargs.get("current_ip") == "192.168.1.10"
                for call in controller._log_structured.mock_calls
            )
        )

    def test_refresh_ip_runs_discovery_when_local_route_is_available_or_unknown(self):
        for route_state in (True, None):
            with self.subTest(route_state=route_state):
                controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
                controller.maybe_refresh_kef_ip_by_mac = Mock(return_value=True)
                controller.maybe_refresh_kef_ip_by_blind = Mock(return_value=False)

                with patch("kef_app.controller.discovery.recovery.has_best_route_to_ipv4", return_value=route_state):
                    refreshed = controller.maybe_refresh_kef_ip("unit_test", "unit_test", force=True)

                self.assertTrue(refreshed)
                controller.maybe_refresh_kef_ip_by_mac.assert_called_once_with(
                    reason="unit_test",
                    trigger="unit_test",
                    force=True,
                )
                controller.maybe_refresh_kef_ip_by_blind.assert_not_called()

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

    def test_scan_kef_devices_releases_blind_discovery_lock_after_cancelled_scan(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")

        with patch("kef_app.controller.discovery.recovery.discover_kef_devices", return_value=[]) as discover:
            result = controller.scan_kef_devices(should_continue=lambda: False)

        self.assertEqual(result, [])
        self.assertTrue(controller._blind_discovery_lock.acquire(blocking=False))
        controller._blind_discovery_lock.release()
        self.assertFalse(discover.call_args.kwargs["should_continue"]())

    def test_select_kef_device_updates_current_target(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
        identity = SpeakerIdentity(
            ip="192.168.1.20",
            mac="AABBCCDDEE02",
            speaker_name="Office Speaker",
            speaker_model="LS50 Wireless II",
        )

        changed = controller.select_kef_device(identity, trigger="unit_test")

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

        changed = controller.select_kef_device(identity, trigger="unit_test")

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

    def test_set_volume_uses_the_default_visible_step_policy(self):
        controller = self.make_controller(kef_ip="192.168.1.10")
        controller._ensure_target_identity = Mock(return_value=True)
        controller.get_speaker = Mock(return_value=Mock())
        controller._log_structured = Mock()

        self.assertTrue(controller.set_volume(40))

        controller._log_structured.assert_called_once_with(
            "STEP",
            action="SET_VOLUME",
            level=40,
            status="success",
        )

    def test_identity_probe_failure_logs_only_the_first_failure_and_offline_threshold(self):
        controller = self.make_controller(kef_ip="192.168.1.10", identity_probe_failure_threshold=3)
        controller.log.setLevel(logging.DEBUG)

        class CaptureHandler(logging.Handler):
            def __init__(self):
                super().__init__()
                self.records: list[logging.LogRecord] = []

            def emit(self, record: logging.LogRecord) -> None:
                self.records.append(record)

        capture = CaptureHandler()
        controller.log.addHandler(capture)
        self.addCleanup(controller.log.removeHandler, capture)

        for _ in range(5):
            controller.record_identity_probe_failure("ui_poll", "web_ui_poll", "identity_refresh_failed")

        warnings = [
            record for record in capture.records if record.getMessage().startswith("WARN action=IDENTITY_PROBE")
        ]
        self.assertEqual([record.levelno for record in warnings], [logging.WARNING, logging.WARNING])
        self.assertIn("failures=1", warnings[0].getMessage())
        self.assertIn("failures=3", warnings[1].getMessage())

    def test_ui_poll_recovery_skips_use_the_shared_debug_policy(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_mac="AA:BB:CC:DD:EE:01")
        controller._log_structured = Mock()
        controller.get_speaker = Mock(return_value=object())
        controller._backend.capture_identity = Mock(return_value=None)

        with patch("kef_app.controller.discovery.identity_probe.identify_kef_device", return_value=None):
            self.assertFalse(controller.capture_identity_from_current_ip("ui_poll", "web_ui_poll_identity"))

        with controller._ip_lock:
            controller._identity.last_mac_discovery_mono = controller.mono()
            controller._identity.last_blind_discovery_mono = controller.mono()
        self.assertFalse(controller.maybe_refresh_kef_ip_by_mac("ui_poll", "web_ui_poll_refresh"))
        self.assertFalse(controller.maybe_refresh_kef_ip_by_blind("ui_poll", "web_ui_poll_refresh"))

        skip_calls = [
            call
            for call in controller._log_structured.mock_calls
            if call.args == ("SKIP",) and str(call.kwargs.get("trigger", "")).startswith("web_ui_poll")
        ]
        skip_causes = {
            call.kwargs.get("cause")
            for call in skip_calls
        }
        self.assertTrue(
            {"identity_probe_failed", "cooldown", "blind_discovery_cooldown"}.issubset(skip_causes)
        )
        self.assertTrue(all("log_level" not in call.kwargs for call in skip_calls))

    def test_action_logger_binds_context_and_centralizes_default_mono(self):
        controller = self.make_controller()
        controller.log.setLevel(logging.DEBUG)

        class CaptureHandler(logging.Handler):
            def __init__(self):
                super().__init__()
                self.records: list[logging.LogRecord] = []

            def emit(self, record: logging.LogRecord) -> None:
                self.records.append(record)

        capture = CaptureHandler()
        controller.log.addHandler(capture)
        self.addCleanup(controller.log.removeHandler, capture)

        action_log = controller._action_log("WAKE", 7, "ui_test")
        action_log.write("STEP", step="request")

        self.assertEqual(len(capture.records), 1)
        message = capture.records[0].getMessage()
        self.assertIn("STEP action=WAKE | gen=7 | reason=ui_test | step=request", message)
        self.assertEqual(message.count("mono="), 1)
        action_log.write("STEP", action="STANDBY")
        self.assertIn("WARN action=STRUCTURED_LOGGING", capture.records[1].getMessage())
        self.assertIn("cause=bound_fields_conflict", capture.records[1].getMessage())
        self.assertIn("STEP action=WAKE", capture.records[2].getMessage())
        self.assertNotIn("action=STANDBY", capture.records[2].getMessage())

    def test_wake_completion_waits_for_a_real_state_poll_before_marking_on(self):
        controller = self.make_controller(kef_ip="192.168.1.10")
        events = self.capture_events(controller)

        controller._emit_power_action_finished("WAKE", "web_ui", "success_attempt_1")

        self.assertEqual(
            [name for name, _payload in events],
            ["power_action_finished"],
        )

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

    def test_external_ui_poll_treats_standby_source_as_powered_off(self):
        controller = self.make_controller(kef_ip="192.168.1.10")
        controller._read_ui_value = Mock(
            side_effect=[(True, True), ("standby", True), (None, False)]
        )
        controller._mark_identity_probe_success = Mock(return_value=False)

        result = controller.poll_external_ui_state("unit_test", "unit_test")

        self.assertEqual(result, ("standby", None, False))
        self.assertFalse(controller._runtime_speaker.power_on)
        self.assertFalse(controller._read_ui_value.call_args_list[0].kwargs["fresh"])

    def test_cached_ui_read_retries_once_with_a_fresh_connector(self):
        controller = self.make_controller(kef_ip="192.168.1.10")
        speaker = Mock(status="powerOn")
        controller.get_speaker = Mock(side_effect=[OSError("stale connector"), speaker])
        controller.reset_speaker = Mock()

        value, ok = controller._read_ui_value(
            "unit_test",
            "unit_test",
            fresh=False,
            step="speaker_status",
            reader=lambda current: current.status,
        )

        self.assertTrue(ok)
        self.assertEqual(value, "powerOn")
        controller.reset_speaker.assert_called_once_with()
        self.assertEqual(
            [call.kwargs["fresh"] for call in controller.get_speaker.call_args_list],
            [False, True],
        )

    def test_external_ui_poll_infers_on_from_live_input_when_status_is_unknown(self):
        controller = self.make_controller(kef_ip="192.168.1.10")
        controller._read_ui_value = Mock(
            side_effect=[(None, False), ("wifi", True), (31, True)]
        )
        controller._mark_identity_probe_success = Mock(return_value=False)

        result = controller.poll_external_ui_state("unit_test", "unit_test")

        self.assertEqual(result, ("wifi", 31, True))
        self.assertTrue(controller._runtime_speaker.power_on)

    def test_web_ui_poll_failure_logs_once_per_rate_limit_window(self):
        controller = self.make_controller(kef_ip="192.168.1.10")
        controller._log_structured = Mock()
        controller.mono = Mock(side_effect=[100.0, 101.0, 131.0])

        controller._log_ui_poll_failure(
            reason="web_ui_poll", trigger="web_ui_poll", step="speaker_status", error=TimeoutError("first")
        )
        controller._log_ui_poll_failure(
            reason="web_ui_poll", trigger="web_ui_poll", step="volume", error=TimeoutError("suppressed")
        )
        controller._log_ui_poll_failure(
            reason="web_ui_poll", trigger="web_ui_poll", step="volume", error=TimeoutError("later")
        )

        self.assertEqual(controller._log_structured.call_count, 2)
        self.assertEqual(controller._log_structured.call_args_list[0].kwargs["status"], "retrying_rate_limited")
        self.assertEqual(controller._log_structured.call_args_list[1].kwargs["step"], "volume")

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
        self.assertNotIn("log_level", transient_logs[0].kwargs)

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

    def test_display_on_wake_leaves_an_already_on_speaker_on_its_current_input(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_input="wifi")
        controller._ensure_target_identity = Mock(return_value=True)
        controller.wait_until_reachable = Mock(return_value=True)
        controller.get_input_source = Mock(return_value="optical")
        controller._set_speaker_source = Mock()
        controller._run_generation_attempts = Mock()

        self.assertTrue(controller.wake_kef(0, "DISPLAY_ON", skip_if_already_on=True))

        controller.get_input_source.assert_called_once_with(fresh=True)
        controller._set_speaker_source.assert_not_called()
        controller._run_generation_attempts.assert_not_called()

    def test_non_display_wake_keeps_forcing_the_configured_input(self):
        controller = self.make_controller(kef_ip="192.168.1.10", kef_input="wifi")
        controller._ensure_target_identity = Mock(return_value=True)
        controller.wait_until_reachable = Mock(return_value=True)
        controller.get_input_source = Mock(return_value="optical")
        controller._run_generation_attempts = Mock(return_value="success_attempt_1")

        self.assertTrue(controller.wake_kef(0, "startup"))

        controller.get_input_source.assert_not_called()
        controller._run_generation_attempts.assert_called_once()


if __name__ == "__main__":
    unittest.main()
