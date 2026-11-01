"""OpenRouter client: request shape, JSON clean-up, schema conformance, failures. HTTP is simulated."""
import asyncio
import json

import httpx
import pytest

from app.config import settings
from app.llm import client
from app.llm.prompts import EXTRACT_SCHEMA, ROUTE_SCHEMA


@pytest.fixture
def openrouter(monkeypatch):
    s = settings()
    monkeypatch.setattr(s, "llm_provider", "openrouter")
    monkeypatch.setattr(s, "llm_api_key", "test-key")
    monkeypatch.setattr(s, "llm_model", "")
    seen = []
    replies = []

    def handler(req: httpx.Request):
        body = json.loads(req.content)
        seen.append((req, body))
        status, payload = replies.pop(0)
        return httpx.Response(status, json=payload)

    real = httpx.AsyncClient
    monkeypatch.setattr(client.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), timeout=kw.get("timeout")))
    return seen, replies


def msg(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def test_json_reply_is_cleaned_and_conformed(openrouter):
    seen, replies = openrouter
    replies.append((200, msg('```json\n{"crop": "paddy_aman", "stage": "ripe!", "area_bigha": "3", "extra": 1,'
                             ' "confidence": {"crop": 0.9}}\n```')))
    r = asyncio.run(client.complete_json("sys", "user", EXTRACT_SCHEMA))
    req, body = seen[0]
    assert req.url == client.OPENROUTER_URL and req.headers["Authorization"] == "Bearer test-key"
    assert body["model"] == "google/gemma-4-31b-it:free" and body["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in body["messages"][0]["content"]
    assert r["crop"] == "paddy_aman"
    assert r["stage"] is None              # not one of the allowed stages
    assert r["area_bigha"] == 3.0          # "3" coerced to a number
    assert r["village"] is None and "extra" not in r
    assert r["confidence"]["crop"] == 0.9 and r["confidence"]["village"] == 0


def test_router_drops_unknown_intents(openrouter):
    _, replies = openrouter
    replies.append((200, msg('{"intents": ["harvest_window", "launch_rocket"], "language": "bn", "confidence": 0.8}')))
    r = asyncio.run(client.complete_json("sys", "user", ROUTE_SCHEMA))
    assert r["intents"] == ["harvest_window"] and r["language"] == "bn"


def test_retries_without_response_format_on_400(openrouter):
    seen, replies = openrouter
    replies += [(400, {"error": {"message": "response_format not supported"}}), (200, msg('{"intents": [], "language": "en", "confidence": 1}'))]
    r = asyncio.run(client.complete_json("sys", "user", ROUTE_SCHEMA))
    assert r is not None and "response_format" not in seen[1][1]


def test_rate_limit_and_garbage_return_none(openrouter):
    _, replies = openrouter
    replies += [(429, {"error": {"message": "rate limited"}}), (200, msg("sorry, I cannot do JSON"))]
    assert asyncio.run(client.complete_json("sys", "user", ROUTE_SCHEMA)) is None
    assert asyncio.run(client.complete_json("sys", "user", ROUTE_SCHEMA)) is None


def test_text_reply_and_switch_off(openrouter, monkeypatch):
    seen, replies = openrouter
    replies.append((200, msg("  ধান কাটার নিরাপদ দিন বুধবার।  ")))
    assert asyncio.run(client.complete_text("sys", "user")) == "ধান কাটার নিরাপদ দিন বুধবার।"
    assert "response_format" not in seen[0][1]
    from app.sources import switch
    switch.set_enabled("llm", False)
    assert asyncio.run(client.complete_text("sys", "user")) is None


def test_turn_uses_llm_then_guardrail_catches_invented_number(openrouter, saved_forecast, geocoder):
    """Full turn with a simulated model: route + phrase. A reply with an invented number is regenerated,
    then replaced by the template."""
    from fastapi.testclient import TestClient
    from app.main import app
    _, replies = openrouter
    c = TestClient(app)
    with c:
        # Profile without the LLM path mattering: fill it through the farm screen.
        c.patch("/api/farm", json={"village": "Salboni", "district": "Paschim Medinipur", "crop": "paddy_aman",
                                   "stage": "ready", "area_bigha": 3, "insured": True})
        replies += [
            (200, msg('{"intents": ["harvest_window"], "language": "bn", "confidence": 0.9}')),   # route
            (200, msg("ধান কাটুন ১৯ নভেম্বর।")),                                                   # phrase: invented date
            (200, msg("ধান কাটুন ২১ নভেম্বর।")),                                                   # retry: still invented
        ]
        r = c.post("/api/turn", json={"text": "কবে ধান কাটব?"}).json()
        assert r["reply_source"] == "template" and r["guardrail"]["templated"]
        assert "১৯" not in r["reply_text"] and r["decision_ids"]
