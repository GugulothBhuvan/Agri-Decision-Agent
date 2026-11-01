"""Test setup. Saved real Open-Meteo responses live only in tests/fixtures; the app never reads them."""
import json
import os
import tempfile
from pathlib import Path

_tmp = tempfile.mkdtemp()
os.environ["FS_SKIP_DOTENV"] = "1"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["ENABLE_SCHEDULER"] = "0"
os.environ["OPS_PASSCODE"] = "test-pass"
os.environ["LLM_PROVIDER"] = "none"
os.environ.pop("SARVAM_API_KEY", None)

import pytest  # noqa: E402

FIX = Path(__file__).parent / "fixtures"


def load_fixture(name="openmeteo_medinipur_2026-10-31.json") -> dict:
    with open(FIX / name, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(autouse=True)
def _fresh_db():
    from app.db import Base, engine, init_db
    from app.sources import switch
    Base.metadata.drop_all(engine)
    init_db()
    for k in switch.SOURCES:
        switch.set_enabled(k, True)
    yield


@pytest.fixture
def saved_forecast(monkeypatch):
    """Replace the network call with the saved real response, stored exactly as a live fetch would be."""
    from app.sources import openmeteo
    from urllib.parse import urlencode
    raw = load_fixture()
    calls = {"n": 0}

    async def fake_fetch_live(lat, lon, retries=None):
        calls["n"] += 1
        key = openmeteo.loc_key(lat, lon)
        params = openmeteo.request_params(lat, lon)
        url = f"{openmeteo.BASE_URL}?{urlencode(params)}"
        if not openmeteo.switch.is_enabled(openmeteo.SOURCE):
            row = openmeteo._store(key, params, url, False, error="disabled by operator (source-outage switch)")
            return openmeteo.Forecast("unavailable", key, fetch_id=row.id, url=url, error=row.error)
        row = openmeteo._store(key, params, url, True, raw=raw)
        return openmeteo.Forecast("live", key, fetch_id=row.id, raw=raw, fetched_at=row.fetched_at, url=url)

    monkeypatch.setattr(openmeteo, "fetch_live", fake_fetch_live)
    return calls


@pytest.fixture
def geocoder(monkeypatch):
    from app.agent import tools
    from app.sources.geocode import Place

    async def fake(village, district=None):
        if village and village.lower().startswith("nowhere"):
            return None
        return Place(lat=22.42, lon=87.32, display_name=f"{village}, Paschim Medinipur, West Bengal, India")

    monkeypatch.setattr(tools, "nominatim_lookup", fake)
