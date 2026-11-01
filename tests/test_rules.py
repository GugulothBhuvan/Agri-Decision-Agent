"""Rule unit tests on a saved real Open-Meteo response (Paschim Medinipur, fetched 2026-10-31)."""
import copy
from datetime import date, datetime

from app.config import rules
from app.rules.calamity import calamity_check
from app.rules.claim import claim_trigger
from app.rules.harvest import harvest_window
from app.rules.series import Series
from app.rules.tiers import assign_tier

from .conftest import load_fixture

S = Series(load_fixture())


def test_harvest_evening_finds_first_two_day_dry_run():
    o = harvest_window(S, rules(), datetime(2026, 10, 31, 18))
    # Nov 1 (52%), Nov 2 (65%), Nov 3 (5.6 mm) fail; Nov 4 and 5 are dry.
    assert o.data["safe_cut_days"] == ["2026-11-04"]
    assert o.data["first_rain_day"] == "2026-11-01"
    assert [d["dry"] for d in o.data["days"]] == [False, False, False, True, True]


def test_harvest_morning_starts_today_and_has_no_safe_day():
    o = harvest_window(S, rules(), datetime(2026, 10, 31, 9))
    assert o.data["safe_cut_days"] == []
    assert o.data["first_rain_day"] == "2026-10-31"
    assert o.action_at is None


def test_calamity_cover_heap_only_when_cut():
    now = datetime(2026, 10, 29, 6)
    assert calamity_check(S, rules(), "cut", now).data["action"] == "cover_heap"
    assert calamity_check(S, rules(), "ready", now).data["action"] == "none"


def test_calamity_red_at_heavy_bar():
    cfg = copy.deepcopy(rules())
    cfg["calamity"]["protect_red_next24h_mm"] = 30.0
    o = calamity_check(S, cfg, "ready", datetime(2026, 10, 29, 6))
    assert o.data["action"] == "protect_now"
    assert assign_tier(o, changed=True, now=datetime(2026, 10, 29, 6)) == "red"


def test_claim_not_triggered_below_heavy_bar():
    o = claim_trigger(S, rules(), stage="cut", insured=True, cut_date=date(2026, 10, 28),
                      has_open_claim=False, now=datetime(2026, 10, 30, 6))
    assert o.data["action"] == "none"
    assert any("under the 64.5 mm bar" in b for b in o.data["blockers"])


def test_claim_trigger_event_and_deadline():
    cfg = copy.deepcopy(rules())
    cfg["calamity"]["claim_trigger_past24h_mm"] = 30.0
    o = claim_trigger(S, cfg, stage="cut", insured=True, cut_date=date(2026, 10, 28),
                      has_open_claim=False, now=datetime(2026, 10, 30, 6))
    assert o.data["action"] == "claim_open"
    ev = datetime.fromisoformat(o.data["event_at"])
    dl = datetime.fromisoformat(o.data["deadline_at"])
    assert (dl - ev).total_seconds() == 72 * 3600
    assert S.sum_between(ev - __import__("datetime").timedelta(hours=24), ev) >= 30.0


def test_claim_blocked_when_uninsured_or_outside_window():
    cfg = copy.deepcopy(rules())
    cfg["calamity"]["claim_trigger_past24h_mm"] = 30.0
    now = datetime(2026, 10, 30, 6)
    assert claim_trigger(S, cfg, stage="cut", insured=False, cut_date=date(2026, 10, 28),
                         has_open_claim=False, now=now).data["action"] == "none"
    assert claim_trigger(S, cfg, stage="cut", insured=True, cut_date=date(2026, 10, 1),
                         has_open_claim=False, now=now).data["action"] == "none"


def test_tiers():
    now = datetime(2026, 10, 31, 18)
    h = harvest_window(S, rules(), now)
    assert assign_tier(h, changed=False, now=now) == "green"
    assert assign_tier(h, changed=True, now=now) == "yellow"   # Nov 4 08:00 is more than 72 h away
    h2 = harvest_window(S, rules(), datetime(2026, 11, 2, 9))
    assert h2.data["safe_cut_days"][0] == "2026-11-04"
    assert assign_tier(h2, changed=True, now=datetime(2026, 11, 2, 9)) == "amber"


def test_hash_ignores_rain_amount_changes():
    a = calamity_check(S, rules(), "cut", datetime(2026, 10, 29, 6))
    b = calamity_check(S, rules(), "cut", datetime(2026, 10, 29, 7))
    assert a.data["next24_mm"] != b.data["next24_mm"]
    assert a.decision_hash() == b.decision_hash()


def test_blight_skips_in_warm_late_october():
    from app.rules.blight import blight_check
    o = blight_check(S, rules(), datetime(2026, 10, 31, 18))
    assert o.data["action"] == "skip" and not o.actionable
    # Windows start on 29 Oct, the first day with 5 full days of hourly data.
    assert o.data["days"][0]["date"] == "2026-10-29"


def test_blight_raises_risk_when_cool_and_humid():
    from app.rules.blight import blight_check
    cfg = copy.deepcopy(rules())
    r5 = cfg["blight_jhulsacast_wb"]["rule_5day"]
    r5.update(temp_hours=60, rh_hours=30, consecutive_days=2)   # loosen to exercise the risk path
    o = blight_check(S, cfg, datetime(2026, 10, 29, 9))
    assert o.data["action"] in ("spray_now", "spray_by"), o.data
    assert assign_tier(o, changed=True, now=datetime(2026, 10, 29, 9)) in ("amber", "yellow")


def test_sowing_waits_for_cooler_nights():
    from app.rules.sowing import sowing_window
    o = sowing_window(S, rules(), datetime(2026, 10, 31, 18))
    assert o.data["action"] == "wait"
    assert [d["ok"] for d in o.data["days"]][-2:] == [True, True]   # 6 and 7 Nov: minimum under 21 C
    o2 = sowing_window(S, rules(), datetime(2026, 11, 5, 9))
    assert o2.data["days"][0]["date"] == "2026-11-05"
