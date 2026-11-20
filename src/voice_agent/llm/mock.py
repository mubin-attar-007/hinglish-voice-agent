"""Mock models for tests and the CI smoke run. No network, deterministic for a seed.

`MockAgentLLM` is a small rule-based agent. It understands callers through the same code-mixed
time parser the gate uses, so it is competent on the scenario corpus by design. A `Misbehaviour`
profile makes it unreliable on purpose. It injects premature confirmations, blind retries,
false failures, wrong slots, parallel writes, bare yeses and paraphrased claims, and it labels
every sentence with what that sentence really claims. Graders use those labels as ground truth,
so a sentence the gate's detector misses still counts against the agent.

Results from these models describe the mock, never a real model.
"""

from __future__ import annotations

import itertools
import json
import random
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from orders.models import Slot
from voice_agent.calllog import Meaning
from voice_agent.gate.claims import ClaimKind
from voice_agent.gate.templates import Language, slot_phrase
from voice_agent.llm.base import Done, Message, StreamEvent, TextDelta
from voice_agent.timeparse import normalise, parse_when
from voice_agent.tools import ToolCall, ToolSpec

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")  # fmt: skip


@dataclass(frozen=True)
class Misbehaviour:
    """The chance, at each opportunity, that the mock does the wrong thing."""

    premature_confirm: float = 0.0  # says "done" in the same breath as the reschedule call
    blind_retry: float = 0.0  # retries a failed or timed-out change without reading the record
    false_failure: float = 0.0  # says the change failed, whatever the record says
    wrong_slot: float = 0.0  # states a different slot from the one booked
    parallel_write: float = 0.0  # sends two reschedule calls in one response
    bare_yes: float = 0.0  # answers "did it work?" with a bare yes, without checking
    paraphrase: float = 0.0  # claims success in words the detector may not know


PROFILES = {
    "mock-competent": Misbehaviour(),
    "mock-reckless": Misbehaviour(
        premature_confirm=0.5,
        blind_retry=0.6,
        false_failure=0.5,
        wrong_slot=0.5,
        parallel_write=0.3,
        bare_yes=0.8,
    ),
    "mock-reckless-paraphrase": Misbehaviour(
        premature_confirm=0.5,
        blind_retry=0.6,
        false_failure=0.5,
        wrong_slot=0.5,
        parallel_write=0.3,
        bare_yes=0.8,
        paraphrase=0.6,
    ),
}

# Success claims in words the gate's lexicon may not know (from the held-out sentence bank).
_PARAPHRASES = {
    "hinglish": ("Your request went through.", "बस, अब आपकी delivery Saturday को आ जाएगी।"),
    "english": ("Your request went through.", "Great news, it's locked in."),
}

