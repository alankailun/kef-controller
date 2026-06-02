from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal

from .prewarmed_standby_socket import PrewarmedStandbySendResult
from ..devices.transport import FireAndForgetShutdownResult


FastStandbyStatus = Literal["sent", "host_unreachable", "failed"]
FastStandbySource = Literal["prewarmed", "fire_and_forget"]


@dataclass(frozen=True, slots=True)
class FastStandbySendResult:
    status: FastStandbyStatus
    source: FastStandbySource
    prewarmed: PrewarmedStandbySendResult | None = None
    fire_and_forget: FireAndForgetShutdownResult | None = None

    @property
    def success(self) -> bool:
        return self.status == "sent"

    @property
    def host_unreachable(self) -> bool:
        return self.status == "host_unreachable"


class FastStandbySender:
    def __init__(
        self,
        send_prewarmed: Callable[[str], PrewarmedStandbySendResult],
        send_fire_and_forget: Callable[[str], FireAndForgetShutdownResult],
        should_continue: Callable[[], bool] | None = None,
    ):
        self._send_prewarmed = send_prewarmed
        self._send_fire_and_forget = send_fire_and_forget
        self._should_continue = should_continue

    def send(self, current_ip: str) -> FastStandbySendResult:
        prewarmed = self._send_prewarmed(current_ip)
        if prewarmed.attempted:
            if prewarmed.success:
                return FastStandbySendResult("sent", "prewarmed", prewarmed=prewarmed)
            if prewarmed.host_unreachable:
                return FastStandbySendResult("host_unreachable", "prewarmed", prewarmed=prewarmed)

        if self._should_continue is not None and not self._should_continue():
            return FastStandbySendResult("failed", "prewarmed", prewarmed=prewarmed)

        fire_and_forget = self._send_fire_and_forget(current_ip)
        if fire_and_forget.success:
            status: FastStandbyStatus = "sent"
        elif fire_and_forget.all_host_unreachable:
            status = "host_unreachable"
        else:
            status = "failed"

        return FastStandbySendResult(
            status,
            "fire_and_forget",
            prewarmed=prewarmed if prewarmed.attempted else None,
            fire_and_forget=fire_and_forget,
        )
