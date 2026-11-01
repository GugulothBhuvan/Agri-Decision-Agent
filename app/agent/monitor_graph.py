"""Monitor graph: the scheduled run. Re-evaluates every farm and delivers only what changed.

load_farms -> fetch_sources -> validate -> (refetch_or_cache) -> evaluate_rules -> diff
           -> assign_tier -> compose -> guardrail -> deliver
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from ..config import rules
from ..db import Alert, Decision, Farm, session_scope
from ..llm import client as llm
from ..llm.prompts import PHRASE_SYSTEM, phrase_user
from ..sources import openmeteo
from . import templates, tools
from .guardrail import check_reply
from .trace import traced

G = "monitor"


class MonitorState(TypedDict, total=False):
    run_id: str
    now: datetime | None
    farms: list[dict]
    fetches: dict[str, Any]          # loc_key -> Forecast
    stale: list[str]                 # locations needing a re-fetch
    unavailable: list[str]           # farm ids with no usable data
    decisions: list[dict]            # recorded decisions this run
    deliverable: list[dict]
    composed: list[dict]
    deliveries: list[dict]
    errors: list[str]


@traced(G, "load_farms", lambda o: f"{len(o.get('farms', []))} active farms")
async def load_farms(state: MonitorState) -> dict:
    with session_scope() as s:
        rows = (s.query(Farm).filter(Farm.lat.isnot(None), Farm.stage.isnot(None), Farm.crop.isnot(None)).all())
        return {"farms": [tools.farm_dict(f) for f in rows]}


@traced(G, "fetch_sources")
async def fetch_sources(state: MonitorState) -> dict:
    locs: dict[str, tuple[float, float]] = {}
    for p in state.get("farms", []):
        locs.setdefault(openmeteo.loc_key(p["lat"], p["lon"]), (p["lat"], p["lon"]))
    results = await asyncio.gather(*(openmeteo.fetch_live(lat, lon) for lat, lon in locs.values()))
    return {"fetches": dict(zip(locs.keys(), results))}


@traced(G, "validate", lambda o: f"{len(o.get('stale', []))} stale locations")
async def validate(state: MonitorState) -> dict:
    fresh = rules()["freshness_hours"]
    stale = [k for k, fc in state.get("fetches", {}).items() if openmeteo.validate(fc, fresh) is not None]
    return {"stale": stale}


def _after_validate(state: MonitorState) -> str:
    return "refetch_or_cache" if state.get("stale") else "evaluate_rules"


@traced(G, "refetch_or_cache")
async def refetch_or_cache(state: MonitorState) -> dict:
    """One more fetch; else the last real fetch under 6 h, labelled cached; else data unavailable."""
    fresh = rules()["freshness_hours"]
    fetches = dict(state.get("fetches", {}))
    for key in state.get("stale", []):
        lat, lon = (float(x) for x in key.split(","))
        fc = await openmeteo.fetch_live(lat, lon, retries=[])
        if fc.status == "live" and openmeteo.validate(fc, fresh) is None:
            fetches[key] = fc
            continue
        cached = openmeteo.last_real_fetch(key, fresh)
        if cached and openmeteo.validate(cached, fresh) is None:
            fetches[key] = cached
        else:
            fetches[key] = openmeteo.Forecast("unavailable", key, fetch_id=fc.fetch_id, url=fc.url,
                                              error=fc.error or "no usable forecast")
    return {"fetches": fetches, "stale": []}


@traced(G, "evaluate_rules", lambda o: f"{len(o.get('decisions', []))} outcomes, {len(o.get('unavailable', []))} farms without data")
async def evaluate_rules(state: MonitorState) -> dict:
    """Evaluate and record (diff happens in record_decision: hash vs the previous decision)."""
    decisions, unavailable, errors = [], [], list(state.get("errors") or [])
    for p in state.get("farms", []):
        fc = state["fetches"].get(openmeteo.loc_key(p["lat"], p["lon"]))
        if fc is None or not fc.usable:
            unavailable.append(p["id"])
            continue
        try:
            outs = tools.evaluate(p, fc, state.get("now"))
        except Exception as e:  # noqa: BLE001 - rule exception logged; module skipped
            errors.append(f"evaluate {p['id']}: {e}")
            continue
        for o in outs:
            decisions.append({"farm": p, "outcome": o, "fc": fc})
    return {"decisions": decisions, "unavailable": unavailable, "errors": errors}


@traced(G, "diff", lambda o: f"{sum(1 for d in o.get('decisions', []) if d['rec']['changed'])} changed")
async def diff(state: MonitorState) -> dict:
    out = []
    for d in state.get("decisions", []):
        rec = tools.record_decision(d["farm"], d["outcome"], d["fc"], "monitor", state.get("now"))
        out.append({**d, "rec": rec})
    return {"decisions": out}


@traced(G, "assign_tier", lambda o: f"{len(o.get('deliverable', []))} to deliver")
async def assign_tier(state: MonitorState) -> dict:
    """Tier was assigned from the TRD table when recorded; keep changed, actionable, sourced, non-green ones."""
    keep = [d for d in state.get("decisions", [])
            if d["rec"]["changed"] and d["rec"]["actionable"] and not d["rec"]["blocked"] and d["rec"]["tier"] != "green"]
    return {"deliverable": keep}


def _alert_facts(d: dict) -> dict:
    rec = d["rec"]
    return {"module": rec["module"], **rec["data"], "window_hours": 24,
            "forecast_fetched_at": rec["forecast_fetched_at"], "forecast_cached": rec["forecast_cached"]}


def template_alert(d: dict, lang: str) -> str:
    facts = _alert_facts(d)
    m = facts["module"]
    if m == "harvest":
        return templates.harvest(facts, lang)
    if m == "calamity":
        return templates.calamity(facts, lang)
    if m == "blight":
        return templates.blight(facts, lang)
    if m == "sowing":
        return templates.sowing(facts, lang)
    if m == "claim":
        p = d["farm"]
        dl = datetime.fromisoformat(facts["deadline_at"])
        if lang == "en":
            return (f"Heavy rain hit your cut paddy (estimated {facts['past24_mm']} mm in 24 hours). A claim kit is open: "
                    f"report the loss by {dl.strftime('%H:%M')} on {templates.fmt_day(dl.date(), 'en')}. "
                    f"Source: Bangla Shasya Bima prospectus, report within {facts['report_within_hours']} hours.")
        return (f"আপনার কাটা ধানে ভারী বৃষ্টি হয়েছে (২৪ ঘণ্টায় আনুমানিক {templates.num(facts['past24_mm'], 'bn')} মিমি)। দাবির চেকলিস্ট খোলা হয়েছে: "
                f"{templates.fmt_day(dl.date(), 'bn')}, {templates.num(dl.strftime('%H:%M'), 'bn')}-এর মধ্যে ক্ষতি জানান। "
                f"সূত্র: বাংলা শস্য বিমার নিয়ম, {templates.num(facts['report_within_hours'], 'bn')} ঘণ্টার মধ্যে।")
    return d["rec"]["reason_bn"] if lang == "bn" else d["rec"]["reason_en"]


@traced(G, "compose", lambda o: f"{len(o.get('composed', []))} alerts phrased")
async def compose(state: MonitorState) -> dict:
    out = []
    for d in state.get("deliverable", []):
        texts = {}
        for lang in ("bn", "en"):
            texts[lang] = await llm.complete_text(PHRASE_SYSTEM, phrase_user(_alert_facts(d), lang, d["rec"]["tier"]))
        out.append({**d, "texts": texts})
    return {"composed": out}


@traced(G, "guardrail")
async def guardrail(state: MonitorState) -> dict:
    """Number and action checks: regenerate once naming the problem, then use the template."""
    out = []
    for d in state.get("composed", []):
        facts = _alert_facts(d)
        final = {}
        for lang in ("bn", "en"):
            text = d["texts"].get(lang)
            if text and not check_reply(text, facts).ok:
                problem = check_reply(text, facts).problem
                text = await llm.complete_text(
                    PHRASE_SYSTEM + f"\nYour previous draft failed a check ({problem}). Fix it.",
                    phrase_user(facts, lang, d["rec"]["tier"]))
                if text and not check_reply(text, facts).ok:
                    text = None
            final[lang] = text or template_alert(d, lang)
        out.append({**d, "final": final})
    return {"composed": out}


@traced(G, "deliver", lambda o: f"{len(o.get('deliveries', []))} delivered")
async def deliver(state: MonitorState) -> dict:
    deliveries, errors = [], list(state.get("errors") or [])
    for d in state.get("composed", []):
        rec = d["rec"]
        try:
            claim = None
            if rec["module"] == "claim" and rec["data"].get("action") == "claim_open":
                claim = tools.open_claim(d["farm"], rec)
            with session_scope() as s:
                s.add(Alert(farm_id=d["farm"]["id"], decision_id=rec["decision_id"], tier=rec["tier"],
                            text_bn=d["final"]["bn"], text_en=d["final"]["en"]))
                dec = s.get(Decision, rec["decision_id"])
                dec.delivered = True
            deliveries.append({"farm_id": d["farm"]["id"], "decision_id": rec["decision_id"], "tier": rec["tier"],
                               "module": rec["module"], "claim": claim})
        except Exception as e:  # noqa: BLE001 - shown on the operator view
            errors.append(f"deliver {rec['decision_id']}: {e}")
    return {"deliveries": deliveries, "errors": errors}


def build():
    g = StateGraph(MonitorState)
    for name, fn in [("load_farms", load_farms), ("fetch_sources", fetch_sources), ("validate", validate),
                     ("refetch_or_cache", refetch_or_cache), ("evaluate_rules", evaluate_rules), ("diff", diff),
                     ("assign_tier", assign_tier), ("compose", compose), ("guardrail", guardrail),
                     ("deliver", deliver)]:
        g.add_node(name, fn)
    g.add_edge(START, "load_farms")
    g.add_edge("load_farms", "fetch_sources")
    g.add_edge("fetch_sources", "validate")
    g.add_conditional_edges("validate", _after_validate, ["refetch_or_cache", "evaluate_rules"])
    g.add_edge("refetch_or_cache", "evaluate_rules")
    for a, b in [("evaluate_rules", "diff"), ("diff", "assign_tier"), ("assign_tier", "compose"),
                 ("compose", "guardrail"), ("guardrail", "deliver")]:
        g.add_edge(a, b)
    g.add_edge("deliver", END)
    return g.compile()


_graph = None
_lock = asyncio.Lock()


async def run(now: datetime | None = None) -> dict:
    """One monitor run. Single-flight: overlapping run-now presses wait for the current run."""
    global _graph
    if _graph is None:
        _graph = build()
    async with _lock:
        run_id = "run-" + uuid.uuid4().hex[:10]
        st = await _graph.ainvoke({"run_id": run_id, "now": now, "errors": []})
    cached = sum(1 for fc in (st.get("fetches") or {}).values() if getattr(fc, "status", "") == "cached")
    return {"run_id": run_id, "farms": len(st.get("farms") or []), "locations": len(st.get("fetches") or {}),
            "cached_locations": cached, "unavailable_farms": len(st.get("unavailable") or []),
            "decisions": len(st.get("decisions") or []), "delivered": st.get("deliveries") or [],
            "errors": st.get("errors") or []}
