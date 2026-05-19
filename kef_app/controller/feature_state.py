from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class EarlyStandbyDedupState:
    last_success_mono: float = 0.0

    def clear(self) -> None:
        self.last_success_mono = 0.0

    def mark_success(self, mono: float) -> None:
        self.last_success_mono = mono

    def is_recent(self, mono: float, window: float) -> bool:
        return self.last_success_mono > 0.0 and (mono - self.last_success_mono) < window
