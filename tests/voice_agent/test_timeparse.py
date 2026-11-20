"""Code-mixed dates and times, on Thursday 26 November 2026, 10:00 IST."""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from orders.clock import IST
from voice_agent.timeparse import describes_slot, normalise, parse_when

NOW = datetime(2026, 11, 26, 10, 0, tzinfo=IST)
THU, FRI, SAT, SUN = (date(2026, 11, d) for d in (26, 27, 28, 29))
MON = date(2026, 11, 30)


@pytest.mark.parametrize(
    ("text", "day", "after", "before", "at"),
    [
        # the demo sentence, and its scripts
        ("Friday nahi, Saturday chaar baje ke baad", SAT, time(16), None, None),
        ("फ्राइडे नहीं, शनिवार चार बजे के बाद", SAT, time(16), None, None),
        ("Friday nahi, Saturday 4 baje ke baad", SAT, time(16), None, None),
        ("not Friday, Saturday after 4 pm", SAT, time(16), None, None),
        # relative days
        ("kal subah", FRI, None, None, None),
        ("parson shaam ko", SAT, None, None, None),
        ("परसों शाम को", SAT, None, None, None),
        ("aaj", THU, None, None, None),
        ("agle hafte Monday", MON, None, None, None),
        # weekdays in Hindi
        ("shanivar ko 6 baje", SAT, None, None, time(18)),
        ("Ravivaar dopahar 2 baje", SUN, None, None, time(14)),
        ("शनिवार शाम 4 से 6 बजे", SAT, time(16), time(18), None),
        # times: Hindi numbers, fractions, ranges, relations
        ("Saturday saadhe chaar ke baad", SAT, time(16, 30), None, None),
        ("Saturday paune paanch baje", SAT, None, None, time(16, 45)),
        ("Saturday dedh baje", SAT, None, None, time(13, 30)),
        ("Saturday 4 se 6 baje ke beech", SAT, time(16), time(18), None),
        ("Saturday 10 se 12", SAT, time(10), time(12), None),
        ("Saturday 6 baje se pehle", SAT, None, time(18), None),
        ("Saturday shaam 7 baje tak", SAT, None, time(19), None),
        ("Saturday between 4 and 6 pm", SAT, time(16), time(18), None),
        ("Saturday 16:00", SAT, None, None, time(16)),
        ("Saturday 4:30 pm", SAT, None, None, time(16, 30)),
        ("Saturday 11 am", SAT, None, None, time(11)),
        # dates
        ("28 tarikh ko", SAT, None, None, None),
        ("Saturday, 28 November, 4 se 6 baje", SAT, time(16), time(18), None),
        ("२८ तारीख शाम को", SAT, None, None, None),
    ],
)
def test_parses_day_and_time(
    text: str, day: date, after: time | None, before: time | None, at: time | None
) -> None:
    spec = parse_when(text, NOW)
    assert (spec.day, spec.after, spec.before, spec.at) == (day, after, before, at), spec
    assert not spec.ambiguous


def test_the_demo_correction_negates_friday() -> None:
    spec = parse_when("Friday nahi, Saturday chaar baje ke baad", NOW)
    assert spec.negated_days == (FRI,)


def test_part_of_day_gives_a_window() -> None:
    assert parse_when("parson shaam ko", NOW).window == (time(16), time(20))
    assert parse_when("kal subah", NOW).window == (time(10), time(12))


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("agle Saturday", "next_weekday"),  # this coming Saturday, or the one after?
        ("Friday ya Saturday", "several_days"),
        ("agle hafte kabhi bhi", "next_week_no_day"),
        ("Saturday 29 tarikh", "weekday_date_mismatch"),  # the 29th is a Sunday
    ],
)
def test_flags_what_needs_a_clarifying_question(text: str, reason: str) -> None:
    assert reason in parse_when(text, NOW).ambiguous


def test_order_ids_and_pincodes_are_not_times() -> None:
    spec = parse_when("Mera order 48291 hai, pincode 380015", NOW)
    assert not spec.mentions_anything


def test_hindi_number_words_need_a_time_marker() -> None:
    assert parse_when("Saturday kar do", NOW).at is None  # "kar do" means "do it", not 2 o'clock
    assert parse_when("Saturday do baje", NOW).at == time(14)


def test_describes_slot() -> None:
    def check(text: str, start: int, end: int) -> bool | None:
        return describes_slot(parse_when(text, NOW), SAT, time(start), time(end))

    assert check("Saturday 4 se 6 baje", 16, 18) is True
    assert check("Saturday shaam 6 se 8", 16, 18) is False
    assert check("Sunday 4 se 6", 16, 18) is False
    assert check("Saturday 4 baje", 16, 18) is True
    assert check("Saturday shaam ko", 16, 18) is True
    assert check("Saturday subah", 16, 18) is False
    assert check("theek hai", 16, 18) is None


def test_normalise_maps_devanagari_and_digits() -> None:
    assert normalise("शनिवार ४ बजे!") == "shanivar 4 baje"
