"""Soil intelligence: rate Soil Health Card values. Never computes a fertilizer dose.

If the card itself prints a rating, that rating wins over ours (labs use slightly different bands).
The dose, if the farmer typed it from the card, is repeated word for word; otherwise none is given.
"""
from __future__ import annotations

from datetime import datetime

from ..i18n import num
from .base import Outcome

LABEL = {"low": ("low", "কম"), "medium": ("medium", "মাঝারি"), "high": ("high", "বেশি"),
         "acidic": ("acidic", "অম্ল"), "normal": ("normal", "স্বাভাবিক"), "alkaline_tending": ("tending to alkaline", "ক্ষারের দিকে"),
         "alkaline": ("alkaline", "ক্ষারীয়")}
NAMES = {"organic_carbon_pct": ("Organic carbon", "জৈব কার্বন", "%"), "nitrogen_kg_ha": ("Nitrogen (N)", "নাইট্রোজেন", "kg/ha"),
         "phosphorus_kg_ha": ("Phosphorus (P)", "ফসফরাস", "kg/ha"), "potassium_kg_ha": ("Potassium (K)", "পটাশ", "kg/ha"),
         "ph": ("pH", "পিএইচ", "")}


def _band(v: float, b: dict) -> str:
    return "low" if v < b["low_below"] else "high" if v > b["high_above"] else "medium"


def rate_soil(card: dict, cfg: dict) -> Outcome:
    sc = cfg["soil_rating"]
    rows, missing = [], []
    for key in ("ph", "organic_carbon_pct", "nitrogen_kg_ha", "phosphorus_kg_ha", "potassium_kg_ha"):
        v = card.get(key)
        if v is None:
            missing.append(key)
            continue
        v = float(v)
        if key == "ph":
            p = sc["ph"]
            r = ("acidic" if v < p["acidic_below"] else "normal" if v <= p["normal_to"]
                 else "alkaline" if v > p["alkaline_above"] else "alkaline_tending")
        else:
            r = _band(v, sc[key])
        printed = (card.get("printed_ratings") or {}).get(key)
        rows.append({"key": key, "value": v, "rating": printed or r, "our_rating": r, "from_card": bool(printed)})
    low = [r for r in rows if r["rating"] in ("low", "acidic", "alkaline")]
    dose = (card.get("card_dose") or "").strip() or None
    data = {"rows": rows, "missing": missing, "card_dose": dose, "card_date": card.get("card_date"),
            "action": "review" if low else ("ok" if rows else "no_card")}

    def line(r, L):
        en, bn, unit = NAMES[r["key"]]
        lab = LABEL[r["rating"]][0 if L == "en" else 1]
        val = f"{num(r['value'], L)}{(' ' + unit) if unit else ''}"
        return f"{en} {val}: {lab}" if L == "en" else f"{bn} {val}: {lab}"

    if not rows:
        en = "No Soil Health Card values on file. Add them on My farm to get them explained."
        bn = "আপনার মাটি পরীক্ষার কার্ডের তথ্য নেই। 'আমার জমি' পাতায় লিখলে বুঝিয়ে দেব।"
    else:
        en = "Your soil card: " + "; ".join(line(r, "en") for r in rows) + "."
        bn = "আপনার মাটির কার্ড: " + "; ".join(line(r, "bn") for r in rows) + "।"
        if dose:
            en += f" Your card's fertilizer advice: \"{dose}\"."
            bn += f" কার্ডে লেখা সারের পরামর্শ: \"{dose}\"।"
        else:
            en += " For a fertilizer dose, follow the dose printed on your card or ask your block agriculture office; I don't calculate doses."
            bn += " সারের মাত্রা কার্ডে যা লেখা আছে সেটাই মানুন, বা ব্লক কৃষি অফিসে জিজ্ঞেস করুন; আমি মাত্রা হিসাব করি না।"
    return Outcome("soil", data, en, bn, None, ["action", "rows", "card_dose"],
                   rule_sources=[{"name": sc["source"], "url": sc["url"], "what": "soil test rating bands",
                                  "what_bn": "মাটি পরীক্ষার মান (কম / মাঝারি / বেশি)"}],
                   team_choices=["the rating printed on the card wins over the chart"],
                   team_choices_bn=["কার্ডে ছাপা মানই আগে ধরা হয়"],
                   actionable=bool(low))
