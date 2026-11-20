"""Parse code-mixed delivery dates and times: Roman Hinglish, Devanagari and English.

On Thursday 26 November 2026, for example:

    "Friday nahi, Saturday chaar baje ke baad"  -> Sat 28 Nov, after 16:00 (Friday negated)
    "parson shaam ko"                           -> Sat 28 Nov, evening (16:00-20:00)
    "शनिवार शाम 4 से 6 बजे"                     -> Sat 28 Nov, 16:00-18:00
    "agle Saturday"                             -> ambiguous: this Saturday or the next one?

Delivery hours are 10:00-20:00, so a bare "chaar baje" (four o'clock) means 16:00.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

# Devanagari words the parser understands, mapped to the Roman forms it reads.
_DEVANAGARI_WORDS = {
    "सोमवार": "somvar",
    "मंगलवार": "mangalvar",
    "बुधवार": "budhvar",
    "गुरुवार": "guruvar",
    "बृहस्पतिवार": "guruvar",
    "शुक्रवार": "shukravar",
    "शनिवार": "shanivar",
    "रविवार": "ravivar",
    "इतवार": "ravivar",
    "मंडे": "monday",
    "ट्यूज़डे": "tuesday",
    "ट्यूसडे": "tuesday",
    "वेडनेसडे": "wednesday",
    "थर्सडे": "thursday",
    "फ्राइडे": "friday",
    "सैटरडे": "saturday",
    "संडे": "sunday",
    "आज": "aaj",
    "कल": "kal",
    "परसों": "parson",
    "परसो": "parson",
    "अगले": "agle",
    "अगला": "agle",
    "हफ्ते": "hafte",
    "हफ़्ते": "hafte",
    "बजे": "baje",
    "सुबह": "subah",
    "दोपहर": "dopahar",
    "शाम": "shaam",
    "रात": "raat",
    "के": "ke",
    "बाद": "baad",
    "से": "se",
    "पहले": "pehle",
    "तक": "tak",
    "बीच": "beech",
    "और": "aur",
    "नहीं": "nahi",
    "नही": "nahi",
    "तारीख": "tarikh",
    "तारीख़": "tarikh",
    "एक": "ek",
    "दो": "do",
    "तीन": "teen",
    "चार": "chaar",
    "पांच": "paanch",
    "पाँच": "paanch",
    "छह": "chhe",
    "छः": "chhe",
    "सात": "saat",
    "आठ": "aath",
    "नौ": "nau",
    "दस": "das",
    "ग्यारह": "gyarah",
    "बारह": "baarah",
    "साढ़े": "saadhe",
    "साढे": "saadhe",
    "सवा": "sava",
    "पौने": "paune",
    "डेढ़": "dedh",
    "डेढ": "dedh",
    "ढाई": "dhaai",
}
_DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

_WEEKDAYS = {
    0: ("monday", "mon", "somvar", "somvaar", "somwar", "somwaar"),
    1: ("tuesday", "tue", "tues", "mangalvar", "mangalvaar", "mangalwar", "mangal"),
    2: ("wednesday", "wed", "budhvar", "budhvaar", "budhwar", "budh"),
    3: ("thursday", "thu", "thurs", "guruvar", "guruvaar", "guruwar", "brihaspativar", "veervar"),
    4: ("friday", "fri", "shukravar", "shukravaar", "shukrawar", "shukra"),
    5: ("saturday", "sat", "shanivar", "shanivaar", "shaniwar", "shani"),
    6: ("sunday", "sun", "ravivar", "ravivaar", "raviwar", "itvaar", "itwar", "itvar"),
}
_WEEKDAY_OF = {word: day for day, words in _WEEKDAYS.items() for word in words}

_NUMBER_WORDS = {
    "ek": 1,
    "one": 1,
    "do": 2,
    "two": 2,
    "teen": 3,
    "tin": 3,
    "three": 3,
    "chaar": 4,
    "char": 4,
    "four": 4,
    "paanch": 5,
    "panch": 5,
    "five": 5,
    "chhe": 6,
    "chhah": 6,
    "chah": 6,
    "che": 6,
    "six": 6,
    "saat": 7,
    "seven": 7,
    "aath": 8,
    "ath": 8,
    "eight": 8,
    "nau": 9,
    "nine": 9,
    "das": 10,
    "ten": 10,
    "gyarah": 11,
    "gyaarah": 11,
    "eleven": 11,
    "baarah": 12,
    "barah": 12,
    "twelve": 12,
}
NUMBER_WORDS = _NUMBER_WORDS  # public: hour words, for readers of agent text
_FRACTION_MINUTES = {"saadhe": 30, "sadhe": 30, "saade": 30, "sava": 15, "savaa": 15}
_QUARTER_TO = {"paune", "pone"}
_SPECIAL_TIMES = {"dedh": (1, 30), "dhaai": (2, 30), "dhai": (2, 30)}

PART_OF_DAY_WINDOWS = {
    "morning": (time(10), time(12)),
    "afternoon": (time(12), time(16)),
    "evening": (time(16), time(20)),
    "night": (time(18), time(20)),
}
_PART_OF_DAY = {
    "subah": "morning",
    "morning": "morning",
    "savere": "morning",
    "dopahar": "afternoon",
    "dupahar": "afternoon",
    "afternoon": "afternoon",
    "shaam": "evening",
    "sham": "evening",
    "evening": "evening",
    "raat": "night",
    "night": "night",
}
_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

_NUM = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
_FRACTION = "|".join(sorted([*_FRACTION_MINUTES, *_QUARTER_TO], key=len, reverse=True))
_SPECIAL = "|".join(_SPECIAL_TIMES)
_ATOM = (
    rf"(?:(?:(?P<{{p}}frac>{_FRACTION})\s+)?"
    rf"(?P<{{p}}num>(?<![\d:])\d{{{{1,2}}}}(?:[:.]\d{{{{2}}}})?(?![\d:])|\b(?:{_NUM})\b)"
    rf"|\b(?P<{{p}}special>{_SPECIAL})\b)"
)
_SUFFIX = r"(?:baje|bje|baj|o'?clock|(?P<{p}ampm>am|pm|a\.m\.?|p\.m\.?))"


def _atom(prefix: str) -> str:
    return _ATOM.format(p=prefix)


def _suffix(prefix: str) -> str:
    return _SUFFIX.format(p=prefix)


_RANGE = re.compile(
    rf"{_atom('a')}\s*(?:{_suffix('a')}\s*)?(?:se|to|till|until|-|–)\s*{_atom('b')}"  # noqa: RUF001 (the en dash of "4 to 6" ranges)
    rf"(?:\s*{_suffix('b')})?"
)
_BETWEEN = re.compile(
    rf"(?:between\s+)?{_atom('a')}\s*(?:{_suffix('a')}\s*)?(?:aur|and)\s*{_atom('b')}"
    rf"(?:\s*{_suffix('b')})?\s*(?:ke\s+)?beech"
    rf"|between\s+{_atom('c')}\s*(?:{_suffix('c')}\s*)?and\s+{_atom('d')}(?:\s*{_suffix('d')})?"
)
_AFTER = re.compile(
    rf"{_atom('a')}\s*(?:{_suffix('a')}\s*)?(?:ke\s+|k\s+)?(?:baad|bad)\b"
    rf"|\bafter\s+{_atom('b')}(?:\s*{_suffix('b')})?"
)
_BEFORE = re.compile(
    rf"{_atom('a')}\s*(?:{_suffix('a')}\s*)?(?:se\s+pehle|se\s+pahle|tak)\b"
    rf"|\b(?:before|by|until|till)\s+{_atom('b')}(?:\s*{_suffix('b')})?"
)
_POINT = re.compile(
    rf"{_atom('a')}\s*{_suffix('a')}|(?<![\d:])(?P<clock>\d{{1,2}}:\d{{2}})(?![\d:])"
)

_DATE = re.compile(
    r"(?<!\d)(?P<d>[0-3]?\d)(?!\d)\s*(?:st|nd|rd|th)?\s*"
    r"(?:(?P<tarikh>tarikh|tareekh|tarik)|(?P<month>" + "|".join(_MONTHS) + r")\b)"
    r"|\b(?P<month2>" + "|".join(_MONTHS) + r")\s+(?P<d2>[0-3]?\d)(?!\d)"
)


@dataclass(frozen=True)
class WhenSpec:
    """What a sentence says about a delivery day and time."""

    day: date | None = None
    after: time | None = None  # slot must start at or after this
    before: time | None = None  # slot must end at or before this
    at: time | None = None  # a specific time ("4 baje")
    part_of_day: str | None = None
    negated_days: tuple[date, ...] = ()
    ambiguous: tuple[str, ...] = ()  # reasons a clarifying question is needed
    alternatives: tuple[date, ...] = ()  # candidate days when the day is ambiguous

    @property
    def mentions_anything(self) -> bool:
        return any((self.day, self.after, self.before, self.at, self.part_of_day))

    @property
    def window(self) -> tuple[time | None, time | None]:
        """The (after, before) bounds a slot must fit, from explicit times or the part of day."""
        after, before = self.after, self.before
        if self.at is not None and after is None and before is None:
            after = self.at
        if after is None and before is None and self.part_of_day:
            after, before = PART_OF_DAY_WINDOWS[self.part_of_day]
        return after, before


def normalise(text: str) -> str:
    """Lower-case, Devanagari to Roman for known words, digits to ASCII, punctuation to spaces."""
    text = text.translate(_DEVANAGARI_DIGITS)
    text = re.sub(r"[ऀ-ॿ]+", lambda m: _DEVANAGARI_WORDS.get(m.group(0), m.group(0)), text)
    text = text.lower().replace("’", "'")  # noqa: RUF001 (curly apostrophe, as typed on phones)
    text = re.sub(r"\b([ap])\.\s?m\b\.?", r"\1m", text)  # "8 a.m." -> "8 am", before full stops go
    text = re.sub(r"(\d)\s*(am|pm)\b", r"\1 \2", text)
    text = re.sub(r"[,;!?।|()\"]+", " ", text)
    text = re.sub(r"(?<!\d)\.(?!\d)", " ", text)  # full stops, but not 4.30
    return re.sub(r"\s+", " ", text).strip()


def parse_when(text: str, now: datetime) -> WhenSpec:
    """Read the delivery day and time a caller (or the agent) is talking about."""
    norm = normalise(text)
    today = now.date()
    part = _part_of_day(norm)

    days, negated, ambiguous, alternatives = _days(norm, today)
    after = before = at = None

    taken: list[tuple[int, int]] = []

    def free(match: re.Match[str]) -> bool:
        return all(match.end() <= s or match.start() >= e for s, e in taken)

    for match in _BETWEEN.finditer(norm):
        if free(match):
            pa, pb = ("a", "b") if match.group("anum") or match.group("aspecial") else ("c", "d")
            after, before = _resolve_pair(match, pa, pb, part)
            taken.append(match.span())
    for match in _RANGE.finditer(norm):
        if free(match):
            after, before = _resolve_pair(match, "a", "b", part)
            taken.append(match.span())
    for match in _AFTER.finditer(norm):
        if free(match):
            p = "a" if match.group("anum") or match.group("aspecial") else "b"
            after = _resolve(match, p, part)
            taken.append(match.span())
    for match in _BEFORE.finditer(norm):
        if free(match):
            p = "a" if match.group("anum") or match.group("aspecial") else "b"
            before = _resolve(match, p, part)
            taken.append(match.span())
    for match in _POINT.finditer(norm):
        if free(match):
            if match.group("clock"):
                hour, minute = (int(x) for x in match.group("clock").split(":"))
                at = _apply_meridiem(hour, minute, None, part)
            else:
                at = _resolve(match, "a", part)
            taken.append(match.span())

    day = days[0] if len(days) == 1 else None
    if len(days) > 1:
        ambiguous = (*ambiguous, "several_days")
        alternatives = tuple(days)
    return WhenSpec(
        day=day,
        after=after,
        before=before,
        at=at,
        part_of_day=part,
        negated_days=tuple(negated),
        ambiguous=ambiguous,
        alternatives=alternatives,
    )


def describes_slot(spec: WhenSpec, day: date, start: time, end: time) -> bool | None:
    """Whether a statement's day and time agree with a slot. None if it names neither."""
    if not spec.mentions_anything:
        return None
    if spec.day is not None and spec.day != day:
        return False
    if spec.after is not None and spec.before is not None:
        return spec.after == start and spec.before == end
    if spec.at is not None and not (start <= spec.at < end):
        return False
    if spec.after is not None and spec.after > start:
        return False
    if spec.before is not None and spec.before < end:
        return False
    if spec.part_of_day is not None and spec.at is None and spec.after is None:
        lo, hi = PART_OF_DAY_WINDOWS[spec.part_of_day]
        if not (lo <= start and end <= hi):
            return False
    return True


