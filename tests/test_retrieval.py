"""Embeddings: village matching across scripts, intent fallback, cited knowledge search."""
import json
from pathlib import Path

import pytest

from app.agent.retrieval import knowledge_search, route_by_examples
from app.embeddings import NgramEmbedder, phonetic_key, transliterate_bn
from app.sources import places

# Real Paschim Medinipur / Jhargram place names: Bengali script vs the Latin spelling used on maps.
PAIRS = [("শালবনি", "Salboni"), ("মেদিনীপুর", "Midnapore"), ("গড়বেতা", "Garbeta"), ("খড়গপুর", "Kharagpur"),
         ("ঝাড়গ্রাম", "Jhargram"), ("কেশপুর", "Keshpur"), ("চন্দ্রকোণা", "Chandrakona"), ("দাঁতন", "Dantan")]


@pytest.mark.parametrize("bn,en", PAIRS)
def test_cross_script_names_match(bn, en):
    e = NgramEmbedder().embed([bn, en], "place")
    assert float(e[0] @ e[1]) > 0.9, (transliterate_bn(bn), phonetic_key(bn), phonetic_key(en))


def test_different_places_do_not_match():
    e = NgramEmbedder().embed(["শালবনি", "Garbeta", "Keshpur"], "place")
    assert float(e[0] @ e[1]) < 0.3 and float(e[0] @ e[2]) < 0.3


@pytest.fixture
def place_file(tmp_path: Path):
    # Test entries only: names are real places, coordinates are placeholders for the matcher logic.
    data = {"places": [
        {"osm_id": 1, "name": "Salboni", "name_bn": "শালবনি", "lat": 1.0, "lon": 1.0, "district": "Paschim Medinipur"},
        {"osm_id": 2, "name": "Garbeta", "name_bn": None, "lat": 2.0, "lon": 2.0, "district": "Paschim Medinipur"},
        {"osm_id": 3, "name": "Keshpur", "name_bn": "কেশপুর", "lat": 3.0, "lon": 3.0, "district": "Paschim Medinipur"}]}
    p = tmp_path / "places.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_resolve_accepts_confirms_and_misses(place_file):
    m = places.resolve("গড়বেতা", "Paschim Medinipur", path=place_file)
    assert m.status == "accept" and m.name == "Garbeta" and m.lat == 2.0
    assert places.resolve("Salbonee", None, path=place_file).name == "Salboni"
    assert places.resolve("Kolkata", None, path=place_file).status == "miss"


def test_resolve_without_list_misses(tmp_path):
    assert places.resolve("Salboni", None, path=str(tmp_path / "none.json")).status == "miss"


def test_intent_fallback_on_held_out_sentences(capsys):
    ev = json.loads((Path(__file__).parent / "intent_eval.json").read_text(encoding="utf-8"))
    right = wrong = abstain = 0
    for text, expected in ev:
        got, _ = route_by_examples(text)
        if not got:
            abstain += 1
        elif got[0] == expected:
            right += 1
        else:
            wrong += 1
    with capsys.disabled():
        print(f"\nintent fallback (n-gram engine): {right} right, {abstain} abstained, {wrong} wrong of {len(ev)}")
    assert right / len(ev) >= 0.8
    assert wrong <= 1          # when unsure it must abstain, not guess


def test_knowledge_finds_the_cited_passage_or_nothing():
    assert knowledge_search("ভারী বৃষ্টি মানে কত মিমি")[0]["id"] == "imd-rain-amounts"
    assert knowledge_search("why 72 hours for the claim")[0]["id"].startswith("sbi-bsb")
    assert knowledge_search("আলুর জন্য কত তাপমাত্রা")[0]["id"] == "cpri-potato-thermal"
    assert knowledge_search("what is the price of gold") == []
    assert knowledge_search("tell me a joke") == []
