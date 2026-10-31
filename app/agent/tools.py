"""Tool registry used by both graphs, so a reply and an alert can never disagree."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from ..config import rules
from ..db import Claim, Decision, Farm, SourceFetch, session_scope, utcnow
from ..i18n import IST
from ..rules.base import Outcome
from ..rules.calamity import calamity_check as rule_calamity
from ..rules.claim import claim_trigger as rule_claim
from ..rules.harvest import harvest_window as rule_harvest
from ..rules.series import Series
from ..rules.tiers import assign_tier
from ..sources import openmeteo
from ..sources.geocode import geocode_village as nominatim_lookup
from . import templates
from .guardrail import source_ok

PROFILE_FIELDS = ["name", "village", "district", "crop", "area_bigha", "stage", "cut_date", "insured"]


# ---------- profile ----------

def farm_dict(f: Farm) -> dict:
    return {
        "id": f.id, "name": f.name, "village": f.village, "district": f.district, "place_name": f.place_name,
        "lat": f.lat, "lon": f.lon, "crop": f.crop, "area_bigha": f.area_bigha, "stage": f.stage,
        "cut_date": f.cut_date.isoformat() if f.cut_date else None, "insured": f.insured,
        "language": f.language, "profile_confirmed": f.profile_confirmed, "address_as": f.address_as,
    }


def missing_fields(p: dict) -> list[str]:
    """Fields still needed, in the order the agent asks for them, one at a time."""
    out = []
    if not p.get("village") or p.get("lat") is None:
        out.append("village")
    if not p.get("crop"):
        out.append("crop")
    if not p.get("stage"):
        out.append("stage")
    if p.get("area_bigha") is None:
        out.append("area_bigha")
    if p.get("stage") == "cut" and not p.get("cut_date"):
        out.append("cut_date")
    if p.get("crop") == "paddy_aman" and p.get("stage") in ("ready", "cut") and p.get("insured") is None:
        out.append("insured")
    return out


def get_farm(farm_id: str) -> dict | None:
    with session_scope() as s:
        f = s.get(Farm, farm_id)
        return farm_dict(f) if f else None


def create_farm(language: str = "bn") -> dict:
    with session_scope() as s:
        f = Farm(language=language)
        s.add(f)
        s.flush()
        return farm_dict(f)


async def geocode_village(village: str, district: str | None) -> dict:
    place = await nominatim_lookup(village, district)
    if place is None:
        return {"found": False, "village": village, "district": district}
    return {"found": True, "lat": place.lat, "lon": place.lon, "display_name": place.display_name}


async def update_profile(farm_id: str, fields: dict, *, today: date | None = None) -> dict:
    """Save stated fields; geocode a new village. Returns {profile, missing, geocode_failed, changed}."""
    today = today or datetime.now(IST).date()
    clean = {}
    for k in ("name", "village", "district", "crop", "stage", "address_as", "language"):
        v = fields.get(k)
        if isinstance(v, str) and v.strip():
            clean[k] = v.strip()
    if fields.get("area_bigha") is not None:
        try:
            a = float(fields["area_bigha"])
            if 0 < a < 1000:
                clean["area_bigha"] = a
        except (TypeError, ValueError):
            pass
    if fields.get("insured") is not None:
        clean["insured"] = bool(fields["insured"])
    if fields.get("cut_days_ago") is not None:
        try:
            n = int(fields["cut_days_ago"])
            if 0 <= n <= 60:
                clean["cut_date"] = today - timedelta(days=n)
                clean.setdefault("stage", "cut")
        except (TypeError, ValueError):
            pass
    if fields.get("cut_date"):
        try:
            clean["cut_date"] = date.fromisoformat(str(fields["cut_date"]))
        except ValueError:
            pass
    if clean.get("crop") not in (None, "paddy_aman", "potato"):
        clean.pop("crop")
    if clean.get("stage") not in (None, "growing", "ready", "cut", "sold", "to_sow"):
        clean.pop("stage")

    geocode_failed = False
    with session_scope() as s:
        f = s.get(Farm, farm_id)
        cur = farm_dict(f)
    if "village" in clean or ("district" in clean and cur.get("village") and cur.get("lat") is None):
        g = await geocode_village(clean.get("village", cur.get("village")), clean.get("district", cur.get("district")))
        if g["found"]:
            clean.update(lat=g["lat"], lon=g["lon"], place_name=g["display_name"])
        else:
            geocode_failed = True
            clean.update(lat=None, lon=None, place_name=None)
    with session_scope() as s:
        f = s.get(Farm, farm_id)
        changed = []
        for k, v in clean.items():
            if getattr(f, k) != v:
                setattr(f, k, v)
                changed.append(k)
        if changed and any(k in changed for k in PROFILE_FIELDS):
            f.profile_confirmed = False
        p = farm_dict(f)
    return {"profile": p, "missing": missing_fields(p), "geocode_failed": geocode_failed, "changed": changed}


def confirm_profile(farm_id: str) -> None:
    with session_scope() as s:
        f = s.get(Farm, farm_id)
        f.profile_confirmed = True


def delete_farm(farm_id: str) -> None:
    with session_scope() as s:
        f = s.get(Farm, farm_id)
        if f:
            s.delete(f)


# ---------- rules over a forecast ----------

def has_open_claim(farm_id: str, now: datetime | None = None) -> bool:
    now = now or utcnow()
    with session_scope() as s:
        c = (s.query(Claim).filter(Claim.farm_id == farm_id).order_by(Claim.created_at.desc()).first())
        if not c:
            return False
        return c.sent_at is None and _aware(c.deadline_at) > now


def evaluate(p: dict, fc: openmeteo.Forecast, now: datetime | None = None,
             open_claim: bool | None = None, modules: list[str] | None = None) -> list[Outcome]:
    """Run every rule that applies to this farm's crop and stage. Pure apart from the open-claim lookup."""
    cfg = rules()
    series = Series(fc.raw)
    out: list[Outcome] = []
    stage, crop = p.get("stage"), p.get("crop")
    want = set(modules or ["harvest", "calamity", "claim"])
    if crop == "paddy_aman" and stage in ("ready", "cut"):
        if "harvest" in want:
            out.append(rule_harvest(series, cfg, now))
        if "calamity" in want:
            out.append(rule_calamity(series, cfg, stage, now))
    if crop == "paddy_aman" and stage == "cut" and "claim" in want:
        oc = has_open_claim(p["id"]) if open_claim is None else open_claim
        cut = date.fromisoformat(p["cut_date"]) if p.get("cut_date") else None
        out.append(rule_claim(series, cfg, stage=stage, insured=p.get("insured"), cut_date=cut,
                              has_open_claim=oc, now=now))
    return out