# ---- internals --------------------------------------------------------------------------------


def _part_of_day(norm: str) -> str | None:
    for word in norm.split():
        if word in _PART_OF_DAY:
            return _PART_OF_DAY[word]
    return None


def _days(
    norm: str, today: date
) -> tuple[list[date], list[date], tuple[str, ...], tuple[date, ...]]:
    """Positive days, negated days, ambiguity reasons and alternatives."""
    words = norm.split()
    found: list[tuple[int, date, bool]] = []  # (word index, day, negated)
    ambiguous: list[str] = []
    alternatives: list[date] = []
    next_week = bool(re.search(r"\b(?:agle|agla|next)\s+(?:hafte|hafta|week)\b", norm))

    for i, word in enumerate(words):
        day: date | None = None
        if word in ("aaj", "aj", "today"):
            day = today
        elif word in ("kal", "tomorrow"):
            day = today + timedelta(days=1)
        elif word in ("parson", "parso", "parsoon"):
            day = today + timedelta(days=2)
        elif word in _WEEKDAY_OF:
            weekday = _WEEKDAY_OF[word]
            ahead = (weekday - today.weekday()) % 7 or 7
            coming = today + timedelta(days=ahead)
            prev = words[i - 1] if i > 0 else ""
            if next_week:
                monday = today + timedelta(days=(7 - today.weekday()))
                day = monday + timedelta(days=weekday)
            elif prev in ("agle", "agla", "next") and coming < today + timedelta(
                days=7 - today.weekday()
            ):
                day = coming
                ambiguous.append("next_weekday")
                alternatives.extend([coming, coming + timedelta(days=7)])
            else:
                day = coming
        if day is not None:
            negated = _negated_at(words, i)
            found.append((i, day, negated))

    for match in _DATE.finditer(norm):
        number = int(match.group("d") or match.group("d2"))
        month_word = match.group("month") or match.group("month2")
        explicit = _date_from_number(number, _MONTHS[month_word] if month_word else None, today)
        if explicit is None:
            continue
        positives = [d for _, d, neg in found if not neg]
        if positives and explicit not in positives:
            ambiguous.append("weekday_date_mismatch")
            alternatives.extend([*positives, explicit])
        if explicit not in [d for _, d, _ in found]:
            found.append((len(words), explicit, False))

    positive: list[date] = []
    for _, day, negated in found:
        if not negated and day not in positive:
            positive.append(day)
    negated_days = [day for _, day, negated in found if negated]
    if next_week and not any(w in _WEEKDAY_OF for w in words):
        ambiguous.append("next_week_no_day")
    return positive, negated_days, tuple(ambiguous), tuple(alternatives)


