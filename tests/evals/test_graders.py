"""Grader truth rules, and grades of trials whose outcome is known."""

from __future__ import annotations

from evals.graders.deterministic import grade, truth
from evals.harness.runner import mock_factory, run_trial
from evals.schema import load_corpus
from voice_agent.calllog import Meaning
from voice_agent.gate.claims import ClaimKind
from voice_agent.tools import SafetyConfig

SAT_4PM, FRI_2PM = "20261128-1600", "20261127-1400"


def test_truth_of_each_kind_of_claim() -> None:
    confirm = Meaning(ClaimKind.CONFIRM, SAT_4PM)
    assert truth(confirm, SAT_4PM, SAT_4PM) is True
    assert truth(confirm, FRI_2PM, SAT_4PM) is False  # asked for, not saved
    assert truth(confirm, SAT_4PM, None) is False  # no change was ever asked for
    fail = Meaning(ClaimKind.FAIL)
    assert truth(fail, FRI_2PM, SAT_4PM) is True
    assert truth(fail, SAT_4PM, SAT_4PM) is False  # a false failure
    assert truth(Meaning(ClaimKind.STATE_SLOT, FRI_2PM), FRI_2PM, None) is True
    assert truth(Meaning(ClaimKind.NONE), FRI_2PM, None) is True


def test_any_slot_that_fits_the_goal_passes_the_final_state() -> None:
    """An agent that reads "evening" as after 5 PM and books 6 to 8 has done what was asked."""
    from datetime import date, time

    from evals.graders.deterministic import _fits
    from evals.schema import Goal

    evening = Goal(date=date(2026, 11, 28), after=time(16))
    assert _fits("20261128-1800", evening, original=FRI_2PM) is True
    assert _fits("20261128-1200", evening, original=FRI_2PM) is False  # afternoon
    assert _fits(FRI_2PM, Goal(date=date(2026, 11, 27)), original=FRI_2PM) is False  # no change
    assert _fits(None, evening, original=FRI_2PM) is False


def scenario(name: str):  # type: ignore[no-untyped-def]
    return next(s for s in load_corpus("v0").scenarios if s.id == name)


def test_the_demo_passes_with_the_safety_layers_on() -> None:
    record = run_trial(
        scenario("interrupt-demo"),
        mock_factory("mock-reckless"),
        safety=SafetyConfig(),
        faults="on",
        seed_=0,
    )
    result = grade(record)
    assert result.passed, result.checks
    assert result.writes == 1


def test_without_a_deadline_a_good_faith_retry_double_writes() -> None:
    """Even a competent model double-books when a slow write lands after its retry."""
    record = run_trial(
        scenario("fault-slow-late"),
        mock_factory("mock-competent"),
        safety=SafetyConfig.baseline(),
        faults="on",
        seed_=0,
    )
    result = grade(record)
    assert result.duplicate_writes == 1
    assert not result.passed


def test_a_cancelled_change_writes_nothing_and_passes() -> None:
    record = run_trial(
        scenario("mind-cancel"),
        mock_factory("mock-competent"),
        safety=SafetyConfig(),
        faults="on",
        seed_=0,
    )
    result = grade(record)
    assert result.writes == 0
    assert result.passed, result.checks


def test_a_slot_that_fits_but_was_never_chosen_fails_the_final_state() -> None:
    """The agent must book the slot the caller chose, not just any slot in the window."""
    from types import SimpleNamespace

    from evals.graders.deterministic import _final_ok

    found = scenario("plain-hi-roman")
    chose_4pm = [{"what": "picked:20261128-1600", "seq": 9, "turn": 3, "said": "4 se 6 wala"}]

    def record(final: str, log: list[dict[str, object]]) -> object:
        return SimpleNamespace(
            scenario=found, final_slot=final, original_slot=FRI_2PM, caller_log=log
        )

    assert _final_ok(record(SAT_4PM, chose_4pm)) is True  # type: ignore[arg-type]
    assert _final_ok(record("20261128-1800", chose_4pm)) is False  # type: ignore[arg-type]
    assert _final_ok(record(SAT_4PM, [])) is False  # booked without any choice  # type: ignore[arg-type]
