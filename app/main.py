"""FastAPI app: website, API, agent and scheduler in one service."""
from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, text
from starlette.middleware.sessions import SessionMiddleware

from .agent import convo_graph, monitor_graph, templates as tpl, tools
from .config import settings
from .db import Alert, AppError, Claim, Decision, Farm, Message, SourceFetch, Trace, engine, init_db, log_error, session_scope
from .i18n import fmt_day, fmt_time, num, strings, t
from .sources import switch
from .speech import sarvam
from .llm import client as llm
from .web import views

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("fosol")
WEB = Path(__file__).resolve().parent / "web"
MAX_AUDIO_BYTES = 2_500_000  # ~60 s of opus at browser bitrates, with headroom


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    sched = None
    if settings().enable_scheduler:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        sched = AsyncIOScheduler(timezone="Asia/Kolkata")
        sched.add_job(_scheduled_run, "interval", hours=settings().schedule_hours, id="monitor",
                      max_instances=1, coalesce=True)
        sched.start()
    yield
    if sched:
        sched.shutdown(wait=False)


async def _scheduled_run():
    try:
        r = await monitor_graph.run()
        log.info("scheduled run %s: %s delivered", r["run_id"], len(r["delivered"]))
    except Exception as e:  # noqa: BLE001
        log_error("scheduled run", e)


app = FastAPI(title="Fosol Sathi", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings().session_secret, max_age=60 * 60 * 24 * 90,
                   same_site="lax", https_only=False)
app.mount("/static", StaticFiles(directory=WEB / "static"), name="static")
jinja = Jinja2Templates(directory=WEB / "templates")


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    """No request returns an unhandled error: log it, show it on /ops, answer politely."""
    log.exception("unhandled error on %s", request.url.path)
    log_error(f"{request.method} {request.url.path}", exc)
    if request.url.path.startswith("/api/"):
        lang = request.session.get("lang", "bn")
        return JSONResponse({"error": "internal", "message": t("error.generic", lang)}, status_code=500)
    return HTMLResponse(f"<p>{t('error.generic', request.session.get('lang', 'bn'))}</p>", status_code=500)


# ---------- session helpers ----------

def _lang(request: Request) -> str:
    return request.session.get("lang", "bn")


def _farm_id(request: Request) -> str | None:
    fid = request.session.get("farm_id")
    if fid and tools.get_farm(fid) is None:
        request.session.pop("farm_id", None)
        return None
    return fid


def _require_farm(request: Request) -> str:
    fid = _farm_id(request)
    if not fid:
        raise HTTPException(404, "no farm in this session")
    return fid


def _is_ops(request: Request) -> bool:
    return bool(request.session.get("ops"))


def _ops_check(request: Request, passcode: str | None = None):
    if _is_ops(request):
        return
    code = settings().ops_passcode
    if code and passcode and hmac.compare_digest(passcode, code):
        return
    raise HTTPException(401, "operator passcode required")


def _page(request: Request, name: str, **ctx) -> HTMLResponse:
    lang = _lang(request)
    other = "en" if lang == "bn" else "bn"
    s, s2 = strings(lang), strings(other)
    return jinja.TemplateResponse(request, name, {"lang": lang, "other": other, "s": s, "s2": s2,
                                                  "strings_json": {"bn": strings("bn"), "en": strings("en")}, **ctx})


# ---------- pages ----------

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    fid = _farm_id(request)
    lang = _lang(request)
    p = tools.get_farm(fid) if fid else None
    card, alerts, claim = None, [], None
    if p:
        d = views.home_decision(fid)
        card = views.decision_card(d, lang) if d else None
        with session_scope() as s:
            rows = s.query(Alert).filter(Alert.farm_id == fid).order_by(Alert.created_at.desc()).limit(5).all()
            alerts = [{"id": a.id, "tier": a.tier, "text": a.text_bn if lang == "bn" else a.text_en,
                       "decision_id": a.decision_id, "unread": a.read_at is None,
                       "time": fmt_time(a.created_at, lang)} for a in rows]
        st = tools.claim_status(fid)
        claim = st if st.get("open") else None
    return _page(request, "home.html", farm=p, farm_line=views.farm_line(p, lang) if p else "", card=card,
                 alerts=alerts, claim=claim, missing=tools.missing_fields(p) if p else None)


@app.get("/talk", response_class=HTMLResponse)
async def talk(request: Request):
    fid = _farm_id(request)
    msgs = []
    if fid:
        with session_scope() as s:
            rows = (s.query(Message).filter(Message.farm_id == fid).order_by(Message.created_at.desc(), Message.id.desc())
                    .limit(30).all())
            msgs = [{"role": m.role, "text": m.text, "decision_ids": m.decision_ids or []} for m in reversed(rows)]
    return _page(request, "talk.html", messages=msgs, speech=sarvam.available())