def _negated_at(words: list[str], i: int) -> bool:
    nxt = words[i + 1 : i + 3]
    prev = words[i - 1] if i > 0 else ""
    return (bool(nxt) and nxt[0] in ("nahi", "nahin", "nai")) or prev == "not"


def _date_from_number(number: int, month: int | None, today: date) -> date | None:
    if not 1 <= number <= 31:
        return None
    year, mon = today.year, month or today.month
    if month is None and number < today.day:
        mon += 1
    if mon > 12:
        year, mon = year + 1, mon - 12
    try:
        candidate = date(year, mon, number)
    except ValueError:
        return None
    if month is not None and candidate < today:
        candidate = date(year + 1, mon, number)
    return candidate


def _resolve_pair(
    match: re.Match[str], pa: str, pb: str, part: str | None
) -> tuple[time | None, time | None]:
    own_first, own_second = match.group(f"{pa}ampm"), match.group(f"{pb}ampm")
    second = _resolve(match, pb, part, own_second or own_first)
    first = _resolve(match, pa, part, own_first or own_second)
    if first and second and first >= second and not own_first:
        first = _resolve(match, pa, part, None)  # "11 to 1 pm": the pm belongs to the 1 only
    if first and second and second <= first and second.hour < 12:
        second = time(second.hour + 12, second.minute) if second.hour + 12 < 24 else second
    return first, second


