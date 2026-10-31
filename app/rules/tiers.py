"""Tiers from the TRD table."""
from __future__ import annotations

from datetime import datetime, timedelta

from .base import Outcome
from .series import Series

TIERS = ("green", "yellow", "amber", "red")


def assign_tier(outcome: Outcome, changed: bool, now: datetime | None = None) -> str:
    """Red: protect at the heavy bar or a claim kit. Amber: changed, action within 72 h.
    Yellow: changed, action more than 72 h away. Green: no change (logged only)."""
    if outcome.module == "calamity" and outcome.data.get("action") == "protect_now":
        return "red"
    if outcome.module == "claim" and outcome.data.get("action") == "claim_open":
        return "red"
    if not changed or not outcome.actionable:
        return "green"
    now_l = Series.local_now(now)
    if outcome.action_at is not None and outcome.action_at - now_l <= timedelta(hours=72):
        return "amber"
    return "yellow"
