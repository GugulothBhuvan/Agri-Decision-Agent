"""Harvest window (stage ready or cut): dry days in the next 5 days by the TRD rule."""
from __future__ import annotations

from datetime import datetime, timedelta

from ..i18n import fmt_day, num
from .base import Outcome
from .series import Series


def harvest_window(series: Series, cfg: dict, now: datetime | None = None) -> Outcome:
    hc = cfg["harvest"]
    now_l = Series.local_now(now)
    # After midday there is not enough daylight left to cut and start drying today.
    first = now_l.date() if now_l.hour < 12 else now_l.date() + timedelta(days=1)
    days = series.days_from(first, hc["horizon_days"])
    dry = [d.rain_mm < hc["dry_day_max_rain_mm"] and d.prob_max < hc["dry_day_max_prob_pct"] for d in days]
    run = hc["consecutive_dry_days"]
    safe = [days[i].date.isoformat() for i in range(len(days) - run + 1) if all(dry[i:i + run])]
    first_rain = next((days[i].date.isoformat() for i, ok in enumerate(dry) if not ok), None)
    table = [{"date": d.date.isoformat(), "rain_mm": round(d.rain_mm, 1), "prob_max_pct": int(d.prob_max),
              "dry": ok} for d, ok in zip(days, dry)]
    data = {"safe_cut_days": safe, "first_rain_day": first_rain, "days": table,
            "horizon_days": hc["horizon_days"], "dry_rule": {
                "max_rain_mm": hc["dry_day_max_rain_mm"], "max_prob_pct": hc["dry_day_max_prob_pct"],
                "consecutive_days": run}}

    rule_en = (f"A day counts as dry if forecast rain is under {hc['dry_day_max_rain_mm']} mm and the "
               f"chance of rain is under {hc['dry_day_max_prob_pct']}%; a safe cut day needs {run} dry days in a row "
               f"so the cut paddy can lie in the field.")
    rule_bn = (f"যেদিন {num(hc['dry_day_max_rain_mm'], 'bn')} মিমি-র কম বৃষ্টি আর বৃষ্টির সম্ভাবনা "
               f"{num(hc['dry_day_max_prob_pct'], 'bn')}%-এর কম, সেদিন শুকনো দিন; কাটার জন্য পরপর "
               f"{num(run, 'bn')}টি শুকনো দিন দরকার, যাতে কাটা ধান মাঠে শুকোতে পারে।")
    if safe:
        days_en = ", ".join(fmt_day(d, "en") for d in safe)
        days_bn = ", ".join(fmt_day(d, "bn") for d in safe)
        reason_en = f"Safe days to cut: {days_en}. " + rule_en
        reason_bn = f"ধান কাটার নিরাপদ দিন: {days_bn}। " + rule_bn
        action_at = datetime.fromisoformat(safe[0]).replace(hour=8)
    else:
        reason_en = f"No safe cut day in the next {hc['horizon_days']} days. " + rule_en
        reason_bn = f"আগামী {num(hc['horizon_days'], 'bn')} দিনে ধান কাটার নিরাপদ দিন নেই। " + rule_bn
        action_at = None
    if first_rain:
        reason_en += f" First rainy day: {fmt_day(first_rain, 'en')}."
        reason_bn += f" প্রথম বৃষ্টির দিন: {fmt_day(first_rain, 'bn')}।"
    cpc = cfg["cpc_quality"]
    return Outcome(
        module="harvest", data=data, reason_en=reason_en, reason_bn=reason_bn, action_at=action_at,
        hash_keys=["safe_cut_days", "first_rain_day"],
        rule_sources=[
            {"name": cfg["rain_categories_mm_24h"]["source"], "url": cfg["rain_categories_mm_24h"]["url"],
             "what": "light-rain threshold (2.5 mm)", "what_bn": "হালকা বৃষ্টির সীমা (২.৫ মিমি)"},
            {"name": cpc["source"], "url": cpc["url"],
             "what": f"procurement limits: {cpc['max_moisture_pct']}% moisture, "
                     f"{cpc['max_damaged_sprouted_pct']}% sprouted grain",
             "what_bn": f"ক্রয়কেন্দ্রের মান: আর্দ্রতা {num(cpc['max_moisture_pct'], 'bn')}%, অঙ্কুরিত দানা "
                        f"{num(cpc['max_damaged_sprouted_pct'], 'bn')}%"},
        ],
        team_choices=[f"rain chance under {hc['dry_day_max_prob_pct']}%",
                      f"{run} dry days in a row"],
        team_choices_bn=[f"বৃষ্টির সম্ভাবনা {num(hc['dry_day_max_prob_pct'], 'bn')}%-এর কম",
                         f"পরপর {num(run, 'bn')}টি শুকনো দিন"],
        actionable=True,
    )
