"""Embeddings: they find things (a village, an intent example, a passage). They never decide.

Two engines behind one interface:
- "ngram" (default): hashed character n-grams and words. No download, tiny memory, deterministic.
  For place names it also builds a phonetic consonant key, so Bengali-script and Latin-script spellings
  of the same village land close together ("শালবনি" ~ "Salboni").
- "fastembed": a neural multilingual model through fastembed (ONNX, CPU), chosen with EMBEDDINGS_MODEL,
  e.g. google/embeddinggemma-300m. Better at paraphrase; needs the model download (~1.2 GB) and RAM.

Thresholds differ per engine; callers read them from rules.yaml under embeddings.<engine>.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import unicodedata
from functools import lru_cache

import numpy as np

log = logging.getLogger("fosol.embeddings")
DIM = 2048

# ---------- Bengali -> rough Latin ----------
_CONS = {"ক": "k", "খ": "kh", "গ": "g", "ঘ": "gh", "ঙ": "ng", "চ": "ch", "ছ": "chh", "জ": "j", "ঝ": "jh",
         "ঞ": "n", "ট": "t", "ঠ": "th", "ড": "d", "ঢ": "dh", "ণ": "n", "ত": "t", "থ": "th", "দ": "d", "ধ": "dh",
         "ন": "n", "প": "p", "ফ": "ph", "ব": "b", "ভ": "bh", "ম": "m", "য": "j", "র": "r", "ল": "l", "শ": "sh",
         "ষ": "sh", "স": "s", "হ": "h", "\u09dc": "r", "\u09dd": "rh", "\u09df": "y", "ৎ": "t"}
_VOWELS = {"অ": "o", "আ": "a", "ই": "i", "ঈ": "i", "উ": "u", "ঊ": "u", "ঋ": "ri", "এ": "e", "ঐ": "oi", "ও": "o",
           "ঔ": "ou"}
_SIGNS = {"া": "a", "ি": "i", "ী": "i", "ু": "u", "ূ": "u", "ৃ": "ri", "ে": "e", "ৈ": "oi", "ো": "o", "ৌ": "ou"}
_MARKS = {"ং": "ng", "ঃ": "h", "ঁ": "n"}  # chandrabindu: English spellings write the nasal as "n"
_HASANTA = "্"


def transliterate_bn(text: str) -> str:
    """Bengali script to rough Latin. Good enough for matching names, not for display."""
    text = unicodedata.normalize("NFC", text)
    # ড় ঢ় য় are composition exclusions: they often arrive as consonant + nukta (U+09BC).
    text = text.replace("\u09a1\u09bc", "\u09dc").replace("\u09a2\u09bc", "\u09dd").replace("\u09af\u09bc", "\u09df")
    out, chars = [], list(text)
    for i, ch in enumerate(chars):
        nxt = chars[i + 1] if i + 1 < len(chars) else ""
        if ch in _CONS:
            out.append(_CONS[ch])
            if nxt not in _SIGNS and nxt != _HASANTA and nxt in _CONS:
                out.append("o")  # inherent vowel between consonants (word-final schwa is dropped)
        elif ch in _VOWELS:
            out.append(_VOWELS[ch])
        elif ch in _SIGNS:
            out.append(_SIGNS[ch])
        elif ch in _MARKS:
            out.append(_MARKS[ch])
        elif ch == _HASANTA:
            continue
        else:
            out.append(ch)
    return "".join(out)


_DIGRAPHS = [("chh", "c"), ("sh", "s"), ("kh", "k"), ("gh", "g"), ("ch", "c"), ("jh", "j"), ("th", "t"),
             ("dh", "d"), ("ph", "f"), ("bh", "b"), ("rh", "r"), ("ng", "n"), ("ck", "k"), ("q", "k"),
             ("v", "b"), ("w", "b"), ("z", "j"), ("x", "ks")]


def phonetic_key(name: str) -> str:
    """A spelling-tolerant key for place names: transliterate, fold digraphs, drop vowels after the first letter."""
    s = transliterate_bn(name).lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if c.isascii() and (c.isalpha() or c == " "))
    for a, b in _DIGRAPHS:
        s = s.replace(a, b)
    words = []
    for w in s.split():
        if not w:
            continue
        head, tail = w[0], re.sub(r"[aeiouy]", "", w[1:])
        key = head + tail
        key = re.sub(r"(.)\1+", r"\1", key)  # squash doubled letters
        words.append(key)
    return " ".join(words)


# ---------- the n-gram engine ----------

def _hash(tok: str) -> int:
    return int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=4).digest(), "little") % DIM


def _ngrams(s: str, n: int) -> list[str]:
    s = f" {s} "
    return [s[i:i + n] for i in range(max(0, len(s) - n + 1))]


def _norm_text(text: str) -> str:
    t = unicodedata.normalize("NFC", text.lower())
    t = re.sub(r"[^\w\sঀ-৿ऀ-ॿ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


class NgramEmbedder:
    name = "ngram"

    def embed(self, texts: list[str], kind: str = "text") -> np.ndarray:
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        for row, text in enumerate(texts):
            feats: list[tuple[str, float]] = []
            if kind == "place":
                key = phonetic_key(text)
                feats += [(f"p3:{g}", 1.0) for g in _ngrams(key, 3)]
                feats += [(f"p2:{g}", 0.5) for g in _ngrams(key, 2)]
                feats += [(f"pw:{w}", 1.5) for w in key.split()]
            else:
                t = _norm_text(text)
                feats += [(f"w:{w}", 1.0) for w in t.split()]
                feats += [(f"c3:{g}", 0.4) for w in t.split() for g in _ngrams(w, 3)]
                lat = _norm_text(transliterate_bn(t))
                if lat != t:
                    feats += [(f"l3:{g}", 0.2) for w in lat.split() for g in _ngrams(w, 3)]
            for tok, wt in feats:
                out[row, _hash(tok)] += wt
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms


class FastEmbedEmbedder:
    """Neural multilingual embeddings. Loaded lazily; raises at first use if the model can't load."""

    def __init__(self, model: str):
        from fastembed import TextEmbedding
        self.name = f"fastembed:{model}"
        self._model = TextEmbedding(model_name=model)

    def embed(self, texts: list[str], kind: str = "text") -> np.ndarray:
        if kind == "place":
            texts = [f"{t} ({transliterate_bn(t)})" for t in texts]
        vecs = np.array(list(self._model.embed(texts)), dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms


@lru_cache
def get_embedder():
    model = os.environ.get("EMBEDDINGS_MODEL", "").strip()
    if model:
        try:
            return FastEmbedEmbedder(model)
        except Exception as e:  # noqa: BLE001 - fall back rather than fail the app
            log.warning("could not load %s (%s); using the n-gram engine", model, e)
    return NgramEmbedder()


def engine_name() -> str:
    return "ngram" if isinstance(get_embedder(), NgramEmbedder) else "neural"


class VectorIndex:
    """A small in-memory index: brute-force cosine over a few thousand rows is a few milliseconds."""

    def __init__(self, items: list[dict], text_key: str, kind: str = "text"):
        self.items = items
        self.kind = kind
        texts = [it[text_key] for it in items]
        self.matrix = get_embedder().embed(texts, kind) if texts else np.zeros((0, DIM), dtype=np.float32)

    def search(self, query: str, k: int = 5) -> list[tuple[float, dict]]:
        if not self.items or not query.strip():
            return []
        q = get_embedder().embed([query], self.kind)[0]
        scores = self.matrix @ q
        top = np.argsort(-scores)[:k]
        return [(float(scores[i]), self.items[i]) for i in top]
