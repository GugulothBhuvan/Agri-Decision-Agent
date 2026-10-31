"""The guardrail must catch every planted wrong number."""
from app.agent.guardrail import check_reply, source_ok

FACTS = {"safe_cut_days": ["2026-11-04"], "first_rain_day": "2026-11-01", "next24_mm": 4.4, "window_hours": 24,
         "report_within_hours": 72, "horizon_days": 5, "forecast_fetched_at": "2026-10-31T17:40:00+00:00"}

GOOD = [
    "ধান কাটার নিরাপদ দিন বুধবার, ৪ নভেম্বর। ১ নভেম্বর বৃষ্টির সম্ভাবনা আছে। সূত্র: ওপেন-মিটিও।",
    "Safe day to cut: Wed 4 Nov. Rain likely on 1 Nov. About 4.4 mm in the next 24 hours.",
    "Report within 72 hours. No safe day in the next 5 days.",
]
PLANTED = [
    "Safe day to cut: Thu 5 Nov.",
    "About 40 mm of rain in the next 24 hours.",
    "ধান কাটার নিরাপদ দিন ৬ নভেম্বর।",
    "Report within 48 hours.",
    "There is a 70% chance of rain.",
    "আগামী ৩৬ ঘণ্টায় বৃষ্টি।",
    "No safe day in the next seven days.",
    "আগামী সাত দিনে কাটার দিন নেই।",
    "Your 12 bigha field is safe.",
    "Rain of 4.5 mm expected.",
]


def test_good_replies_pass():
    for r in GOOD:
        assert check_reply(r, FACTS).ok, (r, check_reply(r, FACTS).problem)


def test_all_planted_numbers_caught():
    caught = [r for r in PLANTED if not check_reply(r, FACTS).ok]
    assert len(caught) == 10, [r for r in PLANTED if check_reply(r, FACTS).ok]


def test_action_claims_blocked():
    assert not check_reply("Your claim has been filed.", FACTS).ok
    assert not check_reply("দাবি জমা দেওয়া হয়েছে।", FACTS).ok
    assert check_reply("Report message ready.", FACTS).ok


def test_source_check():
    assert source_ok([{"name": "Open-Meteo", "url": "https://x", "fetched_at": "2026-10-31T10:00:00"}])
    assert not source_ok([{"name": "Open-Meteo", "url": "https://x", "fetched_at": None}])
    assert not source_ok([])
