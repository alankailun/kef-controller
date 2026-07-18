from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import Mock, patch

import main_gui


class _Event:
    def __init__(self) -> None:
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


class MainGuiWebViewHostTests(unittest.TestCase):
    def test_host_starts_hidden_then_shows_after_load_and_hides_on_close(self) -> None:
        window = Mock()
        window.events = types.SimpleNamespace(closing=_Event(), loaded=_Event())
        webview = types.SimpleNamespace(create_window=Mock(return_value=window), start=Mock())

        with (
            patch.object(main_gui, "_webview2_is_ready", return_value=True),
            patch.dict(sys.modules, {"webview": webview}),
        ):
            main_gui._run_webview_host("http://127.0.0.1:4096/")

        self.assertTrue(webview.create_window.call_args.kwargs["hidden"])
        self.assertEqual(window.events.closing.handlers[0](), False)
        window.hide.assert_called_once_with()
        window.events.loaded.handlers[0]()
        window.show.assert_called_once_with()
        webview.start.assert_called_once_with(gui="edgechromium", debug=False)
