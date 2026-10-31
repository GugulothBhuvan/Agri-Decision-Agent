"""Claim trigger: heavy rain on cut, insured paddy within the post-harvest window."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from ..i18n import fmt_day, num
from .base import Outcome
from .series import Series


def claim_trigger(series: Series, cfg: dict, *, stage: str | None, insured: bool | None,
                  cut_date: date | None, has_open_claim: bool, now: datetime | None = None) -> Outcome:
    cc = cfg["calamity"]
    now_l = Series.local_now(now)
    start = now_l - timedelta(hours=24)
    past24 = series.sum_between(start, now_l)
    bar = cc["claim_trigger_past24h_mm"]
    days_since_cut = (now_l.date() - cut_date).days if cut_date else None

    blockers = []
    if stage != "cut":
        blockers.append("paddy is not marked as cut")
    if not insured:
        blockers.append("farm is not marked as insured")
    if days_since_cut is None:
        blockers.append("cut date unknown")
    elif not (0 <= days_since_cut <= cc["post_harvest_window_days"]):
        blockers.append(f"cut {days_since_cut} days ago, outside the {cc['post_harvest_window_days']}-day window")
    if past24 < bar:
        blockers.append(f"{past24} mm in the past 24 hours is under the {bar} mm bar")
    if has_open_claim:
        blockers.append("a claim kit is already open")

    data = {"past24_mm": past24, "bar_mm": bar, "days_since_cut": days_since_cut,
            "report_within_hours": cc["report_within_hours"], "estimated": True}
    if blockers:
        data["action"] = "none"
        data["blockers"] = blockers
        return Outcome("claim", data, "No claim trigger: " + "; ".join(blockers) + ".",
                       "দাবির শর্ত পূরণ হয়নি।", None, ["action"],
                       rule_sources=[{"name": cc["source"], "url": cc["url"], "what": "reporting rule",
                                      "what_bn": "ক্ষতি জানানোর নিয়ম"}],
                       actionable=False)

    # Event time = first hour at which the trailing 24-hour total crossed the bar.
    event_at = now_l
    for i, t in enumerate(series.times):
        if t <= start or t > now_l:
            continue
        if series.sum_between(t - timedelta(hours=23), t + timedelta(hours=1)) >= bar:
            event_at = t + timedelta(hours=1)  # the hour's total is complete at its end
            break
    deadline = event_at + timedelta(hours=cc["report_within_hours"])
    data.update({"action": "claim_open", "event_at": event_at.isoformat(), "deadline_at": deadline.isoformat()})
    hrs = cc["report_within_hours"]
    reason_en = (f"An estimated {past24} mm of rain fell on your plot in the past 24 hours (model estimate, not a "
                 f"rain gauge), {days_since_cut} days after cutting. Bangla Shasya Bima asks for post-harvest loss "
                 f"to be reported within {hrs} hours: by {deadline.strftime('%H:%M')} on {fmt_day(deadline.date(), 'en')}.")
    reason_bn = (f"গত ২৪ ঘণ্টায় আপনার জমিতে আনুমানিক {num(past24, 'bn', 1)} মিমি বৃষ্টি হয়েছে (মডেলের হিসাব), "
                 f"ধান কাটার {num(days_since_cut, 'bn')} দিন পরে। বাংলা শস্য বিমায় ফসল কাটার পরের ক্ষতি "
                 f"{num(hrs, 'bn')} ঘণ্টার মধ্যে জানাতে হয়: {fmt_day(deadline.date(), 'bn')}, "
                 f"{num(deadline.strftime('%H:%M'), 'bn')}-এর মধ্যে।")
    return Outcome(
        "claim", data, reason_en, reason_bn, action_at=deadline, hash_keys=["action", "event_at"],
        rule_sources=[{"name": cc["source"], "url": cc["url"],
                       "what": f"report post-harvest loss within {hrs} hours",
                       "what_bn": f"ফসল কাটার পরের ক্ষতি {num(hrs, 'bn')} ঘণ্টার মধ্যে জানাতে হয়"}],
        team_choices=[f"claim trigger at {bar} mm in 24 h (IMD heavy-rain bar)"],
        team_choices_bn=[f"২৪ ঘণ্টায় {num(bar, 'bn')} মিমি বৃষ্টিতে দাবি (IMD-র ভারী বৃষ্টির সীমা)"],
        actionable=True,
    )
