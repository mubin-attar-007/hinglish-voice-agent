"""Statistics, checked against brute force and known values."""

from __future__ import annotations

import itertools
import math

import pytest

from evals.stats import bootstrap_mean, pass_hat_k, wilson, zero_event_bounds


def test_pass_hat_k_matches_brute_force_over_all_k_subsets() -> None:
    runs = [True, True, False, True, True]
    for k in range(1, 6):
        subsets = list(itertools.combinations(runs, k))
        brute = sum(all(s) for s in subsets) / len(subsets)
        assert pass_hat_k(sum(runs), len(runs), k) == pytest.approx(brute)


def test_pass_hat_k_needs_enough_runs() -> None:
    with pytest.raises(ValueError):
        pass_hat_k(2, 2, 3)


def test_wilson_interval_known_value() -> None:
    low, high = wilson(0, 60)
    assert low == 0.0
    assert high == pytest.approx(0.0602, abs=1e-3)


def test_zero_event_bounds() -> None:
    bounds = zero_event_bounds(60)
    assert bounds["rule_of_three"] == pytest.approx(0.05)
    assert bounds["exact"] == pytest.approx(1 - 0.05 ** (1 / 60))
    assert bounds["exact"] < bounds["rule_of_three"]


def test_bootstrap_interval_contains_the_mean_and_is_deterministic() -> None:
    groups = [[1.0, 1.0, 0.0], [1.0, 1.0, 1.0], [0.0, 0.0, 1.0], [1.0, 0.0, 1.0]]
    low, high = bootstrap_mean(groups)
    mean = sum(sum(g) / len(g) for g in groups) / len(groups)
    assert low <= mean <= high
    assert (low, high) == bootstrap_mean(groups)
    assert not math.isnan(low)
