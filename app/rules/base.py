"""Shared outcome type and change-detection hash."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Outcome:
    module: str                      # harvest | calamity | claim | blight
    data: dict                       # JSON-safe outcome
    reason_en: str
    reason_bn: str
    action_at: datetime | None       # when the farmer must act (local naive), for tiers
    hash_keys: list[str]             # outcome keys that define "the decision" for change detection
    rule_sources: list[dict] = field(default_factory=list)  # [{name, url}]
    team_choices: list[str] = field(default_factory=list)   # thresholds that are our design choices
    team_choices_bn: list[str] = field(default_factory=list)
    actionable: bool = True          # False = "nothing to do"; stored, never delivered

    def decision_hash(self) -> str:
        core = {k: self.data.get(k) for k in sorted(self.hash_keys)}
        core["module"] = self.module
        return hashlib.sha256(json.dumps(core, sort_keys=True, default=str).encode()).hexdigest()
