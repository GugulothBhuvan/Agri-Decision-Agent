"""Market intelligence: today's mandi prices against the recent level, weighed with the rain risk.

It compares and discloses; it never predicts a price. With no price data it says so.
"""
from __future__ import annotations

from datetime import datetime
from statistics import median

from ..embeddings import phonetic_key
from ..i18n import num
from .base import Outcome


def _same_district(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    ka, kb = phonetic_key(a).replace(" ", ""), phonetic_key(b).replace(" ", "")
    # "Paschim Medinipur" / "Medinipur(W)" / "West Midnapore" share the core "mdnpr".
    core = "mdnpr" if "mdnpr" in ka else ka[-5:]
    return core in kb or kb in ka


def market_advice(today: list[dict], history: list[list[dict]], *, district: str | None, crop: str, stage: str | None,
                  rain_risk: bool, cfg: dict, fetched_at: datetime | None = None) -> Outcome:
    mc = cfg["market"]
    local = [r for r in today if _same_district(district, r.get("district"))]
    scope = "district" if local else "state"
    rows = local or today
    src = [{"name": mc["source"], "url": mc["url"], "what": "daily mandi prices (Rs per quintal)",
            "what_bn": "মান্ডির দৈনিক দাম (টাকা প্রতি কুইন্টাল)"}]
    team = [f"compare with the median of the last {mc['history_days']} days fetched",
            f"a {mc['move_pct']}% change counts as a real move"]
    team_bn = [f"গত {num(mc['history_days'], 'bn')} দিনে আনা দামের মাঝামাঝি মানের সঙ্গে তুলনা",
               f"{num(mc['move_pct'], 'bn')}% বদল হলে তবেই দাম বেড়েছে বা কমেছে ধরা হয়"]
    if not rows:
        data = {"action": "no_data", "scope": scope, "markets": []}
        return Outcome("market", data, "No mandi price data is available right now, so I can't compare prices.",
                       "এখন মান্ডির দামের তথ্য পাওয়া যাচ্ছে না, তাই দাম তুলনা করতে পারছি না।", None, ["action"],
                       rule_sources=src, actionable=False)

    today_median = round(median(r["modal"] for r in rows))
    top = sorted(rows, key=lambda r: r["modal"], reverse=True)[:3]
    past = []
    for recs in history:
        h = [r for r in recs if _same_district(district, r.get("district"))] if scope == "district" else recs
        if h:
            past.append(median(r["modal"] for r in h))
    recent = round(median(past)) if len(past) >= 3 else None
    change = round((today_median - recent) / recent * 100, 1) if recent else None
    trend = ("unknown" if change is None else "up" if change >= mc["move_pct"]
             else "down" if change <= -mc["move_pct"] else "flat")
    if rain_risk and stage in ("cut", "ready"):
        action = "sell_or_store_dry"
    elif trend == "up":
        action = "price_above_recent"
    elif trend == "down":
        action = "price_below_recent"
    else:
        action = "compare"
    markets = [{"market": r["market"], "district": r["district"], "variety": r["variety"], "modal": round(r["modal"]),
                "min": r["min"], "max": r["max"], "date": r["date"]} for r in top]
    data = {"action": action, "scope": scope, "today_median": today_median, "recent_median": recent,
            "change_pct": change, "trend": trend, "days_of_history": len(past), "markets": markets,
            "crop": crop, "rain_risk": rain_risk}

    where_en = "your district" if scope == "district" else "West Bengal (no market from your district today)"
    where_bn = "আপনার জেলায়" if scope == "district" else "পশ্চিমবঙ্গে (আজ আপনার জেলার কোনো বাজারের দাম নেই)"
    best = top[0]
    en = (f"Today's modal price in {where_en} is about Rs {today_median} per quintal; the highest is "
          f"Rs {round(best['modal'])} at {best['market']}.")
    bn = (f"আজ {where_bn} মাঝারি দাম কুইন্টাল প্রতি প্রায় {num(today_median, 'bn')} টাকা; সবচেয়ে বেশি "
          f"{best['market']}-এ {num(round(best['modal']), 'bn')} টাকা।")
    if change is None:
        en += " There isn't enough recent price history yet to say whether prices are rising."
        bn += " দাম বাড়ছে কি না বলার মতো আগের তথ্য এখনও নেই।"
    else:
        en += f" That is {abs(change)}% {'above' if change >= 0 else 'below'} the recent level of Rs {recent}."
        bn += f" এটি সাম্প্রতিক দাম {num(recent, 'bn')} টাকার চেয়ে {num(abs(change), 'bn')}% {'বেশি' if change >= 0 else 'কম'}।"
    if action == "sell_or_store_dry":
        en += (" Rain is forecast and your paddy is in the field: waiting for a better price risks wet, sprouted grain "
               "that procurement centres reject. Sell soon or move it to dry storage.")
        bn += (" বৃষ্টির পূর্বাভাস আছে আর ধান মাঠে: ভালো দামের আশায় অপেক্ষা করলে ধান ভিজে অঙ্কুর বেরোতে পারে, "
               "যা ক্রয়কেন্দ্র নেয় না। তাড়াতাড়ি বিক্রি করুন বা শুকনো জায়গায় তুলে রাখুন।")
    en += " Also compare with the government procurement price at your nearest CPC."
    bn += " কাছের সরকারি ধান ক্রয়কেন্দ্রের দামের সঙ্গেও মিলিয়ে নিন।"
    return Outcome("market", data, en, bn, datetime.now() if action == "sell_or_store_dry" else None,
                   ["action", "trend"], rule_sources=src, team_choices=team, team_choices_bn=team_bn,
                   actionable=action != "compare")
