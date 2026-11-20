"""The scripted caller, on wordings a real model used in the first real-model run (20 Nov 2026)."""

from __future__ import annotations

from typing import Any

from evals.harness.caller import CallerAction, ScriptedCaller
from evals.schema import Scenario, load_corpus
from voice_agent.calllog import Meaning, Spoken
from voice_agent.gate.claims import ClaimKind

FRIDAY_2PM = "20261127-1400"


def scenario(name: str) -> Scenario:
    return next(s for s in load_corpus("v0").scenarios if s.id == name)


def turn(
    caller: ScriptedCaller,
    *lines: tuple[str, str, Meaning | None],
    listed: list[dict[str, Any]] | None = None,
) -> CallerAction:
    spoken = [
        Spoken(turn=1, text=text, source=source, meaning=meaning, seq=1, at_ms=0)  # type: ignore[arg-type]
        for text, source, meaning in lines
    ]
    caller.barge_in_point(spoken, listed or [])
    return caller.hear(spoken, seq=1)


def caller_for(name: str) -> tuple[ScriptedCaller, Scenario]:
    found = scenario(name)
    caller = ScriptedCaller(found.caller, now=found.now)
    caller.opening()
    return caller, found


def test_when_would_you_like_after_the_current_slot_is_a_question_not_a_goodbye() -> None:
    caller, found = caller_for("plain-en")
    action = turn(
        caller,
        (
            "Your delivery is on Friday, 27 November, between 2 and 4 PM.",
            "template",
            Meaning(ClaimKind.STATE_SLOT, FRIDAY_2PM),
        ),
        ("When would you like to reschedule it to?", "model", None),
    )
    assert action.say == found.caller.facts.wish


def test_kis_date_aur_time_is_a_question_about_when() -> None:
    caller, found = caller_for("plain-devanagari")
    action = turn(
        caller,
        (
            "आपकी delivery अभी Friday, 27 November, दोपहर 2 से 4 बजे के बीच है।",
            "template",
            Meaning(ClaimKind.STATE_SLOT, FRIDAY_2PM),
        ),
        ("Aap kis date aur time par delivery chahte hain?", "model", None),
    )
    assert action.say == found.caller.facts.wish


def test_after_turning_an_offer_down_the_caller_accepts_another_proposed_day() -> None:
    caller, found = caller_for("unavailable-full")
    afternoon = {
        "slot_id": "20261128-1200",
        "date": "2026-11-28",
        "window": "12:00-14:00",
        "available": True,
    }
    rejected = turn(
        caller,
        ("Kya aap 12:00 se 14:00 wala slot lena chahenge?", "model", None),
        listed=[afternoon],
    )
    assert rejected.say == found.caller.facts.reject
    proposal = turn(caller, ("Kya aap Sunday ya kisi aur din ka dekhna chahenge?", "model", None))
    assert proposal.say == found.caller.facts.fallback_wish


def test_a_stated_slot_with_no_question_still_ends_a_cancelled_change() -> None:
    caller, found = caller_for("mind-cancel")
    action = turn(
        caller,
        (
            "आपकी delivery अभी Friday, 27 November, दोपहर 2 से 4 बजे के बीच है।",
            "template",
            Meaning(ClaimKind.STATE_SLOT, FRIDAY_2PM),
        ),
    )
    assert action.say == found.caller.close
