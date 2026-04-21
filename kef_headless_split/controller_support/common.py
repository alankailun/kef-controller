from __future__ import annotations

import contextlib
import socket
import threading


_socket_timeout_lock = threading.Lock()


@contextlib.contextmanager
def temporary_socket_timeout(seconds: float):
    # socket.setdefaulttimeout is process-global; serialize updates so threads
    # do not trample each other's timeout settings.
    with _socket_timeout_lock:
        previous = socket.getdefaulttimeout()
        socket.setdefaulttimeout(seconds)
        try:
            yield
        finally:
            socket.setdefaulttimeout(previous)