@app.get("/why/{decision_id}", response_class=HTMLResponse)
async def why(request: Request, decision_id: int):
    lang = _lang(request)
    fid = _farm_id(request)
    d = tools.explain_decision(decision_id, None if _is_ops(request) else fid)
    if d is None:
        return _page(request, "message.html", title=t("why.title", lang), body=t("why.not_found", lang))
    for src in d["sources"]:
        src["time"] = fmt_time(datetime.fromisoformat(src["fetched_at"]), lang)
    reason = d["reason_en"] if lang == "en" else d["reason_bn"]
    days = d["outcome"].get("days") or []
    for x in days:
        x["label"] = fmt_day(x["date"], lang)
    return _page(request, "why.html", d=d, reason=reason, days=days,
                 tier_label=t(f"tier.{d['tier']}", lang), when=fmt_time(datetime.fromisoformat(d["created_at"]), lang))


@app.get("/claim/{claim_id}", response_class=HTMLResponse)
async def claim_page(request: Request, claim_id: int):
    fid = _farm_id(request)
    lang = _lang(request)
    c = _claim_dict(claim_id, None if _is_ops(request) else fid, lang)
    if c is None:
        return _page(request, "message.html", title=t("claim.title", lang), body=t("claim.not_found", lang))
    return _page(request, "claim.html", c=c)


@app.get("/farm", response_class=HTMLResponse)
async def farm_page(request: Request):
    fid = _farm_id(request)
    p = tools.get_farm(fid) if fid else None
    return _page(request, "farm.html", farm=p, missing=tools.missing_fields(p) if p else [])


@app.get("/ops", response_class=HTMLResponse)
async def ops_page(request: Request):
    if not _is_ops(request):
        return _page(request, "ops_login.html", configured=bool(settings().ops_passcode))
    return _page(request, "ops.html", o=_ops_overview(request))


@app.post("/ops/login")
async def ops_login(request: Request, passcode: str = Form(...)):
    code = settings().ops_passcode
    if code and hmac.compare_digest(passcode, code):
        request.session["ops"] = True
        return RedirectResponse("/ops", status_code=303)
    return _page(request, "ops_login.html", configured=bool(code), failed=True)


@app.post("/ops/logout")
async def ops_logout(request: Request):
    request.session.pop("ops", None)
    return RedirectResponse("/", status_code=303)


# ---------- API ----------

@app.post("/api/turn")
async def api_turn(request: Request, audio: UploadFile | None = File(None)):
    lang = _lang(request)
    text_in = None
    if audio is None:
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001
            body = {}
        text_in = (body.get("text") or "").strip()
        if not text_in:
            raise HTTPException(400, "send multipart 'audio' or JSON 'text'")
        if len(text_in) > 1000:
            text_in = text_in[:1000]
    blob = await audio.read() if audio is not None else None
    if blob is not None and len(blob) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "recording too long")
    fid = _farm_id(request)
    if not fid:
        fid = tools.create_farm(lang)["id"]
        request.session["farm_id"] = fid
    r = await convo_graph.run_turn(fid, text=text_in, audio=blob, lang=lang, awaiting=request.session.get("awaiting"))
    request.session["awaiting"] = r.pop("awaiting")
    if not r["reply_text"]:
        r["reply_text"] = t("error.generic", lang)
    if r["errors"]:
        for e in r["errors"]:
            log_error("turn", e)
    return r


@app.get("/api/farm")
async def api_farm_get(request: Request):
    fid = _require_farm(request)
    p = tools.get_farm(fid)
    return {"profile": p, "missing": tools.missing_fields(p)}


@app.patch("/api/farm")
async def api_farm_patch(request: Request, body: dict = Body(...)):
    fid = _farm_id(request)
    if not fid:
        fid = tools.create_farm(_lang(request))["id"]
        request.session["farm_id"] = fid
    allowed = {k: body[k] for k in ("name", "village", "district", "crop", "area_bigha", "stage", "cut_date",
                                    "insured", "address_as", "language") if k in body}
    if "language" in allowed:
        if allowed["language"] not in ("bn", "en"):
            raise HTTPException(400, "language must be bn or en")
        request.session["lang"] = allowed["language"]
    res = await tools.update_profile(fid, allowed)
    # Edits on the farm screen are the family's own confirmation.
    if not res["missing"] and not res["geocode_failed"]:
        tools.confirm_profile(fid)
        res["profile"] = tools.get_farm(fid)
    for k in ("lat", "lon"):
        if k in body and body[k] not in (None, ""):
            with session_scope() as s:  # manual pin when geocoding fails
                f = s.get(Farm, fid)
                setattr(f, k, float(body[k]))
            res["profile"] = tools.get_farm(fid)
            res["missing"] = tools.missing_fields(res["profile"])
            if not res["missing"]:
                tools.confirm_profile(fid)
                res["profile"] = tools.get_farm(fid)
    return res