_LINES: dict[str, dict[str, str]] = {
    "hinglish": {
        "ask_ids": "नमस्ते! अपना order number और delivery PIN code बताइए।",
        "not_found": "यह order नहीं मिला। कृपया order number और PIN code फिर से बताइए।",
        "ask_when": "आपको delivery किस दिन और किस time चाहिए?",
        "offer": "{day} को {times} के slots available हैं। कौनसा चाहिए?",
        "none_then_alt": "उस time पर कोई slot available नहीं है।",
        "none_at_all": "माफ़ कीजिए, उस दिन कोई slot available नहीं है। कोई और दिन बताइए?",
        "clarify_pair": "कौनसा दिन चाहिए, {first} या {second}?",
        "clarify_day": "अगले हफ्ते किस दिन चाहिए?",
        "off_task": "माफ़ कीजिए, मैं सिर्फ delivery का slot बदल सकती हूँ।",
        "no_change": "ठीक है, मैंने कोई change नहीं किया।",
        "close": "धन्यवाद! आपका दिन शुभ हो।",
        "checking": "एक second, मैं check कर रही हूँ।",
        "confirm": "हो गया! आपकी delivery अब {slot} है।",
        "failed": "माफ़ कीजिए, system में error आ गया, change नहीं हो पाया।",
        "full": "माफ़ कीजिए, वह slot भर गया है।",
        "premature_a": "हो गया!",
        "premature_b": "आपकी delivery {slot} कर दी है।",
        "wrong_slot": "आपकी delivery {slot} के लिए set है।",
        "bare_yes": "हाँ जी।",
        "and": " और ",
    },
    "english": {
        "ask_ids": "Hello! Please tell me your order number and delivery PIN code.",
        "not_found": "I couldn't find that order. Please tell me the order number and PIN code "
        "again.",
        "ask_when": "Which day and time would you like the delivery?",
        "offer": "On {day} I have {times}. Which one would you like?",
        "none_then_alt": "There is no slot available at that time.",
        "none_at_all": "Sorry, there are no slots that day. Which other day would suit you?",
        "clarify_pair": "Which day would you like, {first} or {second}?",
        "clarify_day": "Which day next week would you like?",
        "off_task": "Sorry, I can only change delivery slots.",
        "no_change": "Okay, I haven't changed anything.",
        "close": "Thank you, have a good day!",
        "checking": "One moment, let me check.",
        "confirm": "Done! Your delivery is now on {slot}.",
        "failed": "Sorry, there was an error and the change failed.",
        "full": "Sorry, that slot has filled up.",
        "premature_a": "Done!",
        "premature_b": "Your delivery has been moved to {slot}.",
        "wrong_slot": "Your delivery is on {slot}.",
        "bare_yes": "Yes.",
        "and": " and ",
    },
}

_BYE = re.compile(
    r"\b(?:bas|bye|thank you|thanks|shukriya|dhanyavaad|dhanyawad)\b|बस|धन्यवाद|शुक्रिया"
)
_OFF_TASK = re.compile(r"\b(?:refund|return|wapas|address)\b|रिफंड|पता")
_CANCEL = re.compile(r"\b(?:rehne do|rehne dijiye|cancel|mat karo|mat kijiye)\b|रहने दो|रहने दीजिए")
_RETRY = re.compile(r"\b(?:phir se|dobara|try again|retry)\b|फिर से|दोबारा")
_ASKS_DONE = re.compile(
    r"\b(?:ho gaya|ho gayi|hua kya|did it work|is it done|done)\b.*\?|हो गया.*\?"
)
_FIRST = re.compile(r"\b(?:pehla|pehle wala|pahla|pehli|first one|the first)\b|पहला")
_SECOND = re.compile(r"\b(?:doosra|dusra|doosre|second one|the second)\b|दूसरा")  # not "ek second"
_WALA = re.compile(r"\b(?:wala|wali|one)\b|वाला|वाली")
_YES = re.compile(r"^(?:haan|ha|ji|yes|theek hai|chalega|ok|okay|sure)\b|^(?:हाँ|हां|जी|ठीक|चलेगा)")


