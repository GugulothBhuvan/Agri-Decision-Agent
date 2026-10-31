"""LLM access. The LLM only extracts, routes and phrases; it never decides.

Provider is chosen by LLM_PROVIDER: "anthropic" (Claude via the official SDK) or "none"
(no LLM; every caller falls back to field-by-field questions and template replies).
Every call has an 8-second timeout and returns None on any failure, so callers always
have a deterministic path.
"""
from __future__ import annotations

import json
import logging

from ..config import settings
from ..sources import switch

log = logging.getLogger("fosol.llm")

DEFAULT_MODEL = "claude-opus-5-5"
_client = None


class LLMUnavailable(Exception):
    pass


def available() -> bool:
    s = settings()
    return s.llm_provider == "anthropic" and bool(s.llm_api_key) and switch.is_enabled("llm")


def _anthropic():
    global _client
    if _client is None:
        import anthropic
        s = settings()
        _client = anthropic.AsyncAnthropic(api_key=s.llm_api_key, timeout=s.llm_timeout_s, max_retries=0)
    return _client


async def _create(system: str, user: str, max_tokens: int, output_config: dict) -> str | None:
    import anthropic
    s = settings()
    try:
        resp = await _anthropic().beta.messages.create(
            model=s.llm_model or DEFAULT_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
            # A declined request is re-run server-side on Anthropic's recommended fallback model.
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
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


async def complete_json(system: str, user: str, schema: dict, max_tokens: int = 2000) -> dict | None:
    """Structured output constrained to `schema`. None on any failure."""
    if not available():
        return None
    text = await _create(system, user, max_tokens, {
        # Latency-sensitive voice path: low effort keeps a turn inside the 12 s budget.
        "effort": "low",
        "format": {"type": "json_schema", "schema": schema},
    })
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        log.warning("LLM returned invalid JSON")
        return None


async def complete_text(system: str, user: str, max_tokens: int = 1500) -> str | None:
    if not available():
        return None
    return await _create(system, user, max_tokens, {"effort": "low"})
