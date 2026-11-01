"""Operator source-outage switch. Single instance, so in-process state is enough."""
from __future__ import annotations

SOURCES = {
    "openmeteo": "Open-Meteo forecast API",
    "nominatim": "Nominatim (OpenStreetMap)",
    "sarvam": "Sarvam speech",
    "llm": "LLM (OpenRouter / Claude)",
}

_enabled: dict[str, bool] = {k: True for k in SOURCES}


class SourceDisabled(Exception):
    pass


def is_enabled(source: str) -> bool:
    return _enabled.get(source, True)


def set_enabled(source: str, enabled: bool) -> dict[str, bool]:
    if source not in SOURCES:
        raise KeyError(source)
    _enabled[source] = bool(enabled)
    return dict(_enabled)


def state() -> dict[str, bool]:
    return dict(_enabled)
