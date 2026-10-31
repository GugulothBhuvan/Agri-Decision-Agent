"""Extraction golden set (20 utterances written by the team). The PRD sets no target until measured:
this test reports per-field accuracy of the no-LLM fallback parser. scripts/eval_extraction.py scores the
LLM extractor the same way when an API key is set."""
import json
from pathlib import Path

from app.agent.parse import parse_fields

GOLD = json.loads((Path(__file__).parent / "extraction_golden.json").read_text(encoding="utf-8"))
FIELDS = ["village", "district", "crop", "area_bigha", "stage", "cut_days_ago", "insured"]


def score(extract) -> dict:
    hit = {f: 0 for f in FIELDS}
    total = {f: 0 for f in FIELDS}
    for g in GOLD:
        got = extract(g["text"])
        for f, v in g["expect"].items():
            total[f] += 1
            gv = got.get(f)
            if isinstance(v, str) and isinstance(gv, str):
                ok = v.lower() in gv.lower() or gv.lower() in v.lower()
            elif isinstance(v, (int, float)) and not isinstance(v, bool) and gv is not None and not isinstance(gv, bool):
                ok = abs(float(gv) - float(v)) < 0.01
            else:
                ok = gv == v
            hit[f] += ok
    return {f: (hit[f], total[f]) for f in FIELDS if total[f]}


def test_golden_set_shape():
    assert len(GOLD) == 20


def test_report_fallback_parser_accuracy(capsys):
    res = score(lambda t: parse_fields(t, None))
    with capsys.disabled():
        print("\nfallback parser accuracy: " + ", ".join(f"{f} {h}/{n}" for f, (h, n) in res.items()))
    # The fallback must at least read crop and stage most of the time.
    assert res["crop"][0] / res["crop"][1] >= 0.8
