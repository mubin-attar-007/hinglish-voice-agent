"""Dated model prices, and the meter that enforces a run's cost cap.

Sources are in docs/DECISIONS.md. Prices change: re-check them before publishing a cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict

from voice_agent.llm.base import Usage


class Price(TypedDict):
    input_per_mtok_usd: float
    output_per_mtok_usd: float  # thinking tokens are billed as output
    checked: str
    source: str


_SOURCE = "https://aistudio.google.com/docs/pricing"

PRICES: dict[str, Price] = {
    # Standard paid tier; 3.8 Flash doubles to $1.50 / $7.50 from 1 Jan 2027.
    "gemini-3.8-flash": Price(
        input_per_mtok_usd=0.75, output_per_mtok_usd=3.75, checked="2026-11-20", source=_SOURCE
    ),
    "gemini-3.5-flash-lite": Price(
        input_per_mtok_usd=0.30, output_per_mtok_usd=2.50, checked="2026-11-20", source=_SOURCE
    ),
    "gemini-3.1-flash-lite": Price(
        input_per_mtok_usd=0.25, output_per_mtok_usd=1.50, checked="2026-11-20", source=_SOURCE
    ),
}


def cost_usd(input_tokens: int, output_tokens: int, price: Price | None) -> float:
    if price is None:
        return 0.0
    return (
        input_tokens / 1e6 * price["input_per_mtok_usd"]
        + output_tokens / 1e6 * price["output_per_mtok_usd"]
    )


class BudgetExceeded(RuntimeError):
    """The run's cost cap was reached; no further request is sent."""


@dataclass
class CostMeter:
    """Counts every request's tokens at list price, and refuses requests once the cap is hit."""

    price: Price | None
    cap_usd: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0  # includes thinking tokens
    thinking_tokens: int = 0
    requests: int = 0

    @property
    def spent_usd(self) -> float:
        return cost_usd(self.input_tokens, self.output_tokens, self.price)

    def check(self) -> None:
        """Call before each request."""
        if self.cap_usd is not None and self.spent_usd >= self.cap_usd:
            raise BudgetExceeded(f"cost cap of ${self.cap_usd:.2f} reached")

    def charge(self, usage: Usage) -> None:
        """Call after each response."""
        self.requests += 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.thinking_tokens += usage.thinking_tokens
