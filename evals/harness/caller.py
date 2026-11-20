"""The scripted caller: it answers with facts when the agent asks, in whatever order it asks.

It recognises what the agent's turn is doing (asking for the order id, asking a clarifying
question, offering slots, telling the outcome, saying goodbye) and replies from its script. It
picks the earliest offered slot that fits its goal. Triggers fire on events, for example on the
first slot offer, and can barge in after a given number of the agent's words. If it fails to
recognise two agent turns in a row, it gives up and the trial is reported as `harness_stuck`,
separately from agent failures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

from evals.schema import CallerScript, Goal
from voice_agent.calllog import Spoken
from voice_agent.gate.claims import ClaimKind, classify

_ASKS_IDS = re.compile(r"order (?:number|id|nambar)|pin ?code|पिन|order नंबर", re.IGNORECASE)
_NOT_FOUND = re.compile(r"नहीं मिला|nahi mila|couldn't find|could not find", re.IGNORECASE)
_ASKS_WHEN = re.compile(r"किस दिन|kis din|which day|what day|what time|kab chahiye", re.IGNORECASE)
_CLARIFY = re.compile(r"\b(?:ya|or)\b|\sया\s|which day next week|अगले हफ्ते किस दिन", re.IGNORECASE)
_OFF_TASK_REPLY = re.compile(r"सिर्फ delivery|sirf delivery|only change delivery", re.IGNORECASE)
_NO_CHANGE = re.compile(r"कोई change नहीं|koi change nahi|haven't changed", re.IGNORECASE)
_GOODBYE = re.compile(r"धन्यवाद|शुभ हो|have a good day|thank you", re.IGNORECASE)


@dataclass(frozen=True)
class CallerAction:
    say: str | None
    interrupt_at_word: int | None = None
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


class ScriptedCaller:
    def __init__(self, script: CallerScript, *, now: datetime) -> None:
        self.script = script
        self.now = now
        self.log: list[dict[str, Any]] = []
        self.stuck = False
        self._ids = list(script.facts.ids)
        self._after_outcome = list(script.after_outcome)
        self._after_not_saved = list(script.after_not_saved)
        self._fired: set[int] = set()
        self._offers = 0
        self._confirmed = 0
        self._told: set[tuple[ClaimKind, str | None]] = set()  # outcomes already heard
        self._repeated = False  # this turn repeated an outcome heard before
        self._unmatched = 0
        self._closed = False
        self.turns = 0

    def opening(self) -> str:
        self._note("opening", 0)
        return self.script.opening

    def hear(self, spoken: list[Spoken], listed: list[dict[str, Any]], seq: int) -> CallerAction:
        """React to one agent turn. `listed` holds the slots the agent listed during that turn."""
        self.turns += 1
        heard = [s for s in spoken if s.heard and s.heard_text]
        text = " ".join(s.heard_text for s in heard)
        if self._closed:
            return CallerAction(None, ended=True)
        if self.turns >= self.script.max_turns:
            self.stuck = True
            self._note("max_turns", seq)
            return CallerAction(None, ended=True)

        offered = [slot for slot in listed if slot["available"]]
        if offered:
            self._offers += 1
        for i, trigger in enumerate(self.script.triggers):
            if i not in self._fired and offered and self._offers == trigger.nth:
                self._fired.add(i)
                return self._reply(trigger.say, f"trigger:{i}", seq, trigger.interrupt_at_word)

        outcome = self._outcome(heard)
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
        if "?" in text and _CLARIFY.search(text) and not offered:
            return self._reply(
                self.script.facts.clarify or self.script.facts.wish, "asked:clarify", seq
            )
        if offered:
            choice = self._choose(offered)
            if choice is not None:
                return self._reply(
                    pick_phrase(choice, self.script.pick_style), f"picked:{choice['slot_id']}", seq
                )
            return self._reply(self.script.facts.reject, "rejected_offer", seq)
        if _ASKS_WHEN.search(text):
            return self._reply(self.script.facts.wish, "asked:when", seq)
        states_slot = any(
            s.source == "template" and s.meaning and s.meaning.kind is ClaimKind.STATE_SLOT
            for s in heard
        )
        if self._repeated:  # the agent answered "did it work?" with the outcome again
            if self._after_outcome:
                return self._reply(self._after_outcome.pop(0), "after_outcome", seq)
            return self._close(seq)
        if (
            outcome is ClaimKind.FAIL
            or states_slot
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
        """The last new outcome statement the caller heard in full this turn, if any.

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

    def _reply(
        self, line: str, why: str, seq: int, interrupt_at_word: int | None = None
    ) -> CallerAction:
        self._unmatched = 0
        self._note(why, seq, line)
        return CallerAction(line, interrupt_at_word=interrupt_at_word)

    def _close(self, seq: int) -> CallerAction:
        self._closed = True
        return self._reply(self.script.close, "close", seq)

    def _note(self, what: str, seq: int, line: str | None = None) -> None:
        self.log.append({"turn": self.turns, "what": what, "seq": seq, "said": line})
