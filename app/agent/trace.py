"""Per-node traces: node, inputs hash, output summary, duration and error."""
from __future__ import annotations

import functools
import hashlib
import json
import time
from typing import Awaitable, Callable

from ..db import Trace, session_scope


def _hash(obj) -> str:
    try:
        blob = json.dumps(obj, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001
        blob = repr(obj)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def write(run_id: str, graph: str, node: str, inputs, summary: str, ms: int, error: str | None = None) -> None:
    try:
        with session_scope() as s:
            s.add(Trace(run_id=run_id, graph=graph, node=node, inputs_hash=_hash(inputs),
                        summary=(summary or "")[:500], duration_ms=ms, error=(error or None) and error[:1000]))
    except Exception:  # noqa: BLE001 - tracing must never break a run
        pass


def traced(graph: str, node: str, summarize: Callable[[dict], str] | None = None):
    """Wrap a LangGraph node. Exceptions are recorded in state['errors'] instead of crashing the run."""
    def deco(fn: Callable[[dict], Awaitable[dict]]):
        @functools.wraps(fn)
        async def wrapper(state: dict) -> dict:
            t0 = time.perf_counter()
            inputs = {k: v for k, v in state.items() if k in ("farm_id", "text", "intents", "run_id")}
            try:
                out = await fn(state) or {}
                ms = int((time.perf_counter() - t0) * 1000)
                write(state.get("run_id", "?"), graph, node, inputs, summarize(out) if summarize else _auto(out), ms)
                return out
            except Exception as e:  # noqa: BLE001
                ms = int((time.perf_counter() - t0) * 1000)
                err = f"{type(e).__name__}: {e}"
                write(state.get("run_id", "?"), graph, node, inputs, "failed", ms, err)
                return {"errors": list(state.get("errors") or []) + [f"{node}: {err}"]}
        return wrapper
    return deco


def _auto(out: dict) -> str:
    parts = []
    for k, v in out.items():
        if isinstance(v, (list, dict)):
            parts.append(f"{k}={len(v)}")
        elif isinstance(v, (str, int, float, bool)) or v is None:
            parts.append(f"{k}={str(v)[:60]}")
    return ", ".join(parts)
