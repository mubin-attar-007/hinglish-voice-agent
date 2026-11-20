"""The claim detector against the labelled sentence bank."""

from __future__ import annotations

from evals.bank import BANK_NOW, load_bank, score
from voice_agent.gate.claims import classify


def test_the_dev_split_is_classified_exactly() -> None:
    items = load_bank("dev")
    wrong = [
        (item.text, item.kind, kind)
        for item in items
        if (kind := classify(item.text, BANK_NOW).kind) is not item.kind
    ]
    assert wrong == []


def test_held_out_recall_is_measured_and_has_a_floor() -> None:
    """Held-out recall is measured, never tuned on. The floor only catches regressions.

    `python -m evals.bank` prints the full report, with every miss.
    """
    result = score(load_bank("heldout"))
    assert result.claim_recall >= 0.6, [miss.text for miss in result.misses]
