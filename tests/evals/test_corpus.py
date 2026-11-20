"""Corpus v0 integrity: it loads, it covers every category, and its expectations are possible."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from evals.schema import load_corpus
from orders.db import DEFAULT_WINDOWS, slot_id_for


def test_corpus_v0_has_twenty_scenarios_across_every_category() -> None:
    corpus = load_corpus("v0")
    assert len(corpus.scenarios) == 20
    assert Counter(s.category for s in corpus.scenarios) == {
        "plain": 3,
        "interruption": 3,
        "code_mixed_time": 3,
        "clarification": 2,
        "unavailable": 2,
        "fault": 3,
        "change_of_mind": 2,
        "bad_input": 2,
    }
    assert len(corpus.sha256) == 64


def test_every_expected_final_slot_exists_in_its_world() -> None:
    for scenario in load_corpus("v0").scenarios:
        world = scenario.world
        slots = {
            slot_id_for(world.first_day + timedelta(days=d), start)
            for d in range(world.days)
            for start, _ in DEFAULT_WINDOWS
        }
        assert scenario.expected_final_slot in slots, scenario.id


def test_fault_modes() -> None:
    demo = next(s for s in load_corpus("v0").scenarios if s.id == "interrupt-demo")
    plain = next(s for s in load_corpus("v0").scenarios if s.id == "plain-en")
    assert len(demo.fault_plan("on").rules) == 1  # its own timeout after commit, nothing added
    assert [r.kind.value for r in plain.fault_plan("on").rules] == ["timeout_after_commit"]
    assert plain.fault_plan("scenario").rules == ()
    assert demo.fault_plan("off").rules == ()