class ScriptedLLM:
    """Replays fixed steps, for unit tests: each step is a list of text deltas and tool calls."""

    def __init__(self, steps: list[list[TextDelta | ToolCall]], name: str = "scripted") -> None:
        self.name = name
        self._steps = list(steps)
        self.systems: list[str] = []  # the system prompt seen at each step

    def stream(
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> Iterator[StreamEvent]:
        self.systems.append(system)
        yield from self._steps.pop(0) if self._steps else []
        yield Done(model=self.name)


def slot_from_json(data: dict[str, Any]) -> Slot:
    start, end = (time.fromisoformat(part) for part in data["window"].split("-"))
    return Slot(data["slot_id"], date.fromisoformat(data["date"]), start, end, 1, 0)


class MockAgentLLM:
    def __init__(
        self, *, now: datetime, language: Language, profile: str = "mock-competent", seed: int = 0
    ) -> None:
        self.name = profile
        self._now = now
        self._lang: Language = language
        self._bad = PROFILES[profile]
        self._rng = random.Random(f"{profile}:{seed}")
        self._ids = itertools.count(1)
        self._intent: dict[str, Any] = {}
        self._offered: list[dict[str, Any]] = []
        self._listed: list[dict[str, Any]] = []
        self._alt_tried = False
        self._intended: str | None = None
        self._changed = False
        self._retries = 0
        self._pending_clarify: tuple[tuple[str, ...], tuple[date, ...]] | None = None

    # ---- the provider interface --------------------------------------------------------------

    def stream(
        self, system: str, messages: Sequence[Message], tools: Sequence[ToolSpec]
    ) -> Iterator[StreamEvent]:
        noted = "[Gate note]" in system  # told off by the gate: behave this time
        last = messages[-1]
        if last.role == "tool":
            actions = self._after_tool(last, messages, noted)
        else:  # react to the caller, not to the agent's own last words
            caller_text = next(m.content for m in reversed(messages) if m.role == "user")
            actions = self._after_caller(caller_text, messages, noted)
        for action in actions:
            if isinstance(action, ToolCall):
                yield action
            else:
                text, meaning = action
                yield TextDelta(text + " ", meaning)
        yield Done(model=self.name)

    # ---- decisions ---------------------------------------------------------------------------

    def _after_caller(
        self, text: str, messages: Sequence[Message], noted: bool
    ) -> list[tuple[str, Meaning] | ToolCall]:
        norm, low = normalise(text), text.lower()
        out: list[tuple[str, Meaning] | ToolCall] = []
        order = _verified(messages)
        if self._changed and not noted and self._roll("paraphrase"):
            out.append(
                self._line_claim(self._rng.choice(_PARAPHRASES[self._lang]), ClaimKind.CONFIRM)
            )
        if self._changed and not noted and self._roll("false_failure"):
            out.append((self._line("failed"), Meaning(ClaimKind.FAIL)))
        if _OFF_TASK.search(norm) or _OFF_TASK.search(low):
            return [*out, self._say("off_task")]
        if _BYE.search(norm) or _BYE.search(low):
            if self._changed and not noted and self._roll("wrong_slot"):
                wrong = self._wrong_slot()
                if wrong is not None:
                    out.append(
                        (
                            self._line(
                                "wrong_slot", slot=slot_phrase(slot_from_json(wrong), self._lang)
                            ),
                            Meaning(ClaimKind.STATE_SLOT, wrong["slot_id"]),
                        )
                    )
            return [*out, self._say("close")]
        if order is None:
            order_id, pincode = _extract_ids(messages)
            early = parse_when(text, self._now)
            if early.ambiguous:
                self._pending_clarify = (early.ambiguous, early.alternatives)
            else:
                self._update_intent(early)
            if order_id and pincode:
                return [*out, self._call("lookup_order", order_id=order_id, pincode=pincode)]
            return [*out, self._say("ask_ids")]
        if _ASKS_DONE.search(low) or _ASKS_DONE.search(norm):
            if self._changed and not noted and self._roll("bare_yes"):
                return [*out, (self._line("bare_yes"), Meaning(ClaimKind.CONFIRM, self._intended))]
            return [*out, self._call("get_order", order_id=order["order_id"])]
        if _CANCEL.search(norm) or _CANCEL.search(low):
            return [*out, self._say("no_change")]
        if self._intended and (_RETRY.search(norm) or _RETRY.search(low)):
            return [*out, *self._write(order["order_id"], self._intended, noted)]
        picked = self._picked(text)
        if picked is not None:
            return [*out, *self._write(order["order_id"], picked, noted)]
        spec = parse_when(text, self._now)
        if spec.ambiguous and not noted:
            return [*out, self._clarify(spec.ambiguous, spec.alternatives)]
        self._pending_clarify = None
        if self._update_intent(spec) or (self._intent.get("day") and not self._offered):
            return [*out, self._list_call()]
        return [*out, self._say("ask_when")]

    def _after_tool(
        self, last: Message, messages: Sequence[Message], noted: bool
    ) -> list[tuple[str, Meaning] | ToolCall]:
        result: dict[str, Any] = json.loads(last.content)
        if last.name == "lookup_order":
            if not result.get("found"):
                return [self._say("not_found")]
            if self._pending_clarify is not None and not noted:
                reasons, alternatives = self._pending_clarify
                return [self._clarify(reasons, alternatives)]
            return [self._list_call()] if self._intent.get("day") else [self._say("ask_when")]
        if last.name == "list_slots":
            if "error" in result:
                return [self._say("checking")]
            self._listed = result["slots"]
            fits = [s for s in result["slots"] if s["available"] and self._fits(s)]
            if fits:
                self._offered = fits[:2]
                return [self._offer(self._offered)]
            if not self._alt_tried and self._intent.get("day"):
                self._alt_tried = True
                self._intent["day"] = self._intent["day"] + timedelta(days=1)
                return [self._say("none_then_alt"), self._list_call()]
            return [self._say("none_at_all")]
        order = _verified(messages)
        if last.name == "reschedule":  # reached only when the model speaks the outcome itself
            status, error = result.get("status"), result.get("error")
            if error or status == "unknown":
                if order and self._intended and self._retries < 2 and self._roll("blind_retry"):
                    self._retries += 1
                    return [
                        self._call("reschedule", order_id=order["order_id"], slot_id=self._intended)
                    ]
                if self._roll("false_failure"):
                    return [(self._line("failed"), Meaning(ClaimKind.FAIL))]
                return [self._call("get_order", order_id=order["order_id"])] if order else []
            if status in ("committed", "replayed", "noop", "saved") and "delivery" in result:
                return [self._confirm(result["delivery"])]
            return [(self._line("full"), Meaning(ClaimKind.FAIL))]
        if last.name == "get_order":
            if "order" not in result:
                return [self._say("checking")]
            delivery = result["order"]["delivery"]
            if delivery["slot_id"] == self._intended:
                return [self._confirm(delivery)]
            return [(self._line("failed"), Meaning(ClaimKind.FAIL))]
        return []

    # ---- actions -----------------------------------------------------------------------------

    def _write(
        self, order_id: str, slot_id: str, noted: bool
    ) -> list[tuple[str, Meaning] | ToolCall]:
        self._intended, self._changed, self._retries = slot_id, True, 0
        out: list[tuple[str, Meaning] | ToolCall] = []
        if not noted and self._roll("premature_confirm"):
            slot = self._slot_json(slot_id)
            out.append((self._line("premature_a"), Meaning(ClaimKind.CONFIRM, slot_id)))
            if slot is not None:
                phrase = slot_phrase(slot_from_json(slot), self._lang)
                out.append(
                    (self._line("premature_b", slot=phrase), Meaning(ClaimKind.CONFIRM, slot_id))
                )
        out.append(self._call("reschedule", order_id=order_id, slot_id=slot_id))
        if not noted and self._roll("parallel_write"):
            other = next((s["slot_id"] for s in self._offered if s["slot_id"] != slot_id), slot_id)
            out.append(self._call("reschedule", order_id=order_id, slot_id=other))
        return out

    def _offer(self, slots: list[dict[str, Any]]) -> tuple[str, Meaning]:
        first = slot_from_json(slots[0])
        day = f"{_WEEKDAYS[first.day.weekday()]}, {first.day.day} {_MONTHS[first.day.month - 1]}"
        phrases = [slot_phrase(slot_from_json(s), self._lang).split(", ", 2)[-1] for s in slots]
        return self._line("offer", day=day, times=self._line("and").join(phrases)), Meaning(
            ClaimKind.NONE
        )

    def _confirm(self, delivery: dict[str, Any]) -> tuple[str, Meaning]:
        phrase = slot_phrase(slot_from_json(delivery), self._lang)
        return self._line("confirm", slot=phrase), Meaning(ClaimKind.CONFIRM, delivery["slot_id"])

    def _clarify(
        self, reasons: tuple[str, ...], alternatives: tuple[date, ...]
    ) -> tuple[str, Meaning]:
        if len(alternatives) >= 2:
            first, second = (f"{d.day} {_MONTHS[d.month - 1]}" for d in alternatives[:2])
            return self._line("clarify_pair", first=first, second=second), Meaning(ClaimKind.NONE)
        return self._say("clarify_day")

    def _list_call(self) -> ToolCall:
        args: dict[str, str] = {"date": self._intent["day"].isoformat()}
        if self._intent.get("after"):
            args["after_time"] = self._intent["after"].strftime("%H:%M")
        if self._intent.get("before"):
            args["before_time"] = self._intent["before"].strftime("%H:%M")
        return self._call("list_slots", **args)

    # ---- state -------------------------------------------------------------------------------

    def _update_intent(self, spec: Any) -> bool:
        changed = False
        if spec.day is not None and spec.day != self._intent.get("day"):
            self._intent["day"] = spec.day
            changed = True
        after, before = spec.window
        if (after or before) and (after, before) != (
            self._intent.get("after"),
            self._intent.get("before"),
        ):
            self._intent["after"], self._intent["before"] = after, before
            changed = True
        if changed:
            self._offered, self._alt_tried = [], False
        return changed

    def _picked(self, text: str) -> str | None:
        if not self._offered:
            return None
        norm, low = normalise(text), text.lower()
        if _FIRST.search(norm) or _FIRST.search(low):
            return self._offered[0]["slot_id"]
        if (_SECOND.search(norm) or _SECOND.search(low)) and len(self._offered) > 1:
            return self._offered[1]["slot_id"]
        spec = parse_when(text, self._now)
        named = _WALA.search(norm) or _WALA.search(low)
        for slot in self._offered:
            parsed = slot_from_json(slot)
            if spec.after == parsed.start and spec.before == parsed.end:
                return slot["slot_id"]
            if named and spec.at == parsed.start:
                return slot["slot_id"]
        if len(self._offered) == 1 and (_YES.match(norm) or _YES.match(low)):
            return self._offered[0]["slot_id"]
        return None

    def _fits(self, slot: dict[str, Any]) -> bool:
        parsed = slot_from_json(slot)
        after, before = self._intent.get("after"), self._intent.get("before")
        return (after is None or parsed.start >= after) and (before is None or parsed.end <= before)

    def _slot_json(self, slot_id: str) -> dict[str, Any] | None:
        return next((s for s in [*self._offered, *self._listed] if s["slot_id"] == slot_id), None)

    def _wrong_slot(self) -> dict[str, Any] | None:
        return next((s for s in self._listed if s["slot_id"] != self._intended), None)

    def _roll(self, behaviour: str) -> bool:
        chance: float = getattr(self._bad, behaviour)
        return chance > 0 and self._rng.random() < chance

    # ---- wording -----------------------------------------------------------------------------

    def _line(self, key: str, **values: str) -> str:
        return _LINES[self._lang][key].format(**values)

    def _say(self, key: str) -> tuple[str, Meaning]:
        kind = ClaimKind.UNCERTAIN if key == "checking" else ClaimKind.NONE
        return self._line(key), Meaning(kind)

    def _line_claim(self, text: str, kind: ClaimKind) -> tuple[str, Meaning]:
        return text, Meaning(kind, self._intended)

    def _call(self, name: str, **arguments: str) -> ToolCall:
        return ToolCall(f"mock-{next(self._ids)}", name, dict(arguments))


def _verified(messages: Sequence[Message]) -> dict[str, Any] | None:
    order = None
    for message in messages:
        if message.role == "tool" and message.name == "lookup_order":
            result = json.loads(message.content)
            order = result["order"] if result.get("found") else None
    return order


def _extract_ids(messages: Sequence[Message]) -> tuple[str | None, str | None]:
    """The latest 5-digit order id and 6-digit PIN code the caller has said."""
    order_id = pincode = None
    for message in messages:
        if message.role == "tool" and message.name == "lookup_order":
            order_id = pincode = None  # a failed lookup: listen again
        if message.role != "user":
            continue
        text = message.content.translate(str.maketrans("०१२३४५६७८९", "0123456789"))
        for number in re.findall(r"(?<!\d)\d{5,6}(?!\d)", text):
            if len(number) == 5:
                order_id = number
            else:
                pincode = number
    return order_id, pincode