@app.delete("/api/farm", status_code=204)
async def api_farm_delete(request: Request):
    fid = _farm_id(request)
    if fid:
        tools.delete_farm(fid)
    request.session.pop("farm_id", None)
    request.session.pop("awaiting", None)
    return Response(status_code=204)


@app.post("/api/lang")
async def api_lang(request: Request, body: dict = Body(...)):
    lang = body.get("lang")
    if lang not in ("bn", "en"):
        raise HTTPException(400, "lang must be bn or en")
    request.session["lang"] = lang
    fid = _farm_id(request)
    if fid:
        await tools.update_profile(fid, {"language": lang})
    return {"lang": lang}


@app.get("/api/decisions/{decision_id}")
async def api_decision(request: Request, decision_id: int):
    d = tools.explain_decision(decision_id, None if _is_ops(request) else _farm_id(request))
    if d is None:
        raise HTTPException(404, "not found")
    return d


@app.get("/api/alerts")
async def api_alerts(request: Request):
    fid = _require_farm(request)
    with session_scope() as s:
        rows = s.query(Alert).filter(Alert.farm_id == fid).order_by(Alert.created_at.desc()).limit(50).all()
        out = [{"id": a.id, "tier": a.tier, "text_bn": a.text_bn, "text_en": a.text_en, "decision_id": a.decision_id,
                "read_at": a.read_at.isoformat() if a.read_at else None, "created_at": a.created_at.isoformat()}
               for a in rows]
        for a in rows:
            a.read_at = a.read_at or datetime.now(timezone.utc)
    return out


def _claim_dict(claim_id: int, farm_id: str | None, lang: str) -> dict | None:
    with session_scope() as s:
        c = s.get(Claim, claim_id)
        if c is None or (farm_id is not None and c.farm_id != farm_id) or (farm_id is None and False):
            return None
        st = tools.claim_status(c.farm_id)
        items = {i["key"]: i for i in tpl.CHECKLIST}
        checklist = [{"key": x["key"], "done": x["done"], "label": items[x["key"]][lang],
                      "label_2": items[x["key"]]["en" if lang == "bn" else "bn"]} for x in c.checklist if x["key"] in items]
        from .config import rules
        cc = rules()["calamity"]
        return {"id": c.id, "decision_id": c.decision_id, "deadline_at": tools._aware(c.deadline_at).isoformat(),
                "event_at": tools._aware(c.event_at).isoformat(),
                "deadline_label": fmt_time(c.deadline_at, lang), "event_label": fmt_time(c.event_at, lang),
                "rain_mm": num(c.rain_mm or 0, lang), "checklist": checklist,
                "report_text": c.report_text_bn if lang == "bn" else c.report_text_en,
                "report_text_2": c.report_text_en if lang == "bn" else c.report_text_bn,
                "sent": c.sent_at is not None, "sent_label": fmt_time(c.sent_at, lang) if c.sent_at else "",
                "closed": st.get("closed") if st.get("claim_id") == c.id else tools._aware(c.deadline_at) <= datetime.now(timezone.utc),
                "rule_source": cc["source"], "rule_url": cc["url"], "hours": num(cc["report_within_hours"], lang)}


@app.get("/api/claims/{claim_id}")
async def api_claim(request: Request, claim_id: int):
    c = _claim_dict(claim_id, None if _is_ops(request) else _farm_id(request), _lang(request))
    if c is None:
        raise HTTPException(404, "not found")
    return c


@app.patch("/api/claims/{claim_id}")
async def api_claim_patch(request: Request, claim_id: int, body: dict = Body(...)):
    """Checklist items and 'sent' are marked by the family, never by the agent."""
    fid = _farm_id(request)
    with session_scope() as s:
        c = s.get(Claim, claim_id)
        if c is None or c.farm_id != fid:
            raise HTTPException(404, "not found")
        states = body.get("checklist") or {}
        c.checklist = [{"key": x["key"], "done": bool(states.get(x["key"], x["done"]))} for x in c.checklist]
        if "sent" in body:
            c.sent_at = datetime.now(timezone.utc) if body["sent"] else None
    return _claim_dict(claim_id, fid, _lang(request))


