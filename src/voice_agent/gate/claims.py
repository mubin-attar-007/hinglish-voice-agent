"""Find claims about the outcome of a change in one sentence of the agent's reply.

The lexicon errs towards finding a claim. A false alarm only costs a rewrite from the record,
while a miss could let a false statement reach the caller. Its recall is measured on a labelled
sentence bank (evals/bank), and the misses are published.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from voice_agent.timeparse import WhenSpec, normalise, parse_when


class ClaimKind(StrEnum):
    CONFIRM = "confirm"  # says the change is done
    FAIL = "fail"  # says the change failed, or did not happen
    STATE_SLOT = "state_slot"  # states the order's delivery day or time as a fact
    UNCERTAIN = "uncertain"  # says it is checking; no outcome
    NONE = "none"  # no claim: a question, an offer, small talk

    @property
    def is_claim(self) -> bool:
        return self in (ClaimKind.CONFIRM, ClaimKind.FAIL, ClaimKind.STATE_SLOT)


@dataclass(frozen=True)
class Claim:
    kind: ClaimKind
    when: WhenSpec
    cues: tuple[str, ...] = ()


def _patterns(*items: str) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(item) for item in items)


_NAHI = r"(?:nahi|nahin|nai)"
_FAIL = _patterns(
    rf"\b{_NAHI} ho (?:paya|paaya|payi|paayi|pai|saka|saki|sakaa)\b",
    rf"\b{_NAHI} hu(?:a|i|aa)\b",
    rf"\b{_NAHI} kar (?:paya|paaya|payi|paayi|pai|saka|saki|sakaa)\b",
    rf"\b(?:save|change|update|book|shift) {_NAHI}\b",
    rf"\babhi tak {_NAHI}\b",
    r"\b(?:abhi bhi|still)\b",
    r"\bfail(?:ed|ure)?\b",
    r"\b(?:error|problem|issue|dikkat|gadbad|gadbadi) (?:aa gay[ai]|aayi|aaya|hui|ho gay[ai])\b",
    r"\bcould(?:n't| not)\b",
    r"\b(?:unable|not able)\b",
    r"\b(?:wasn't|hasn't|didn't|did not|was not|has not|have not|haven't)\b",
    r"\bnot (?:been |yet )?(?:saved|changed|moved|updated|booked|confirmed|rescheduled|done)\b",
    r"\b(?:didn't|did not) go through\b",
)
_FAIL_DEVANAGARI = ("नहीं हो पाया", "नहीं हो पाई", "नहीं हुआ", "नहीं हुई", "नहीं कर पाया", "नहीं कर पाई",
                    "विफल", "गड़बड़", "अभी भी", "अभी तक नहीं", "save नहीं")  # fmt: skip

_CONFIRM = _patterns(
    r"\bho (?:gaya|gayi|gai|gya|gyi|chuka|chuki)\b",
    r"\b(?:hogaya|hogya|hogayi)\b",
    r"\bkar (?:diya|di|dee|diye|dia|chuka|chuki|chuke)\b",
    r"\b(?:badal|shift|set|book|confirm|fix|update|reschedule|change|move|save)"
    r"(?: kar)? (?:diya|di|dia|diye)\b",
    r"\b(?:is|are|has been|have been|got|been|was) "
    r"(?:confirmed|booked|rescheduled|moved|changed|updated|saved|done|set)\b",
    r"\b(?:i've|i have|we've|we have|i) (?:moved|changed|rescheduled|booked|updated|saved)\b",
    r"\b(?:rescheduled|confirmed)\b",
    r"\b(?:all set|sab set|done|pakka|successful|successfully|saved)\b",
)
_CONFIRM_DEVANAGARI = ("हो गया", "हो गई", "हो गयी", "हो चुका", "हो चुकी", "कर दिया", "कर दी",
                       "बदल दिया", "बदल दी", "बुक हो", "कन्फर्म हो", "पक्का")  # fmt: skip

_UNCERTAIN = _patterns(
    r"\bcheck kar(?:ti|ta|ke|rahi|raha|na|ungi|unga|te)?\b",
    r"\bdekh (?:rahi|raha|leti|leta)\b",
    r"\bek (?:second|minute|min|pal)\b",
    r"\b(?:one|just a) (?:moment|second|minute)\b",
    r"\b(?:let me check|checking|verify|verifying)\b",
    r"\bpata kar\b",
)
_UNCERTAIN_DEVANAGARI = ("एक second", "चेक कर", "देख रही", "देख रहा", "एक पल", "एक सेकंड")

_OFFER = _patterns(
    r"\b(?:kar doon|kar du|kar dun|karoon|karu|karun|chahiye|chahenge|chahengi|chahte|chahti)\b",
    r"\b(?:would you like|shall i|should i|do you want|want me to|can i|kya main|kya aap)\b",
    r"\b(?:batayein|bataiye|bataaiye|choose|chunein|pick|which|kaunsa|kaun sa|kaun si)\b",
    r"\b(?:available|options|option|mil sakta|mil sakti|ho sakta|ho sakti|possible)\b",
)
_ABOUT_DELIVERY = _patterns(
    r"\b(?:aapki|aapka|apki|apka|your|delivery|order|saaman|samaan|package|parcel)\b",
)
_ABOUT_DELIVERY_DEVANAGARI = ("आपकी", "आपका", "डिलीवरी", "सामान", "ऑर्डर")
_FACT_VERB = _patterns(
    r"\b(?:hogi|hoga|aayegi|aayega|aa jayegi|aa jayega|pahunchegi|pahunchega|hai|hain)\b",
    r"\b(?:is|will be|will arrive|arrives|is scheduled|is due|is on|comes)\b",
)
_FACT_VERB_DEVANAGARI = ("होगी", "होगा", "आएगी", "आएगा", "पहुंचेगी", "पहुँचेगी", "है")

_BARE_YES = {"haan", "haan ji", "ji haan", "ji", "yes", "han", "ha", "haa", "bilkul", "sure",
             "of course", "yes it is", "jee", "हाँ", "हां", "जी", "जी हाँ", "बिल्कुल"}  # fmt: skip
_ASKS_IF_DONE = re.compile(
    r"(?:ho gaya|ho gayi|hua|done|confirm|booked|changed|हो गया|हुआ)[^?]*\?|"
    r"\b(?:kya|did|is it|has it)\b.*\b(?:ho gaya|hua|done|work|changed|booked)\b"
)


def _hits(patterns: tuple[re.Pattern[str], ...], text: str) -> list[str]:
    return [m.group(0) for p in patterns for m in [p.search(text)] if m]


def _hits_raw(words: tuple[str, ...] | set[str], raw: str) -> list[str]:
    return [word for word in words if word in raw]


def classify(sentence: str, now: datetime, *, caller_last: str | None = None) -> Claim:
    """Classify one sentence of the agent's reply."""
    raw = sentence.strip().lower()
    norm = normalise(sentence)
    when = parse_when(sentence, now)

    fail = _hits(_FAIL, norm) + _hits_raw(_FAIL_DEVANAGARI, raw)
    if fail:
        return Claim(ClaimKind.FAIL, when, tuple(fail))
    confirm = _hits(_CONFIRM, norm) + _hits_raw(_CONFIRM_DEVANAGARI, raw)
    if confirm:
        return Claim(ClaimKind.CONFIRM, when, tuple(confirm))
    bare = re.sub(r"[\s,.!।]+$", "", raw).strip()
    if bare in _BARE_YES and caller_last and _ASKS_IF_DONE.search(caller_last.lower()):
        return Claim(ClaimKind.CONFIRM, when, ("bare yes to: " + caller_last,))
    uncertain = _hits(_UNCERTAIN, norm) + _hits_raw(_UNCERTAIN_DEVANAGARI, raw)
    if uncertain:
        return Claim(ClaimKind.UNCERTAIN, when, tuple(uncertain))
    is_question = raw.endswith("?") or bool(_hits(_OFFER, norm))
    about = _hits(_ABOUT_DELIVERY, norm) + _hits_raw(_ABOUT_DELIVERY_DEVANAGARI, raw)
    fact = _hits(_FACT_VERB, norm) + _hits_raw(_FACT_VERB_DEVANAGARI, raw)
    if when.mentions_anything and about and fact and not is_question:
        return Claim(ClaimKind.STATE_SLOT, when, tuple(about + fact))
    return Claim(ClaimKind.NONE, when)
