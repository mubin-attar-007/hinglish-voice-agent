"""Dated model prices, for the cost line of a results file. Sources are in docs/DECISIONS.md.

Prices change: re-check them before publishing a cost.
"""

from __future__ import annotations

from typing import TypedDict


class Price(TypedDict):
    input_per_mtok_usd: float
    output_per_mtok_usd: float
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
