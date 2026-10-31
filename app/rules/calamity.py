"""Calamity protect alert (stage ready or cut): heavy rain in the next 24 hours."""
from __future__ import annotations

from datetime import datetime, timedelta

from ..i18n import num
from .base import Outcome
from .series import Series


def calamity_check(series: Series, cfg: dict, stage: str, now: datetime | None = None) -> Outcome:
    cc = cfg["calamity"]
    now_l = Series.local_now(now)
    end = now_l + timedelta(hours=24)
    next24 = series.sum_between(now_l, end)
    red, amber = cc["protect_red_next24h_mm"], cc["protect_amber_next24h_mm"]
    if next24 >= red:
        action, actionable = "protect_now", True
        reason_en = (f"{next24} mm of rain is forecast for your plot in the next 24 hours, at or above the IMD "
                     f"'heavy rain' bar of {red} mm. Move or cover the paddy now.")
        reason_bn = (f"আগামী ২৪ ঘণ্টায় আপনার জমিতে {num(next24, 'bn', 1)} মিমি বৃষ্টির পূর্বাভাস, যা IMD-র "
                     f"'ভারী বৃষ্টি'-র সীমা {num(red, 'bn', 1)} মিমি ছাড়িয়েছে। এখনই ধান সরিয়ে নিন বা ঢেকে দিন।")
    elif stage == "cut" and next24 >= amber:
        action, actionable = "cover_heap", True
        reason_en = (f"{next24} mm of rain is forecast in the next 24 hours, at or above the IMD 'moderate rain' "
                     f"bar of {amber} mm, and your paddy is cut. Cover the heap.")
        reason_bn = (f"আগামী ২৪ ঘণ্টায় {num(next24, 'bn', 1)} মিমি বৃষ্টির পূর্বাভাস (IMD-র 'মাঝারি বৃষ্টি'-র সীমা "
                     f"{num(amber, 'bn', 1)} মিমি), আর আপনার ধান কাটা হয়ে গেছে। গাদা ঢেকে রাখুন।")
    else:
        action, actionable = "none", False
        reason_en = f"{next24} mm of rain forecast in the next 24 hours; below the protect bars."
        reason_bn = f"আগামী ২৪ ঘণ্টায় {num(next24, 'bn', 1)} মিমি বৃষ্টির পূর্বাভাস; সতর্কতার সীমার নিচে।"
    rc = cfg["rain_categories_mm_24h"]
    return Outcome(
        module="calamity",
        data={"action": action, "next24_mm": next24, "window_start": now_l.isoformat(),
              "window_end": end.isoformat(), "red_bar_mm": red, "amber_bar_mm": amber, "stage": stage},
        reason_en=reason_en, reason_bn=reason_bn,
        action_at=now_l if actionable else None,
        hash_keys=["action"],
        rule_sources=[{"name": rc["source"], "url": rc["url"], "what": "heavy and moderate rain bars",
                       "what_bn": "ভারী ও মাঝারি বৃষ্টির সীমা"}],
        actionable=actionable,
    )
