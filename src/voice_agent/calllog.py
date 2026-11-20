"""What happened in one call, as the agent saw it: what was said, decided and called.

The order system keeps its own append-only event log. Every spoken sentence here is stamped with
that log's sequence number, so graders can check each statement against the record as it stood
at the moment the caller heard it. No audio is kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from voice_agent.gate.claims import ClaimKind


@dataclass(frozen=True)
class Meaning:
    """What a sentence claims about the order: known for templates and mock models."""

    kind: ClaimKind
    slot_id: str | None = None


@dataclass
class Spoken:
    turn: int
    text: str
    source: Literal["model", "template", "holding"]
    meaning: Meaning | None  # ground truth when known; None for a real model's own words
    seq: int  # order-system event sequence number when it was spoken
    at_ms: int
    rule: str | None = None  # the gate rule that decided it
    agreed: bool | None = None  # for a rewrite: did the model's own claim match the record?
    history_index: int = -1  # the assistant message in the model's context it belongs to
    heard_text: str = ""  # what the caller heard; shorter if they barged in
    cut: bool = False
    heard: bool = True

    def __post_init__(self) -> None:
        if not self.heard_text:
            self.heard_text = self.text


@dataclass
class CallLog:
    spoken: list[Spoken] = field(default_factory=list)
    verdicts: list[dict[str, Any]] = field(default_factory=list)  # every gate decision
    tools: list[dict[str, Any]] = field(default_factory=list)  # every tool call and result
    steps: list[dict[str, Any]] = field(default_factory=list)  # every model step, with usage
    dropped: list[dict[str, Any]] = field(default_factory=list)  # model text that was not spoken
