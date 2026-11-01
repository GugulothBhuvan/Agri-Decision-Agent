"""Retrieval over fixed, cited data: intent examples (routing fallback) and knowledge passages.

Nothing here decides an outcome. Intent routing picks which tool to call; knowledge search picks which
cited passage to quote. No farmer conversation is ever embedded or stored as a vector.
"""
from __future__ import annotations

import json
from collections import defaultdict
from functools import lru_cache

from ..config import ROOT, rules
from ..embeddings import VectorIndex, engine_name

INTENTS_FILE = ROOT / "data" / "intent_examples.json"
KB_FILE = ROOT / "data" / "kb_passages.json"


@lru_cache
def _intent_index() -> VectorIndex:
    data = json.loads(INTENTS_FILE.read_text(encoding="utf-8"))
    rows = [{"text": ex, "intent": intent} for intent, exs in data.items() for ex in exs]
    return VectorIndex(rows, "text")


@lru_cache
def _kb() -> tuple[VectorIndex, list[dict]]:
    passages = json.loads(KB_FILE.read_text(encoding="utf-8"))
    rows = []
    for p in passages:  # short topic lines match short questions far better than the long passage itself
        for t in (p["text"], p.get("topic_bn"), p.get("topic_en")):
            if t:
                rows.append({"text": t, "passage": p})
    return VectorIndex(rows, "text"), passages


def route_by_examples(text: str, k: int = 5) -> tuple[list[str], float]:
    """Top-k nearest examples vote, weighted by similarity. Returns (intents best first, best score)."""
    hits = _intent_index().search(text, k=k)
    if not hits:
        return [], 0.0
    votes: dict[str, float] = defaultdict(float)
    for score, row in hits:
        votes[row["intent"]] += max(score, 0.0)
    ranked = sorted(votes.items(), key=lambda kv: kv[1], reverse=True)
    best_score = hits[0][0]
    if best_score < rules()["embeddings"][engine_name()]["intent_route"]:
        return [], best_score
    return [ranked[0][0]], best_score


def knowledge_search(question: str, k: int = 3) -> list[dict]:
    """Passages above the threshold, best first, each with its score, source and URL."""
    idx, _ = _kb()
    floor = rules()["embeddings"][engine_name()]["kb_min"]
    best: dict[str, tuple[float, dict]] = {}
    for score, row in idx.search(question, k=k * 3):
        pid = row["passage"]["id"]
        if score >= floor and score > best.get(pid, (-1, None))[0]:
            best[pid] = (score, row["passage"])
    ranked = sorted(best.values(), key=lambda x: x[0], reverse=True)[:k]
    return [{**p, "score": round(s, 3)} for s, p in ranked]
