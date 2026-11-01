"""Village gazetteer: match a spoken village name to a known place with coordinates.

The list lives in data/places.json, built by scripts/build_places.py from OpenStreetMap place nodes
(name and name:bn) for the target districts. Matching uses the embedding engine's place mode, so a
Bengali-script name finds its Latin-script spelling. Nominatim stays as the fallback.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..config import ROOT, rules
from ..embeddings import VectorIndex, engine_name

log = logging.getLogger("fosol.places")
PLACES_FILE = ROOT / "data" / "places.json"


@dataclass
class PlaceMatch:
    status: str          # accept | confirm | miss
    score: float
    name: str | None = None
    name_bn: str | None = None
    lat: float | None = None
    lon: float | None = None
    district: str | None = None
    source: str = "OpenStreetMap place list"
    osm_id: str | None = None

    @property
    def display(self) -> str:
        bits = [self.name or "", self.district or ""]
        return ", ".join(b for b in bits if b)


def _load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data.get("places", data) if isinstance(data, dict) else data
    except (OSError, ValueError) as e:
        log.warning("could not read %s: %s", path, e)
        return []


@lru_cache
def _index(path: str = str(PLACES_FILE)) -> VectorIndex | None:
    places = _load(Path(path))
    if not places:
        return None
    rows = []
    for p in places:
        for nm in {p.get("name"), p.get("name_bn"), p.get("name_en")} - {None, ""}:
            rows.append({"text": nm, "place": p})
    return VectorIndex(rows, "text", kind="place")


def available(path: str = str(PLACES_FILE)) -> bool:
    return _index(path) is not None


def count(path: str = str(PLACES_FILE)) -> int:
    return len(_load(Path(path)))


def resolve(village: str, district: str | None = None, path: str = str(PLACES_FILE)) -> PlaceMatch:
    idx = _index(path)
    if idx is None or not village or not village.strip():
        return PlaceMatch("miss", 0.0)
    th = rules()["embeddings"][engine_name()]
    hits = idx.search(village, k=10)
    if district:
        # A candidate in the stated district gets a small lift; it never turns a weak match into a strong one.
        dk = district.lower()[:5]
        def lift(h):
            d = (h[1]["place"].get("district") or "").lower()
            return h[0] + (0.03 if d and (dk in d or d[:5] in district.lower()) else 0.0)
        hits = sorted(hits, key=lift, reverse=True)
    score, row = hits[0]
    p = row["place"]
    status = "accept" if score >= th["place_accept"] else "confirm" if score >= th["place_confirm"] else "miss"
    # Two different places tied at the top cannot be accepted silently.
    if status == "accept":
        rivals = [h for h in hits if h[1]["place"] is not p and abs(h[0] - score) < 0.02
                  and h[1]["place"].get("osm_id") != p.get("osm_id")]
        if rivals:
            status = "confirm"
    return PlaceMatch(status, round(score, 3), p.get("name"), p.get("name_bn"), p.get("lat"), p.get("lon"),
                      p.get("district"), osm_id=str(p.get("osm_id") or ""))
