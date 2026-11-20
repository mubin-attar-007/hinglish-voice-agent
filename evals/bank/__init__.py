"""The labelled sentence bank that measures the claim detector.

`dev.yaml` is for tuning and must score 100%. `heldout.yaml` is never tuned on; its recall is
what gets published, with every miss listed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

from orders.clock import IST
from voice_agent.gate.claims import ClaimKind, classify

BANK_DIR = Path(__file__).parent
BANK_NOW = datetime(2026, 11, 26, 10, 0, tzinfo=IST)


@dataclass(frozen=True)
class BankItem:
    text: str
    kind: ClaimKind
    caller_last: str | None = None  # what the caller said just before, when it matters


@dataclass(frozen=True)
class BankScore:
    n: int
    accuracy: float  # exact kind
    claims: int  # items labelled confirm, fail or state_slot
    claim_recall: float  # of those, the share detected as some claim
    false_alarms: int  # items not labelled a claim but detected as one
    misses: tuple[BankItem, ...]  # labelled a claim, detected as none


def load_bank(split: str) -> list[BankItem]:
    rows = yaml.safe_load((BANK_DIR / f"{split}.yaml").read_text(encoding="utf-8"))
    return [BankItem(row["text"], ClaimKind(row["kind"]), row.get("caller_last")) for row in rows]


def score(items: list[BankItem]) -> BankScore:
    detected = [classify(item.text, BANK_NOW, caller_last=item.caller_last).kind for item in items]
    claims = [
        (item, kind) for item, kind in zip(items, detected, strict=True) if item.kind.is_claim
    ]
    caught = sum(kind.is_claim for _, kind in claims)
    return BankScore(
        n=len(items),
        accuracy=sum(item.kind is kind for item, kind in zip(items, detected, strict=True))
        / len(items),
        claims=len(claims),
        claim_recall=caught / len(claims) if claims else 1.0,
        false_alarms=sum(
            kind.is_claim and not item.kind.is_claim
            for item, kind in zip(items, detected, strict=True)
        ),
        misses=tuple(item for item, kind in claims if not kind.is_claim),
    )
