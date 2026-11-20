"""The scripted caller: it answers with facts when the agent asks, in whatever order it asks.

It recognises what the agent's turn is doing (asking for the order id, asking a clarifying
question, offering slots, telling the outcome, saying goodbye) and replies from its script. It
picks the earliest slot that fits its goal among the slots the agent both listed and said aloud.

Each agent turn has two steps:
1. `barge_in_point` looks at the turn as it is being spoken. If a trigger fires (for example on
   the first slot offer), it returns the word to barge in after.
2. After the runner has cut the agent off there, `hear` reacts only to what was heard.

If it fails to recognise two agent turns in a row, or runs out of turns, it gives up and the trial
is reported as `harness_stuck`, separately from agent failures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

from evals.schema import CallerScript, Goal, Trigger
from voice_agent.calllog import Spoken
from voice_agent.gate.claims import ClaimKind, classify
from voice_agent.timeparse import NUMBER_WORDS, normalise, parse_when

_ASKS_IDS = re.compile(r"order (?:number|id|nambar)|pin ?code|पिन|order नंबर", re.IGNORECASE)
_NOT_FOUND = re.compile(r"नहीं मिला|nahi mila|couldn't find|could not find", re.IGNORECASE)
_ASKS_WHEN = re.compile(
    r"\bwhen\b[^.?!]*\?|\b(?:which|what) (?:day|date|time)\b|\bkis (?:din|date|time|samay)\b|"
    r"\bkab\b|किस (?:दिन|date|time|समय)|कब",
    re.IGNORECASE,
)
_CLARIFY = re.compile(r"\b(?:ya|or)\b|\sया\s|which day next week|अगले हफ्ते किस दिन", re.IGNORECASE)
_OFF_TASK_REPLY = re.compile(r"सिर्फ delivery|sirf delivery|only change delivery", re.IGNORECASE)
_NO_CHANGE = re.compile(r"कोई change नहीं|koi change nahi|haven't changed", re.IGNORECASE)
_GOODBYE = re.compile(r"धन्यवाद|शुभ हो|have a good day|thank you", re.IGNORECASE)


@dataclass(frozen=True)
class CallerAction:
    say: str | None
    ended: bool = False


def _h12(hour: int) -> int:
    return hour % 12 or 12


def pick_phrase(slot: dict[str, Any], style: str) -> str:
    start, end = (int(part.split(":")[0]) for part in slot["window"].split("-"))
    if style == "devanagari":
        return f"{_h12(start)} से {_h12(end)} वाला ठीक है।"
    if style == "english":
        return f"The {_h12(start)} to {_h12(end)} one, please."
    return f"{_h12(start)} se {_h12(end)} wala theek hai."


def _fits(slot: dict[str, Any], goal: Goal | None) -> bool:
    if goal is None or slot["date"] != goal.date.isoformat():
        return False
    start, end = (time.fromisoformat(part) for part in slot["window"].split("-"))
    return (goal.after is None or start >= goal.after) and (
        goal.before is None or end <= goal.before
    )


def _hours_said(text: str) -> set[int]:
    """Hours the agent said: digits ("4", "16:00") and number words ("chaar", "four")."""
    norm = normalise(text)
    hours = {int(n) for n in re.findall(r"(?<![\d:])(\d{1,2})(?::\d{2})?(?!\d)", norm)}
    hours |= {value for word, value in NUMBER_WORDS.items() if re.search(rf"\b{word}\b", norm)}
    return {h for h in hours if 1 <= h <= 24}


class ScriptedCaller:
    def __init__(self, script: CallerScript, *, now: datetime) -> None:
        self.script = script
        self.now = now
        self.log: list[dict[str, Any]] = []
        self.stuck = False
        self.turns = 0
        self._ids = list(script.facts.ids)
        self._after_outcome = list(script.after_outcome)
        self._after_not_saved = list(script.after_not_saved)
        self._fired: set[int] = set()
        self._armed: tuple[int, Trigger] | None = None
        self._offers = 0
        self._offered: list[dict[str, Any]] = []
        self._confirmed = 0
        self._told: set[tuple[ClaimKind, str | None]] = set()  # outcomes already heard
        self._repeated = False  # this turn repeated an outcome heard before
        self._unmatched = 0
        self._closed = False
        self._rejected = False  # turned down an offer: the fallback day is now the wish

    def opening(self) -> str:
        self._note("opening", 0)
        return self.script.opening

    def barge_in_point(self, spoken: list[Spoken], listed: list[dict[str, Any]]) -> int | None:
        """As the agent speaks: which slots is it offering, and does a trigger fire?"""
        said = " ".join(s.text for s in spoken)
        hours = _hours_said(said)
        self._offered = [
            slot
            for slot in listed
            if slot["available"]
            and (int(slot["window"][:2]) in hours or _h12(int(slot["window"][:2])) in hours)
        ]
        if not self._offered:
            return None
        self._offers += 1
        for i, trigger in enumerate(self.script.triggers):
            if i not in self._fired and self._offers == trigger.nth:
                self._fired.add(i)
                self._armed = (i, trigger)
                return trigger.interrupt_at_word
        return None

    def hear(self, spoken: list[Spoken], seq: int) -> CallerAction:
        """React to what was heard of the agent's turn (after any barge-in)."""
        self.turns += 1
        if self._closed:
            return CallerAction(None, ended=True)
        action = self._react([s for s in spoken if s.heard and s.heard_text], seq)
        if self.turns >= self.script.max_turns and not self._closed and not action.ended:
            self.stuck = True
            self._note("max_turns", seq)
            return CallerAction(None, ended=True)
        return action

    # ---- what to say -------------------------------------------------------------------------

    def _react(self, heard: list[Spoken], seq: int) -> CallerAction:
        text = " ".join(s.heard_text for s in heard)
        outcome = self._outcome(heard)
        if self._armed is not None:
            index, trigger = self._armed
            self._armed = None
            return self._reply(trigger.say, f"trigger:{index}", seq)

        if outcome is ClaimKind.CONFIRM:
            self._confirmed += 1
            if self._after_outcome:
                return self._reply(self._after_outcome.pop(0), "after_outcome", seq)
            return self._close(seq)
        if outcome is ClaimKind.FAIL and self._after_not_saved:
            return self._reply(self._after_not_saved.pop(0), "after_not_saved", seq)

        if _NOT_FOUND.search(text) or _ASKS_IDS.search(text):
            answer = self._ids.pop(0) if len(self._ids) > 1 else self._ids[0]
            return self._reply(answer, "asked:ids", seq)
        if _OFF_TASK_REPLY.search(text):
            return self._reply(self.script.facts.wish, "after_off_task", seq)
        # A proposed other day comes first: it often reads like a clarifying question
        # ("Kya aap Sunday ya kisi aur din ka dekhna chahenge?").
        fallback = self.script.fallback_goal
        fallback_wish = self.script.facts.fallback_wish
        if fallback and fallback_wish and "?" in text and not self._offered:
            spec = parse_when(text, self.now)
            if spec.day == fallback.date or fallback.date in spec.alternatives:
                return self._reply(fallback_wish, "accepted_other_day", seq)
        if "?" in text and _CLARIFY.search(text) and not self._offered:
            return self._reply(
                self.script.facts.clarify or self.script.facts.wish, "asked:clarify", seq
            )
        if self._offered:
            choice = self._choose(self._offered)
            if choice is not None:
                return self._reply(
                    pick_phrase(choice, self.script.pick_style), f"picked:{choice['slot_id']}", seq
                )
            self._rejected = True
            return self._reply(self.script.facts.reject, "rejected_offer", seq)
        if _ASKS_WHEN.search(text):
            wish = fallback_wish if self._rejected and fallback_wish else self.script.facts.wish
            return self._reply(wish, "asked:when", seq)
        if self._repeated:  # the agent answered "did it work?" with the outcome again
            if self._after_outcome:
                return self._reply(self._after_outcome.pop(0), "after_outcome", seq)
            return self._close(seq)
        states_slot = any(
            s.source == "template" and s.meaning and s.meaning.kind is ClaimKind.STATE_SLOT
            for s in heard
        )
        if (
            outcome is ClaimKind.FAIL
            or (
                states_slot and "?" not in text
            )  # a stated slot ends a call only if nothing was asked
            or _NO_CHANGE.search(text)
            or _GOODBYE.search(text)
        ):
            return self._close(seq)

        self._unmatched += 1
        if self._unmatched >= 2:
            self.stuck = True
            self._note("harness_stuck", seq)
            return CallerAction(None, ended=True)
        return self._reply(self.script.facts.repeat or self.script.facts.wish, "unmatched", seq)

    # ---- helpers -----------------------------------------------------------------------------

    def _outcome(self, heard: list[Spoken]) -> ClaimKind | None:
        """The last new outcome statement heard in full this turn, if any.

        An outcome heard before (the same confirmation, repeated) is not news, so the caller
        moves on to whatever else the agent said.
        """
        found: tuple[ClaimKind, str | None] | None = None
        for spoken in heard:
            if spoken.cut:
                continue
            meaning = spoken.meaning
            kind = meaning.kind if meaning else classify(spoken.heard_text, self.now).kind
            if kind in (ClaimKind.CONFIRM, ClaimKind.FAIL):
                found = (kind, meaning.slot_id if meaning else None)
        self._repeated = found is not None and found in self._told
        if found is None or self._repeated:
            return None
        self._told.add(found)
        return found[0]

    def _choose(self, offered: list[dict[str, Any]]) -> dict[str, Any] | None:
        goals = self.script.goals
        goal = goals[min(self._confirmed, len(goals) - 1)] if goals else None
        for candidate_goal in (goal, self.script.fallback_goal):
            fitting = [slot for slot in offered if _fits(slot, candidate_goal)]
            if fitting:
                return min(fitting, key=lambda slot: (slot["date"], slot["window"]))
        return None

    def _reply(self, line: str, why: str, seq: int) -> CallerAction:
        self._unmatched = 0
        self._note(why, seq, line)
        return CallerAction(line)

    def _close(self, seq: int) -> CallerAction:
        self._closed = True
        return self._reply(self.script.close, "close", seq)

    def _note(self, what: str, seq: int, line: str | None = None) -> None:
        self.log.append({"turn": self.turns, "what": what, "seq": seq, "said": line})
