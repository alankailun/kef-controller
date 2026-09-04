from __future__ import annotations

import atexit
import ctypes
import logging
import os
import signal
import threading
import time
import traceback

import win32api
import win32con
import win32gui

from ..config import AppConfig
from ..controller import KefPowerController
from ..platform.windows.api import (
    DEVICE_NOTIFY_WINDOW_HANDLE,
    LID_CLOSED,
    MONITOR_DISPLAY_OFF,
    MONITOR_DISPLAY_ON,
    NOTIFY_FOR_THIS_SESSION,
    PBT_APMRESUMEAUTOMATIC,
    PBT_APMRESUMESUSPEND,
    PBT_APMSUSPEND,
    PBT_POWERSETTINGCHANGE,
    POWER_SETTING_GUIDS,
    WM_POWERBROADCAST,
    WM_WTSSESSION_CHANGE,
    WTS_SESSION_DESKTOP_READY,
    WTS_SESSION_LOCK,
    WTS_SESSION_UNLOCK,
    IpInterfaceChangeMonitor,
    RegisterPowerSettingNotification,
    UnregisterPowerSettingNotification,
    WTSRegisterSessionNotification,
    WTSUnRegisterSessionNotification,
    create_shutdown_block_reason,
    decode_power_setting_change,
    destroy_shutdown_block_reason,
    get_process_image_path,
    get_raw_command_line,
    guess_launch_source,
)
from ..structured_logging import log_structured
from .logging_setup import shutdown_logger


def uptime_seconds(process_start_mono: float) -> float:
    return time.monotonic() - process_start_mono


def log_startup_context(log: logging.Logger, process_start_wall: str):
    import sys

    pid = os.getpid()
    ppid = os.getppid()
    parent_image = get_process_image_path(ppid)
    launch_hint = guess_launch_source(parent_image)
    argv_repr = repr(sys.argv)
    raw_cmdline = get_raw_command_line()
    script_path = os.path.abspath(sys.argv[0]) if sys.argv else "<unknown>"

    log_structured(
        log,
        "EVENT",
        action="PROCESS",
        reason="startup",
        name="PROCESS_START",
        wall=process_start_wall,
        pid=pid,
        ppid=ppid,
        parent_image=parent_image,
        launch_hint=launch_hint,
    )
    log_structured(
        log,
        "EVENT",
        action="PROCESS",
        reason="startup",
        name="PROCESS_CONTEXT",
        cwd=os.getcwd(),
        executable=sys.executable,
        script=script_path,
    )
    log_structured(
        log, "EVENT", action="PROCESS", reason="startup", name="PROCESS_COMMAND_LINE", raw=raw_cmdline
    )
    log_structured(log, "EVENT", action="PROCESS", reason="startup", name="PROCESS_ARGV", argv=argv_repr)


