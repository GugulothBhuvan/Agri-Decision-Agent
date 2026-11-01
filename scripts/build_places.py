"""Build data/places.json from OpenStreetMap place nodes (villages, towns, hamlets) for target districts.

    python -m scripts.build_places                      # Paschim Medinipur (default)
    python -m scripts.build_places --district "Bankura" --district "Purba Medinipur"

Uses the public Overpass API (please keep requests light; it retries with backoff when busy).
Data (c) OpenStreetMap contributors, ODbL. Each place keeps its OSM id so it can be traced back.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "places.json"
ENDPOINTS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]

QUERY = """[out:json][timeout:120];
area["boundary"="administrative"]["name"="{district}"]->.d;
(node["place"~"^(village|hamlet|town|suburb|neighbourhood)$"](area.d););
out body;"""


def fetch(district: str) -> list[dict]:
    q = QUERY.format(district=district)
    for attempt in range(4):
        for url in ENDPOINTS:
            try:
                r = httpx.post(url, data={"data": q}, timeout=150,
                               headers={"User-Agent": "agri-decision-agent/0.1 (places builder)"})
                if r.status_code == 200:
                    return r.json().get("elements", [])
                print(f"  {url}: HTTP {r.status_code}", file=sys.stderr)
            except httpx.HTTPError as e:
                print(f"  {url}: {e}", file=sys.stderr)
        time.sleep(5 * 2 ** attempt)
    raise SystemExit(f"Overpass unavailable for {district}; try again later")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--district", action="append", default=None)
    args = ap.parse_args()
    districts = args.district or ["Paschim Medinipur"]
    places, seen = [], set()
    for d in districts:
        print(f"Fetching {d}…")
        for el in fetch(d):
            tags = el.get("tags", {})
            name = tags.get("name:en") or tags.get("name")
            if not name or el["id"] in seen:
                continue
            seen.add(el["id"])
            places.append({"osm_id": el["id"], "name": name, "name_bn": tags.get("name:bn"),
                           "place": tags.get("place"), "lat": el["lat"], "lon": el["lon"], "district": d})
        print(f"  {len(places)} places so far")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"source": "OpenStreetMap contributors (ODbL), via Overpass API",
                               "districts": districts, "built_at": time.strftime("%Y-%m-%d"),
                               "places": places}, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"Wrote {len(places)} places to {OUT}")


if __name__ == "__main__":
    main()
