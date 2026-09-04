from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import Mock, patch

import main_gui
from kef_app.platform.webview2_runtime import MIN_DOTNET_RELEASE, WebView2Readiness
from kef_app.ui.web_api_server import WEBVIEW_HOST_URL_ENV


class _Event:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class MainGuiWebViewHostTests(unittest.TestCase):
    def test_consumes_the_private_host_url_from_the_environment(self) -> None:
        url = "http://127.0.0.1:4096/?token=private-token"
        with patch.dict(main_gui.os.environ, {WEBVIEW_HOST_URL_ENV: url}, clear=False):
            self.assertEqual(main_gui._consume_webview_host_url(), url)
            self.assertNotIn(WEBVIEW_HOST_URL_ENV, main_gui.os.environ)

    def test_host_starts_hidden_then_shows_after_load_and_hides_on_close(self) -> None:
        window = Mock()
        window.events = types.SimpleNamespace(closing=_Event(), loaded=_Event())
        webview = types.SimpleNamespace(create_window=Mock(return_value=window), start=Mock())

        with (
            patch.object(
                main_gui,
                "check_webview2_readiness",
                return_value=WebView2Readiness("120.0.0.0", MIN_DOTNET_RELEASE),
            ) as readiness,
            patch.dict(sys.modules, {"webview": webview}),
        ):
            main_gui._run_webview_host("http://127.0.0.1:4096/")

        readiness.assert_called_once_with()
        self.assertTrue(webview.create_window.call_args.kwargs["hidden"])
        self.assertEqual(window.events.closing.handlers[0](), False)
        window.hide.assert_called_once_with()
        window.events.loaded.handlers[0]()
        window.show.assert_called_once_with()
        webview.start.assert_called_once_with(gui="edgechromium", debug=False)
