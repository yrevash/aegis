"""The gold set loads, validates, and hashes — and its schema refuses a broken case.

A gold set with a broken anchor measures nothing while looking exactly like one that
measures everything, so the loader is strict and this file proves it is.
"""

from __future__ import annotations

import json

import pytest

from aegis.evals.goldset import (
    FIXTURE_GOLD_SET_PATH,
    MAX_SPAN_WORDS,
    GoldCase,
    GoldKind,
    dump_gold_set,
    gold_set_hash,
    hit_ranks,
    is_hit,
    load_gold_set,
)

FIXTURES = {
    "transformer-single-column.pdf",
    "bert-two-column.pdf",
    "census-income-tables.pdf",
    "irs-1040-instructions-tables.pdf",
}


@pytest.fixture(scope="module")
def gold():
    return load_gold_set()


def test_the_shipped_gold_set_loads(gold):
    assert len(gold) >= 50, "n<50 cannot defend a 15-point delta (see eval-design §4.3)"
    assert len({case.id for case in gold}) == len(gold)


def test_every_case_names_a_real_fixture_or_is_unanswerable(gold):
    for case in gold:
        if case.kind is GoldKind.UNANSWERABLE:
            assert case.doc_id == ""
        else:
            assert case.doc_id in FIXTURES, case.id


def test_the_set_covers_all_four_fixtures_and_every_kind(gold):
    assert {case.doc_id for case in gold if case.doc_id} == FIXTURES
    assert {case.kind for case in gold} == set(GoldKind)


def test_every_span_is_short_enough_to_sit_inside_one_chunk(gold):
    for case in gold:
        for span in case.required_spans:
            assert len(span.split()) <= MAX_SPAN_WORDS, case.id


def test_multi_hop_cases_are_the_only_multi_span_ones(gold):
    for case in gold:
        if case.also_requires:
            assert case.kind is GoldKind.MULTI_HOP, case.id
        if case.kind is GoldKind.MULTI_HOP:
            assert case.also_requires, case.id


def test_unanswerable_cases_are_not_retrieval_gradeable(gold):
    unanswerable = [c for c in gold if c.kind is GoldKind.UNANSWERABLE]
    assert len(unanswerable) >= 5
    for case in unanswerable:
        assert not case.gradeable
        assert hit_ranks(case, ["anything at all"]) == ()


def test_no_two_cases_share_an_answer_span(gold):
    """Reusing a span across cases would correlate two 'independent' measurements."""
    spans = [span for case in gold for span in case.required_spans]
    assert len(set(spans)) == len(spans)


def test_the_hash_is_stable_and_order_sensitive(gold):
    assert gold_set_hash(gold) == gold_set_hash(load_gold_set())
    assert gold_set_hash(gold) != gold_set_hash(list(reversed(gold)))


def test_a_round_trip_through_the_file_is_lossless(gold, tmp_path):
    path = tmp_path / "gold.jsonl"
    dump_gold_set(gold, path)

    assert load_gold_set(path) == gold
    assert gold_set_hash(load_gold_set(path)) == gold_set_hash(gold)


def test_the_file_on_disk_is_one_json_object_per_line():
    lines = FIXTURE_GOLD_SET_PATH.read_text(encoding="utf-8").splitlines()
    assert lines
    for line in lines:
        assert isinstance(json.loads(line), dict)


# ── the loader refuses a gold set that would measure nothing ─────────────────

def _write(tmp_path, record):
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")
    return path


BASE = {
    "id": "g-001",
    "query": "q?",
    "doc_id": "d.pdf",
    "answer_span": "a verbatim sentence",
    "kind": "handwritten",
    "provenance": "test",
}


def test_a_missing_field_fails_the_load(tmp_path):
    record = {k: v for k, v in BASE.items() if k != "answer_span"}
    with pytest.raises(ValueError, match="answer_span"):
        load_gold_set(_write(tmp_path, record))


def test_an_over_long_span_fails_the_load(tmp_path):
    record = {**BASE, "answer_span": " ".join(["word"] * (MAX_SPAN_WORDS + 1))}
    with pytest.raises(ValueError, match="word limit|over the"):
        load_gold_set(_write(tmp_path, record))


def test_an_answerable_case_with_no_span_fails_the_load(tmp_path):
    with pytest.raises(ValueError, match="must carry an answer span"):
        load_gold_set(_write(tmp_path, {**BASE, "answer_span": ""}))


def test_an_unanswerable_case_carrying_a_span_fails_the_load(tmp_path):
    record = {**BASE, "kind": "unanswerable"}
    with pytest.raises(ValueError, match="cannot carry an answer span"):
        load_gold_set(_write(tmp_path, record))


def test_a_duplicate_id_fails_the_load(tmp_path):
    path = tmp_path / "dup.jsonl"
    path.write_text(json.dumps(BASE) + "\n" + json.dumps(BASE) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_gold_set(path)


# ── the hit rule ─────────────────────────────────────────────────────────────

def test_a_hit_is_the_same_containment_check_a_citation_uses():
    assert is_hit("prefix. A VERBATIM  sentence! suffix", "a verbatim sentence")
    assert not is_hit("a different sentence", "a verbatim sentence")


def test_hit_ranks_are_one_based_and_per_span():
    case = GoldCase(
        id="g-x",
        query="q",
        doc_id="d.pdf",
        answer_span="alpha one",
        kind=GoldKind.MULTI_HOP,
        provenance="test",
        also_requires=("beta two",),
    )
    ranks = hit_ranks(case, ["nothing", "alpha one here", "beta two here", "alpha one"])

    assert ranks == ((2, 4), (3,))


def test_the_hit_rule_is_chunking_agnostic():
    """The whole point: the same span grades a fixed window and a structural chunk.

    The two arms cut the document differently, so their chunk ids share nothing — but
    both chunks contain the sentence, and both are hits.
    """
    span = "the official poverty rate in 2022 was 11.5 percent"
    fixed_window = (
        "...significant changes in their poverty rates between 2021 and 2022. "
        "The official poverty rate in 2022 was 11.5 percent, with 37.9 million people..."
    )
    structural = (
        "[Poverty in the United States: 2022 · report · 2023-09-12 · CHANGES]\n"
        "The official poverty rate in 2022 was 11.5 percent, with 37.9 million people "
        "in poverty (Figure 1 and Table A-1)."
    )

    assert is_hit(fixed_window, span)
    assert is_hit(structural, span)
