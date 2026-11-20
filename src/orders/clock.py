"""A virtual clock, so that timeouts and slow responses are simulated without real waiting."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30), "IST")


@dataclass
class VirtualClock:
    """Milliseconds since `start`. It moves forward only when told to."""

    start: datetime
    now_ms: int = 0

    def now(self) -> datetime:
        return self.start + timedelta(milliseconds=self.now_ms)

    def advance(self, ms: int) -> None:
        if ms < 0:
            raise ValueError("the clock only moves forward")
        self.now_ms += ms

    def advance_to(self, ms: int) -> None:
        self.now_ms = max(self.now_ms, ms)
