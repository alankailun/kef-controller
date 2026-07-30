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
from .logging_setup import shutdown_logger
from ..platform.windows import (
    DEVICE_NOTIFY_WINDOW_HANDLE,
    IpInterfaceChangeMonitor,
    LID_CLOSED,
    MONITOR_DISPLAY_OFF,
    MONITOR_DISPLAY_ON,
    NOTIFY_FOR_THIS_SESSION,
    PBT_APMSUSPEND,
    PBT_APMRESUMEAUTOMATIC,
    PBT_APMRESUMESUSPEND,
    PBT_POWERSETTINGCHANGE,
    POWER_SETTING_GUIDS,
    RegisterPowerSettingNotification,
    UnregisterPowerSettingNotification,
    WM_POWERBROADCAST,
    WM_WTSSESSION_CHANGE,
    WTSRegisterSessionNotification,
    WTSUnRegisterSessionNotification,
    WTS_SESSION_DESKTOP_READY,
    WTS_SESSION_LOCK,
    WTS_SESSION_UNLOCK,
    create_shutdown_block_reason,
    decode_power_setting_change,
    destroy_shutdown_block_reason,
    get_process_image_path,
    get_raw_command_line,
    guess_launch_source,
)


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

    log.info(
        f"PROCESS_START | wall={process_start_wall} pid={pid} ppid={ppid} "
        f"parent_image={parent_image} launch_hint={launch_hint}"
    )
    log.info(f"PROCESS_CONTEXT | cwd={os.getcwd()} executable={sys.executable} script={script_path}")
    log.info(f"PROCESS_COMMAND_LINE | raw={raw_cmdline}")
    log.info(f"PROCESS_ARGV | argv={argv_repr}")


