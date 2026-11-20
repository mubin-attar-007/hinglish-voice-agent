"""Sentence splitting for the gate, including streamed text in arbitrary chunks."""

from __future__ import annotations

import random

import pytest

from voice_agent.sentences import SentenceSplitter, split_sentences

REPLY = (
    "Ho gaya! Aapki delivery Saturday ko 4.30 baje ke baad hai. "
    "Kya main kuch aur madad karoon? Delivery 6 p.m. tak hogi। धन्यवाद"
)
EXPECTED = [
    "Ho gaya!",
    "Aapki delivery Saturday ko 4.30 baje ke baad hai.",
    "Kya main kuch aur madad karoon?",
    "Delivery 6 p.m. tak hogi।",
    "धन्यवाद",
]


def test_splits_on_endings_but_not_inside_numbers_or_abbreviations() -> None:
    assert split_sentences(REPLY) == EXPECTED


@pytest.mark.parametrize("seed", range(20))
def test_streaming_in_random_chunks_gives_the_same_sentences(seed: int) -> None:
    rng = random.Random(seed)
    splitter = SentenceSplitter()
    sentences: list[str] = []
    i = 0
    while i < len(REPLY):
        size = rng.randint(1, 7)
        sentences += splitter.feed(REPLY[i : i + size])
        i += size
    sentences += splitter.flush()
    assert sentences == EXPECTED


def test_pm_ends_a_sentence_but_p_m_continues_one() -> None:
    assert split_sentences("Your delivery is on Saturday between 4 and 6 PM. Which day?") == [
        "Your delivery is on Saturday between 4 and 6 PM.",
        "Which day?",
    ]
    assert split_sentences("Saturday 6 p.m. Which day?") == ["Saturday 6 p.m.", "Which day?"]
    assert split_sentences("Delivery 6 p.m. tak hogi.") == ["Delivery 6 p.m. tak hogi."]


def test_a_trailing_full_stop_waits_for_more_text() -> None:
    splitter = SentenceSplitter()
    assert splitter.feed("Delivery 4.") == []  # could still become "4.30"
    assert splitter.feed("30 baje hai. ") == ["Delivery 4.30 baje hai."]
