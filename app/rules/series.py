"""Parse a stored Open-Meteo response into hourly and daily series in local time (IST)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..i18n import IST


@dataclass
class Day:
    date: date
    rain_mm: float
    prob_max: float
    tmax: float | None
    tmin: float | None


class Series:
    def __init__(self, raw: dict):
        h = raw["hourly"]
        # Open-Meteo returns naive local times when timezone=Asia/Kolkata.
        self.times = [datetime.fromisoformat(t) for t in h["time"]]
        self.precip = [v or 0.0 for v in h["precipitation"]]
        self.prob = [v or 0 for v in h["precipitation_probability"]]
        self.rh = h.get("relative_humidity_2m") or []
        self.temp = h.get("temperature_2m") or []
        d = raw["daily"]
        self.days = [
            Day(date.fromisoformat(t), d["precipitation_sum"][i] or 0.0,
                d["precipitation_probability_max"][i] or 0,
                (d.get("temperature_2m_max") or [None] * len(d["time"]))[i],
                (d.get("temperature_2m_min") or [None] * len(d["time"]))[i])
            for i, t in enumerate(d["time"])
        ]

    @staticmethod
    def local_now(now: datetime | None = None) -> datetime:
        """Current local time as a naive IST datetime floored to the hour."""
        now = now or datetime.now(IST)
        if now.tzinfo is not None:
            now = now.astimezone(IST).replace(tzinfo=None)
        return now.replace(minute=0, second=0, microsecond=0)

    def sum_between(self, start: datetime, end: datetime) -> float:
        """Precipitation summed over hourly steps with start <= t < end."""
        return round(sum(p for t, p in zip(self.times, self.precip) if start <= t < end), 1)

    def covers(self, start: datetime, end: datetime) -> bool:
        return bool(self.times) and self.times[0] <= start and self.times[-1] >= end - timedelta(hours=1)

    def days_from(self, first: date, n: int) -> list[Day]:
        out = [d for d in self.days if d.date >= first]
        return out[:n]

    def hour_index(self, t: datetime) -> int | None:
        try:
            return self.times.index(t)
        except ValueError:
            return None