class HeadlessRuntime:
    def __init__(self, config: AppConfig, controller: KefPowerController, log: logging.Logger):
        self.config = config
        self.controller = controller
        self.log = log
        self.process_start_mono = time.monotonic()
        self.process_start_wall = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        self._hwnd: int = 0
        self._hwnd_lock = threading.Lock()

    def request_stop(self) -> None:
        with self._hwnd_lock:
            hwnd = self._hwnd
        if hwnd:
            try:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
            except Exception as exc:
                self.log.info(f"request_stop could not post WM_CLOSE | {exc}")

    def run(self):
        self.controller.log_banner()
        log_startup_context(self.log, self.process_start_wall)

        exit_reason = "main_return"
        last_exception_trace = None

        if not self.controller.get_current_kef_ip():
            self.controller.maybe_refresh_kef_ip(reason="startup_missing_ip", trigger="startup_missing_ip", force=True)

        try:
            self.controller.get_speaker(fresh=True)
            self.controller.capture_identity_from_current_ip(reason="startup_prebuild", trigger="startup_prebuild_success")
            self.controller.log_current_http_identity_snapshot(
                reason="startup_prebuild",
                trigger="startup_http_identity",
            )
            self.log.info(
                f"Prebuilt the initial KEF connection | ip={self.controller.get_current_kef_ip()} | "
                f"mono={self.controller.mono():.3f}"
            )
        except Exception as exc:
            self.log.info(
                f"Initial KEF prebuild failed; continuing and retrying later | "
                f"ip={self.controller.get_current_kef_ip()} | mono={self.controller.mono():.3f} | {exc}"
            )
            self.controller.reset_speaker()
            if self.controller.maybe_refresh_kef_ip(reason="startup_prebuild", trigger="startup_prebuild", force=True):
                try:
                    self.controller.get_speaker(fresh=True)
                    self.controller.capture_identity_from_current_ip(
                        reason="startup_prebuild",
                        trigger="startup_prebuild_recover_success",
                    )
                    self.controller.log_current_http_identity_snapshot(
                        reason="startup_prebuild",
                        trigger="startup_http_identity_recover_success",
                    )
                    self.log.info(
                        f"Recovered the IP and prebuilt the KEF connection | "
                        f"ip={self.controller.get_current_kef_ip()} | mono={self.controller.mono():.3f}"
                    )
                except Exception as exc2:
                    self.log.info(
                        f"Prebuild still failed after IP recovery | ip={self.controller.get_current_kef_ip()} | "
                        f"mono={self.controller.mono():.3f} | {exc2}"
                    )
                    self.controller.reset_speaker()

        threading.Thread(target=self.controller.on_startup, daemon=True, name="StartupWake").start()
        self.controller.start_speaker_event_monitor("headless_runtime")
        self.controller.start_prewarmed_standby_socket_monitor("headless_runtime")
        self.controller.start_display_off_standby_dispatcher()

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
                        self.log.info(f"Unregistered session notifications | hwnd={hwnd}")
                    except Exception as exc:
                        self.log.info(f"Failed to unregister session notifications | hwnd={hwnd} | {exc}")
                    finally:
                        session_notify_registered = False

                for setting_name, handle in power_notify_handles:
                    try:
                        UnregisterPowerSettingNotification(handle)
                        self.log.info(f"Unregistered power setting notification | setting={setting_name} | handle={handle}")
                    except Exception as exc:
                        self.log.info(
                            f"Failed to unregister power setting notification | setting={setting_name} | handle={handle} | {exc}"
                        )
                power_notify_handles = []

                if network_interface_monitor is not None:
                    try:
                        network_interface_monitor.close()
                        self.log.info("Unregistered network interface change notifications")
                    except Exception as exc:
                        self.log.info(f"Failed to unregister network interface change notifications | {exc}")
                    finally:
                        network_interface_monitor = None

            if allow_destroy_window and hwnd:
                try:
                    if win32gui.IsWindow(hwnd):
                        win32gui.DestroyWindow(hwnd)
                        self.log.info(f"Destroyed the hidden message window | hwnd={hwnd}")
                except Exception as exc:
                    self.log.info(f"Failed to destroy the hidden message window | hwnd={hwnd} | {exc}")
                finally:
                    hwnd = None

            # WM_DESTROY is delivered while the window still belongs to its
            # class.  Unregistering here fails with WinError 1412, so defer
            # it until the destroy call has returned or PumpMessages exits.
            if allow_destroy_window and class_registered and wc is not None:
                try:
                    win32gui.UnregisterClass(wc.lpszClassName, wc.hInstance)
                    self.log.info(f"Unregistered the window class | class={wc.lpszClassName}")
                except Exception as exc:
                    self.log.info(f"Failed to unregister the window class | class={wc.lpszClassName} | {exc}")
                else:
                    class_registered = False

        def handle_signal(signum, _frame):
            try:
                signal_label = signal.Signals(signum).name
            except ValueError:
                signal_label = str(signum)

            self.log.info(f"Received exit signal | signal={signal_label}")
            try:
                if hwnd and win32gui.IsWindow(hwnd):
                    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                    return
            except Exception as exc:
                self.log.info(f"Could not post WM_CLOSE during signal handling; cleaning up directly | {exc}")

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
                    self.log.info(f"Could not register signal handler | signal={sig_name} | {exc}")
        else:
            self.log.info("HeadlessRuntime is running on a background thread, so signal handlers were not registered")

        def wnd_proc(hwnd_, msg, wparam, lparam):
            try:
                if msg == win32con.WM_QUERYENDSESSION:
                    shutdown_block_active = False
                    try:
                        create_shutdown_block_reason(hwnd_, "Putting the KEF speaker into standby")
                        shutdown_block_active = True
                        self.log.info(
                            f"Created shutdown block reason while sending speaker standby | hwnd={hwnd_}"
                        )
                    except Exception as exc:
                        self.log.info(
                            f"Could not create shutdown block reason; continuing with bounded standby | hwnd={hwnd_} | {exc}"
                        )

                    try:
                        should_post_self_close = self.controller.on_query_end_session(wparam, lparam)
                    finally:
                        if shutdown_block_active:
                            try:
                                destroy_shutdown_block_reason(hwnd_)
                                self.log.info(
                                    f"Destroyed shutdown block reason after speaker standby | hwnd={hwnd_}"
                                )
                            except Exception as exc:
                                self.log.info(
                                    f"Could not destroy shutdown block reason | hwnd={hwnd_} | {exc}"
                                )
                    if should_post_self_close:
                        try:
                            win32gui.PostMessage(hwnd_, win32con.WM_CLOSE, 0, 0)
                            self.log.info(
                                f"WM_QUERYENDSESSION(CLOSEAPP) posted WM_CLOSE to self so the app can exit through its own cleanup path | hwnd={hwnd_}"
                            )
                        except Exception as exc:
                            self.log.info(f"WM_QUERYENDSESSION could not post WM_CLOSE | hwnd={hwnd_} | {exc}")
                    return True

                if msg == win32con.WM_ENDSESSION:
                    ending = bool(wparam)
                    self.controller.on_end_session(ending, lparam)
                    if ending and self.config.fast_exit_on_endsession:
                        self.log.info(
                            f"WM_ENDSESSION with ending=True is exiting the message loop quickly to avoid Application Hang | hwnd={hwnd_}"
                        )
                        cleanup_resources(allow_destroy_window=False)
                        win32gui.PostQuitMessage(0)
                    return 0

                if msg == WM_POWERBROADCAST:
                    if wparam == PBT_POWERSETTINGCHANGE:
                        event_mono = self.controller.mono()
                        change = decode_power_setting_change(lparam)
                        if change is None:
                            self.controller.log_power_event(
                                "PBT_POWERSETTINGCHANGE_UNPARSED",
                                wparam,
                                lparam,
                                event_mono=event_mono,
                            )
                            return True

                        if change.name == "GUID_CONSOLE_DISPLAY_STATE" and change.value == MONITOR_DISPLAY_OFF:
                            # Record enough state for cancellation, enqueue the
                            # resident sender, then write diagnostics.  A slow
                            # logging path must never stand before the first
                            # display-off send attempt.
                            self.controller._record_power_setting_event_state(change, event_mono)
                            self.controller.on_display_off(event_mono)
                            self.controller._log_power_setting_event_line(change, wparam, lparam, event_mono)
                        else:
                            self.controller.log_power_setting_event(change, wparam, lparam, event_mono=event_mono)
                        if change.name == "GUID_LIDSWITCH_STATE_CHANGE" and change.value == LID_CLOSED:
                            self.controller.dispatch_off_pump_standby(
                                "lid_closed",
                                "POWER_LID_CLOSED",
                                event_mono,
                                callback_started_mono=event_mono,
                                step="dispatch_bounded_early_standby",
                            )
                        elif change.name == "GUID_CONSOLE_DISPLAY_STATE" and change.value == MONITOR_DISPLAY_ON:
                            self.controller.on_display_on(event_mono)
                        return True

                    if wparam == PBT_APMSUSPEND:
                        event_mono = self.controller.mono()
                        try:
                            self.controller.log_power_event("PBT_APMSUSPEND", wparam, lparam, event_mono=event_mono)
                            self.controller.dispatch_off_pump_standby(
                                "suspend",
                                "PBT_APMSUSPEND",
                                event_mono,
                                callback_started_mono=event_mono,
                                step="dispatch_suspend_standby",
                            )
                        finally:
                            self.controller.stop_speaker_event_monitor()
                            self.controller.stop_prewarmed_standby_socket_monitor()
                        return True
                    if wparam == PBT_APMRESUMEAUTOMATIC:
                        self.controller.log_power_event("PBT_APMRESUMEAUTOMATIC", wparam, lparam)
                        self.controller.start_display_off_standby_dispatcher()
                        self.controller.start_prewarmed_standby_socket_monitor("PBT_APMRESUMEAUTOMATIC")
                        self.controller.start_speaker_event_monitor("PBT_APMRESUMEAUTOMATIC")
                        self.controller.on_resume("PBT_APMRESUMEAUTOMATIC")
                        return True
                    if wparam == PBT_APMRESUMESUSPEND:
                        self.controller.log_power_event("PBT_APMRESUMESUSPEND", wparam, lparam)
                        self.controller.start_display_off_standby_dispatcher()
                        self.controller.start_prewarmed_standby_socket_monitor("PBT_APMRESUMESUSPEND")
                        self.controller.start_speaker_event_monitor("PBT_APMRESUMESUSPEND")
                        self.controller.on_resume("PBT_APMRESUMESUSPEND")
                        return True
                    self.controller.log_power_event("WM_POWERBROADCAST_OTHER", wparam, lparam)
                    return True

                if msg == WM_WTSSESSION_CHANGE:
                    if wparam == WTS_SESSION_LOCK:
                        msg_entry_mono = self.controller.mono()
                        self.controller._record_session_event_state("WTS_SESSION_LOCK", msg_entry_mono)
                        state_recorded_mono = self.controller.mono()
                        self.controller._log_structured(
                            "EVENT",
                            log_level="info",
                            kind="SESSION",
                            name="WTS_SESSION_LOCK_MSG_ENTRY",
                            wparam=f"0x{wparam:04X}",
                            session=lparam,
                            async_worker=True,
                            mono=f"{msg_entry_mono:.3f}",
                        )
                        self.controller._log_session_event_line("WTS_SESSION_LOCK", wparam, lparam, msg_entry_mono)
                        self.controller.dispatch_off_pump_standby(
                            "lock",
                            "WTS_SESSION_LOCK",
                            msg_entry_mono,
                            state_recorded_mono=state_recorded_mono,
                            callback_started_mono=msg_entry_mono,
                            step="dispatch_bounded_early_standby",
                        )
                        return 0
                    if wparam == WTS_SESSION_UNLOCK:
                        self.controller.log_session_event("WTS_SESSION_UNLOCK", wparam, lparam)
                        self.controller.on_unlock("WTS_SESSION_UNLOCK")
                        return 0
                    if wparam == WTS_SESSION_DESKTOP_READY:
                        self.controller.log_session_event("WTS_SESSION_DESKTOP_READY", wparam, lparam)
                        self.log.info("WTS_SESSION_DESKTOP_READY is recorded for diagnostics only and does not trigger wake")
                        return 0
                    self.controller.log_session_event("WM_WTSSESSION_CHANGE_OTHER", wparam, lparam)
                    return 0

                if msg == win32con.WM_CLOSE:
                    self.log.info(f"Received WM_CLOSE | hwnd={hwnd_}")
                    win32gui.DestroyWindow(hwnd_)
                    return 0

                if msg == win32con.WM_DESTROY:
                    cleanup_resources(allow_destroy_window=False)
                    win32gui.PostQuitMessage(0)
                    return 0

                return win32gui.DefWindowProc(hwnd_, msg, wparam, lparam)
            except Exception:
                self.log.error("wnd_proc hit an unhandled exception:\n%s", traceback.format_exc())
                if msg == win32con.WM_QUERYENDSESSION:
                    return True
                if msg in (win32con.WM_ENDSESSION, WM_WTSSESSION_CHANGE):
                    return 0
                if msg == WM_POWERBROADCAST:
                    return True
                return win32gui.DefWindowProc(hwnd_, msg, wparam, lparam)

        try:
            wc = win32gui.WNDCLASS()
            wc.lpfnWndProc = wnd_proc
            wc.lpszClassName = f"KEFController_{os.getpid()}"
            wc.hInstance = win32api.GetModuleHandle(None)
            win32gui.RegisterClass(wc)
            class_registered = True

            hwnd = win32gui.CreateWindow(
                wc.lpszClassName,
                self.config.app_name,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                wc.hInstance,
                None,
            )
            if not hwnd:
                raise RuntimeError("CreateWindow returned 0; power/session events cannot be received")

            with self._hwnd_lock:
                self._hwnd = hwnd

            if WTSRegisterSessionNotification(hwnd, NOTIFY_FOR_THIS_SESSION):
                session_notify_registered = True
                self.log.info(f"Registered session lock/unlock notifications | hwnd={hwnd} | mono={self.controller.mono():.3f}")
            else:
                self.log.info(f"Failed to register session notifications | hwnd={hwnd} | mono={self.controller.mono():.3f}")

            for setting_name, setting_guid in POWER_SETTING_GUIDS:
                try:
                    handle = RegisterPowerSettingNotification(
                        hwnd,
                        ctypes.byref(setting_guid),
                        DEVICE_NOTIFY_WINDOW_HANDLE,
                    )
                except Exception as exc:
                    self.log.info(
                        f"Failed to register power setting notification | setting={setting_name} | hwnd={hwnd} | {exc}"
                    )
                    continue

                if handle:
                    power_notify_handles.append((setting_name, handle))
                    self.log.info(
                        f"Registered power setting notification | setting={setting_name} | hwnd={hwnd} | handle={handle} | "
                        f"mono={self.controller.mono():.3f}"
                    )
                else:
                    err = ctypes.get_last_error()
                    self.log.info(
                        f"Failed to register power setting notification | setting={setting_name} | hwnd={hwnd} | err={err} | "
                        f"mono={self.controller.mono():.3f}"
                    )

            try:
                network_interface_monitor = IpInterfaceChangeMonitor(self.controller.log_network_interface_event).start()
                if network_interface_monitor.active:
                    self.log.info(
                        f"Registered network interface change notifications | mono={self.controller.mono():.3f}"
                    )
                else:
                    self.log.info(
                        f"Failed to register network interface change notifications | "
                        f"error={network_interface_monitor.error} | mono={self.controller.mono():.3f}"
                    )
            except Exception as exc:
                self.log.info(
                    f"Failed to register network interface change notifications | {exc} | "
                    f"mono={self.controller.mono():.3f}"
                )

            self.log.info(f"Listening for shutdown, sleep, and session events | hwnd={hwnd} | mono={self.controller.mono():.3f}")
            win32gui.PumpMessages()
            self.log.info("PumpMessages returned")
        except SystemExit as exc:
            exit_reason = f"SystemExit({exc.code})"
            raise
        except Exception:
            exit_reason = "UnhandledException"
            last_exception_trace = traceback.format_exc()
            self.log.error("HeadlessRuntime hit an unhandled exception:\n%s", last_exception_trace)
            raise
        finally:
            self.log.info(
                f"PROCESS_EXIT_BEGIN | reason={exit_reason} pid={os.getpid()} "
                f"ppid={os.getppid()} uptime={uptime_seconds(self.process_start_mono):.1f}s cleaned_up={cleaned_up}"
            )
            if last_exception_trace:
                self.log.info("PROCESS_EXIT_CONTEXT | exception_trace was logged above")
            cleanup_resources(allow_destroy_window=True)
            self.log.info(
                f"PROCESS_EXIT_END | reason={exit_reason} pid={os.getpid()} "
                f"uptime={uptime_seconds(self.process_start_mono):.1f}s class_registered={class_registered} "
                f"session_notify_registered={session_notify_registered}"
            )
            shutdown_logger(self.log)
            logging.shutdown()


def run_headless(config: AppConfig, controller: KefPowerController, log: logging.Logger):
    HeadlessRuntime(config, controller, log).run()
