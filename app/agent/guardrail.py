"""Guardrails: number check, source check, no-action-claims check.

Number check: every digit sequence and spelled-out number in a reply must appear in
that turn's tool outputs. Bengali digits are normalised to ASCII first.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..i18n import BN_MONTHS, EN_MONTHS, to_ascii_digits

EN_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
            "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
            "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
            "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100}
BN_WORDS = {"শূন্য": 0, "দুই": 2, "দু": 2, "তিন": 3, "চার": 4, "পাঁচ": 5, "ছয়": 6, "ছয়": 6, "সাত": 7,
            "আট": 8, "নয়": 9, "নয়": 9, "দশ": 10, "বারো": 12, "পনেরো": 15, "কুড়ি": 20, "বিশ": 20,
            "চব্বিশ": 24, "তিরিশ": 30, "চল্লিশ": 40, "পঞ্চাশ": 50, "বাহাত্তর": 72, "একশো": 100}
# "এক" (one) is omitted on purpose: it is also the indefinite article ("একটু", "একবার").

BANNED = [
    r"claim (has been |was )?(filed|submitted)", r"filed (your|the) claim", r"message (has been |was )?sent",
    r"i (have )?(sent|filed|submitted)", r"দাবি জমা (দেওয়া|করা) হয়েছে", r"বার্তা পাঠানো হয়েছে",
    r"আমি (পাঠিয়েছি|জমা দিয়েছি)", r"দাবি দাখিল করা হয়েছে",
]

_num_re = re.compile(r"\d+(?:\.\d+)?")
_time_re = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_iso_re = re.compile(r"(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})?)?")
_MONTHS = {**{m.lower(): i + 1 for i, m in enumerate(EN_MONTHS)},
           **{m.lower(): i + 1 for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July",
                                                      "August", "September", "October", "November", "December"])},
           **{m: i + 1 for i, m in enumerate(BN_MONTHS)}}
_month_alt = "|".join(sorted((re.escape(m) for m in _MONTHS), key=len, reverse=True))
_date_re = re.compile(rf"(\d{{1,2}})\s*({_month_alt})", re.IGNORECASE)


def _variants(n: str) -> set[str]:
    out = {n}
    try:
        f = float(n)
        out.add(f"{f:g}")
        if f.is_integer():
            out.add(str(int(f)))
    except ValueError:
        pass
    return out


@dataclass
class Allowed:
    numbers: set[str]
    times: set[str]
    dates: set[tuple[int, int]]


def _strip_tokens(text: str, allowed: Allowed | None = None) -> tuple[str, set[str], set[tuple[int, int]]]:
    """Pull out dates (day+month) and clock times as whole tokens; return the rest of the text."""
    times, dates = set(), set()
    for m in _date_re.finditer(text):
        dates.add((int(m.group(1)), _MONTHS[m.group(2).lower() if m.group(2).isascii() else m.group(2)]))
    text = _date_re.sub(" ", text)
    for m in _time_re.finditer(text):
        times.add(f"{int(m.group(1))}:{m.group(2)}")
    text = _time_re.sub(" ", text)
    return text, times, dates


def allowed_numbers(tool_outputs) -> Allowed:
    blob = to_ascii_digits(json.dumps(tool_outputs, ensure_ascii=False, default=str))
    nums, times, dates = set(), set(), set()
    for m in _iso_re.finditer(blob):
        y, mo, d, hh, mm, tz = m.groups()
        dates.add((int(d), int(mo)))
        nums |= {y, str(int(d))}
        if hh is not None and tz is None:  # naive times are local (IST) rule times; aware ones are spoken via *_spoken
            times.add(f"{int(hh)}:{mm}")
    blob = _iso_re.sub(" ", blob)
    blob, t2, d2 = _strip_tokens(blob)
    times |= t2
    dates |= d2
    for n in _num_re.findall(blob):
        nums |= _variants(n)
    return Allowed(nums, times, dates)


@dataclass
class Check:
    ok: bool
    bad_numbers: list[str]
    banned: list[str]

    @property
    def problem(self) -> str:
        parts = []
        if self.bad_numbers:
            parts.append("numbers not in the facts: " + ", ".join(self.bad_numbers))
        if self.banned:
            parts.append("claims an action the farmer did not take")
        return "; ".join(parts)


def check_reply(reply: str, tool_outputs) -> Check:
    allowed = allowed_numbers(tool_outputs)
    text = to_ascii_digits(reply)
    rest, times, dates = _strip_tokens(text)
    bad = [f"{d}/{m}" for d, m in dates if (d, m) not in allowed.dates]
    bad += [t for t in times if t not in allowed.times]
    for n in _num_re.findall(rest):
        if not _variants(n) & allowed.numbers:
            bad.append(n)
    low = reply.lower()
    for w, v in EN_WORDS.items():
        if re.search(rf"\b{w}\b", low) and str(v) not in allowed.numbers:
            bad.append(w)
    for w, v in BN_WORDS.items():
        if re.search(rf"(?<![\u0980-\u09FF]){w}(?![\u0980-\u09FF])", reply) and str(v) not in allowed.numbers:
            bad.append(w)
    banned = [p for p in BANNED if re.search(p, low)]
    return Check(ok=not bad and not banned, bad_numbers=sorted(set(bad)), banned=banned)


def source_ok(sources: list[dict]) -> bool:
    """A decision needs at least one source with a name, URL and fetch time."""
    return any(s.get("name") and s.get("url") and s.get("fetched_at") for s in sources)