def _resolve(
    match: re.Match[str], p: str, part: str | None, meridiem: str | None = None
) -> time | None:
    special = match.group(f"{p}special")
    if special:
        hour, minute = _SPECIAL_TIMES[special]
    else:
        raw = match.group(f"{p}num")
        if raw is None:
            return None
        if raw in _NUMBER_WORDS:
            hour, minute = _NUMBER_WORDS[raw], 0
        else:
            pieces = re.split(r"[:.]", raw)
            hour, minute = int(pieces[0]), int(pieces[1]) if len(pieces) > 1 else 0
        frac = match.group(f"{p}frac")
        if frac in _FRACTION_MINUTES:
            minute = _FRACTION_MINUTES[frac]
        elif frac in _QUARTER_TO:
            hour, minute = hour - 1, 45
    own = match.groupdict().get(f"{p}ampm")
    return _apply_meridiem(hour, minute, own or meridiem, part)


def _apply_meridiem(hour: int, minute: int, meridiem: str | None, part: str | None) -> time | None:
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    if hour >= 13 or hour == 0:
        return time(hour, minute)
    tag = (meridiem or "").replace(".", "")
    if tag == "pm" and hour < 12:
        hour += 12
    elif tag == "am":
        hour = 0 if hour == 12 else hour
    elif part in ("afternoon", "evening", "night") and hour < 12:
        hour += 12
    elif part == "morning":
        pass
    elif 1 <= hour <= 8:  # delivery hours are 10:00-20:00, so "chaar baje" is 16:00
        hour += 12
    return time(hour, minute)
