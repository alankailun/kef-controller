from __future__ import annotations

import json
import mimetypes
import threading
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from .web_bridge import WebControllerBridge


class WebApiServer:
    """Serve the bundled web UI and a loopback-only bridge for QtWebView."""

    def __init__(self, root: Path, bridge: "WebControllerBridge") -> None:
        self._root = root.resolve()
        self._bridge = bridge
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._events: deque[dict[str, object]] = deque(maxlen=500)
        self._events_lock = threading.Lock()
        self._next_event_id = 1

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("Web API server has not been started")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/"

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

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="WebApiServer")
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None

    def publish(self, channel: str, payload: str) -> None:
        with self._events_lock:
            event_id = self._next_event_id
            self._next_event_id += 1
            self._events.append({"id": event_id, "channel": channel, "payload": payload})

    def _updates_since(self, cursor: int) -> dict[str, object]:
        with self._events_lock:
            updates = [event.copy() for event in self._events if int(event["id"]) > cursor]
            latest = self._next_event_id - 1
        return {"cursor": latest, "updates": updates}

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
        content_type, _encoding = mimetypes.guess_type(str(candidate))
        handler.send_response(HTTPStatus.OK)
        handler.send_header("Content-Type", content_type or "application/octet-stream")
        handler.send_header("Content-Length", str(len(content)))
        handler.end_headers()
        handler.wfile.write(content)

    def _serve_api(self, handler: BaseHTTPRequestHandler) -> None:
        path = urlparse(handler.path).path
        if not path.startswith("/api/"):
            self._send_error(handler, HTTPStatus.NOT_FOUND, "Not found")
            return
        try:
            size = int(handler.headers.get("Content-Length", "0"))
            payload = json.loads(handler.rfile.read(size).decode("utf-8") or "{}")
            args = payload.get("args", []) if isinstance(payload, dict) else []
            if not isinstance(args, list):
                raise ValueError("args must be a list")
            method = path.rsplit("/", 1)[-1]
            if method == "updates":
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
