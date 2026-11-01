"""Conversation graph: one voice or text turn.

transcribe -> load_context -> route -> clarify -(question)-> speak
                                         \\-> act -> compose -> guardrail -> speak
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ..db import Message, session_scope
from ..i18n import fmt_day, fmt_time
from ..llm import client as llm
from ..llm.prompts import (EXTRACT_SCHEMA, EXTRACT_SYSTEM, PHRASE_SYSTEM, ROUTE_SCHEMA, phrase_user, route_system,
                           route_user)
from ..speech import sarvam
from . import parse, retrieval, templates, tools
from .guardrail import check_reply
from .trace import traced

G = "conversation"
NEEDS_FARM = {"harvest_window", "calamity_check", "claim_status", "blight_check", "sowing_window",
              "market_advice", "soil_advice"}
# Answers built only from fixed text or verbatim quotes: never re-phrased by the LLM.
VERBATIM_TOOLS = {"fixed", "knowledge_question", "soil_advice"}
CONF_MIN = 0.7


class TurnState(TypedDict, total=False):
    run_id: str
    farm_id: str
    lang: str
    reply_lang: str
    audio: bytes | None
    text: str | None
    transcript: str
    awaiting: dict | None          # {"field": ..., "value": ...} the agent's last question
    awaiting_next: dict | None
    profile: dict
    history: list[dict]
    intents: list[str]
    extracted: dict
    question: str | None           # set by clarify: ask one thing and stop
    prefix: list[str]              # short acknowledgements before tool answers
    tool_outputs: list[dict]
    decision_ids: list[int]
    reply: str
    reply_source: str              # llm | template | fixed
    guardrail: dict
    audio_b64: str | None
    timings: dict
    errors: list[str]
    now: datetime | None
    started: float


def _t(state: TurnState, key: str, t0: float) -> dict:
    timings = dict(state.get("timings") or {})
    timings[key] = int((time.perf_counter() - t0) * 1000)
    return timings


@traced(G, "transcribe", lambda o: "typed" if o.get("transcript") is None else f"{len(o.get('transcript') or '')} chars")
async def transcribe(state: TurnState) -> dict:
    t0 = time.perf_counter()
    if not state.get("audio"):
        return {"transcript": (state.get("text") or "").strip(), "timings": _t(state, "stt_ms", t0)}
    try:
        r = await sarvam.stt(state["audio"], state.get("lang", "bn"))
        return {"transcript": r["transcript"], "timings": _t(state, "stt_ms", t0)}
    except sarvam.SpeechError as e:
        return {"transcript": "", "question": templates.fixed("stt_failed", state.get("lang", "bn")),
                "awaiting_next": state.get("awaiting"), "errors": [f"stt: {e}"], "timings": _t(state, "stt_ms", t0),
                "reply_source": "fixed"}


def _after_transcribe(state: TurnState) -> str:
    return "speak" if state.get("question") else "load_context"


@traced(G, "load_context")
async def load_context(state: TurnState) -> dict:
    p = tools.get_farm(state["farm_id"])
    with session_scope() as s:
        rows = (s.query(Message).filter(Message.farm_id == state["farm_id"])
                .order_by(Message.created_at.desc(), Message.id.desc()).limit(6).all())
        history = [{"role": m.role, "text": m.text} for m in reversed(rows)]
    return {"profile": p, "history": history, "lang": p.get("language") or state.get("lang", "bn")}


@traced(G, "route", lambda o: ",".join(o.get("intents", [])))
async def route(state: TurnState) -> dict:
    text = state["transcript"]
    p = state["profile"]
    awaiting = (state.get("awaiting") or {}).get("field")
    lang = state.get("lang", "bn")
    intents: list[str] = []
    reply_lang = lang
    r = await llm.complete_json(route_system(), route_user(text, _public(p), awaiting), ROUTE_SCHEMA, max_tokens=800)
    if r and r.get("intents"):
        intents = list(dict.fromkeys(r["intents"]))[:3]
        if r.get("language") == "hi":
            reply_lang = "hi"  # P1: Hindi detected, reply in Hindi (LLM phrasing only)
    else:
        # LLM unavailable or unsure: nearest intent examples by embedding, then keyword rules.
        intents, _score = retrieval.route_by_examples(text)
        if not intents:
            intents = parse.keyword_intents(text)
    # An answer to the agent's own question is a profile update or a confirmation.
    if awaiting in tools.PROFILE_FIELDS or awaiting in ("village", "cut_date", "area_bigha"):
        # ...unless it is clearly something else (a greeting, a question about rain or cutting).
        other = [i for i in parse.keyword_intents(text) if i != "profile_update"]
        if "profile_update" not in intents and not (other and not parse.parse_fields(text, None)):
            intents.insert(0, "profile_update")
    if awaiting in ("confirm", "confirm_value"):
        yn = parse.yes_no(text)
        if yn is True and "confirm_yes" not in intents:
            intents.insert(0, "confirm_yes")
        elif yn is False and "confirm_no" not in intents:
            intents.insert(0, "confirm_no")
    if not intents:
        intents = ["profile_update"] if tools.missing_fields(p) else ["out_of_scope"]
    # A farmer describing the farm without a farm on file is a profile update.
    if tools.missing_fields(p) and parse.parse_fields(text, None) and "profile_update" not in intents:
        intents.insert(0, "profile_update")
    return {"intents": intents[:3], "reply_lang": reply_lang}


async def _extract(text: str, p: dict, awaiting: str | None) -> tuple[dict, dict]:
    """Returns (fields to save, low-confidence fields to confirm)."""
    llm_out = await llm.complete_json(EXTRACT_SYSTEM, f"Current profile: {_public(p)}\nFarmer said: {text}",
                                      EXTRACT_SCHEMA, max_tokens=1200)
    save, unsure = {}, {}
    if llm_out:
        conf = llm_out.get("confidence") or {}
        for k in ("name", "village", "district", "crop", "area_bigha", "stage", "cut_days_ago", "insured"):
            v = llm_out.get(k)
            if v is None:
                continue
            (save if conf.get(k, 0) >= CONF_MIN or k == awaiting else unsure)[k] = v
    rule = parse.parse_fields(text, awaiting)
    for k, v in rule.items():
        save.setdefault(k, v)
        unsure.pop(k, None)
    if awaiting == "village" and "village" not in save and "village" in unsure:
        save["village"] = unsure.pop("village")
    return save, unsure


@traced(G, "clarify", lambda o: ("asks: " + (o.get("question") or "")[:60]) if o.get("question") else "no question")
async def clarify(state: TurnState) -> dict:
    """If a required slot is missing or confidence is low, ask one question and stop."""
    p, lang, text = state["profile"], state["lang"], state["transcript"]
    intents = list(state["intents"])
    aw = state.get("awaiting") or {}
    prefix: list[str] = []

    if "confirm_yes" in intents:
        if aw.get("field") == "confirm_value" and aw.get("key") == "place":
            p = tools.set_place(p["id"], aw["value"])
        elif aw.get("field") == "confirm_value":
            res = await tools.update_profile(p["id"], {aw["key"]: aw["value"]})
            p = res["profile"]
        elif aw.get("field") == "confirm":
            tools.confirm_profile(p["id"])
            p = tools.get_farm(p["id"])
            prefix.append(templates.fixed("confirm_thanks", lang))
    if "confirm_no" in intents and aw.get("field") in ("confirm", "confirm_value"):
        if not ({"profile_update"} & set(intents)) or not parse.parse_fields(text, None):
            return {"question": ("Which detail is wrong? Say just that one, for example the village or the stage."
                                 if lang == "en" else "কোনটা ভুল? শুধু সেটা বলুন, যেমন গ্রাম বা ধানের অবস্থা।"),
                    "awaiting_next": {"field": "correct"}, "profile": p}

    if "profile_update" in intents or aw.get("field") == "correct":
        awaiting = aw.get("field") if aw.get("field") not in ("confirm", "confirm_value", "correct") else None
        save, unsure = await _extract(text, p, awaiting)
        res = await tools.update_profile(p["id"], save) if save else {"profile": p, "missing": tools.missing_fields(p),
                                                                       "geocode_failed": False, "changed": []}
        p = res["profile"]
        if res.get("place_confirm"):
            c = res["place_confirm"]
            name = c.get("village_bn") if lang == "bn" and c.get("village_bn") else c.get("village")
            q = (f"Did you mean {name} ({c.get('display_name')})? Yes or no." if lang == "en"
                 else f"আপনি কি {name} ({c.get('display_name')}) বললেন? হ্যাঁ বা না বলুন।")
            return {"question": q, "awaiting_next": {"field": "confirm_value", "key": "place", "value": c}, "profile": p}
        if res["geocode_failed"]:
            return {"question": templates.ask("village_retry", lang), "awaiting_next": {"field": "village"},
                    "profile": p}
        if unsure:
            k, v = next(iter(unsure.items()))
            label = {"village": ("village", "গ্রাম"), "district": ("district", "জেলা"), "crop": ("crop", "ফসল"),
                     "area_bigha": ("area in bigha", "জমি (বিঘা)"), "stage": ("stage", "অবস্থা"),
                     "cut_days_ago": ("days since cutting", "কাটার পর কত দিন"), "insured": ("insured", "বিমা"),
                     "name": ("name", "নাম")}[k]
            q = (f"Did I hear right: {label[0]} = {v}? Yes or no." if lang == "en"
                 else f"ঠিক শুনলাম তো: {label[1]} — {v}? হ্যাঁ বা না বলুন।")
            return {"question": q, "awaiting_next": {"field": "confirm_value", "key": k, "value": v}, "profile": p}
        if res["changed"] and p.get("profile_confirmed") is False and aw.get("field") == "correct":
            prefix.append(templates.fixed("profile_saved", lang))

    missing = tools.missing_fields(p)
    wants_tools = bool(set(intents) & NEEDS_FARM)
    if missing and ("profile_update" in intents or wants_tools or not p.get("village")):
        q = templates.ask(missing[0], lang)
        if wants_tools and "profile_update" not in intents:
            q = templates.fixed("need_profile", lang) + " " + q
        return {"question": " ".join(prefix + [q]), "awaiting_next": {"field": missing[0]}, "profile": p}
    if not missing and not p.get("profile_confirmed"):
        return {"question": " ".join(prefix + [templates.readback(p, lang)]), "awaiting_next": {"field": "confirm"},
                "profile": p}
    # Profile complete and confirmed: if the turn was only profile talk, give today's decision.
    rest = [i for i in intents if i not in ("profile_update", "confirm_yes", "confirm_no")]
    if not rest:
        if p.get("crop") == "potato":
            rest = {"growing": ["blight_check"], "to_sow": ["sowing_window"]}.get(p.get("stage"), [])
        else:
            rest = ["calamity_check", "harvest_window"] if p.get("stage") in ("ready", "cut") else []
    return {"intents": rest, "prefix": prefix, "profile": p, "awaiting_next": None}


def _after_clarify(state: TurnState) -> str:
    return "speak" if state.get("question") else "act"


@traced(G, "act", lambda o: ",".join(x["tool"] for x in o.get("tool_outputs", [])))
async def act(state: TurnState) -> dict:
    """Call the tool for each intent in order; collect outputs with sources."""
    p, now = state["profile"], state.get("now")
    outs, ids = [], []
    for intent in state["intents"]:
        if intent == "harvest_window":
            if p.get("crop") != "paddy_aman" or p.get("stage") not in ("ready", "cut"):
                outs.append({"tool": "fixed", "key": "not_ready_stage"})
                continue
            r = await tools.harvest_window(p["id"], now)
        elif intent == "calamity_check":
            if p.get("crop") != "paddy_aman" or p.get("stage") not in ("ready", "cut"):
                outs.append({"tool": "fixed", "key": "not_ready_stage"})
                continue
            r = await tools.calamity_check(p["id"], now)
            if r.get("claim_opened"):
                outs.append({"tool": "calamity_check", "result": r})
                ids += [x for x in (r.get("decision_id"), r.get("claim_decision_id")) if x]
                r = tools.claim_status(p["id"], now)
                intent = "claim_status"
        elif intent == "claim_status":
            r = tools.claim_status(p["id"], now)
        elif intent in ("blight_check", "sowing_window"):
            need = "growing" if intent == "blight_check" else "to_sow"
            if p.get("crop") != "potato" or p.get("stage") != need:
                outs.append({"tool": "fixed", "key": "potato_stage" if p.get("crop") == "potato" else "out_of_scope"})
                continue
            r = await (tools.blight_check if intent == "blight_check" else tools.sowing_window)(p["id"], now)
        elif intent == "explain_decision":
            latest = [d for d in tools.latest_decisions(p["id"]) if d["actionable"]] or tools.latest_decisions(p["id"])
            r = tools.explain_decision(latest[0]["decision_id"], p["id"]) if latest else None
            if r is None:
                outs.append({"tool": "fixed", "key": "no_decision"})
                continue
        elif intent == "soil_advice":
            r = tools.soil_advice(p["id"], now)
            if r.get("no_card"):
                outs.append({"tool": "fixed", "key": "no_soil_card"})
                continue
        elif intent == "fertilizer_dose":
            # Only the dose printed on the farmer's own card is ever repeated; none is calculated.
            if p.get("soil_card_dose"):
                r = tools.soil_advice(p["id"], now)
                intent = "soil_advice"
            else:
                outs.append({"tool": "fixed", "key": "fertilizer_dose"})
                continue
        elif intent == "market_advice":
            r = await tools.market_advice(p["id"], now)
            if r.get("available") is False:
                outs.append({"tool": "fixed", "key": "market_unavailable", "error": r.get("error")})
                continue
            if not r.get("applicable"):
                outs.append({"tool": "fixed", "key": "out_of_scope"})
                continue
        elif intent == "knowledge_question":
            r = tools.knowledge_answer(state["transcript"])
            if not r["found"]:
                outs.append({"tool": "fixed", "key": "no_source"})
                continue
        elif intent in ("greeting", "out_of_scope"):
            outs.append({"tool": "fixed", "key": intent})
            continue
        else:
            continue
        if isinstance(r, dict) and r.get("available") is False:
            outs.append({"tool": "fixed", "key": "forecast_unavailable", "error": r.get("error")})
            continue
        outs.append({"tool": intent, "result": r})
        if isinstance(r, dict) and r.get("decision_id"):
            ids.append(r["decision_id"])
    return {"tool_outputs": outs, "decision_ids": list(dict.fromkeys(ids))}


def _template_reply(state: TurnState, lang: str) -> str:
    parts = list(state.get("prefix") or [])
    outs = state.get("tool_outputs", [])
    forecast_tools = [i for i, o in enumerate(outs)
                      if o["tool"] in ("harvest_window", "calamity_check", "blight_check", "sowing_window")]
    last_fc = forecast_tools[-1] if forecast_tools else -1
    for idx, o in enumerate(outs):
        if o["tool"] == "fixed":
            if o["key"] == "no_decision":
                parts.append("There is no advice to explain yet." if lang == "en" else "এখনও ব্যাখ্যা করার মতো কোনো পরামর্শ নেই।")
            else:
                parts.append(templates.fixed(o["key"], lang))
        elif o["tool"] == "harvest_window":
            parts.append(templates.harvest(o["result"], lang, with_source=idx == last_fc))
        elif o["tool"] == "calamity_check":
            parts.append(templates.calamity(o["result"], lang, with_source=idx == last_fc))
        elif o["tool"] == "blight_check":
            parts.append(templates.blight(o["result"], lang, with_source=idx == last_fc))
        elif o["tool"] == "sowing_window":
            parts.append(templates.sowing(o["result"], lang, with_source=idx == last_fc))
        elif o["tool"] == "claim_status":
            parts.append(templates.claim(o["result"], lang))
        elif o["tool"] == "explain_decision":
            parts.append(templates.explain(o["result"], lang))
        elif o["tool"] == "soil_advice":
            parts.append(templates.soil(o["result"], lang))
        elif o["tool"] == "market_advice":
            parts.append(templates.market(o["result"], lang))
        elif o["tool"] == "knowledge_question":
            parts.append(templates.knowledge(o["result"], lang))
    return " ".join(p for p in parts if p)


def _phrase_facts(state: TurnState, lang: str) -> list[dict]:
    """Tool outputs plus pre-formatted dates, so the phraser never computes a date itself."""
    facts = []
    L = "en" if lang == "en" else "bn"
    for o in state.get("tool_outputs", []):
        if o["tool"] == "fixed":
            facts.append({"say_exactly": templates.fixed(o["key"], L) if o["key"] != "no_decision" else "no advice yet"})
            continue
        r = dict(o["result"])
        for k in ("safe_cut_days", "plant_days"):
            if r.get(k):
                r[k + "_spoken"] = [fmt_day(d, L) for d in r[k]]
        for k in ("first_rain_day", "first_risk_day", "spray_by"):
            if r.get(k):
                r[k + "_spoken"] = fmt_day(r[k], L)
        for k in ("deadline_at", "event_at", "forecast_fetched_at"):
            if r.get(k):
                r[k + "_spoken"] = fmt_time(datetime.fromisoformat(r[k]), L)
        r.pop("days", None)
        if o["tool"] == "knowledge_question":
            r = {"passages": [{"source": x["source"], "url": x["url"], "quote": x["text"]} for x in r["passages"]]}
        facts.append({"tool": o["tool"], **r})
    if state.get("prefix"):
        facts.insert(0, {"say_first": " ".join(state["prefix"])})
    return facts


@traced(G, "compose", lambda o: o.get("reply_source", ""))
async def compose(state: TurnState) -> dict:
    t0 = time.perf_counter()
    lang = state.get("reply_lang") or state["lang"]
    facts = _phrase_facts(state, lang)
    if all(o["tool"] in VERBATIM_TOOLS for o in state.get("tool_outputs", [])):
        # Quotes and fixed texts are given exactly as written; an LLM paraphrase could change their meaning.
        return {"reply": _template_reply(state, "en" if lang == "en" else "bn"), "reply_source": "template",
                "timings": _t(state, "llm_ms", t0)}
    text = await llm.complete_text(PHRASE_SYSTEM, phrase_user(facts, lang, None))
    if text:
        return {"reply": text, "reply_source": "llm", "timings": _t(state, "llm_ms", t0)}
    return {"reply": _template_reply(state, "en" if lang == "en" else "bn"), "reply_source": "template",
            "timings": _t(state, "llm_ms", t0)}


@traced(G, "guardrail", lambda o: str(o.get("guardrail")))
async def guardrail(state: TurnState) -> dict:
    lang = state.get("reply_lang") or state["lang"]
    facts = _phrase_facts(state, lang)
    allowed = [facts, state.get("tool_outputs")]
    reply, source = state["reply"], state["reply_source"]
    c = check_reply(reply, allowed)
    log = {"first": c.ok}
    if not c.ok and source == "llm":
        retry = await llm.complete_text(PHRASE_SYSTEM + f"\nYour previous draft failed a check ({c.problem}). Fix it.",
                                        phrase_user(facts, lang, None))
        c2 = check_reply(retry, allowed) if retry else None
        log["retry"] = bool(c2 and c2.ok)
        if c2 and c2.ok:
            reply = retry
        else:
            reply, source = _template_reply(state, "en" if lang == "en" else "bn"), "template"
            log["templated"] = True
            log["problem"] = c.problem
    elif not c.ok:
        log["problem"] = c.problem  # template text; logged for the operator view
    return {"reply": reply, "reply_source": source, "guardrail": log}


@traced(G, "speak", lambda o: "audio" if o.get("audio_b64") else "text only")
async def speak(state: TurnState) -> dict:
    t0 = time.perf_counter()
    reply = state.get("question") or state.get("reply") or ""
    lang = state.get("reply_lang") or state.get("lang", "bn")
    audio = await sarvam.tts(reply, lang)
    timings = _t(state, "tts_ms", t0)
    if state.get("started"):
        timings["total_ms"] = int((time.perf_counter() - state["started"]) * 1000)
    try:
        with session_scope() as s:
            if state.get("transcript"):
                s.add(Message(farm_id=state["farm_id"], role="farmer", text=state["transcript"], lang=state.get("lang")))
            s.add(Message(farm_id=state["farm_id"], role="sathi", text=reply, lang=lang, timings=timings,
                          decision_ids=state.get("decision_ids") or []))
    except Exception as e:  # noqa: BLE001 - the request still answers
        return {"reply": reply, "audio_b64": audio, "timings": timings,
                "errors": list(state.get("errors") or []) + [f"db: {e}"]}
    return {"reply": reply, "audio_b64": audio, "timings": timings}


def _public(p: dict) -> dict:
    return {k: p.get(k) for k in ("name", "village", "district", "crop", "area_bigha", "stage", "cut_date", "insured")}


def build():
    g = StateGraph(TurnState)
    for name, fn in [("transcribe", transcribe), ("load_context", load_context), ("route", route),
                     ("clarify", clarify), ("act", act), ("compose", compose), ("guardrail", guardrail),
                     ("speak", speak)]:
        g.add_node(name, fn)
    g.add_edge(START, "transcribe")
    g.add_conditional_edges("transcribe", _after_transcribe, ["speak", "load_context"])
    g.add_edge("load_context", "route")
    g.add_edge("route", "clarify")
    g.add_conditional_edges("clarify", _after_clarify, ["speak", "act"])
    g.add_edge("act", "compose")
    g.add_edge("compose", "guardrail")
    g.add_edge("guardrail", "speak")
    g.add_edge("speak", END)
    return g.compile()


_graph = None


async def run_turn(farm_id: str, *, text: str | None = None, audio: bytes | None = None, lang: str = "bn",
                   awaiting: dict | None = None, now: datetime | None = None) -> dict[str, Any]:
    global _graph
    if _graph is None:
        _graph = build()
    t0 = time.perf_counter()
    st = await _graph.ainvoke({"run_id": "turn-" + uuid.uuid4().hex[:10], "farm_id": farm_id, "text": text,
                               "audio": audio, "lang": lang, "awaiting": awaiting, "now": now, "errors": [],
                               "timings": {}, "started": t0})
    timings = dict(st.get("timings") or {})
    timings["total_ms"] = int((time.perf_counter() - t0) * 1000)
    reply = st.get("reply") or st.get("question") or ""
    return {"transcript": st.get("transcript") or "", "reply_text": reply,
            "reply_audio_b64": st.get("audio_b64"), "decision_ids": st.get("decision_ids") or [],
            "timings": timings, "awaiting": st.get("awaiting_next"), "reply_source": st.get("reply_source"),
            "guardrail": st.get("guardrail"), "errors": st.get("errors") or [],
            "speech_available": sarvam.available()}
