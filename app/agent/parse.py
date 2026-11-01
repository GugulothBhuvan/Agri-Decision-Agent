"""Deterministic parsing used when the LLM is off, and for answers to a question the agent just asked."""
from __future__ import annotations

import re

from ..i18n import to_ascii_digits

YES = r"(^|\s)(হ্যাঁ|হা|হাঁ|ঠিক আছে|ঠিক|আছে|yes|yeah|yep|correct|right|ha|haan|हाँ|हां|सही)(\s|$|[।.!,])"
NO = r"(^|\s)(না|নেই|ভুল|no|nope|wrong|not|नहीं|गलत)(\s|$|[।.!,])"


def yes_no(text: str) -> bool | None:
    t = text.strip().lower()
    if re.search(NO, t):
        return False
    if re.search(YES, t):
        return True
    return None


def parse_crop(t: str) -> str | None:
    t = t.lower()
    if re.search(r"ধান|paddy|dhan|rice|धान|aman|আমন", t):
        return "paddy_aman"
    if re.search(r"আলু|potato|aloo|alu|आलू", t):
        return "potato"
    return None


def parse_stage(t: str) -> str | None:
    t = t.lower()
    if re.search(r"কেটে (ফেলেছি|ফেলেছি|রেখেছি|নিয়েছি)|কাটা হয়ে|কেটেছি|already cut|\bcut\b|harvested|কাটা ধান", t):
        return "cut"
    if re.search(r"পেক|পাকা|পেকেছে|ripe|ready|কাটার (সময়|সময়|মতো)|পক गया|पक", t):
        return "ready"
    if re.search(r"বিক্রি|sold|बेच", t):
        return "sold"
    if re.search(r"বোনা হয়নি|বুনব|to sow|not (yet )?sown|बोना", t):
        return "to_sow"
    if re.search(r"বাড়ছে|সবুজ|growing|green|बढ़", t):
        return "growing"
    return None


def parse_area(t: str, bare_number: bool = False) -> float | None:
    a = to_ascii_digits(t.lower())
    m = re.search(r"(\d+(?:\.\d+)?)\s*(বিঘা|bigha|बीघा)", a)
    if m:
        return float(m.group(1))
    m = re.search(r"(\d+(?:\.\d+)?)\s*(acre|একর)", a)
    if m:
        return round(float(m.group(1)) * 3, 1)
    if bare_number:
        m = re.search(r"\d+(?:\.\d+)?", a)
        if m:
            return float(m.group(0))
    return None


def parse_cut_days_ago(t: str) -> int | None:
    a = to_ascii_digits(t.lower())
    if re.search(r"আজ|today|आज", a):
        return 0
    if re.search(r"গতকাল|কাল|yesterday|कल", a):
        return 1
    if re.search(r"পরশু|day before yesterday|परसों", a):
        return 2
    m = re.search(r"(\d+)\s*(দিন|days?|दिन)", a)
    if m:
        return int(m.group(1))
    return None


def parse_insured(t: str, direct: bool = False) -> bool | None:
    low = t.lower()
    if re.search(r"বিমা নেই|বিমা করা নেই|not insured|no insurance|बीमा नहीं", low):
        return False
    if re.search(r"বিমা আছে|বিমা করা আছে|insured|have insurance|শস্য বিমা|बीमा है", low):
        return True
    return yes_no(t) if direct else None


_VILLAGE_FILLER = r"(আমার|গ্রামের নাম|গ্রাম|জেলা|থেকে|বলছি|my|village|is|in|district|i am from|from|मेरा|गाँव|गांव|जिला)"


def parse_place(t: str) -> tuple[str | None, str | None]:
    """Treat an answer to "which village?" as 'village[, district]'."""
    parts = [p.strip(" ।.,") for p in re.split(r"[,،।]|\bজেলা\b|\bdistrict\b", t) if p.strip(" ।.,")]
    clean = []
    for p in parts:
        q = re.sub(_VILLAGE_FILLER, " ", p, flags=re.IGNORECASE)
        q = re.sub(r"\s+", " ", q).strip()
        if q:
            clean.append(q)
    if not clean:
        return None, None
    return clean[0], (clean[1] if len(clean) > 1 else None)


def parse_fields(text: str, awaiting: str | None) -> dict:
    """Best-effort extraction of profile fields without the LLM."""
    f: dict = {}
    if awaiting == "village":
        v, d = parse_place(text)
        if v:
            f["village"], f["district"] = v, d
    c = parse_crop(text)
    if c:
        f["crop"] = c
    s = parse_stage(text)
    if s:
        f["stage"] = s
    a = parse_area(text, bare_number=(awaiting == "area_bigha"))
    if a is not None:
        f["area_bigha"] = a
    if awaiting == "cut_date" or f.get("stage") == "cut":
        n = parse_cut_days_ago(text)
        if n is not None:
            f["cut_days_ago"] = n
    ins = parse_insured(text, direct=(awaiting == "insured"))
    if ins is not None:
        f["insured"] = ins
    return f


KEYWORDS = [
    ("fertilizer_dose", r"সার|ইউরিয়া|পটাশ|fertili[sz]er|urea|dap|potash|खाद|यूरिया"),
    ("claim_status", r"দাবি|ক্লেম|বিমার টাকা|ক্ষতিপূরণ|claim|compensation|क्लेम|मुआवजा"),
    ("explain_decision", r"কেন|কোথা থেকে|সূত্র|\bwhy\b|source|where .* from|क्यों"),
    ("blight_check", r"ঝলসা|ধসা|ধ্বসা|blight|ছত্রাক|fungicide|spray|স্প্রে|झुलसा"),
    ("sowing_window", r"কবে (আলু )?বুন|বোনার|লাগাব|plant(ing)? potato|when .*(sow|plant)|बुवाई"),
    ("calamity_check", r"ঝড়|ঝড়|ভারী বৃষ্টি|বৃষ্টি আসবে|ঢাক|storm|heavy rain|cyclone|cover|तूफान|बारिश"),
    ("harvest_window", r"কাটব|কাটার|কবে কাট|কাটা যাবে|harvest|when .*cut|safe day|cut (my|the)|कटाई|काट"),
    ("greeting", r"^(নমস্কার|হ্যালো|hello|hi|namaskar|ধন্যবাদ|thanks|thank you|नमस्ते)\b"),
]


def keyword_intents(text: str) -> list[str]:
    low = text.lower().strip()
    out = [name for name, rx in KEYWORDS if re.search(rx, low)]
    return out[:3]
