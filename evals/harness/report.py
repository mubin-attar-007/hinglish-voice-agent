"""Turn graded trials into the versioned results JSON: every number with its method and sample."""

from __future__ import annotations

import subprocess
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from evals.graders.deterministic import Grade
from evals.harness.runner import TrialRecord
from evals.schema import Corpus
from evals.stats import bootstrap_mean, pass_hat_k, wilson, zero_event_bounds
from orders.clock import IST

SCHEMA = "hinglish-voice-agent/results/v1"
MOCK_NOTE = (
    "A mocked model: a rule-based test double, some profiles unreliable on purpose. These numbers "
    "describe the mock and the safety layers around it, not any real language model."
)


def _git_commit() -> str:
    """The code's commit, marked "-dirty" if tracked files outside results/ differ from it."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, check=True
        )
        diff = ["git", "diff", "--quiet", "HEAD", "--", ".", ":(exclude)results"]
        dirty = subprocess.run(diff, check=False).returncode != 0
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip() + ("-dirty" if dirty else "")


def _rate(trials_with: int, n: int, total: int, method: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "count": total,
        "trials_with_any": trials_with,
        "trials": n,
        "rate_per_trial": trials_with / n if n else 0.0,
        "ci95": list(wilson(trials_with, n)),
        "method": method,
    }
    if trials_with == 0:
        entry["upper95_when_zero"] = zero_event_bounds(n)
    return entry


def summarise(
    records: Sequence[TrialRecord],
    grades: Sequence[Grade],
    *,
    corpus: Corpus,
    k: int,
    seeds: Sequence[int],
    prices: dict[str, float] | None = None,
) -> dict[str, Any]:
    first = records[0]
    by_scenario: dict[str, list[Grade]] = defaultdict(list)
    for grade in grades:
        by_scenario[grade.scenario_id].append(grade)
    n = len(grades)

    def count(field: str) -> tuple[int, int]:
        values = [getattr(g, field) for g in grades]
        return sum(1 for v in values if v), sum(values)

    passes = [[1.0 if g.passed else 0.0 for g in group] for group in by_scenario.values()]
    hat_k = [
        pass_hat_k(sum(g.passed for g in group), len(group), min(k, len(group)))
        for group in by_scenario.values()
    ]
    gate_totals: dict[str, int] = defaultdict(int)
    for grade in grades:
        for key, value in grade.gate.items():
            gate_totals[key] += value
    tokens_in = sum(step["input_tokens"] for r in records for step in r.log.steps)
    tokens_out = sum(step["output_tokens"] for r in records for step in r.log.steps)
    prices = prices or {}
    usd = tokens_in / 1e6 * prices.get("input_per_mtok_usd", 0.0) + tokens_out / 1e6 * prices.get(
        "output_per_mtok_usd", 0.0
    )

    clarify_required = [
        g
        for g in grades
        if next(
            r for r in records if r.scenario.id == g.scenario_id
        ).scenario.expected.clarify_before_write
    ]
    dup_with, dup_total = count("duplicate_writes")
    fc_with, fc_total = count("false_confirmations")
    ff_with, ff_total = count("false_failures")
    ux_with, ux_total = count("unexpected_writes")

    return {
        "schema": SCHEMA,
        "date": datetime.now(IST).date().isoformat(),
        "git_commit": _git_commit(),
        "mode": "text",
        "corpus": {
            "version": corpus.version,
            "sha256": corpus.sha256,
            "scenarios": len(by_scenario),
        },
        "model": {
            "name": first.model,
            "note": MOCK_NOTE if first.model.startswith("mock") else "",
        },
        "safety": {"label": first.safety.label, **first.safety.__dict__},
        "faults": first.faults,
        "k": k,
        "seeds": list(seeds),
        "trials": n,
        "metrics": {
            "pass_at_1": {
                "value": sum(g.passed for g in grades) / n,
                "ci95": list(bootstrap_mean(passes)),
                "method": "share of trials that pass every check; 95% interval from a bootstrap "
                "over scenarios",
            },
            "pass_hat_k": {
                "k": k,
                "value": sum(hat_k) / len(hat_k),
                "method": "mean over scenarios of C(passes, k) / C(runs, k)",
            },
            "duplicate_writes": _rate(
                dup_with, n, dup_total, "commits that re-apply a change already committed"
            ),
            "unexpected_writes": _rate(
                ux_with, n, ux_total, "commits beyond the scenario's expected number of writes"
            ),
            "false_confirmations": _rate(
                fc_with,
                n,
                fc_total,
                "heard statements that a change is done, or of a slot, that the record did not "
                "show when they were heard (or by the end of the call)",
            ),
            "false_failures": _rate(
                ff_with,
                n,
                ff_total,
                "heard statements that a change failed while the record showed it saved (or that "
                "a later write made false)",
            ),
            "clarification": {
                "required": len(clarify_required),
                "correct": sum(g.checks["clarified_before_acting"] for g in clarify_required),
                "method": "a clarifying question was answered before the first write",
            },
            "harness_stuck": sum(g.harness_stuck for g in grades),
            "unlabelled_claims": sum(g.unlabelled_claims for g in grades),
            "checks_failed": {
                check: sum(not g.checks[check] for g in grades) for check in grades[0].checks
            },
            "gate": dict(gate_totals),
            "by_category": {
                category: sum(g.passed for g in grades if g.category == category)
                / max(1, sum(1 for g in grades if g.category == category))
                for category in sorted({g.category for g in grades})
            },
        },
        "cost": {
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "usd": round(usd, 4),
            "prices": prices,
        },
        "failures": [
            {
                "scenario": g.scenario_id,
                "seed": g.seed,
                "failed_checks": [name for name, ok in g.checks.items() if not ok],
                "notes": g.notes,
                "transcript": transcript(
                    next(r for r in records if r.scenario.id == g.scenario_id and r.seed == g.seed)
                ),
            }
            for g in grades
            if not g.passed
        ],
    }


def transcript(record: TrialRecord) -> list[str]:
    """A readable transcript, in time order: what each side said, and what the tools did.

    Within a turn, items sort by the order system's event sequence number: a sentence spoken
    before a tool call shares the call's starting number, and sorts first.
    """
    lines: list[str] = [f"caller: {record.scenario.caller.opening}"]
    caller_by_turn = {e["turn"]: e["said"] for e in record.caller_log if e["said"]}
    for turn in range(1, record.turns + 1):
        items: list[tuple[int, int, str]] = []
        for call in (t for t in record.log.tools if t["turn"] == turn):
            items.append(
                (
                    call["seq_before"],
                    1,
                    f"  [tool] {call['name']}({call['arguments']}) -> {call['result']}",
                )
            )
        for spoken in (s for s in record.spoken if s.turn == turn):
            tag = f"{spoken.source}/{spoken.rule}" if spoken.rule else spoken.source
            cut = " [cut off]" if spoken.cut else ""
            text = spoken.heard_text if spoken.heard else "(not heard)"
            items.append((spoken.seq, 0, f"agent ({tag}): {text}{cut}"))
        lines += [line for _, _, line in sorted(items, key=lambda item: (item[0], item[1]))]
        if turn in caller_by_turn:
            lines.append(f"caller: {caller_by_turn[turn]}")
    return lines
