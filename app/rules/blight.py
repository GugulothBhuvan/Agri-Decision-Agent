"""Blight watch (P1, potato, stage growing): the West Bengal JHULSACAST rule on hourly humidity and temperature.

For each day d, look at the window ending on d. The 5-day rule holds on d if, over the 5 days ending d, hours with
RH >= 90% number at least 65, hours with 7.2-26.6 C at least 105, and rain over d-1 and d is at least 2.5 mm.
Risk is raised on d when the 5-day rule holds on 5 consecutive days ending d (or the 7-day rule on 7 consecutive
days). The model was validated on station observations; running it on forecasts is our extension.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from ..i18n import fmt_day, num
from .base import Outcome
from .series import Series


def _window_counts(series: Series, end: date, days: int, rh_min: float, t_lo: float, t_hi: float) -> tuple[int, int, int]:
    start = end - timedelta(days=days - 1)
    rh_h = t_h = n = 0
    for t, rh, temp in zip(series.times, series.rh, series.temp):
        if start <= t.date() <= end:
            n += 1
            if rh is not None and rh >= rh_min:
                rh_h += 1
            if temp is not None and t_lo <= temp <= t_hi:
                t_h += 1
    return rh_h, t_h, n


def blight_check(series: Series, cfg: dict, now: datetime | None = None) -> Outcome:
    bc = cfg["blight_jhulsacast_wb"]
    r5, r7 = bc["rule_5day"], bc["rule_7day"]
    now_l = Series.local_now(now)
    today = now_l.date()
    daily_rain = {d.date: d.rain_mm for d in series.days}
    all_days = sorted(daily_rain)

    rows, meets5, meets7 = [], {}, {}
    for d in all_days:
        rh5, t5, n5 = _window_counts(series, d, 5, r5["rh_pct"], r5["temp_c"][0], r5["temp_c"][1])
        rh7, t7, n7 = _window_counts(series, d, 7, r7["rh_pct"], r7["temp_c"][0], r7["temp_c"][1])
        rain2 = round(daily_rain.get(d, 0) + daily_rain.get(d - timedelta(days=1), 0), 1)
        full5, full7 = n5 >= 5 * 24, n7 >= 7 * 24
        meets5[d] = full5 and rh5 >= r5["rh_hours"] and t5 >= r5["temp_hours"] and rain2 >= r5["rain_2day_mm"]
        meets7[d] = full7 and rh7 >= r7["rh_hours"] and t7 >= r7["temp_hours"]
        if full5 and today - timedelta(days=7) <= d <= today + timedelta(days=7):
            rows.append({"date": d.isoformat(), "rh_hours": rh5, "temp_hours": t5, "rain_2day_mm": rain2,
                         "meets": meets5[d]})

    def run_ending(flags: dict, d: date, n: int) -> bool:
        return all(flags.get(d - timedelta(days=i), False) for i in range(n))

    risk_days = [d for d in all_days if today - timedelta(days=7) <= d <= today + timedelta(days=7)
                 and (run_ending(meets5, d, r5["consecutive_days"]) or run_ending(meets7, d, r7["consecutive_days"]))]
    recent = [d for d in risk_days if today - timedelta(days=2) <= d <= today]
    future = [d for d in risk_days if d > today]

    src = [{"name": bc["source"], "url": bc["url"], "what": "JHULSACAST late-blight rule for West Bengal",
            "what_bn": "পশ্চিমবঙ্গের জন্য ঝলসা রোগের (JHULSACAST) নিয়ম"}]
    team = ["running the station-validated rule on forecast data"]
    team_bn = ["স্টেশনের মাপে যাচাই করা নিয়ম পূর্বাভাসের তথ্যে চালানো"]
    base = {"days": rows, "rule": {"rh_pct": r5["rh_pct"], "rh_hours": r5["rh_hours"], "temp_c": r5["temp_c"],
                                   "temp_hours": r5["temp_hours"], "rain_2day_mm": r5["rain_2day_mm"],
                                   "consecutive_days": r5["consecutive_days"]}}
    if recent:
        data = {**base, "action": "spray_now", "first_risk_day": recent[0].isoformat()}
        en = (f"Late blight conditions have held on your field (risk day {fmt_day(recent[0], 'en')}). "
              f"Spray a protective fungicide now, as your block agriculture office advises.")
        bn = (f"আপনার জমিতে ঝলসা রোগের অনুকূল আবহাওয়া চলছে (ঝুঁকির দিন {fmt_day(recent[0], 'bn')})। "
              f"ব্লক কৃষি অফিসের পরামর্শমতো এখনই প্রতিরোধক ছত্রাকনাশক স্প্রে করুন।")
        action_at = now_l
    elif future:
        spray_by = future[0] - timedelta(days=1)
        data = {**base, "action": "spray_by", "first_risk_day": future[0].isoformat(), "spray_by": spray_by.isoformat()}
        en = (f"Late blight risk is forecast from {fmt_day(future[0], 'en')}. Spray a protective fungicide by "
              f"{fmt_day(spray_by, 'en')}.")
        bn = (f"{fmt_day(future[0], 'bn')} থেকে ঝলসা রোগের ঝুঁকির পূর্বাভাস। {fmt_day(spray_by, 'bn')}-এর মধ্যে "
              f"প্রতিরোধক ছত্রাকনাশক স্প্রে করুন।")
        action_at = datetime.combine(spray_by, datetime.min.time()).replace(hour=8)
    else:
        data = {**base, "action": "skip", "first_risk_day": None}
        en = "No late blight risk in the past or next 7 days by the JHULSACAST rule. No spray needed now."
        bn = "JHULSACAST নিয়মে গত ও আগামী ৭ দিনে ঝলসা রোগের ঝুঁকি নেই। এখন স্প্রে দরকার নেই।"
        action_at = None
    rule_en = (f" Rule: {r5['consecutive_days']} days in a row with at least {r5['rh_hours']} hours of humidity "
               f"{r5['rh_pct']}% or more, {r5['temp_hours']} hours between {r5['temp_c'][0]} and {r5['temp_c'][1]} °C "
               f"over 5 days, and {r5['rain_2day_mm']} mm of rain over 2 days.")
    rule_bn = (f" নিয়ম: পরপর {num(r5['consecutive_days'], 'bn')} দিন, ৫ দিনে অন্তত {num(r5['rh_hours'], 'bn')} ঘণ্টা আর্দ্রতা "
               f"{num(r5['rh_pct'], 'bn')}% বা বেশি, {num(r5['temp_hours'], 'bn')} ঘণ্টা তাপমাত্রা "
               f"{num(r5['temp_c'][0], 'bn')}–{num(r5['temp_c'][1], 'bn')} °সে, আর ২ দিনে {num(r5['rain_2day_mm'], 'bn')} মিমি বৃষ্টি।")
    return Outcome("blight", data, en + rule_en, bn + rule_bn, action_at, ["action", "first_risk_day"],
                   rule_sources=src, team_choices=team, team_choices_bn=team_bn, actionable=data["action"] != "skip")