def forecast_source(fc: openmeteo.Forecast) -> dict:
    return {"name": openmeteo.SOURCE_NAME, "url": fc.url, "what": "hourly rain forecast and 7-day model estimate",
            "fetched_at": fc.fetched_at.isoformat() if fc.fetched_at else None, "cached": fc.cached,
            "fetch_id": fc.fetch_id}


def record_decision(p: dict, o: Outcome, fc: openmeteo.Forecast, origin: str,
                    now: datetime | None = None) -> dict:
    """Store a rules outcome with its hash, tier and sources. Blocks delivery without a source."""
    h = o.decision_hash()
    with session_scope() as s:
        prev = (s.query(Decision).filter(Decision.farm_id == p["id"], Decision.module == o.module)
                .order_by(Decision.created_at.desc(), Decision.id.desc()).first())
        changed = prev is None or prev.hash != h
        tier = assign_tier(o, changed, now)
        src = forecast_source(fc)
        blocked = None if source_ok([src]) else "no source with name, URL and fetch time"
        d = Decision(farm_id=p["id"], module=o.module, outcome=o.data, reason_en=o.reason_en, reason_bn=o.reason_bn,
                     tier=tier, fetch_ids=[fc.fetch_id] if fc.fetch_id else [],
                     rule_sources=o.rule_sources + [
                         {"name": "Team design choice", "what": t,
                          "what_bn": (o.team_choices_bn[i] if i < len(o.team_choices_bn) else None)}
                         for i, t in enumerate(o.team_choices)],
                     cached=fc.cached, hash=h, changed=changed, blocked_reason=blocked, origin=origin,
                     delivered=(origin == "turn" and blocked is None and o.actionable))
        s.add(d)
        s.flush()
        return {"decision_id": d.id, "module": o.module, "tier": tier, "changed": changed,
                "blocked": blocked, "actionable": o.actionable, "data": o.data,
                "reason_en": o.reason_en, "reason_bn": o.reason_bn,
                "forecast_fetched_at": src["fetched_at"], "forecast_cached": fc.cached}


def open_claim(p: dict, decision: dict) -> dict:
    data = decision["data"]
    # Rule times are naive IST; store UTC (SQLite keeps no offset).
    event_at = datetime.fromisoformat(data["event_at"]).replace(tzinfo=IST).astimezone(timezone.utc)
    deadline = datetime.fromisoformat(data["deadline_at"]).replace(tzinfo=IST).astimezone(timezone.utc)
    with session_scope() as s:
        c = Claim(farm_id=p["id"], decision_id=decision["decision_id"], event_at=event_at, deadline_at=deadline,
                  rain_mm=data.get("past24_mm"),
                  checklist=[{"key": i["key"], "done": False} for i in templates.CHECKLIST],
                  report_text_bn=templates.report_message(p, event_at, data.get("past24_mm") or 0, "bn"),
                  report_text_en=templates.report_message(p, event_at, data.get("past24_mm") or 0, "en"))
        s.add(c)
        s.flush()
        return {"claim_id": c.id, "deadline_at": deadline.isoformat()}


# ---------- tools the conversation graph calls ----------

async def _farm_forecast(p: dict) -> openmeteo.Forecast:
    return await openmeteo.get_forecast(p["lat"], p["lon"], rules()["freshness_hours"])


