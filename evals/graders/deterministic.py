"""Grade one trial from the order system's event log and what the caller heard.

Every heard sentence that claims an outcome is checked against the record as it stood when the
caller heard it, rebuilt from the append-only event log. What a sentence claims comes from its
ground-truth label when there is one: templates carry their meaning, and mock models label what
they inject. A real model's own sentences have no label, so their meaning is inferred with the
detector and counted as `unlabelled_claims`, for human review.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, time

from evals.harness.runner import TrialRecord
from evals.schema import Goal
from orders.db import DEFAULT_WINDOWS, slot_id_for
from voice_agent.calllog import Meaning
from voice_agent.gate.claims import ClaimKind, classify
from voice_agent.timeparse import parse_when


@dataclass
class Grade:
    scenario_id: str
    category: str
    seed: int
    passed: bool
    checks: dict[str, bool]
    writes: int
    duplicate_writes: int
    unexpected_writes: int
    false_confirmations: int
    false_failures: int
    outcome_statements: int
    unlabelled_claims: int
    harness_stuck: bool
    gate: dict[str, int]
    notes: list[str] = field(default_factory=list)


def truth(meaning: Meaning, record_slot: str | None, requested: str | None) -> bool:
    """Is the claim true of the record? `requested` is the latest slot asked for, if any."""
    if meaning.kind is ClaimKind.CONFIRM:
        target = meaning.slot_id or requested
        return requested is not None and record_slot == target
    if meaning.kind is ClaimKind.FAIL:
        return requested is None or record_slot != requested
    if meaning.kind is ClaimKind.STATE_SLOT:
        return meaning.slot_id is None or record_slot == meaning.slot_id
    return True


def grade(record: TrialRecord) -> Grade:
    scenario = record.scenario
    notes: list[str] = []
    commits = [
        (e.seq, e.data["from_slot"], e.data["to_slot"])
        for e in record.events
        if e.kind == "reschedule_committed"
    ]
    requests = [
        (e.seq, e.data["slot_id"]) for e in record.events if e.kind == "reschedule_requested"
    ]

    def slot_at(seq: int) -> str:
        landed = [to for s, _, to in commits if s <= seq]
        return landed[-1] if landed else record.original_slot

    def requested_at(seq: int) -> str | None:
        asked = [slot for s, slot in requests if s <= seq]
        return asked[-1] if asked else None

    unlabelled = 0
    claims: list[tuple[int, Meaning, bool]] = []
    for spoken in record.spoken:
        if not spoken.heard:
            continue
        meaning = spoken.meaning
        if meaning is None:
            meaning = _infer(spoken.heard_text, record)
            if meaning.kind.is_claim:
                unlabelled += 1
        if not meaning.kind.is_claim:
            continue
        claims.append(
            (spoken.seq, meaning, truth(meaning, slot_at(spoken.seq), requested_at(spoken.seq)))
        )

    false_confirmations = sum(
        1 for _, m, ok in claims if not ok and m.kind in (ClaimKind.CONFIRM, ClaimKind.STATE_SLOT)
    )
    false_failures = sum(1 for _, m, ok in claims if not ok and m.kind is ClaimKind.FAIL)

    outcomes = [c for c in claims if c[1].kind in (ClaimKind.CONFIRM, ClaimKind.FAIL)]
    last_request = requests[-1][1] if requests else None
    communicated = scenario.expected.writes == 0
    if outcomes:
        _, last, was_true = outcomes[-1]
        still_true = truth(last, record.final_slot, last_request)
        if was_true and not still_true:  # a write landed after the caller's last word on it
            notes.append("the last outcome statement was made false by a later write")
            if last.kind is ClaimKind.FAIL:
                false_failures += 1
            else:
                false_confirmations += 1
        if scenario.expected.writes > 0:
            communicated = last.kind is ClaimKind.CONFIRM and still_true

    writes = len(commits)
    duplicates = sum(1 for _, before, after in commits if before == after)
    unexpected = max(0, writes - scenario.expected.writes)

    clarified = True
    if scenario.expected.clarify_before_write:
        first_write = requests[0][0] if requests else None
        asked = [e["seq"] for e in record.caller_log if e["what"] == "asked:clarify"]
        clarified = bool(asked) and (first_write is None or asked[0] < first_write)

    checks = {
        "final_state": _final_ok(record),
        "no_duplicate_writes": duplicates == 0,
        "no_unexpected_writes": unexpected == 0,
        "no_false_confirmation": false_confirmations == 0,
        "no_false_failure": false_failures == 0,
        "outcome_communicated": communicated,
        "clarified_before_acting": clarified,
        "harness_ok": not record.stuck,
    }
    return Grade(
        scenario_id=scenario.id,
        category=scenario.category,
        seed=record.seed,
        passed=all(checks.values()),
        checks=checks,
        writes=writes,
        duplicate_writes=duplicates,
        unexpected_writes=unexpected,
        false_confirmations=false_confirmations,
        false_failures=false_failures,
        outcome_statements=len(outcomes),
        unlabelled_claims=unlabelled,
        harness_stuck=record.stuck,
        gate=_gate_counts(record),
        notes=notes,
    )


def _final_ok(record: TrialRecord) -> bool:
    """No change expected: unchanged. A change: a slot in the goal, and the one the caller chose."""
    scenario, final = record.scenario, record.final_slot
    if scenario.expected.final_slot == "unchanged":
        return final == record.original_slot
    picked = [
        e["what"][len("picked:") :] for e in record.caller_log if e["what"].startswith("picked:")
    ]
    windows = scenario.world.spec().windows
    in_goal = final == scenario.expected_final_slot or _fits(
        final, scenario.expected.final_fits, record.original_slot, windows
    )
    return in_goal and bool(picked) and final == picked[-1]


def _fits(
    slot_id: str | None,
    goal: Goal | None,
    original: str,
    windows: tuple[tuple[time, time], ...] = DEFAULT_WINDOWS,
) -> bool:
    """Whether the final slot is a change that fits the caller's goal."""
    if slot_id is None or goal is None or slot_id == original:
        return False
    day = date(int(slot_id[:4]), int(slot_id[4:6]), int(slot_id[6:8]))
    start = time(int(slot_id[9:11]), int(slot_id[11:13]))
    end = next((e for s, e in windows if s == start), None)
    if day != goal.date or end is None:
        return False
    return (goal.after is None or start >= goal.after) and (
        goal.before is None or end <= goal.before
    )


def _infer(text: str, record: TrialRecord) -> Meaning:
    """The detector's reading of an unlabelled sentence, for real models."""
    now = record.scenario.now
    kind = classify(text, now).kind
    spec = parse_when(text, now)
    start = spec.after or spec.at
    slot_id = slot_id_for(spec.day, start) if spec.day and start else None
    return Meaning(kind, slot_id)


def _gate_counts(record: TrialRecord) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for verdict in record.log.verdicts:
        if verdict["enforced"]:
            counts[verdict["action"]] += 1
            if verdict["action"] == "rewrite" and verdict["agreed"] is False:
                counts["rewrite_disagreed"] += 1
        elif verdict["would"]:
            counts[f"would_{verdict['would']}"] += 1
    counts["outcome_by_code"] = sum(1 for s in record.spoken if s.rule == "outcome_by_code")
    counts["silenced_claims"] = sum(  # claims in a response that made a change: never spoken
        1
        for d in record.log.dropped
        if d["why"] == "same_response_as_write"
        and d.get("claim") in ("confirm", "fail", "state_slot")
    )
    return dict(counts)
