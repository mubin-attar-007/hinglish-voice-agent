"""Sentences about the outcome, rendered by code from the record. The model never writes these.

Hinglish templates use Devanagari for Hindi words and Latin script for English words: Sarvam's
TTS guidance says Romanised Hindi lowers quality, and STT's codemix mode writes the same way.
"""

from __future__ import annotations

from typing import Literal

from orders.models import Slot, WriteStatus

Language = Literal["hinglish", "english"]

_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
           "September", "October", "November", "December")  # fmt: skip


def _day(slot: Slot) -> str:
    return f"{_WEEKDAYS[slot.day.weekday()]}, {slot.day.day} {_MONTHS[slot.day.month - 1]}"


def _h12(hour: int) -> int:
    return hour % 12 or 12


def _part_hi(hour: int) -> str:
    if hour < 12:
        return "सुबह"
    if hour < 16:
        return "दोपहर"
    return "शाम"


def slot_phrase(slot: Slot, language: Language) -> str:
    """'Saturday, 28 November, शाम 4 से 6 बजे' or 'Saturday, 28 November, between 4 and 6 PM'."""
    start, end = slot.start.hour, slot.end.hour
    if language == "hinglish":
        return f"{_day(slot)}, {_part_hi(start)} {_h12(start)} से {_h12(end)} बजे"
    start_tag = "AM" if start < 12 else "PM"
    end_tag = "AM" if end < 12 else "PM"
    times = (
        f"between {_h12(start)} and {_h12(end)} {end_tag}"
        if start_tag == end_tag
        else f"between {_h12(start)} {start_tag} and {_h12(end)} {end_tag}"
    )
    return f"{_day(slot)}, {times}"


def confirmed(slot: Slot, language: Language) -> str:
    if language == "hinglish":
        return f"हो गया। आपकी delivery अब {slot_phrase(slot, language)} के बीच होगी।"
    return f"Done. Your delivery is now on {slot_phrase(slot, language)}."


def unchanged(current: Slot, reason: WriteStatus | None, language: Language) -> str:
    """The change was not saved, and the delivery stays where the record says it is."""
    if language == "hinglish":
        why = {
            WriteStatus.SLOT_FULL: "वह slot अभी भर गया है, इसलिए ",
            WriteStatus.SLOT_NOT_FOUND: "वह slot मौजूद नहीं है, इसलिए ",
        }.get(reason or WriteStatus.COMMITTED, "")
        return (
            f"{why}यह change save नहीं हुआ। आपकी delivery अभी भी "
            f"{slot_phrase(current, language)} के बीच है।"
        )
    why = {
        WriteStatus.SLOT_FULL: "That slot has just filled up, so ",
        WriteStatus.SLOT_NOT_FOUND: "That slot doesn't exist, so ",
    }.get(reason or WriteStatus.COMMITTED, "")
    sentence = f"{why}the change was not saved"
    sentence = sentence[0].upper() + sentence[1:]
    return f"{sentence}. Your delivery is still on {slot_phrase(current, language)}."


def current(slot: Slot, language: Language) -> str:
    if language == "hinglish":
        return f"आपकी delivery अभी {slot_phrase(slot, language)} के बीच है।"
    return f"Your delivery is on {slot_phrase(slot, language)}."


def holding(language: Language) -> str:
    if language == "hinglish":
        return "एक second, मैं order system में check कर रही हूँ।"
    return "One moment, I'm checking the order system."


def cannot_verify(language: Language) -> str:
    if language == "hinglish":
        return "माफ़ कीजिए, order system अभी जवाब नहीं दे रहा, इसलिए मैं अभी confirm नहीं कर सकती।"
    return "Sorry, the order system isn't answering, so I can't confirm the change right now."
