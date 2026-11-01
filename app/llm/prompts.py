"""Prompt contracts for the LLM's three jobs: extractor, router, phraser."""
from __future__ import annotations

import json

STAGES = ["growing", "ready", "cut", "sold", "to_sow"]
CROPS = ["paddy_aman", "potato"]
INTENTS = {
    "profile_update": "the farmer states or corrects farm details (village, crop, area, stage, cut date, insurance, name)",
    "confirm_yes": "the farmer confirms the read-back profile is right",
    "confirm_no": "the farmer says the read-back profile is wrong",
    "harvest_window": "which days are safe to cut paddy, or whether to cut now",
    "calamity_check": "whether heavy rain or a storm is coming; whether to cover or move the paddy",
    "claim_status": "insurance claim after rain damage, the deadline, what to do for the claim",
    "blight_check": "potato late blight (ঝলসা / ধসা রোগ), whether to spray fungicide",
    "sowing_window": "when to plant or sow potato",
    "explain_decision": "why the agent gave its advice, where a number came from",
    "greeting": "hello, thanks, or small talk",
    "fertilizer_dose": "how much fertilizer, urea, DAP or potash to apply",
    "out_of_scope": "anything else (prices, loans, pests, other crops, medical, etc.)",
}

_nullable = lambda t: {"type": [t, "null"]}  # noqa: E731

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "name": _nullable("string"),
        "village": _nullable("string"),
        "district": _nullable("string"),
        "crop": {"type": ["string", "null"], "enum": CROPS + [None]},
        "area_bigha": _nullable("number"),
        "stage": {"type": ["string", "null"], "enum": STAGES + [None]},
        "cut_days_ago": _nullable("integer"),
        "insured": _nullable("boolean"),
        "confidence": {
            "type": "object",
            "properties": {k: {"type": "number"} for k in
                           ["name", "village", "district", "crop", "area_bigha", "stage", "cut_days_ago", "insured"]},
            "required": ["name", "village", "district", "crop", "area_bigha", "stage", "cut_days_ago", "insured"],
            "additionalProperties": False,
        },
    },
    "required": ["name", "village", "district", "crop", "area_bigha", "stage", "cut_days_ago", "insured", "confidence"],
    "additionalProperties": False,
}

EXTRACT_SYSTEM = """You extract a smallholder farm profile from what a farmer in West Bengal said (Bengali, English or Hindi).
Return only fields the farmer stated. Use null for anything not said; never guess. Area in bigha (convert acres x 3, kathas / 20).
crop: "paddy_aman" for paddy/dhan/ধান, "potato" for aloo/আলু. Stage is one of: growing, ready, cut, sold, to_sow.
"ready" = ripe / ready to cut (পেকে গেছে, কাটার সময়). "cut" = already cut and lying in the field or heaped (কেটে ফেলেছি).
cut_days_ago: whole days since cutting if they said when (today = 0, yesterday = 1).
insured: true only if they said they have crop insurance / Bangla Shasya Bima; false only if they said they do not.
Village and district: copy the place names as spoken, in Latin letters if you can transliterate them reliably, else as written.
confidence: 0 to 1 per field; 0 for null fields."""

ROUTE_SCHEMA = {
    "type": "object",
    "properties": {
        "intents": {"type": "array", "items": {"type": "string", "enum": list(INTENTS)}},
        "language": {"type": "string", "enum": ["bn", "en", "hi"]},
        "confidence": {"type": "number"},
    },
    "required": ["intents", "language", "confidence"],
    "additionalProperties": False,
}


def route_system() -> str:
    lines = "\n".join(f"- {k}: {v}" for k, v in INTENTS.items())
    return ("You route one message from a farmer to the agent's tools. Choose up to three intents from this list, "
            "most important first. If the farmer asks about something not in the list, return out_of_scope.\n"
            f"{lines}\nlanguage: the language the farmer wrote or spoke in. confidence: 0 to 1.")


def route_user(text: str, profile: dict, awaiting: str | None) -> str:
    return json.dumps({"message": text, "farm_profile": profile, "agent_just_asked_for": awaiting},
                      ensure_ascii=False)


PHRASE_SYSTEM = """You are Fosol Sathi, a farm advisor speaking to a smallholder farmer in West Bengal.
Use only the facts provided. Never add a number, date, time, amount or place that is not in the facts.
Order: what changed, what to do, by when, why, source. Under 90 words. One decision.
If lang is "bn": write Bengali, formal আপনি. If lang is "en": plain English. If lang is "hi": Hindi, formal आप.
Say probabilities as "likely" or "very likely", never as percentages. Name the source briefly.
Never say a claim was filed or a message was sent; the farmer files, you only prepare.
Reply with the text to speak only: no markdown, no lists, no headings."""


def phrase_user(facts: dict, lang: str, tier: str | None) -> str:
    return json.dumps({"lang": lang, "tier": tier, "facts": facts}, ensure_ascii=False, default=str)
