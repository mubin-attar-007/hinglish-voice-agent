"""The statistics the results report: rates with intervals, and reliability over k runs."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence


def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """The Wilson score interval for a proportion (95% by default)."""
    if n == 0:
        return (0.0, 1.0)
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def zero_event_bounds(n: int) -> dict[str, float]:
    """Upper 95% bounds on a rate when 0 events were seen in n trials."""
    if n == 0:
        return {"rule_of_three": 1.0, "exact": 1.0}
    return {"rule_of_three": min(1.0, 3 / n), "exact": 1 - 0.05 ** (1 / n)}


def pass_hat_k(passes: int, runs: int, k: int) -> float:
    """The unbiased estimate of passing all k of k runs, from `passes` out of `runs`."""
    if runs < k:
        raise ValueError("need at least k runs")
    return math.comb(passes, k) / math.comb(runs, k)


def bootstrap_mean(
    groups: Sequence[Sequence[float]], *, reps: int = 2000, seed: int = 0
) -> tuple[float, float]:
    """A 95% interval for the mean over groups (scenarios), resampling whole groups."""
    if not groups:
        return (0.0, 0.0)
    rng = random.Random(seed)
    means = sorted(
        sum(sum(g) / len(g) for g in (rng.choice(groups) for _ in groups)) / len(groups)
        for _ in range(reps)
    )
    last = reps - 1  # symmetric percentile indices: as many values below as above
    return (means[math.floor(0.025 * last)], means[math.ceil(0.975 * last)])
