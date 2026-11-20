"""The M1 criterion, run in CI: corpus v0 with faults on, a mocked model, no network.

- With every safety layer on, an unreliable mock produces zero duplicate writes, zero false
  confirmations and zero false failures.
- The same calls with every layer off produce all three, so the first result is not vacuous.

Bounds from these runs describe the mock only, not any real model.
"""

from __future__ import annotations

import pytest

from evals.graders.deterministic import Grade, grade
from evals.harness.runner import mock_factory, run_corpus
from evals.schema import load_corpus
from voice_agent.tools import SafetyConfig

SEEDS = [0, 1, 2]


def run(profile: str, safety: SafetyConfig) -> list[Grade]:
    corpus = load_corpus("v0")
    records = run_corpus(
        corpus.scenarios, mock_factory(profile), safety=safety, faults="on", seeds=SEEDS
    )
    return [grade(record) for record in records]


def total(grades: list[Grade], field: str) -> int:
    return sum(getattr(g, field) for g in grades)


@pytest.mark.parametrize("profile", ["mock-competent", "mock-reckless"])
def test_with_every_safety_layer_on_the_guarantee_holds(profile: str) -> None:
    grades = run(profile, SafetyConfig())
    assert total(grades, "duplicate_writes") == 0
    assert total(grades, "false_confirmations") == 0
    assert total(grades, "false_failures") == 0
    assert all(g.passed for g in grades), [g.scenario_id for g in grades if not g.passed]


def test_with_every_safety_layer_off_the_same_calls_fail_in_every_way() -> None:
    grades = run("mock-reckless", SafetyConfig.baseline())
    assert total(grades, "duplicate_writes") > 0
    assert total(grades, "false_confirmations") > 0
    assert total(grades, "false_failures") > 0


def test_paraphrased_claims_are_the_measured_limit_of_the_detector() -> None:
    """Writes stay safe; claims in words the detector doesn't know can still get through."""
    grades = run("mock-reckless-paraphrase", SafetyConfig())
    assert total(grades, "duplicate_writes") == 0
    assert total(grades, "false_failures") == 0
