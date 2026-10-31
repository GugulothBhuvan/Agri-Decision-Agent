"""View models for the server-rendered pages."""
from __future__ import annotations

import re
from datetime import datetime

from ..i18n import fmt_day, fmt_time, num, t
from ..agent import templates, tools

TIER_LABEL = {"green": ("tier.green",), "yellow": ("tier.yellow",), "amber": ("tier.amber",), "red": ("tier.red",)}


def both(key: str, lang: str, **kw) -> dict:
    """Primary line in the chosen language, secondary line in the other."""
    other = "en" if lang == "bn" else "bn"
    return {"primary": t(key, lang, **kw), "secondary": t(key, other, **kw)}


def action_line(d: dict, lang: str) -> str:
    o, m = d["outcome"], d["module"]
    if m == "harvest":
        if o.get("safe_cut_days"):
            return t("card.harvest_safe", lang, day=fmt_day(o["safe_cut_days"][0], lang))
        return t("card.harvest_wait", lang, n=num(o.get("horizon_days", 5), lang))
    if m == "calamity":
        a = o.get("action")
        if a == "protect_now":
            return t("card.protect_now", lang)
        if a == "cover_heap":
            return t("card.cover_heap", lang)
        return t("card.no_heavy_rain", lang)
    if m == "claim":
        if o.get("action") == "claim_open":
            dl = datetime.fromisoformat(o["deadline_at"])
            return t("card.claim_open", lang, time=f"{fmt_day(dl.date(), lang)}, {num(dl.strftime('%H:%M'), lang)}")
        return t("card.no_claim", lang)
    return ""


def decision_card(d: dict, lang: str) -> dict:
    other = "en" if lang == "bn" else "bn"
    reason = d["reason_en"] if lang == "en" else d["reason_bn"]
    sentences = [x.strip() for x in re.split(r"(?<=[।.])\s+", reason or "") if x.strip()]
    # Harvest's first sentence repeats the action line; its second sentence is the rule.
    pick = sentences[1] if d["module"] == "harvest" and len(sentences) > 1 else (sentences[0] if sentences else "")
    first_sentence = pick
    return {"id": d["decision_id"], "module": d["module"], "tier": d["tier"],
            "tier_label": t(f"tier.{d['tier']}", lang), "tier_label_2": t(f"tier.{d['tier']}", other),
            "action": action_line(d, lang), "action_2": action_line(d, other), "reason": first_sentence,
            "updated": fmt_time(datetime.fromisoformat(d["created_at"]), lang), "cached": d.get("cached")}


def home_decision(farm_id: str) -> dict | None:
    """Today's one decision: open claim > calamity action > harvest window > latest anything."""
    latest = {d["module"]: d for d in tools.latest_decisions(farm_id)}
    st = tools.claim_status(farm_id)
    if st.get("open") and not st.get("closed") and "claim" in latest and latest["claim"]["outcome"].get("action") == "claim_open":
        return latest["claim"]
    if "calamity" in latest and latest["calamity"]["actionable"]:
        return latest["calamity"]
    for m in ("harvest", "calamity", "claim"):
        if m in latest:
            return latest[m]
    return None


def farm_line(p: dict, lang: str) -> str:
    if not p or not p.get("village"):
        return ""
    L = "en" if lang == "en" else "bn"
    bits = [p["village"]]
    if p.get("crop"):
        bits.append(templates.CROP_NAMES[p["crop"]][L])
    if p.get("area_bigha") is not None:
        bits.append(f"{num(p['area_bigha'], L)} {'bigha' if L == 'en' else 'বিঘা'}")
    if p.get("stage"):
        bits.append(templates.STAGE_NAMES[p["stage"]][L])
    return " · ".join(bits)
