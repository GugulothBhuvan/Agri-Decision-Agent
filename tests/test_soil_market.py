"""Soil ratings and mandi comparison. Market records here are test data shaped like the API's documented fields."""
from app.config import rules
from app.rules.market import market_advice
from app.rules.soil import rate_soil
from app.sources.agmarknet import normalise


def test_soil_bands_and_no_dose():
    o = rate_soil({"ph": 5.4, "organic_carbon_pct": 0.42, "nitrogen_kg_ha": 300, "phosphorus_kg_ha": 25,
                   "potassium_kg_ha": 90}, rules())
    r = {x["key"]: x["rating"] for x in o.data["rows"]}
    assert r == {"ph": "acidic", "organic_carbon_pct": "low", "nitrogen_kg_ha": "medium",
                 "phosphorus_kg_ha": "high", "potassium_kg_ha": "low"}
    assert o.actionable and "don't calculate" in o.reason_en
    assert "kg/ha of urea" not in o.reason_en


def test_printed_rating_and_card_dose_win():
    o = rate_soil({"nitrogen_kg_ha": 260, "printed_ratings": {"nitrogen_kg_ha": "low"},
                   "card_dose": "Urea 50 kg/acre in two splits"}, rules())
    row = o.data["rows"][0]
    assert row["rating"] == "low" and row["our_rating"] == "medium" and row["from_card"]
    assert "Urea 50 kg/acre in two splits" in o.reason_en


def test_no_card():
    assert rate_soil({}, rules()).data["action"] == "no_card"


def rec(market, district, modal, date="01/11/2026"):
    return {"state": "West Bengal", "district": district, "market": market, "commodity": "Paddy(Dhan)(Common)",
            "variety": "Common", "arrival_date": date, "min_price": str(modal - 100), "max_price": str(modal + 100),
            "modal_price": str(modal)}


def test_normalise_maps_documented_fields():
    r = normalise([rec("Garbeta", "Medinipur(W)", 2200), {"modal_price": "NR"}])
    assert len(r) == 1 and r[0]["modal"] == 2200 and r[0]["market"] == "Garbeta" and r[0]["min"] == 2100


def test_market_district_match_trend_and_rain_risk():
    today = normalise([rec("Garbeta", "Medinipur(W)", 2300), rec("Salboni", "Medinipur(W)", 2200),
                       rec("Burdwan", "Purba Bardhaman", 2500)])
    hist = [normalise([rec("Garbeta", "Medinipur(W)", 2000)]) for _ in range(4)]
    o = market_advice(today, hist, district="Paschim Medinipur", crop="paddy_aman", stage="cut",
                      rain_risk=True, cfg=rules())
    d = o.data
    assert d["scope"] == "district" and d["today_median"] == 2250 and d["recent_median"] == 2000
    assert d["trend"] == "up" and d["action"] == "sell_or_store_dry"
    assert "Burdwan" not in [m["market"] for m in d["markets"]]


def test_market_without_history_or_data():
    today = normalise([rec("Garbeta", "Medinipur(W)", 2300)])
    o = market_advice(today, [], district="Paschim Medinipur", crop="paddy_aman", stage="ready",
                      rain_risk=False, cfg=rules())
    assert o.data["trend"] == "unknown" and "isn't enough recent price history" in o.reason_en
    none = market_advice([], [], district="Paschim Medinipur", crop="paddy_aman", stage="ready",
                         rain_risk=False, cfg=rules())
    assert none.data["action"] == "no_data" and not none.actionable