class HeadlessRuntime:
    def __init__(self, config: AppConfig, controller: KefPowerController, log: logging.Logger):
        self.config = config
        self.controller = controller
        self.log = log
        self.process_start_mono = time.monotonic()
        self.process_start_wall = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        self._hwnd: int = 0
        self._hwnd_lock = threading.Lock()

    def _log(
        self,
        tag: str,
        *,
        reason: str = "runtime",
        trigger: str | None = None,
        **fields: object,
    ) -> None:
        log_structured(
            self.log,
            tag,
            action="HEADLESS_RUNTIME",
            reason=reason,
            trigger=trigger,
            **fields,
        )

    def request_stop(self) -> None:
        with self._hwnd_lock:
            hwnd = self._hwnd
        if hwnd:
            try:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception as exc:
                self._log("WARN", trigger="request_stop", cause="post_wm_close_failed", error=repr(exc))

    def _prepare_startup_connection(self) -> None:
        if not self.controller.get_current_kef_ip():
            self.controller.maybe_refresh_kef_ip(reason="startup_missing_ip", trigger="startup_missing_ip", force=True)
        try:
            self.controller.get_speaker(fresh=True)
            self.controller.capture_identity_from_current_ip(reason="startup_prebuild", trigger="startup_prebuild_success")
            self.controller.log_current_http_identity_snapshot(reason="startup_prebuild", trigger="startup_http_identity")
            self._log("STEP", reason="startup", trigger="initial_prebuild", step="prebuild_connection", status="ready", current_ip=self.controller.get_current_kef_ip() or "<empty>")
        except Exception as exc:
            self._log("WARN", reason="startup", trigger="initial_prebuild", cause="connection_prebuild_failed", current_ip=self.controller.get_current_kef_ip() or "<empty>", error=repr(exc))
            self.controller.reset_speaker()
            if self.controller.maybe_refresh_kef_ip(reason="startup_prebuild", trigger="startup_prebuild", force=True):
                try:
                    self.controller.get_speaker(fresh=True)
                    self.controller.capture_identity_from_current_ip(reason="startup_prebuild", trigger="startup_prebuild_recover_success")
                    self.controller.log_current_http_identity_snapshot(reason="startup_prebuild", trigger="startup_http_identity_recover_success")
                    self._log("STEP", reason="startup", trigger="initial_prebuild_recovery", step="prebuild_connection", status="recovered", current_ip=self.controller.get_current_kef_ip() or "<empty>")
                except Exception as recovery_error:
                    self._log("WARN", reason="startup", trigger="initial_prebuild_recovery", cause="connection_prebuild_failed_after_recovery", current_ip=self.controller.get_current_kef_ip() or "<empty>", error=repr(recovery_error))
                    self.controller.reset_speaker()

    def _start_controller_services(self) -> None:
        threading.Thread(target=self.controller.on_startup, daemon=True, name="StartupWake").start()
        self.controller.start_speaker_event_monitor("headless_runtime")
        self.controller.start_prewarmed_standby_socket_monitor("headless_runtime")
        self.controller.start_display_off_standby_dispatcher()

    def _handle_query_end_session(self, hwnd: int, wparam: int, lparam: int) -> bool:
        shutdown_block_active = False
        try:
            create_shutdown_block_reason(hwnd, "Putting the KEF speaker into standby")
            shutdown_block_active = True
            self._log("EVENT", reason="windows_end_session", trigger="wm_queryendsession", name="SHUTDOWN_BLOCK_CREATED", hwnd=hwnd)
        except Exception as exc:
            self._log("WARN", reason="windows_end_session", trigger="wm_queryendsession", cause="shutdown_block_create_failed", hwnd=hwnd, error=repr(exc))
        try:
            should_post_self_close = self.controller.on_query_end_session(wparam, lparam)
        finally:
            if shutdown_block_active:
                try:
                    destroy_shutdown_block_reason(hwnd)
                    self._log("EVENT", reason="windows_end_session", trigger="wm_queryendsession", name="SHUTDOWN_BLOCK_DESTROYED", hwnd=hwnd)
                except Exception as exc:
                    self._log("WARN", reason="windows_end_session", trigger="wm_queryendsession", cause="shutdown_block_destroy_failed", hwnd=hwnd, error=repr(exc))
        if should_post_self_close:
            try:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                self._log("EVENT", reason="windows_end_session", trigger="wm_queryendsession", name="WM_CLOSE_POSTED", hwnd=hwnd)
            except Exception as exc:
                self._log("WARN", reason="windows_end_session", trigger="wm_queryendsession", cause="post_wm_close_failed", hwnd=hwnd, error=repr(exc))
        return True

    def _handle_power_broadcast(self, wparam: int, lparam: int) -> bool:
        if wparam == PBT_POWERSETTINGCHANGE:
            event_mono = self.controller.mono()
            change = decode_power_setting_change(lparam)
            if change is None:
                self.controller.log_power_event("PBT_POWERSETTINGCHANGE_UNPARSED", wparam, lparam, event_mono=event_mono)
                return True
            if change.name == "GUID_CONSOLE_DISPLAY_STATE" and change.value == MONITOR_DISPLAY_OFF:
                self.controller._record_power_setting_event_state(change, event_mono)
                self.controller.on_display_off(event_mono)
                self.controller._log_power_setting_event_line(change, wparam, lparam, event_mono)
            else:
                self.controller.log_power_setting_event(change, wparam, lparam, event_mono=event_mono)
            if change.name == "GUID_LIDSWITCH_STATE_CHANGE" and change.value == LID_CLOSED:
                self.controller.on_lid_closed(
                    "POWER_LID_CLOSED",
                    event_mono,
                    callback_started_mono=event_mono,
                )
            elif change.name == "GUID_CONSOLE_DISPLAY_STATE" and change.value == MONITOR_DISPLAY_ON:
                self.controller.on_display_on(event_mono)
            return True
        if wparam == PBT_APMSUSPEND:
            event_mono = self.controller.mono()
            try:
                self.controller.log_power_event("PBT_APMSUSPEND", wparam, lparam, event_mono=event_mono)
                self.controller.on_suspend("PBT_APMSUSPEND", event_mono, callback_started_mono=event_mono)
            finally:
                self.controller.stop_speaker_event_monitor()
                self.controller.stop_prewarmed_standby_socket_monitor()
            return True
        if wparam in {PBT_APMRESUMEAUTOMATIC, PBT_APMRESUMESUSPEND}:
            reason = "PBT_APMRESUMEAUTOMATIC" if wparam == PBT_APMRESUMEAUTOMATIC else "PBT_APMRESUMESUSPEND"
            self.controller.log_power_event(reason, wparam, lparam)
            self.controller.start_display_off_standby_dispatcher()
            self.controller.start_prewarmed_standby_socket_monitor(reason)
            self.controller.start_speaker_event_monitor(reason)
            self.controller.on_resume(reason)
            return True
        self.controller.log_power_event("WM_POWERBROADCAST_OTHER", wparam, lparam)
        return True

    def _handle_session_change(self, wparam: int, lparam: int) -> int:
        if wparam == WTS_SESSION_LOCK:
            event_mono = self.controller.mono()
            self.controller._record_session_event_state("WTS_SESSION_LOCK", event_mono)
            self.controller._log_structured("EVENT", action="WINDOW_SESSION_EVENT", kind="SESSION", name="WTS_SESSION_LOCK_MSG_ENTRY", wparam=f"0x{wparam:04X}", session=lparam, async_worker=True, mono=f"{event_mono:.3f}")
            self.controller._log_session_event_line("WTS_SESSION_LOCK", wparam, lparam, event_mono)
            self.controller.on_lock("WTS_SESSION_LOCK", event_mono, callback_started_mono=event_mono)
            return 0
        if wparam == WTS_SESSION_UNLOCK:
            self.controller.log_session_event("WTS_SESSION_UNLOCK", wparam, lparam)
            self.controller.on_unlock("WTS_SESSION_UNLOCK")
            return 0
        if wparam == WTS_SESSION_DESKTOP_READY:
            self.controller.log_session_event("WTS_SESSION_DESKTOP_READY", wparam, lparam)
            self._log("SKIP", reason="WTS_SESSION_DESKTOP_READY", trigger="session_event", cause="diagnostic_only_no_wake")
            return 0
        self.controller.log_session_event("WM_WTSSESSION_CHANGE_OTHER", wparam, lparam)
        return 0

    def _register_message_window(self, wnd_proc) -> tuple[object, int, bool, list[tuple[str, int]], IpInterfaceChangeMonitor | None]:
        """Create the hidden window and register every Windows notification."""
        wc = win32gui.WNDCLASS()
        wc.lpfnWndProc = wnd_proc
        wc.lpszClassName = f"KEFController_{os.getpid()}"
        wc.hInstance = win32api.GetModuleHandle(None)
        win32gui.RegisterClass(wc)
        hwnd = win32gui.CreateWindow(wc.lpszClassName, self.config.app_name, 0, 0, 0, 0, 0, 0, 0, wc.hInstance, None)
        if not hwnd:
            raise RuntimeError("CreateWindow returned 0; power/session events cannot be received")
        with self._hwnd_lock:
            self._hwnd = hwnd

        session_registered = bool(WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION))
        if session_registered:
            self._log("EVENT", trigger="startup_registration", name="SESSION_NOTIFICATIONS_REGISTERED", hwnd=hwnd)
        else:
            self._log("WARN", trigger="startup_registration", cause="session_notifications_register_failed", hwnd=hwnd)

        handles: list[tuple[str, int]] = []
        for setting_name, setting_guid in POWER_SETTING_GUIDS:
            try:
                handle = RegisterPowerSettingNotification(hwnd, ctypes.byref(setting_guid), DEVICE_NOTIFY_WINDOW_HANDLE)
            except Exception as exc:
                self._log("WARN", trigger="startup_registration", cause="power_setting_register_failed", setting=setting_name, hwnd=hwnd, error=repr(exc))
                continue
            if handle:
                handles.append((setting_name, handle))
                self._log("EVENT", trigger="startup_registration", name="POWER_SETTING_REGISTERED", setting=setting_name, hwnd=hwnd, handle=handle)
            else:
                self._log("WARN", trigger="startup_registration", cause="power_setting_register_failed", setting=setting_name, hwnd=hwnd, error_code=ctypes.get_last_error())

        monitor: IpInterfaceChangeMonitor | None = None
        try:
            monitor = IpInterfaceChangeMonitor(self.controller.log_network_interface_event).start()
            if monitor.active:
                self._log("EVENT", trigger="startup_registration", name="NETWORK_NOTIFICATIONS_REGISTERED")
            else:
                self._log("WARN", trigger="startup_registration", cause="network_notifications_register_failed", error=monitor.error)
        except Exception as exc:
            self._log("WARN", trigger="startup_registration", cause="network_notifications_register_failed", error=repr(exc))
        return wc, hwnd, session_registered, handles, monitor

    def run(self):
        self.controller.log_banner()
        log_startup_context(self.log, self.process_start_wall)

        exit_reason = "main_return"
        last_exception_trace = None

        self._prepare_startup_connection()
        self._start_controller_services()

        resource_lock = threading.Lock()
        cleaned_up = False
        session_notify_registered = False
        power_notify_handles: list[tuple[str, int]] = []
        network_interface_monitor: IpInterfaceChangeMonitor | None = None
        class_registered = False
        hwnd = None
        wc = None

        def cleanup_resources(allow_destroy_window: bool = False):
            nonlocal cleaned_up, session_notify_registered, power_notify_handles, network_interface_monitor, class_registered, hwnd, wc
            with resource_lock:
                if cleaned_up:
                    first_cleanup = False
                else:
                    cleaned_up = True
                    first_cleanup = True

            if first_cleanup:
                with self._hwnd_lock:
                    self._hwnd = 0
                self.controller.stop_speaker_event_monitor()
                self.controller.stop_prewarmed_standby_socket_monitor()
                self.controller.stop_display_off_standby_dispatcher()

                if session_notify_registered and hwnd:
                    try:
                        WTSUnRegisterSessionNotification(hwnd)
                        self._log("EVENT", trigger="cleanup", name="SESSION_NOTIFICATIONS_UNREGISTERED", hwnd=hwnd)
                    except Exception as exc:
                        self._log(
                            "WARN", trigger="cleanup", cause="session_notifications_unregister_failed", hwnd=hwnd, error=repr(exc)
                        )
                    finally:
                        session_notify_registered = False

                for setting_name, handle in power_notify_handles:
                    try:
                        UnregisterPowerSettingNotification(handle)
                        self._log(
                            "EVENT", trigger="cleanup", name="POWER_SETTING_UNREGISTERED", setting=setting_name, handle=handle
                        )
                    except Exception as exc:
                        self._log(
                            "WARN",
                            trigger="cleanup",
                            cause="power_setting_unregister_failed",
                            setting=setting_name,
                            handle=handle,
                            error=repr(exc),
                        )
                power_notify_handles = []

                if network_interface_monitor is not None:
                    try:
                        network_interface_monitor.close()
                        self._log("EVENT", trigger="cleanup", name="NETWORK_NOTIFICATIONS_UNREGISTERED")
                    except Exception as exc:
                        self._log(
                            "WARN", trigger="cleanup", cause="network_notifications_unregister_failed", error=repr(exc)
                        )
                    finally:
                        network_interface_monitor = None

            if allow_destroy_window and hwnd:
                try:
                    if win32gui.IsWindow(hwnd):
                        win32gui.DestroyWindow(hwnd)
                        self._log("EVENT", trigger="cleanup", name="MESSAGE_WINDOW_DESTROYED", hwnd=hwnd)
                except Exception as exc:
                    self._log("WARN", trigger="cleanup", cause="message_window_destroy_failed", hwnd=hwnd, error=repr(exc))
                finally:
                    hwnd = None

            # WM_DESTROY is delivered while the window still belongs to its
            # class.  Unregistering here fails with WinError 1412, so defer
            # it until the destroy call has returned or PumpMessages exits.
            if allow_destroy_window and class_registered and wc is not None:
                try:
                    win32gui.UnregisterClass(wc.lpszClassName, wc.hInstance)
                    self._log("EVENT", trigger="cleanup", name="WINDOW_CLASS_UNREGISTERED", window_class=wc.lpszClassName)
                except Exception as exc:
                    self._log(
                        "WARN",
                        trigger="cleanup",
                        cause="window_class_unregister_failed",
                        window_class=wc.lpszClassName,
                        error=repr(exc),
                    )
                else:
                    class_registered = False

        def handle_signal(signum, _frame):
            try:
                signal_label = signal.Signals(signum).name
            except ValueError:
                signal_label = str(signum)

            self._log("EVENT", trigger="signal_handler", name="EXIT_SIGNAL_RECEIVED", signal=signal_label)
            try:
                if hwnd and win32gui.IsWindow(hwnd):
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                    return
            except Exception as exc:
                self._log(
                    "WARN", trigger="signal_handler", cause="post_wm_close_failed", error=repr(exc)
                )

            cleanup_resources(allow_destroy_window=True)
            raise SystemExit(0)

        atexit.register(cleanup_resources)

        if threading.current_thread() is threading.main_thread():
            for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
                sig = getattr(signal, sig_name, None)
                if sig is None:
                    continue
                try:
                    signal.signal(sig, handle_signal)
                except (ValueError, OSError) as exc:
                    self._log(
                        "WARN", trigger="signal_registration", cause="signal_handler_register_failed", signal=sig_name, error=repr(exc)
                    )
        else:
            self._log("SKIP", trigger="signal_registration", cause="background_thread")

        def wnd_proc(hwnd_, msg, wparam, lparam):
            try:
                if msg == win32con.WM_QUERYENDSESSION:
                    return self._handle_query_end_session(hwnd_, wparam, lparam)

                if msg == win32con.WM_ENDSESSION:
                    ending = bool(wparam)
                    self.controller.on_end_session(ending, lparam)
                    if ending and self.config.fast_exit_on_endsession:
                        self._log(
                            "EVENT",
                            reason="windows_end_session",
                            trigger="wm_endsession",
                            name="MESSAGE_LOOP_QUICK_EXIT",
                            hwnd=hwnd_,
                        )
                        cleanup_resources(allow_destroy_window=False)
                        win32gui.PostQuitMessage(0)
                    return 0

                if msg == WM_POWERBROADCAST:
                    return self._handle_power_broadcast(wparam, lparam)

                if msg == WM_WTSSESSION_CHANGE:
                    return self._handle_session_change(wparam, lparam)

                if msg == win32con.WM_CLOSE:
                    self._log("EVENT", trigger="wm_close", name="WM_CLOSE_RECEIVED", hwnd=hwnd_)
                    win32gui.DestroyWindow(hwnd_)
                    return 0

                if msg == win32con.WM_DESTROY:
                    cleanup_resources(allow_destroy_window=False)
                    win32gui.PostQuitMessage(0)
                    return 0

                return win32gui.DefWindowProc(hwnd_, msg, wparam, lparam)
            except Exception:
                self._log(
                    "ERROR",
                    trigger="wnd_proc",
                    cause="unhandled_exception",
                    error=traceback.format_exc(),
                    windows_message=msg,
                )
                if msg == win32con.WM_QUERYENDSESSION:
                    return True
                if msg in (win32con.WM_ENDSESSION, WM_WTSSESSION_CHANGE):
                    return 0
                if msg == WM_POWERBROADCAST:
                    return True
                return win32gui.DefWindowProc(hwnd_, msg, wparam, lparam)

        try:
            wc, hwnd, session_notify_registered, power_notify_handles, network_interface_monitor = self._register_message_window(wnd_proc)
            class_registered = True

            self._log("STATE", trigger="message_pump", desired="running", hwnd=hwnd)
            win32gui.PumpMessages()
            self._log("EVENT", trigger="message_pump", name="MESSAGE_PUMP_RETURNED")
        except SystemExit as exc:
            exit_reason = f"SystemExit({exc.code})"
            raise
        except Exception:
            exit_reason = "UnhandledException"
            last_exception_trace = traceback.format_exc()
            self._log("ERROR", trigger="run", cause="unhandled_exception", error=last_exception_trace)
            raise
        finally:
            self._log(
                "EVENT",
                reason="process_exit",
                trigger="run_finally",
                name="PROCESS_EXIT_BEGIN",
                exit_reason=exit_reason,
                pid=os.getpid(),
                ppid=os.getppid(),
                uptime_s=f"{uptime_seconds(self.process_start_mono):.1f}",
                cleaned_up=cleaned_up,
            )
            if last_exception_trace:
                self._log(
                    "EVENT",
                    reason="process_exit",
                    trigger="run_finally",
                    name="PROCESS_EXIT_CONTEXT",
                    status="exception_trace_logged",
                )
            cleanup_resources(allow_destroy_window=True)
            self._log(
                "EVENT",
                reason="process_exit",
                trigger="run_finally",
                name="PROCESS_EXIT_END",
                exit_reason=exit_reason,
                pid=os.getpid(),
                uptime_s=f"{uptime_seconds(self.process_start_mono):.1f}",
                class_registered=class_registered,
                session_notify_registered=session_notify_registered,
            )
            shutdown_logger(self.log)
            logging.shutdown()


def run_headless(config: AppConfig, controller: KefPowerController, log: logging.Logger):
    HeadlessRuntime(config, controller, log).run()
