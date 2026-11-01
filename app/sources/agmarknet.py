"""Agmarknet daily mandi prices via the data.gov.in API. Needs DATA_GOV_API_KEY (free on data.gov.in).

Every response is stored in source_fetches (source "agmarknet"); price history is built from those stored
fetches, so the 14-day comparison only has the days this app has actually fetched. No key, no prices:
the market agent then says prices are unavailable rather than guessing.
"""
from __future__ import annotations

import os
from datetime import timedelta, timezone

import httpx

from ..config import rules
from ..db import SourceFetch, session_scope, utcnow
from . import switch

SOURCE = "agmarknet"
BASE = "https://api.data.gov.in/resource/"


def api_key() -> str:
    return os.environ.get("DATA_GOV_API_KEY", "").strip()


def available() -> bool:
    return bool(api_key()) and switch.is_enabled(SOURCE)


def _field(rec: dict, *names):
    low = {k.lower(): v for k, v in rec.items()}
    for n in names:
        if n.lower() in low and low[n.lower()] not in (None, ""):
            return low[n.lower()]
    return None


def normalise(records: list[dict]) -> list[dict]:
    """Map API records to {market, district, commodity, variety, date, min, max, modal} in Rs per quintal."""
    out = []
    for r in records:
        try:
            modal = float(_field(r, "modal_price", "Modal_Price", "modal_price_rs_quintal"))
        except (TypeError, ValueError):
            continue
        def f(*n):
            v = _field(r, *n)
            try:
                return float(v) if v is not None else None
            except ValueError:
                return None
        out.append({"market": _field(r, "market", "Market"), "district": _field(r, "district", "District"),
                    "commodity": _field(r, "commodity", "Commodity"), "variety": _field(r, "variety", "Variety"),
                    "date": _field(r, "arrival_date", "Arrival_Date"), "min": f("min_price", "Min_Price"),
                    "max": f("max_price", "Max_Price"), "modal": modal})
    return out


async def fetch_prices(state: str, commodity: str) -> dict:
    """Returns {"ok", "fetch_id", "url", "fetched_at", "records", "error"}; never raises."""
    mc = rules()["market"]
    url = BASE + mc["resource_id"]
    params = {"api-key": api_key(), "format": "json", "limit": 500,
              "filters[state.keyword]": state, "filters[commodity]": commodity}
    public_params = {k: v for k, v in params.items() if k != "api-key"}
    if not available():
        err = "disabled by operator" if api_key() else "DATA_GOV_API_KEY not set"
        return {"ok": False, "error": err, "records": [], "url": url}
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(url, params=params)
        r.raise_for_status()
        recs = normalise(r.json().get("records") or [])
        ok, err = True, None
    except Exception as e:  # noqa: BLE001
        recs, ok, err = [], False, f"{type(e).__name__}: {e}"[:300]
    with session_scope() as s:
        row = SourceFetch(source=SOURCE, loc_key=f"{state}|{commodity}", url=url, params=public_params, ok=ok,
                          raw={"records": recs} if ok else None, error=err)
        s.add(row)
        s.flush()
        return {"ok": ok, "fetch_id": row.id, "url": url, "fetched_at": row.fetched_at, "records": recs, "error": err}


def history(state: str, commodity: str, days: int) -> list[dict]:
    """Stored successful fetches for this state and commodity over the last `days` days, oldest first."""
    since = utcnow() - timedelta(days=days)
    with session_scope() as s:
        rows = (s.query(SourceFetch).filter(SourceFetch.source == SOURCE, SourceFetch.ok.is_(True),
                                            SourceFetch.loc_key == f"{state}|{commodity}",
                                            SourceFetch.fetched_at >= since)
                .order_by(SourceFetch.fetched_at.asc()).all())
        return [{"fetch_id": r.id, "fetched_at": (r.fetched_at if r.fetched_at.tzinfo else r.fetched_at.replace(tzinfo=timezone.utc)),
                 "records": (r.raw or {}).get("records", [])} for r in rows]
