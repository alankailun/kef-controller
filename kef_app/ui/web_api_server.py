from __future__ import annotations

import json
import mimetypes
import secrets
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, quote, unquote, urlparse

if TYPE_CHECKING:
    from .web_bridge import WebControllerBridge


_STATIC_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}
_MAX_API_BODY_BYTES = 1 << 20


class WebApiServer:
    """Serve the bundled web UI and a loopback-only bridge for QtWebView."""

    def __init__(self, root: Path, bridge: "WebControllerBridge") -> None:
        self._root = root.resolve()
        self._bridge = bridge
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._events: deque[dict[str, object]] = deque(maxlen=500)
        self._events_condition = threading.Condition()
        self._next_event_id = 1
        self._api_token = secrets.token_urlsafe(32)
        self._last_client_activity_mono = time.monotonic()
        self._stopping = False

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("Web API server has not been started")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/?token={quote(self._api_token)}"

    def start(self) -> None:
        if self._server is not None:
            return
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                owner._serve_static(self)

            def do_POST(self) -> None:  # noqa: N802
                owner._serve_api(self)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        class LoopbackServer(ThreadingHTTPServer):
            # A pending long-poll must never delay shutdown of the tray app.
            daemon_threads = True

        self._stopping = False
        self._server = LoopbackServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="WebApiServer")
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        with self._events_condition:
            self._stopping = True
            self._events_condition.notify_all()
        self._server.shutdown()
        self._server.server_close()
        self._server = None
        thread = self._thread
        self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def publish(self, channel: str, payload: str) -> None:
        with self._events_condition:
            event_id = self._next_event_id
            self._next_event_id += 1
            self._events.append({"id": event_id, "channel": channel, "payload": payload})
            self._events_condition.notify_all()

    @property
    def client_activity_age_s(self) -> float:
        with self._events_condition:
            return max(0.0, time.monotonic() - self._last_client_activity_mono)

    def _touch_client_activity(self) -> None:
        with self._events_condition:
            self._last_client_activity_mono = time.monotonic()

    def _updates_since(self, cursor: int, timeout_s: float = 15.0) -> dict[str, object]:
        """Wait briefly for UI updates instead of forcing a 400 ms request loop.

        Keep this shorter than the browser's 20-second UPDATE_REQUEST_TIMEOUT_MS
        in ``index.html`` so a quiet but healthy connection is never aborted.
        """
        with self._events_condition:
            self._events_condition.wait_for(
                lambda: self._stopping or self._next_event_id - 1 > cursor,
                timeout=max(0.0, timeout_s),
            )
            updates = [event.copy() for event in self._events if int(event["id"]) > cursor]
            latest = self._next_event_id - 1
        return {"cursor": latest, "updates": updates}

    def _bootstrap(self) -> dict[str, object]:
        """Return one current state snapshot and the matching event baseline.

        A newly-created WebView2 host already receives the current state from
        ``initialState``. Replaying every state and toast accumulated while the
        window was hidden only creates a large burst of redundant DOM work.
        Taking the cursor before the snapshot keeps events produced during the
        snapshot available to the first long-poll request.
        """
        with self._events_condition:
            cursor = self._next_event_id - 1
        state = self._bridge.invoke_api("initialState", [])
        return {"state": state, "cursor": cursor}

    def _serve_static(self, handler: BaseHTTPRequestHandler) -> None:
        path = unquote(urlparse(handler.path).path)
        relative = "index.html" if path in {"", "/"} else path.lstrip("/")
        candidate = (self._root / relative).resolve()
        if self._root not in candidate.parents and candidate != self._root:
            self._send_error(handler, HTTPStatus.FORBIDDEN, "Forbidden")
            return
        if not candidate.is_file():
            self._send_error(handler, HTTPStatus.NOT_FOUND, "Not found")
            return
        content = candidate.read_bytes()
        content_type = _STATIC_CONTENT_TYPES.get(candidate.suffix.lower())
        if content_type is None:
            guessed_type, _encoding = mimetypes.guess_type(str(candidate))
            content_type = guessed_type or "application/octet-stream"
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(content)))
        handler.end_headers()
        handler.wfile.write(content)

    def _serve_api(self, handler: BaseHTTPRequestHandler) -> None:
        parsed = urlparse(handler.path)
        path = parsed.path
        if not path.startswith("/api/"):
            self._send_error(handler, HTTPStatus.NOT_FOUND, "Not found")
            return
        token = parse_qs(parsed.query).get("token", [""])[0]
        if not secrets.compare_digest(token, self._api_token):
            self._send_error(handler, HTTPStatus.FORBIDDEN, "Forbidden")
            return
        try:
            size = int(handler.headers.get("Content-Length", "0"))
            if size < 0 or size > _MAX_API_BODY_BYTES:
                self._send_error(handler, HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "Request body too large")
                return
            payload = json.loads(handler.rfile.read(size).decode("utf-8") or "{}")
            args = payload.get("args", []) if isinstance(payload, dict) else []
            if not isinstance(args, list):
                raise ValueError("args must be a list")
            method = path.rsplit("/", 1)[-1]
            if method == "bootstrap":
                result = self._bootstrap()
            elif method == "updates":
                # Only the long-poll represents a live browser renderer.
                # Loading logs or opening a folder must not mask a stalled UI
                # from the host monitor.
                self._touch_client_activity()
                cursor = int(args[0]) if args else 0
                result = self._updates_since(max(0, cursor))
            else:
                result = self._bridge.invoke_api(method, args)
            self._send_json(handler, HTTPStatus.OK, {"result": result})
        except Exception as exc:
            self._send_json(handler, HTTPStatus.BAD_REQUEST, {"error": str(exc)})

    @staticmethod
    def _send_json(handler: BaseHTTPRequestHandler, status: HTTPStatus, payload: object) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    @staticmethod
    def _send_error(handler: BaseHTTPRequestHandler, status: HTTPStatus, message: str) -> None:
        WebApiServer._send_json(handler, status, {"error": message})
