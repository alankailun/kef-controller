"""
KEF Controller - GUI entry point (system tray + main window)

Run (no console window):
    pythonw main_gui.py

Package as .exe:
    pyinstaller --noconsole --onefile --name "KEF Controller" main_gui.py
"""
from __future__ import annotations

import os
import sys

from kef_app.platform.webview2_runtime import check_webview2_readiness
from kef_app.ui.web_api_server import WEBVIEW_HOST_URL_ENV


def _webview2_requirement_message() -> str:
    readiness = check_webview2_readiness()
    details: list[str] = []
    if readiness.missing_runtime:
        details.append("Microsoft Edge WebView2 Runtime")
    if readiness.missing_dotnet:
        details.append("Microsoft .NET Framework 4.6.2 或更高版本")
    requirement = "、".join(details) or "Microsoft Edge WebView2 Runtime"
    return (
        "KEF Controller 无法启动界面，因为此电脑缺少：\n\n"
        f"{requirement}\n\n"
        "请重新运行 KEF Controller 安装程序（保持联网），安装程序会自动安装 WebView2。"
        "程序已停止，以避免显示空白窗口。"
    )


def _show_webview2_requirement_message() -> None:
    import ctypes

    ctypes.windll.user32.MessageBoxW(None, _webview2_requirement_message(), "KEF Controller", 0x10)


def _webview2_is_ready() -> bool:
    return check_webview2_readiness().ready


def _run_webview_host(url: str) -> None:
    if not _webview2_is_ready():
        _show_webview2_requirement_message()
        raise SystemExit(2)

    import webview

    window = webview.create_window(
        "KEF Controller",
        url,
        # Keep the normal launch compact.  The window remains resizable and
        # can still be maximized, but the controller does not open as a large
        # mostly-empty canvas on wide displays.
        width=1120,
        height=760,
        min_size=(840, 620),
        # Do not expose the WinForms/WebView2 placeholder while the one-file
        # host is starting.  The page is still loaded in the hidden window and
        # becomes visible as soon as WebView2 reports it is ready.
        hidden=True,
        background_color="#f4f5f6",
        text_select=True,
    )

    def hide_instead_of_close() -> bool:
        """Keep the warm host alive when the native close button is pressed."""
        window.hide()
        return False

    def show_after_page_load() -> None:
        window.show()

    window.events.closing += hide_instead_of_close
    window.events.loaded += show_after_page_load
    webview.start(gui="edgechromium", debug=False)


def _consume_webview_host_url() -> str:
    """Consume the private host URL without leaving its token in child envs."""
    url = os.environ.pop(WEBVIEW_HOST_URL_ENV, "")
    if not url:
        raise SystemExit("Missing WebView host URL")
    return url


if "--webview-host" in sys.argv:
    _run_webview_host(_consume_webview_host_url())
    raise SystemExit(0)


if "--webview2-import-check" in sys.argv:
    if not _webview2_is_ready():
        raise SystemExit(2)

    import webview
    import webview.platforms.edgechromium as edgechromium

    assert webview and edgechromium
    raise SystemExit(0)

def main() -> None:
    from kef_app.runtime.bootstrap import build_runtime_context, exit_if_startup_task_repair_requested

    exit_if_startup_task_repair_requested()
    if not _webview2_is_ready():
        _show_webview2_requirement_message()
        raise SystemExit(2)

    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    from kef_app.runtime.headless_service import HeadlessRuntime
    from kef_app.ui import KefTrayApp
    from kef_app.ui.app_icon import apply_application_icon, configure_windows_app_user_model_id
    from kef_app.ui.logs import UILogHandler

    configure_windows_app_user_model_id()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("KEF Controller")
    app.setFont(QFont("Segoe UI Variable Text", 10))
    apply_application_icon(app)

    log_handler = UILogHandler()
    runtime_context = build_runtime_context(extra_log_handlers=[log_handler])

    runtime = HeadlessRuntime(runtime_context.config, runtime_context.controller, runtime_context.log)
    tray = KefTrayApp(
        runtime_context.config,
        runtime_context.controller,
        runtime_context.user_config_store,
        runtime_context.log,
        app,
        runtime,
        log_handler,
    )
    tray.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
