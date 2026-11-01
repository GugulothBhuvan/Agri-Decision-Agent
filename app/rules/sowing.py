"""Sowing window (P1, potato, stage to_sow): forecast days inside the potato thermal limits.

A day is suitable when the maximum is below 35 C and the minimum is between 2 and 21 C (ICAR-CPRI thermal limits,
Bhardwaj et al. 2022) and rain is under 2.5 mm (team). A planting day starts a run of 3 suitable days (team).
"""
from __future__ import annotations

from datetime import datetime, timedelta

from ..i18n import fmt_day, num
from .base import Outcome
from .series import Series


def sowing_window(series: Series, cfg: dict, now: datetime | None = None) -> Outcome:
    sc = cfg["sowing_potato"]
    now_l = Series.local_now(now)
    first = now_l.date() if now_l.hour < 12 else now_l.date() + timedelta(days=1)
    days = series.days_from(first, sc["horizon_days"])
    lo, hi = sc["min_temp_c"]
    ok = [d.tmax is not None and d.tmin is not None and d.tmax < sc["max_temp_below_c"] and lo <= d.tmin <= hi
          and d.rain_mm < sc["max_rain_mm"] for d in days]
    run = sc["consecutive_days"]
    plant = [days[i].date.isoformat() for i in range(len(days) - run + 1) if all(ok[i:i + run])]
    table = [{"date": d.date.isoformat(), "tmax_c": d.tmax, "tmin_c": d.tmin, "rain_mm": round(d.rain_mm, 1), "ok": s}
             for d, s in zip(days, ok)]
    data = {"plant_days": plant, "days": table, "horizon_days": sc["horizon_days"],
            "rule": {"max_below_c": sc["max_temp_below_c"], "min_c": [lo, hi], "max_rain_mm": sc["max_rain_mm"],
                     "consecutive_days": run}, "action": "plant" if plant else "wait"}
    rule_en = (f" A day suits planting if the maximum is below {sc['max_temp_below_c']} °C, the minimum is between "
               f"{lo} and {hi} °C and rain is under {sc['max_rain_mm']} mm; we look for {run} such days in a row.")
    rule_bn = (f" সর্বোচ্চ তাপমাত্রা {num(sc['max_temp_below_c'], 'bn')} °সে-র কম, সর্বনিম্ন {num(lo, 'bn')}–{num(hi, 'bn')} °সে "
               f"আর বৃষ্টি {num(sc['max_rain_mm'], 'bn')} মিমি-র কম হলে দিনটি বোনার উপযুক্ত; পরপর {num(run, 'bn')}টি এমন দিন দেখা হয়।")
    if plant:
        en = f"Good days to plant potato: {', '.join(fmt_day(d, 'en') for d in plant)}." + rule_en
        bn = f"আলু বোনার ভালো দিন: {', '.join(fmt_day(d, 'bn') for d in plant)}।" + rule_bn
        action_at = datetime.fromisoformat(plant[0]).replace(hour=8)
    else:
        en = f"No good planting day in the next {sc['horizon_days']} days; nights are still too warm or rain is due." + rule_en
        bn = f"আগামী {num(sc['horizon_days'], 'bn')} দিনে আলু বোনার ভালো দিন নেই; রাত এখনও গরম বা বৃষ্টি আসছে।" + rule_bn
        action_at = None
    return Outcome("sowing", data, en, bn, action_at, ["plant_days"],
                   rule_sources=[{"name": sc["source"], "url": sc["url"], "what": "potato thermal limits",
                                  "what_bn": "আলুর জন্য তাপমাত্রার সীমা"}],
                   team_choices=[f"rain under {sc['max_rain_mm']} mm", f"{run} suitable days in a row"],
                   team_choices_bn=[f"বৃষ্টি {num(sc['max_rain_mm'], 'bn')} মিমি-র কম", f"পরপর {num(run, 'bn')}টি উপযুক্ত দিন"],
                   actionable=bool(plant))
