"""Isolated audit regressions. No speaker I/O or Windows setting changes.
Run from repository root: .venv\\Scripts\\python.exe tests\\review_repro.py
"""
from __future__ import annotations

import json
import logging
import sys
from collections import deque
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from PySide6.QtCore import QObject

from kef_app.config import AppConfig
from kef_app.controller import KefPowerController
from kef_app.devices.speaker_models import SpeakerIdentity
from kef_app.runtime.headless_service import HeadlessRuntime
from kef_app.ui.main_window import KefMainWindow
from kef_app.ui.web_bridge import WebControllerBridge

log = logging.getLogger("isolated-review")
log.handlers = [logging.NullHandler()]
log.propagate = False
results = {}

def controller():
    return KefPowerController(
        AppConfig().with_updates(kef_ip="192.168.1.10", kef_mac="AAAAAAAAAAAA"), log
    )

# 1. Trace the actual dependency's startup HTTP call at its final send boundary.
c = controller()
c.capture_identity_from_current_ip = Mock()
c.log_current_http_identity_snapshot = Mock()
runtime = HeadlessRuntime(c.config, c, log)
timeouts = []
def fake_send(session, request, **kwargs):
    timeouts.append(kwargs.get("timeout"))
    response = requests.Response()
    response.status_code = 200
    response._content = b'[{"i32_":20}]'
    return response
with patch("requests.sessions.Session.send", new=fake_send):
    runtime._prepare_startup_connection()
assert timeouts == [c.config.socket_timeout], timeouts
results["startup_request_timeout"] = timeouts

# 2. A stale recovery result must leave a newly selected target intact.
c = controller()
def finish_old_discovery(*args):
    assert args[0] == "AAAAAAAAAAAA"
    c.config.kef_ip = "192.168.1.20"
    c.config.kef_mac = "BBBBBBBBBBBB"
    c.apply_configured_device_target("user_selected_B")
    return "192.168.1.11"
with patch("kef_app.controller.discovery.recovery.discover_ip_by_mac", side_effect=finish_old_discovery):
    c.maybe_refresh_kef_ip_by_mac("old_recovery", "audit", force=True)
snapshot = c._fast_standby_send_cache.read()
assert c.config.kef_ip == "192.168.1.20"
assert snapshot.target_ip == "192.168.1.20"
assert snapshot.target_mac == "BBBBBBBBBBBB"
results["stale_recovery_rejected"] = {
    "configured_ip": c.config.kef_ip, "actual_ip": snapshot.target_ip,
    "actual_mac": snapshot.target_mac,
}

# 3. Failed prewarm heartbeats do not start background IP discovery.
c = controller()
c._prewarmed.last_ip = c.get_current_kef_ip()
c._prewarmed.last_ok_mono = c.mono()
c.poll_speaker_event_state = Mock(return_value=(None, None, None))
c.maybe_refresh_kef_ip = Mock(return_value=True)
with patch.object(c, "_ensure_persistent_prewarmed_socket", side_effect=OSError("old IP unreachable")):
    c._prewarmed_standby_tick("audit")
    c._prewarmed_standby_tick("audit")
    c._prewarmed_standby_tick("audit")
assert c._prewarmed.failures == 3
assert c._speaker_event_monitor_pause_cause() == "prewarmed_standby_unavailable"
with patch.object(c._speaker_events.stop, "wait", return_value=True):
    c._run_speaker_event_monitor("audit")
assert c.poll_speaker_event_state.call_count == 0
assert c.maybe_refresh_kef_ip.call_count == 0
results["no_background_recovery_after_heartbeat_failure"] = {
    "heartbeat_failures": c._prewarmed.failures,
    "event_poll_calls": c.poll_speaker_event_state.call_count,
    "recovery_calls": c.maybe_refresh_kef_ip.call_count,
}

# 4. A target change clears old identity and UI values.
c = controller()
c.update_identity_from_device_info(
    SpeakerIdentity(ip="192.168.1.10", mac="AAAAAAAAAAAA",
                    speaker_name="Speaker A", speaker_model="LS50 Wireless II"),
    "initial_A",
)
bridge = WebControllerBridge.__new__(WebControllerBridge)
QObject.__init__(bridge)
bridge._controller = c
bridge._config = c.config
bridge._speaker_on = True
bridge._input = "optical"
bridge._volume = 65
bridge._startup_registered = False
bridge._startup_mode = "none"
bridge._startup_busy = False
bridge._startup_snapshot_pending = False
bridge._startup_requested_mode = None
bridge._startup_requested_enabled = None
bridge._last_poll_success_mono = 1.0
bridge._last_action = None
bridge._last_failure = None
bridge._last_action_failure = None
bridge._target_signature = c.get_current_kef_target()
bridge.publish_state = Mock()
c.config.kef_ip = "192.168.1.20"
c.config.kef_mac = "BBBBBBBBBBBB"
c.apply_configured_device_target("selected_B")
bridge._on_identity_changed(c.get_current_identity())
mixed = bridge._runtime_state()["speaker"]
assert mixed["ip"] == "192.168.1.20"
assert mixed["name"] == "No device found" and mixed["volume"] is None and mixed["on"] is False
results["new_target_state_cleared"] = mixed

# 5. A standby between wake events permits a new wake.
c = controller()
c._schedule_delayed_wake = Mock()
c._enqueue_display_off_standby_task = Mock()
clock = [100.0]
c.mono = lambda: clock[0]
c.on_unlock("WTS_SESSION_UNLOCK")
old_generation = c._schedule_delayed_wake.call_args.args[0]
clock[0] = 100.2
c.on_lock("WTS_SESSION_LOCK", clock[0])
clock[0] = 100.5
c.on_unlock("WTS_SESSION_UNLOCK")
assert c._schedule_delayed_wake.call_count == 2
assert c._should_abort_generation(old_generation)
assert c._current_desired_state()[0] == "wake"
results["unlock_after_intervening_lock"] = {
    "scheduled_wakes": c._schedule_delayed_wake.call_count,
    "first_wake_is_stale": c._should_abort_generation(old_generation),
    "desired_state": c._current_desired_state()[0],
}

# 6. Native hangs honor the configured restart cap.
window = KefMainWindow.__new__(KefMainWindow)
QObject.__init__(window)
window._host_ready_mono = 1.0
window._server = Mock(client_activity_age_s=200.0)
window._heartbeat_suspect_mono = 0.0
window._host_restart_times = deque([900.0, 950.0])
window._restart_limit_reported = False
window._log = log
window._host_window_responds = Mock(return_value=False)
with patch("kef_app.ui.main_window.time.monotonic", return_value=1000.0):
    reason = window._host_restart_reason(True)
assert reason is None
assert len(window._host_restart_times) == window._HOST_MAX_RESTARTS_PER_WINDOW
results["native_hang_honors_restart_cap"] = reason

print(json.dumps(results, ensure_ascii=False, indent=2))

