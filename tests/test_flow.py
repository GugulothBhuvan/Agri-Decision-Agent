"""End-to-end flows through the API with the saved real forecast (no LLM, no speech keys)."""
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.db import Decision, SourceFetch, session_scope


def client():
    from app.main import app
    return TestClient(app)


def turn(c, text):
    r = c.post("/api/turn", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


def setup_farm(c, stage_text="আমার ধান পেকে গেছে, ৩ বিঘা জমি"):
    r1 = turn(c, stage_text)
    assert "গ্রাম" in r1["reply_text"]                      # asks for the village
    r2 = turn(c, "শালবনি, পশ্চিম মেদিনীপুর")
    return r1, r2


def test_profile_one_field_at_a_time_then_readback_then_decision(saved_forecast, geocoder):
    c = client()
    with c:
        setup_farm(c)
        r3 = turn(c, "হ্যাঁ, বিমা আছে")                     # insured question answered
        assert "আপনার জমি" in r3["reply_text"] and "ঠিক আছে?" in r3["reply_text"]   # read back once
        r4 = turn(c, "হ্যাঁ ঠিক আছে")
        assert r4["decision_ids"], r4
        assert "সূত্র" in r4["reply_text"]                     # source named in the reply
        farm = c.get("/api/farm").json()
        assert farm["profile"]["profile_confirmed"] and farm["missing"] == []
        d = c.get(f"/api/decisions/{r4['decision_ids'][0]}").json()
        assert d["sources"] and d["sources"][0]["fetched_at"] and d["sources"][0]["url"].startswith("https://api.open-meteo.com")


def test_harvest_question_and_why_page(saved_forecast, geocoder):
    c = client()
    with c:
        setup_farm(c)
        turn(c, "হ্যাঁ")
        turn(c, "হ্যাঁ")
        r = turn(c, "কবে ধান কাটব?")
        assert r["decision_ids"]
        page = c.get(f"/why/{r['decision_ids'][0]}")
        assert page.status_code == 200 and "Open-Meteo" in page.text
        home = c.get("/")
        assert home.status_code == 200 and "card decision" in home.text


def test_correction_after_readback(saved_forecast, geocoder):
    c = client()
    with c:
        setup_farm(c)
        turn(c, "না, বিমা নেই")
        r = turn(c, "না")
        assert "কোনটা ভুল" in r["reply_text"]
        r2 = turn(c, "৫ বিঘা")
        assert c.get("/api/farm").json()["profile"]["area_bigha"] == 5.0
        assert "আপনার জমি" in r2["reply_text"]


def test_geocode_miss_asks_for_block(saved_forecast, geocoder):
    c = client()
    with c:
        turn(c, "ধান পেকে গেছে")
        r = turn(c, "nowhereville")
        assert "ব্লক" in r["reply_text"]


def test_out_of_scope_and_fertilizer(saved_forecast, geocoder):
    c = client()
    with c:
        setup_farm(c)
        turn(c, "হ্যাঁ")
        turn(c, "হ্যাঁ")
        assert "সয়েল হেলথ কার্ড" in turn(c, "কত ইউরিয়া দেব?")["reply_text"]


def test_monitor_run_delivers_once_then_stays_silent(saved_forecast, geocoder):
    c = client()
    with c:
        setup_farm(c)
        turn(c, "হ্যাঁ")
        turn(c, "হ্যাঁ")
        h = {"Content-Type": "application/json"}
        # The farmer has heard the decision in the turn, so the monitor run finds no change.
        r1 = c.post("/api/ops/run", json={"passcode": "test-pass"}, headers=h).json()
        assert r1["farms"] == 1 and r1["delivered"] == []
        # Force a change: drop the latest decisions so the run sees a new outcome.
        with session_scope() as s:
            s.query(Decision).delete()
        r2 = c.post("/api/ops/run", json={"passcode": "test-pass"}, headers=h).json()
        assert len(r2["delivered"]) >= 1
        alerts = c.get("/api/alerts").json()
        assert alerts and alerts[0]["text_bn"] and alerts[0]["text_en"]
        r3 = c.post("/api/ops/run", json={"passcode": "test-pass"}, headers=h).json()
        assert r3["delivered"] == []                           # unchanged: silent


def test_pulled_plug_uses_labelled_cache_then_unavailable(saved_forecast, geocoder):
    c = client()
    with c:
        setup_farm(c)
        turn(c, "হ্যাঁ")
        turn(c, "হ্যাঁ")
        h = {"Content-Type": "application/json"}
        assert c.post("/api/ops/source", json={"passcode": "test-pass", "source": "openmeteo", "enabled": False},
                      headers=h).status_code == 200
        r = turn(c, "কবে ধান কাটব?")
        assert "আগের তথ্য" in r["reply_text"]                  # cached path, with its time
        d = c.get(f"/api/decisions/{r['decision_ids'][0]}").json()
        assert d["cached"] is True
        run = c.post("/api/ops/run", json={"passcode": "test-pass"}, headers=h).json()
        assert run["cached_locations"] == 1 and not run["errors"]
        # Age every real fetch past 6 hours: no cache, so the agent says the forecast is unavailable.
        with session_scope() as s:
            for f in s.query(SourceFetch).all():
                f.fetched_at = datetime.now(timezone.utc) - timedelta(hours=7)
        r2 = turn(c, "কবে ধান কাটব?")
        assert "পূর্বাভাস পাওয়া যাচ্ছে না" in r2["reply_text"]
        run2 = c.post("/api/ops/run", json={"passcode": "test-pass"}, headers=h).json()
        assert run2["unavailable_farms"] == 1 and not run2["errors"]
        assert c.get("/ops").status_code == 200


def test_ops_requires_passcode():
    c = client()
    with c:
        assert c.post("/api/ops/run", json={}).status_code == 401
        assert c.get("/api/ops/overview").status_code == 401
        assert c.get("/api/ops/overview?passcode=test-pass").status_code == 200


def test_delete_farm_removes_rows(saved_forecast, geocoder):
    c = client()
    with c:
        setup_farm(c)
        assert c.delete("/api/farm").status_code == 204
        assert c.get("/api/farm").status_code == 404


def test_english_ui(saved_forecast, geocoder):
    c = client()
    with c:
        c.post("/api/lang", json={"lang": "en"})
        r = turn(c, "My paddy is ripe, 2 bigha")
        assert "village" in r["reply_text"].lower()
        assert "Tap to talk" in c.get("/").text


def test_template_replies_pass_the_guardrail(saved_forecast, geocoder):
    c = client()
    with c:
        setup_farm(c, "ধান কেটে ফেলেছি গতকাল, ২ বিঘা")
        turn(c, "হ্যাঁ")
        r = turn(c, "হ্যাঁ")
        for q in ["কবে ধান কাটব?", "ভারী বৃষ্টি আসবে?", "কেন এই পরামর্শ?", "দাবির কী অবস্থা?"]:
            r = turn(c, q)
            assert r["guardrail"] and r["guardrail"]["first"], (q, r["reply_text"], r["guardrail"])


def test_heavy_rain_opens_red_alert_and_claim_kit(saved_forecast, geocoder, monkeypatch):
    """The saved week peaks at 45.5 mm in 24 h, so the test lowers the bar to exercise the path."""
    import asyncio
    import copy

    from app.agent import monitor_graph, tools
    from app.config import rules
    cfg = copy.deepcopy(rules())
    cfg["calamity"]["claim_trigger_past24h_mm"] = 30.0
    monkeypatch.setattr(tools, "rules", lambda: cfg)
    c = client()
    with c:
        setup_farm(c, "ধান কেটে ফেলেছি, ২ বিঘা")
        c.patch("/api/farm", json={"cut_date": "2026-10-28", "insured": True})
        now = datetime(2026, 10, 30, 6)
        run = asyncio.run(monitor_graph.run(now=now))
        claims = [d for d in run["delivered"] if d["module"] == "claim"]
        assert claims and claims[0]["tier"] == "red", run
        deadline = datetime.fromisoformat(claims[0]["claim"]["deadline_at"])
        alerts = c.get("/api/alerts").json()
        assert any(a["tier"] == "red" for a in alerts)
        cid = claims[0]["claim"]["claim_id"]
        kit = c.get(f"/api/claims/{cid}").json()
        assert datetime.fromisoformat(kit["deadline_at"]) == deadline
        ev = datetime.fromisoformat(kit["event_at"])
        assert (deadline - ev).total_seconds() == 72 * 3600
        assert "Bangla Shasya Bima" in kit["report_text_2"] or "বাংলা শস্য বিমা" in kit["report_text"]
        page = c.get(f"/claim/{cid}")
        assert page.status_code == 200 and "data-deadline" in page.text
        # Checklist and "sent" are marked by the family.
        kit = c.patch(f"/api/claims/{cid}", json={"checklist": {"photos": True}}).json()
        assert [i["done"] for i in kit["checklist"]][0] is True
        assert c.patch(f"/api/claims/{cid}", json={"sent": True}).json()["sent"] is True
        # Next run: same event, no second kit.
        run2 = asyncio.run(monitor_graph.run(now=now))
        assert not [d for d in run2["delivered"] if d["module"] == "claim"]


def test_manual_location_pin_completes_profile(saved_forecast, monkeypatch):
    from app.agent import tools

    async def miss(v, d=None):
        return None
    monkeypatch.setattr(tools, "nominatim_lookup", miss)
    c = client()
    with c:
        r = c.patch("/api/farm", json={"village": "Kovur", "crop": "paddy_aman", "stage": "cut", "area_bigha": 2,
                                       "cut_date": "2026-10-30", "insured": True, "lat": 14.49, "lon": 79.98}).json()
        assert r["missing"] == [] and r["profile"]["profile_confirmed"] and r["profile"]["lat"] == 14.49


def test_potato_sowing_and_blight_flows(saved_forecast, geocoder):
    c = client()
    with c:
        turn(c, "আলু বুনব, এখনও বোনা হয়নি, ২ বিঘা")
        turn(c, "শালবনি, পশ্চিম মেদিনীপুর")
        r = turn(c, "হ্যাঁ")
        assert r["decision_ids"], r            # read-back confirmed -> sowing advice with source
        assert "আলু" in r["reply_text"] and "সূত্র" in r["reply_text"]
        d = c.get(f"/api/decisions/{r['decision_ids'][0]}").json()
        assert d["module"] == "sowing" and d["rule_sources"][0]["url"].startswith("https://pmc.ncbi.nlm.nih.gov")
        assert c.get(f"/why/{d['decision_id']}").status_code == 200
        c.patch("/api/farm", json={"stage": "growing"})
        r2 = turn(c, "ঝলসা রোগের জন্য স্প্রে করব?")
        assert r2["decision_ids"] and r2["guardrail"]["first"]
        assert c.get("/").status_code == 200