@app.post("/api/ops/run")
async def api_ops_run(request: Request, body: dict | None = Body(None)):
    _ops_check(request, (body or {}).get("passcode"))
    return await monitor_graph.run()


@app.post("/api/ops/source")
async def api_ops_source(request: Request, body: dict = Body(...)):
    _ops_check(request, body.get("passcode"))
    try:
        return {"sources": switch.set_enabled(body["source"], bool(body["enabled"]))}
    except KeyError:
        raise HTTPException(400, f"unknown source; one of {list(switch.SOURCES)}")


@app.get("/api/ops/overview")
async def api_ops_overview(request: Request, passcode: str | None = None):
    _ops_check(request, passcode)
    return _ops_overview(request)


def _ops_overview(request: Request) -> dict:
    with session_scope() as s:
        farms = [tools.farm_dict(f) for f in s.query(Farm).order_by(Farm.created_at.desc()).limit(100).all()]
        decs = s.query(Decision).order_by(Decision.created_at.desc(), Decision.id.desc()).limit(40).all()
        fetch_ids = {i for d in decs for i in (d.fetch_ids or [])}
        fetch_map = {f.id: f for f in s.query(SourceFetch).filter(SourceFetch.id.in_(fetch_ids or {0})).all()}
        decisions = []
        for d in decs:
            f = fetch_map.get((d.fetch_ids or [None])[0])
            decisions.append({"id": d.id, "farm_id": d.farm_id, "module": d.module, "tier": d.tier,
                              "action": d.outcome.get("action") or (", ".join(d.outcome.get("safe_cut_days") or []) or "wait"),
                              "changed": d.changed, "delivered": d.delivered, "cached": d.cached, "origin": d.origin,
                              "blocked": d.blocked_reason, "reason": d.reason_en,
                              "source": "Open-Meteo forecast API" if f else None,
                              "source_url": f.url if f else None,
                              "fetched_at": fmt_time(f.fetched_at, "en") if f else None,
                              "created_at": fmt_time(d.created_at, "en")})
        health = []
        for src, name in switch.SOURCES.items():
            last = s.query(SourceFetch).filter(SourceFetch.source == src).order_by(SourceFetch.fetched_at.desc()).first()
            last_ok = (s.query(SourceFetch).filter(SourceFetch.source == src, SourceFetch.ok.is_(True))
                       .order_by(SourceFetch.fetched_at.desc()).first())
            health.append({"source": src, "name": name, "enabled": switch.is_enabled(src),
                           "configured": {"sarvam": sarvam_configured(), "llm": llm_configured()}.get(src, True),
                           "last": fmt_time(last.fetched_at, "en") if last else None, "last_ok": bool(last and last.ok),
                           "last_error": last.error if last and not last.ok else None,
                           "last_success": fmt_time(last_ok.fetched_at, "en") if last_ok else None})
        traces = [{"run_id": t_.run_id, "graph": t_.graph, "node": t_.node, "summary": t_.summary,
                   "ms": t_.duration_ms, "error": t_.error, "at": fmt_time(t_.created_at, "en")}
                  for t_ in s.query(Trace).order_by(Trace.id.desc()).limit(60).all()]
        errors = [{"where": e.where, "error": e.error, "at": fmt_time(e.created_at, "en")}
                  for e in s.query(AppError).order_by(AppError.id.desc()).limit(20).all()]
        n_dec = s.query(func.count(Decision.id)).scalar() or 0
        n_src = s.query(func.count(Decision.id)).filter(Decision.blocked_reason.is_(None)).scalar() or 0
        latest_turns = (s.query(Message).filter(Message.role == "sathi", Message.timings.isnot(None))
                        .order_by(Message.id.desc()).limit(50).all())
    totals = sorted((m.timings or {}).get("total_ms", 0) for m in latest_turns if (m.timings or {}).get("total_ms"))
    p90 = totals[int(0.9 * (len(totals) - 1))] if totals else None
    return {"farms": farms, "decisions": decisions, "health": health, "traces": traces, "errors": errors,
            "sources": switch.state(), "stats": {"decisions": n_dec, "with_source": n_src, "turn_p90_ms": p90}}


def sarvam_configured() -> bool:
    return bool(settings().sarvam_api_key)


def llm_configured() -> bool:
    return settings().llm_provider == "anthropic" and bool(settings().llm_api_key)


@app.get("/healthz")
async def healthz():
    db_ok = True
    try:
        with engine.connect() as c:
            c.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    return {"ok": db_ok, "database": db_ok, "sources": switch.state(),
            "speech_configured": sarvam_configured(), "llm_configured": llm_configured()}
