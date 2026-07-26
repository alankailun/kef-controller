from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

import win32con
import win32gui
import win32process
from PySide6.QtCore import QObject, QTimer, Signal

from ..config import AppConfig
from ..controller import KefPowerController
from ..storage import UserConfigStore
from .controller_events import ControllerEventBridge
from .logs import UILogHandler
from .web_api_server import WebApiServer
from .web_bridge import WebControllerBridge


class KefMainWindow(QObject):
    """Manage a native Edge WebView2 window outside Qt's embedding layer."""

    visibility_changed = Signal(bool)
    _WINDOW_TITLE = "KEF Controller"
    _HOST_HEARTBEAT_GRACE_S = 20.0
    _HOST_HEARTBEAT_TIMEOUT_S = 90.0
    _HOST_HEARTBEAT_CONFIRMATION_S = 15.0
    _HOST_RESTART_WINDOW_S = 300.0
    _HOST_MAX_RESTARTS_PER_WINDOW = 2

    def __init__(
        self,
        config: AppConfig,
        controller: KefPowerController,
        config_store: UserConfigStore,
        controller_bridge: ControllerEventBridge,
        log_handler: UILogHandler,
    ) -> None:
        super().__init__()
        self._log = controller.log
        self._bridge = WebControllerBridge(
            config, controller, config_store, controller_bridge, log_handler, self
        )
        self.visibility_changed.connect(self._bridge.set_ui_visible)
        self._server = WebApiServer(self._web_root(), self._bridge)
        self._server.start()
        self._bridge.state_changed.connect(lambda payload: self._server.publish("state", payload))
        self._bridge.toast.connect(lambda payload: self._server.publish("toast", payload))
        self._bridge.log_line.connect(lambda payload: self._server.publish("log", payload))

        self._host_process: subprocess.Popen[bytes] | None = None
        self._host_hwnd = 0
        self._find_attempts = 0
        self._host_ready_mono = 0.0
        self._heartbeat_suspect_mono = 0.0
        self._host_restart_times: deque[float] = deque()
        self._restart_limit_reported = False
        self._was_effectively_visible = False
        self._monitor = QTimer(self)
        self._monitor.setInterval(500)
        self._monitor.timeout.connect(self._monitor_host)

    @staticmethod
    def _web_root() -> Path:
        frozen_root = getattr(sys, "_MEIPASS", None)
        if frozen_root:
            return Path(frozen_root) / "kef_app" / "ui" / "web"
        return Path(__file__).resolve().parent / "web"

    def isVisible(self) -> bool:
        return self._effectively_visible()

    def _effectively_visible(self) -> bool:
        """Return whether the UI is actually usable, not merely unhidden.

        Win32 reports a minimized window as visible, while Chromium marks its
        document hidden.  Treating those states differently used to make the
        heartbeat watchdog restart a healthy minimized WebView2 window.
        """
        return bool(
            self._host_hwnd
            and win32gui.IsWindow(self._host_hwnd)
            and win32gui.IsWindowVisible(self._host_hwnd)
            and not win32gui.IsIconic(self._host_hwnd)
        )

    def _set_effective_visibility(self, visible: bool, *, refresh_heartbeat: bool = True) -> None:
        visible = bool(visible)
        if visible == self._was_effectively_visible:
            return
        if visible and refresh_heartbeat:
            # Give the browser's visibilitychange request time to arrive after
            # a restore instead of letting the next 500 ms watchdog tick race
            # a heartbeat that was intentionally paused while minimized.
            self._server._touch_client_activity()
        self._was_effectively_visible = visible
        self.visibility_changed.emit(visible)

    def show(self) -> None:
        # A hidden document intentionally stops its long-poll request.  Mark
        # the UI active before restoring the native window so the watchdog
        # cannot race the browser's visibilitychange handler and restart a
        # perfectly healthy WebView2 host.
        self._server._touch_client_activity()
        if self._restart_limit_reported:
            # A deliberate tray restore is a sensible manual recovery point.
            # Allow it to make a new limited recovery attempt instead of
            # leaving a previous automatic-restart cap permanently sticky.
            self._host_restart_times.clear()
            self._restart_limit_reported = False
        if self._host_hwnd and win32gui.IsWindow(self._host_hwnd):
            self._set_effective_visibility(True, refresh_heartbeat=False)
            win32gui.ShowWindow(self._host_hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(self._host_hwnd)
            return

        # Creating an Edge WebView2 window takes a moment.  Until its HWND is
        # discoverable, _host_hwnd is still zero, so treating that as "no host"
        # would start one child process for every tray/menu click.
        if self._host_is_alive():
            self._monitor.start()
            return
        self._launch_host()

    def hide(self) -> None:
        if self._host_hwnd and win32gui.IsWindow(self._host_hwnd):
            win32gui.ShowWindow(self._host_hwnd, win32con.SW_HIDE)
        self._set_effective_visibility(False)

    def toggle(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()

    def dispose(self) -> None:
        self._monitor.stop()
        # WM_CLOSE now means "hide" for the user-facing window, so shutdown
        # must explicitly tear down the one-file host's complete process tree.
        self._terminate_host_tree()
        self._host_process = None
        self._host_hwnd = 0
        self._set_effective_visibility(False)
        self._server.stop()

    def _host_is_alive(self) -> bool:
        return self._host_process is not None and self._host_process.poll() is None

    def _launch_host(self) -> None:
        if self._host_is_alive():
            return
        command = [sys.executable]
        if not getattr(sys, "frozen", False):
            command.append(str(Path(__file__).resolve().parents[2] / "main_gui.py"))
        command.extend(["--webview-host", self._server.url])
        self._log.info("Launching native Edge WebView2 host | url=%s", self._server.url)
        self._host_process = subprocess.Popen(
            command,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            close_fds=True,
        )
        self._host_hwnd = 0
        self._find_attempts = 0
        self._host_ready_mono = 0.0
        self._heartbeat_suspect_mono = 0.0
        self._set_effective_visibility(False)
        self._monitor.start()

    def _monitor_host(self) -> None:
        if self._host_process is not None and self._host_process.poll() is not None:
            self._host_process = None
            self._host_hwnd = 0
            self._monitor.stop()
            self._set_effective_visibility(False)
            return
        if not self._host_hwnd or not win32gui.IsWindow(self._host_hwnd):
            host_pid = self._host_process.pid if self._host_process is not None else None
            self._host_hwnd = self._find_host_window(host_pid)
            self._find_attempts += 1
            if not self._host_hwnd:
                if self._find_attempts == 20:
                    self._log.error("Native Edge WebView2 host window was not found after 10 seconds")
                    self._terminate_host_tree()
                    self._host_process = None
                    self._monitor.stop()
                    self._set_effective_visibility(False)
                return
            self._apply_native_window_effects(self._host_hwnd)
            self._host_ready_mono = time.monotonic()
            self._log.info("Native Edge WebView2 host is ready | hwnd=%s", self._host_hwnd)
        visible = self._effectively_visible()
        self._set_effective_visibility(visible)
        restart_reason = self._host_restart_reason(visible)
        if restart_reason:
            self._restart_unresponsive_host(restart_reason)
            return

    def _host_restart_reason(self, visible: bool) -> str | None:
        if not visible or not self._host_ready_mono:
            self._heartbeat_suspect_mono = 0.0
            return None
        now = time.monotonic()
        if now - self._host_ready_mono < self._HOST_HEARTBEAT_GRACE_S:
            return None
        activity_age = self._server.client_activity_age_s
        if activity_age <= self._HOST_HEARTBEAT_TIMEOUT_S:
            self._heartbeat_suspect_mono = 0.0
            return None

        # A missed JavaScript heartbeat alone is not enough evidence to kill a
        # healthy native window. Record the first miss, give the renderer a
        # short recovery window, and only then rebuild it. If the WinForms
        # message pump itself is hung, that is independent confirmation and we
        # can recover immediately.
        if not self._host_window_responds():
            return "native window stopped responding"
        if not self._heartbeat_suspect_mono:
            self._heartbeat_suspect_mono = now
            self._log.warning(
                "Native Edge WebView2 host missed UI heartbeats; waiting %.0fs before recovery | age_s=%.1f",
                self._HOST_HEARTBEAT_CONFIRMATION_S,
                activity_age,
            )
            return None
        if now - self._heartbeat_suspect_mono < self._HOST_HEARTBEAT_CONFIRMATION_S:
            return None

        while self._host_restart_times and now - self._host_restart_times[0] > self._HOST_RESTART_WINDOW_S:
            self._host_restart_times.popleft()
        if len(self._host_restart_times) >= self._HOST_MAX_RESTARTS_PER_WINDOW:
            if not self._restart_limit_reported:
                self._restart_limit_reported = True
                self._log.error(
                    "Native Edge WebView2 host recovery paused after %s restarts in %.0fs; hide and reopen the window to retry",
                    self._HOST_MAX_RESTARTS_PER_WINDOW,
                    self._HOST_RESTART_WINDOW_S,
                )
            return None
        return f"stopped sending UI heartbeats for {activity_age:.1f}s"

    def _host_window_responds(self) -> bool:
        """Check the native message pump without waiting indefinitely."""
        if not self._host_hwnd or not win32gui.IsWindow(self._host_hwnd):
            return False
        result = ctypes.c_size_t()
        try:
            sent = ctypes.windll.user32.SendMessageTimeoutW(
                self._host_hwnd,
                win32con.WM_NULL,
                0,
                0,
                0x0002,  # SMTO_ABORTIFHUNG
                250,
                ctypes.byref(result),
            )
            return bool(sent)
        except (AttributeError, OSError):
            # Keep the heartbeat's confirmed timeout as the fallback on an
            # unusual Windows API failure.
            return True

    def _restart_unresponsive_host(self, reason: str) -> None:
        """Recover a hung Edge renderer without restarting the controller."""
        self._host_restart_times.append(time.monotonic())
        self._heartbeat_suspect_mono = 0.0
        self._log.warning("Native Edge WebView2 host %s; restarting it", reason)
        self._terminate_host_tree()
        self._host_process = None
        self._host_hwnd = 0
        self._host_ready_mono = 0.0
        self._set_effective_visibility(False)
        self._launch_host()

    def _terminate_host_tree(self) -> None:
        """End the native host and any WebView2 child processes it owns."""
        process = self._host_process
        if process is None or process.poll() is not None:
            return
        try:
            result = subprocess.run(
                ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                capture_output=True,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                timeout=5,
            )
            if result.returncode == 0:
                return
            self._log.warning(
                "taskkill could not end native Edge WebView2 host tree | pid=%s | exit=%s",
                process.pid,
                result.returncode,
            )
        except (OSError, subprocess.TimeoutExpired):
            self._log.warning("Unable to end native Edge WebView2 host process tree", exc_info=True)
        # A direct terminate is only a fallback for an unavailable or failed
        # taskkill; under normal Windows operation /T removes the native host
        # and its WebView2 children in one operation.
        try:
            process.terminate()
        except OSError:
            self._log.debug("Native Edge WebView2 host already exited", exc_info=True)

    @classmethod
    def _find_host_window(cls, host_pid: int | None = None) -> int:
        matches: list[int] = []

        def callback(hwnd: int, _extra: object) -> bool:
            if win32gui.GetWindowText(hwnd) != cls._WINDOW_TITLE:
                return True
            class_name = win32gui.GetClassName(hwnd)
            if not class_name.startswith("WindowsForms10."):
                return True
            if host_pid is not None:
                _thread_id, window_pid = win32process.GetWindowThreadProcessId(hwnd)
                if window_pid != host_pid:
                    return True
            matches.append(hwnd)
            return True

        win32gui.EnumWindows(callback, None)
        return matches[-1] if matches else 0

    def _apply_native_window_effects(self, hwnd: int) -> None:
        try:
            dwm = ctypes.windll.dwmapi
            rounded = ctypes.c_int(2)
            mica = ctypes.c_int(2)
            for attribute, value in ((33, rounded), (38, mica)):
                dwm.DwmSetWindowAttribute(
                    ctypes.c_void_p(hwnd),
                    ctypes.c_uint(attribute),
                    ctypes.byref(value),
                    ctypes.sizeof(value),
                )
        except (AttributeError, OSError, ValueError):
            self._log.debug("Windows DWM effects are unavailable", exc_info=True)
