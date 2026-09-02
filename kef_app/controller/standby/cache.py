from __future__ import annotations

import threading
from dataclasses import dataclass

from ...devices.transport.standby_request import build_standby_request_bytes


@dataclass(frozen=True, slots=True)
class FastStandbyCacheSnapshot:
    target_ip: str
    target_mac: str
    standby_request_bytes: bytes
    version: int
    updated_mono: float


class FastStandbySendCache:
    def __init__(self) -> None:
        self._snapshot: FastStandbyCacheSnapshot | None = None
        self._update_lock = threading.Lock()

    def read(self) -> FastStandbyCacheSnapshot | None:
        return self._snapshot

    def clear(self) -> None:
        with self._update_lock:
            self._snapshot = None

    def update(self, *, target_ip: str, target_mac: str, updated_mono: float) -> FastStandbyCacheSnapshot | None:
        target_ip = str(target_ip or "").strip()
        if not target_ip:
            self.clear()
            return None

        with self._update_lock:
            current = self._snapshot
            version = (current.version + 1) if current is not None else 1
            snapshot = FastStandbyCacheSnapshot(
                target_ip=target_ip,
                target_mac=str(target_mac or "").strip(),
                standby_request_bytes=build_standby_request_bytes(target_ip),
                version=version,
                updated_mono=updated_mono,
            )
            self._snapshot = snapshot
            return snapshot