def _unavailable(fc: openmeteo.Forecast) -> dict:
    return {"available": False, "error": fc.error}


async def harvest_window(farm_id: str, now: datetime | None = None) -> dict:
    p = get_farm(farm_id)
    fc = await _farm_forecast(p)
    if not fc.usable:
        return _unavailable(fc)
    [o] = evaluate(p, fc, now, modules=["harvest"]) or [None]
    if o is None:
        return {"available": True, "applicable": False}
    d = record_decision(p, o, fc, "turn", now)
    return {"available": True, "applicable": True, **_facts(d)}


async def calamity_check(farm_id: str, now: datetime | None = None) -> dict:
    """Protect alert, plus the claim trigger for cut paddy (opens the kit if it fires)."""
    p = get_farm(farm_id)
    fc = await _farm_forecast(p)
    if not fc.usable:
        return _unavailable(fc)
    outs = evaluate(p, fc, now, modules=["calamity", "claim"])
    if not outs:
        return {"available": True, "applicable": False}
    result = {"available": True, "applicable": True}
    for o in outs:
        d = record_decision(p, o, fc, "turn", now)
        if o.module == "calamity":
            result.update(_facts(d))
        elif o.module == "claim" and d["data"].get("action") == "claim_open" and d["blocked"] is None:
            result["claim_opened"] = open_claim(p, d)
            result["claim_decision_id"] = d["decision_id"]
    return result


def claim_status(farm_id: str, now: datetime | None = None) -> dict:
    now = now or utcnow()
    cfg = rules()["calamity"]
    with session_scope() as s:
        c = s.query(Claim).filter(Claim.farm_id == farm_id).order_by(Claim.created_at.desc()).first()
        if not c:
            return {"open": False}
        dl = _aware(c.deadline_at)
        return {"open": c.sent_at is None, "claim_id": c.id, "decision_id": c.decision_id,
                "deadline_at": dl.astimezone(IST).isoformat(), "event_at": _aware(c.event_at).astimezone(IST).isoformat(),
                "hours_left": templates.hours_between(now, dl), "closed": dl <= now,
                "checklist": c.checklist, "sent": c.sent_at is not None,
                "report_within_hours": cfg["report_within_hours"]}


def explain_decision(decision_id: int, farm_id: str | None = None) -> dict | None:
    with session_scope() as s:
        d = s.get(Decision, decision_id)
        if d is None or (farm_id and d.farm_id != farm_id):
            return None
        fetches = s.query(SourceFetch).filter(SourceFetch.id.in_(d.fetch_ids or [0])).all() if d.fetch_ids else []
        sources = [{"name": openmeteo.SOURCE_NAME if f.source == "openmeteo" else f.source, "url": f.url,
                    "fetched_at": _aware(f.fetched_at).isoformat(), "ok": f.ok, "cached": d.cached,
                    "what": "hourly rain forecast and 7-day model estimate"} for f in fetches]
        fetched = sources[0]["fetched_at"] if sources else None
        return {"decision_id": d.id, "module": d.module, "tier": d.tier, "outcome": d.outcome,
                "reason_en": d.reason_en, "reason_bn": d.reason_bn, "cached": d.cached,
                "created_at": _aware(d.created_at).isoformat(), "sources": sources,
                "rule_sources": d.rule_sources or [], "forecast_fetched_at": fetched, "forecast_cached": d.cached,
                "blocked_reason": d.blocked_reason}


def latest_decisions(farm_id: str) -> list[dict]:
    """Latest decision per module, newest first."""
    with session_scope() as s:
        rows = (s.query(Decision).filter(Decision.farm_id == farm_id)
                .order_by(Decision.created_at.desc(), Decision.id.desc()).limit(50).all())
        seen, out = set(), []
        for d in rows:
            if d.module in seen:
                continue
            seen.add(d.module)
            tier = d.tier
            if not d.changed:  # unchanged = green in the log; show the tier it had when it last changed
                first = next((x for x in rows if x.module == d.module and x.hash == d.hash and x.changed), None)
                if first is None:
                    first = (s.query(Decision).filter(Decision.farm_id == farm_id, Decision.module == d.module,
                                                      Decision.hash == d.hash, Decision.changed.is_(True))
                             .order_by(Decision.id.desc()).first())
                tier = first.tier if first else d.tier
            out.append({"decision_id": d.id, "module": d.module, "tier": tier, "outcome": d.outcome,
                        "reason_en": d.reason_en, "reason_bn": d.reason_bn, "cached": d.cached,
                        "created_at": _aware(d.created_at).isoformat(), "actionable": d.outcome.get("action") != "none"})
        return out


def _facts(d: dict) -> dict:
    data = dict(d["data"])
    return {"decision_id": d["decision_id"], "module": d["module"], "tier": d["tier"], "changed": d["changed"],
            **data, "window_hours": 24, "reason_en": d["reason_en"], "reason_bn": d["reason_bn"],
            "forecast_fetched_at": d["forecast_fetched_at"], "forecast_cached": d["forecast_cached"]}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
