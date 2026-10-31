"""Strings files, Bengali digits and date formatting."""
from __future__ import annotations

import json
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
STRINGS_DIR = Path(__file__).resolve().parent / "web" / "strings"

BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")
ASCII_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
BN_MONTHS = ["জানুয়ারি", "ফেব্রুয়ারি", "মার্চ", "এপ্রিল", "মে", "জুন", "জুলাই", "আগস্ট",
             "সেপ্টেম্বর", "অক্টোবর", "নভেম্বর", "ডিসেম্বর"]
BN_WEEKDAYS = ["সোমবার", "মঙ্গলবার", "বুধবার", "বৃহস্পতিবার", "শুক্রবার", "শনিবার", "রবিবার"]
EN_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
EN_WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@lru_cache
def strings(lang: str) -> dict[str, str]:
    lang = lang if lang in ("bn", "en") else "bn"
    with open(STRINGS_DIR / f"{lang}.json", encoding="utf-8") as f:
        return json.load(f)


def t(key: str, lang: str = "bn", **kw) -> str:
    s = strings(lang).get(key) or strings("en").get(key) or key
    if kw:
        try:
            s = s.format(**kw)
        except (KeyError, IndexError):
            pass
    return s


def bn_digits(s: str | int | float) -> str:
    return str(s).translate(BN_DIGITS)


def to_ascii_digits(s: str) -> str:
    return s.translate(ASCII_DIGITS)


def num(x: float | int, lang: str, nd: int | None = None) -> str:
    if isinstance(x, float):
        s = f"{x:g}" if nd is None else f"{x:.{nd}f}"
    else:
        s = str(x)
    return bn_digits(s) if lang == "bn" else s


def as_date(d: str | date) -> date:
    return date.fromisoformat(d) if isinstance(d, str) else d


def fmt_day(d: str | date, lang: str) -> str:
    d = as_date(d)
    if lang == "bn":
        return f"{BN_WEEKDAYS[d.weekday()]}, {bn_digits(d.day)} {BN_MONTHS[d.month - 1]}"
    return f"{EN_WEEKDAYS[d.weekday()]} {d.day} {EN_MONTHS[d.month - 1]}"


def to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def fmt_time(dt: datetime | None, lang: str) -> str:
    if dt is None:
        return ""
    d = to_ist(dt)
    hm = d.strftime("%H:%M")
    if lang == "bn":
        return f"{bn_digits(d.day)} {BN_MONTHS[d.month - 1]}, {bn_digits(hm)}"
    return f"{d.day} {EN_MONTHS[d.month - 1]}, {hm} IST"
