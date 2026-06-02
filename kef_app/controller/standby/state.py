from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


EarlyStandbyStatus = Literal["NONE", "UNCONFIRMED"]


@dataclass(slots=True)
class EarlyStandbyState:
    status: EarlyStandbyStatus = "NONE"
    last_sent_unconfirmed_mono: float = 0.0

    def clear(self) -> None:
        self.status = "NONE"
        self.last_sent_unconfirmed_mono = 0.0

    def mark_sent_unconfirmed(self, mono: float) -> None:
        self.status = "UNCONFIRMED"
        self.last_sent_unconfirmed_mono = mono

    def is_confirmed_recent(self) -> bool:
        return False
