"""Open-Meteo forecast fetch. Every real response (or failure) is stored in source_fetches."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx

from ..db import SourceFetch, session_scope, utcnow
from . import switch

SOURCE = "openmeteo"
SOURCE_NAME = "Open-Meteo forecast API"
BASE_URL = "https://api.open-meteo.com/v1/forecast"
RETRY_DELAYS = [1.0, 3.0]  # two retries with backoff (TRD failure handling)
TIMEOUT_S = 10.0

HOURLY = "precipitation,precipitation_probability,relative_humidity_2m,temperature_2m"
DAILY = "precipitation_sum,precipitation_probability_max,temperature_2m_max,temperature_2m_min"


def loc_key(lat: float, lon: float) -> str:
    """Lat/lon rounded to two decimals: farms within about a kilometre share one fetch."""
    return f"{round(lat, 2):.2f},{round(lon, 2):.2f}"


def request_params(lat: float, lon: float) -> dict[str, Any]:
    return {
        "latitude": round(lat, 2), "longitude": round(lon, 2), "timezone": "Asia/Kolkata",
        "hourly": HOURLY, "daily": DAILY, "past_days": 7, "forecast_days": 7,
    }


@dataclass
class Forecast:
    status: str                 # live | cached | unavailable
    loc_key: str
    fetch_id: int | None = None
    raw: dict | None = None
    fetched_at: datetime | None = None
    url: str | None = None
    error: str | None = None

    @property
    def usable(self) -> bool:
        return self.status in ("live", "cached") and self.raw is not None

    @property
    def cached(self) -> bool:
        return self.status == "cached"


def _store(key: str, params: dict, url: str, ok: bool, raw: Any = None, error: str | None = None) -> SourceFetch:
    with session_scope() as s:
        row = SourceFetch(source=SOURCE, loc_key=key, url=url, params=params, ok=ok, raw=raw, error=error)
        s.add(row)
        s.flush()
        s.refresh(row)
        return row


async def fetch_live(lat: float, lon: float, retries: list[float] | None = None) -> Forecast:
    """Fetch with retries. Returns status 'live' or 'unavailable' (never raises)."""
    key = loc_key(lat, lon)
    params = request_params(lat, lon)
    url = f"{BASE_URL}?{urlencode(params)}"
    if not switch.is_enabled(SOURCE):
        row = _store(key, params, url, False, error="disabled by operator (source-outage switch)")
        return Forecast("unavailable", key, fetch_id=row.id, url=url, error=row.error)
    delays = RETRY_DELAYS if retries is None else retries
    last_err = "unknown error"
    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        for attempt in range(len(delays) + 1):
            try:
                r = await client.get(BASE_URL, params=params)
                r.raise_for_status()
                data = r.json()
                if "hourly" not in data or "daily" not in data:
                    raise ValueError("response missing hourly/daily blocks")
                row = _store(key, params, url, True, raw=data)
                return Forecast("live", key, fetch_id=row.id, raw=data, fetched_at=row.fetched_at, url=url)
            except Exception as e:  # noqa: BLE001 - every failure is recorded
                last_err = f"{type(e).__name__}: {e}"[:500]
                if attempt < len(delays):
                    await asyncio.sleep(delays[attempt])
    row = _store(key, params, url, False, error=last_err)
    return Forecast("unavailable", key, fetch_id=row.id, url=url, error=last_err)


def last_real_fetch(key: str, max_age_hours: float) -> Forecast | None:
    """The last successful real fetch for this location if younger than max_age_hours."""
    cutoff = utcnow() - timedelta(hours=max_age_hours)
    with session_scope() as s:
        row = (s.query(SourceFetch)
               .filter(SourceFetch.source == SOURCE, SourceFetch.loc_key == key, SourceFetch.ok.is_(True))
               .order_by(SourceFetch.fetched_at.desc()).first())
        if row is None:
            return None
        fetched = row.fetched_at if row.fetched_at.tzinfo else row.fetched_at.replace(tzinfo=timezone.utc)
        if fetched < cutoff:
            return None
        return Forecast("cached", key, fetch_id=row.id, raw=row.raw, fetched_at=fetched, url=row.url)


def validate(fc: Forecast, max_age_hours: float, now: datetime | None = None) -> str | None:
    """Return a problem description, or None if the forecast is fit to use."""
    if not fc.usable:
        return fc.error or "no data"
    now = now or utcnow()
    fetched = fc.fetched_at
    if fetched and fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=timezone.utc)
    if fetched and now - fetched > timedelta(hours=max_age_hours):
        return f"fetch older than {max_age_hours:g} h"
    h = fc.raw.get("hourly", {})
    times = h.get("time") or []
    if len(times) < 48:
        return "fewer than 48 hourly values"
    for name in ("precipitation", "precipitation_probability", "relative_humidity_2m", "temperature_2m"):
        vals = h.get(name)
        if not vals or len(vals) != len(times):
            return f"hourly {name} missing or misaligned"
    for v in h["precipitation"]:
        if v is not None and not (0 <= v <= 500):
            return f"precipitation out of range: {v}"
    for v in h["precipitation_probability"]:
        if v is not None and not (0 <= v <= 100):
            return f"precipitation probability out of range: {v}"
    for v in h["temperature_2m"]:
        if v is not None and not (-10 <= v <= 55):
            return f"temperature out of range: {v}"
    return None


async def get_forecast(lat: float, lon: float, max_age_hours: float = 6) -> Forecast:
    """Conversation path: live fetch, else labelled cache under max_age_hours, else unavailable."""
    fc = await fetch_live(lat, lon)
    if fc.status == "live" and validate(fc, max_age_hours) is None:
        return fc
    cached = last_real_fetch(loc_key(lat, lon), max_age_hours)
    if cached and validate(cached, max_age_hours) is None:
        return cached
    return Forecast("unavailable", loc_key(lat, lon), fetch_id=fc.fetch_id, url=fc.url,
                    error=fc.error or "invalid forecast data")
