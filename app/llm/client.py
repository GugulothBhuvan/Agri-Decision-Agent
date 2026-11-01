"""LLM access. The LLM only extracts, routes and phrases; it never decides.

Provider is chosen by LLM_PROVIDER:
- "openrouter": any OpenRouter model through its OpenAI-style chat API (free models end in ":free")
- "anthropic": Claude via the official SDK
- "none": no LLM; callers fall back to field-by-field questions and template replies

Every call has a timeout and returns None on any failure, so callers always have a deterministic path.
JSON replies are checked against the schema: unknown keys are dropped, missing or invalid values become None.
"""
from __future__ import annotations

import json
import logging
import re

import httpx

from ..config import settings
from ..sources import switch

log = logging.getLogger("fosol.llm")

DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "openrouter": "google/gemma-4-31b-it:free"}
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
PROVIDERS = ("openrouter", "anthropic")
_anthropic_client = None


def provider() -> str:
    return settings().llm_provider


def model() -> str:
    s = settings()
    return s.llm_model or DEFAULT_MODELS.get(s.llm_provider, "")


def available() -> bool:
    s = settings()
    return s.llm_provider in PROVIDERS and bool(s.llm_api_key) and switch.is_enabled("llm")


# ---------- OpenRouter ----------

async def _openrouter(system: str, user: str, max_tokens: int, want_json: bool) -> str | None:
    s = settings()
    body = {"model": model(), "max_tokens": max_tokens, "temperature": 0.2,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    if want_json:
        body["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {s.llm_api_key}", "Content-Type": "application/json",
               "HTTP-Referer": "https://github.com/GugulothBhuvan/agri-decision-agent", "X-Title": "Agri Decision Agent"}
    try:
        async with httpx.AsyncClient(timeout=s.llm_timeout_s) as c:
            r = await c.post(OPENROUTER_URL, headers=headers, json=body)
        if r.status_code == 400 and want_json:
            # Some free models reject response_format; retry once relying on the prompt alone.
            body.pop("response_format", None)
            async with httpx.AsyncClient(timeout=s.llm_timeout_s) as c:
                r = await c.post(OPENROUTER_URL, headers=headers, json=body)
        if r.status_code != 200:
            log.warning("OpenRouter %s: %s", r.status_code, r.text[:300])
            return None
        data = r.json()
    except httpx.TimeoutException:
        log.warning("OpenRouter timeout")
        return None
    except (httpx.HTTPError, ValueError) as e:
        log.warning("OpenRouter error: %s", e)
        return None
    if data.get("error"):
        log.warning("OpenRouter error: %s", str(data["error"])[:300])
        return None
    choices = data.get("choices") or []
    if not choices:
        return None
    msg = choices[0].get("message") or {}
    text = (msg.get("content") or "").strip()
    return text or None


# ---------- Anthropic ----------

def _anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        s = settings()
        _anthropic_client = anthropic.AsyncAnthropic(api_key=s.llm_api_key, timeout=s.llm_timeout_s, max_retries=0)
    return _anthropic_client


async def _claude(system: str, user: str, max_tokens: int, output_config: dict) -> str | None:
    import anthropic
    try:
        resp = await _anthropic().beta.messages.create(
            model=model(), max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}], output_config=output_config,
            # A declined request is re-run server-side on Anthropic's recommended fallback model.
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        )
    except anthropic.APITimeoutError:
        log.warning("LLM timeout")
        return None
    except anthropic.RateLimitError:
        log.warning("LLM rate limited")
        return None
    except anthropic.APIStatusError as e:
        log.warning("LLM API error %s: %s", e.status_code, e.message)
        return None
    except anthropic.APIConnectionError as e:
        log.warning("LLM connection error: %s", e)
        return None
    if resp.stop_reason == "refusal":
        log.warning("LLM refusal: %s", getattr(resp.stop_details, "category", None))
        return None
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    return text or None


# ---------- JSON handling ----------

def _parse_json(text: str) -> dict | None:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)  # some models wrap JSON in a code fence
    try:
        v = json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, re.DOTALL)
        if not m:
            return None
        try:
            v = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return v if isinstance(v, dict) else None


def _types(spec: dict) -> list[str]:
    t = spec.get("type")
    return t if isinstance(t, list) else [t] if t else []


def _ok_scalar(v, spec: dict) -> bool:
    if "enum" in spec and v not in spec["enum"]:
        return False
    checks = {"string": lambda x: isinstance(x, str), "boolean": lambda x: isinstance(x, bool),
              "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
              "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
              "null": lambda x: x is None}
    return any(checks[t](v) for t in _types(spec) if t in checks)


def conform(value, schema: dict):
    """Coerce a parsed reply to the schema. Missing or invalid fields become None (or 0 for numbers in objects
    that require them), extra keys are dropped. Callers already treat None as "not said"."""
    if not isinstance(value, dict):
        value = {}
    out = {}
    for key, spec in schema.get("properties", {}).items():
        v = value.get(key)
        types = _types(spec)
        if "object" in types:
            out[key] = conform(v, spec)
        elif "array" in types:
            items = spec.get("items", {})
            out[key] = [x for x in (v if isinstance(v, list) else []) if _ok_scalar(x, items)]
        elif v is not None and _ok_scalar(v, spec):
            out[key] = v
        elif isinstance(v, str) and "number" in types:
            try:
                out[key] = float(v)
            except ValueError:
                out[key] = None
        else:
            out[key] = 0 if types == ["number"] else None
    return out


def _schema_hint(schema: dict) -> str:
    return ("\n\nReply with one JSON object only, no prose, matching this JSON Schema "
            "(use null for unknown values):\n" + json.dumps(schema, ensure_ascii=False))


# ---------- public API ----------

async def complete_json(system: str, user: str, schema: dict, max_tokens: int = 2000) -> dict | None:
    """A JSON object conforming to `schema`, or None on any failure."""
    if not available():
        return None
    if provider() == "anthropic":
        # Latency-sensitive voice path: low effort keeps a turn inside the 12 s budget.
        text = await _claude(system, user, max_tokens,
                             {"effort": "low", "format": {"type": "json_schema", "schema": schema}})
    else:
        text = await _openrouter(system + _schema_hint(schema), user, max_tokens, want_json=True)
    if text is None:
        return None
    parsed = _parse_json(text)
    if parsed is None:
        log.warning("LLM returned invalid JSON")
        return None
    return conform(parsed, schema)


async def complete_text(system: str, user: str, max_tokens: int = 1500) -> str | None:
    if not available():
        return None
    if provider() == "anthropic":
        return await _claude(system, user, max_tokens, {"effort": "low"})
    return await _openrouter(system, user, max_tokens, want_json=False)
