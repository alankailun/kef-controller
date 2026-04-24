from __future__ import annotations

import atexit
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
from ..platform.windows import (
    NOTIFY_FOR_THIS_SESSION,
    PBT_APMSUSPEND,
    PBT_APMRESUMEAUTOMATIC,
    PBT_APMRESUMESUSPEND,
    WM_POWERBROADCAST,
    WM_WTSSESSION_CHANGE,
    WTSRegisterSessionNotification,
    WTSUnRegisterSessionNotification,
    WTS_SESSION_DESKTOP_READY,
    WTS_SESSION_LOCK,
    WTS_SESSION_UNLOCK,
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

        resource_lock = threading.Lock()
        cleaned_up = False
        session_notify_registered = False
        class_registered = False
        hwnd = None
        wc = None

        def cleanup_resources(allow_destroy_window: bool = False):
            nonlocal cleaned_up, session_notify_registered, class_registered, hwnd, wc
            with resource_lock:
                if cleaned_up:
                    return
                cleaned_up = True
            with self._hwnd_lock:
                self._hwnd = 0

            if session_notify_registered and hwnd:
                try:
                    WTSUnRegisterSessionNotification(hwnd)
                    self.log.info(f"Unregistered session notifications | hwnd={hwnd}")
                except Exception as exc:
                    self.log.info(f"Failed to unregister session notifications | hwnd={hwnd} | {exc}")
                finally:
                    session_notify_registered = False

            if allow_destroy_window and hwnd:
                try:
                    if win32gui.IsWindow(hwnd):
                        win32gui.DestroyWindow(hwnd)
                        self.log.info(f"Destroyed the hidden message window | hwnd={hwnd}")
                except Exception as exc:
                    self.log.info(f"Failed to destroy the hidden message window | hwnd={hwnd} | {exc}")
                finally:
                    hwnd = None

            if class_registered and wc is not None:
                try:
                    win32gui.UnregisterClass(wc.lpszClassName, wc.hInstance)
                    self.log.info(f"Unregistered the window class | class={wc.lpszClassName}")
                except Exception as exc:
                    self.log.info(f"Failed to unregister the window class | class={wc.lpszClassName} | {exc}")
                finally:
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
                    should_post_self_close = self.controller.on_query_end_session(wparam, lparam)
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
                    if wparam == PBT_APMSUSPEND:
                        self.controller.log_power_event("PBT_APMSUSPEND", wparam, lparam)
                        self.controller.on_suspend("PBT_APMSUSPEND")
                        return True
                    if wparam == PBT_APMRESUMEAUTOMATIC:
                        self.controller.log_power_event("PBT_APMRESUMEAUTOMATIC", wparam, lparam)
                        self.controller.on_resume("PBT_APMRESUMEAUTOMATIC")
                        return True
                    if wparam == PBT_APMRESUMESUSPEND:
                        self.controller.log_power_event("PBT_APMRESUMESUSPEND", wparam, lparam)
                        self.controller.on_resume("PBT_APMRESUMESUSPEND")
                        return True
                    self.controller.log_power_event("WM_POWERBROADCAST_OTHER", wparam, lparam)
                    return True

                if msg == WM_WTSSESSION_CHANGE:
                    if wparam == WTS_SESSION_LOCK:
                        self.controller.log_session_event("WTS_SESSION_LOCK", wparam, lparam)
                        self.controller.on_lock("WTS_SESSION_LOCK")
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
            logging.shutdown()


def run_headless(config: AppConfig, controller: KefPowerController, log: logging.Logger):
    HeadlessRuntime(config, controller, log).run()
