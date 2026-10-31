"""Nominatim (OpenStreetMap) lookup: one request per second, named user agent."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

from ..config import settings
from ..db import SourceFetch, session_scope
from . import switch

SOURCE = "nominatim"
URL = "https://nominatim.openstreetmap.org/search"
_lock = asyncio.Lock()
_last_call = 0.0


@dataclass
class Place:
    lat: float
    lon: float
    display_name: str
    fetch_id: int | None = None


async def geocode_village(village: str, district: str | None = None) -> Place | None:
    """Return the best match in West Bengal, India, or None. Never raises."""
    global _last_call
    q = ", ".join(p for p in [village, district, "West Bengal", "India"] if p)
    params = {"q": q, "format": "jsonv2", "limit": 1, "countrycodes": "in"}
    if not switch.is_enabled(SOURCE):
        _store(params, False, error="disabled by operator")
        return None
    try:
        async with _lock:
            wait = 1.0 - (time.monotonic() - _last_call)
            if wait > 0:
                await asyncio.sleep(wait)
            async with httpx.AsyncClient(timeout=8.0,
                                         headers={"User-Agent": settings().nominatim_user_agent}) as c:
                r = await c.get(URL, params=params)
            _last_call = time.monotonic()
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # noqa: BLE001
        _store(params, False, error=f"{type(e).__name__}: {e}"[:500])
        return None
    row_id = _store(params, True, raw=data)
    if not data:
        return None
    top = data[0]
    return Place(lat=float(top["lat"]), lon=float(top["lon"]), display_name=top.get("display_name", q),
                 fetch_id=row_id)


def _store(params: dict, ok: bool, raw=None, error: str | None = None) -> int | None:
    try:
        with session_scope() as s:
            row = SourceFetch(source=SOURCE, loc_key=None, url=URL, params=params, ok=ok, raw=raw, error=error)
            s.add(row)
            s.flush()
            return row.id
    except Exception:
        return None
