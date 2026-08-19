"""Task 4.9 — the local ONNX cross-encoder reranker, and its loud fallback.

These tests run the **real** model, not a stand-in: a mocked reranker would prove only that
we can sort a list we already sorted. The weights are read from the on-disk `fastembed`
cache with ``HF_HUB_OFFLINE=1``, so the suite never downloads 134 MB behind someone's back;
on a machine that has not fetched them the model tests skip with the command that fetches
them (``AEGIS_RERANK_MODEL_DOWNLOAD=1``), the same shape as the ingestion fixtures.

The failure test is real too. It asks for a model name `fastembed` does not support, which is
a genuine load failure raised by the library in 0.3 s and needs no network — not a patched
method pretending to fail.

What is proved here:

* the cross-encoder **actually reorders**: a passage the fused RRF order left below the cut
  comes back at rank 1, asserted on its content;
* a local failure lands on the **API reranker** and is logged at ERROR — and reranking still
  happened, rather than the stage quietly switching itself off;
* the knobs (``rerank_enabled``, ``local_rerank_enabled``) do what they say, and the
  composition roots wire the encoder so nobody has to remember to.
"""

from __future__ import annotations

import logging
import os

import pytest

from aegis.retrieval.cache import SemanticCache
from aegis.retrieval.local_reranker import (
    DEFAULT_LOCAL_RERANK_MODEL,
    LocalCrossEncoderReranker,
    rerank_scored_local_first,
)
from aegis.retrieval.memory import (
    InMemoryKnowledgeBackend,
    InMemoryRedis,
    _default_offline_embed,
    build_lite_retriever,
)
from aegis.retrieval.models import Candidate
from aegis.retrieval.pipeline import RetrievalConfig, Retriever, build_local_reranker
from aegis.retrieval.types import RetrievalScope

from .conftest import RecordingComplete

#: The unscoped (no tenant) partition these tests run under.
_SCOPE = RetrievalScope(tenant_id=None)

#: A model name `fastembed` genuinely does not support — a real load failure, offline.
_BROKEN_MODEL = "aegis-tests/no-such-cross-encoder"

_QUERY = "what does clause 7.3.2 say about the refund window"

#: The passage that answers ``_QUERY``. It sits LAST in the fused pool below, i.e. outside a
#: ``final_top_k=3`` cut, so a test that finds it at rank 1 has watched a real promotion.
_ANSWER_TEXT = (
    "Clause 7.3.2 (Refunds). A customer may request a refund within thirty (30) calendar "
    "days of delivery, and refunds are issued to the original payment method."
)

#: Distractors that a lexical/dense arm plausibly ranks above the answer: they share the
#: vocabulary ("clause", "refund", "delivery") without answering the question.
_DISTRACTORS = [
    ("shipping", "Shipping and delivery windows are agreed per order at the point of sale."),
    ("clause-index", "Clause 7.1, Clause 7.2, Clause 7.3 and Clause 7.4 concern payment."),
    ("canteen", "The staff canteen closes at six on weekdays and is shut at weekends."),
    ("warranty", "Warranty claims under clause 8.1 run for twelve months from delivery."),
    ("refund-policy-toc", "Table of contents: refunds, returns, delivery, warranty, audit."),
]


def _pool() -> list[Candidate]:
    """The fused RRF pool: five distractors first, the answer last (below the cut)."""
    pool = [Candidate(id=cid, text=text, score=0.02) for cid, text in _DISTRACTORS]
    pool.append(Candidate(id="refund-clause", text=_ANSWER_TEXT, score=0.01))
    return pool


@pytest.fixture(scope="module")
def encoder() -> LocalCrossEncoderReranker:
    """The real cross-encoder, loaded from the local `fastembed` cache.

    Loads with ``HF_HUB_OFFLINE=1`` so a test run can never start a 134 MB download by
    surprise (`fastembed` reads that variable per call, so setting it here is honoured).
    Set ``AEGIS_RERANK_MODEL_DOWNLOAD=1`` once to fetch the weights — measured at 7.8 s.

    Returns:
        A loaded :class:`LocalCrossEncoderReranker`.
    """
    reranker = LocalCrossEncoderReranker()
    if os.environ.get("AEGIS_RERANK_MODEL_DOWNLOAD"):
        reranker.load()
        return reranker
    previous = os.environ.get("HF_HUB_OFFLINE")
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        reranker.load()
    except Exception as exc:  # noqa: BLE001 — any load failure means "not cached here"
        pytest.skip(
            f"{DEFAULT_LOCAL_RERANK_MODEL} is not in the local fastembed cache ({exc}). "
            "Fetch it once with AEGIS_RERANK_MODEL_DOWNLOAD=1 (~134 MB, ~8 s)."
        )
    finally:
        if previous is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = previous
    return reranker


# ── it actually reranks ───────────────────────────────────────────────────────


def test_local_reranker_promotes_a_passage_from_below_the_cut(encoder):
    # The fused order puts the answering passage last — outside a top-3 cut, i.e. it would
    # never reach the generator without reranking. Asserted on CONTENT, not on a score: a
    # score assertion passes just as happily when the order never changed.
    pool = _pool()
    assert pool[-1].id == "refund-clause"
    assert "refund-clause" not in [c.id for c in pool[:3]]

    outcome = encoder.rerank(_QUERY, pool, top_k=3)

    assert outcome.candidates[0].id == "refund-clause"
    assert "thirty (30) calendar days" in outcome.candidates[0].text
    assert outcome.engine == "local"
    # A cross-encoder grades every pair; there is no partial outcome to launder.
    assert outcome.graded is True
    assert outcome.ungraded == 0
    assert len(outcome.candidates) == 3


def test_local_reranker_grades_the_answer_above_every_distractor(encoder):
    # Not just "first after sorting" — the raw scores separate the answer from the passages
    # that merely share its vocabulary.
    pool = _pool()
    scores = encoder.score(_QUERY, [c.text for c in pool])

    answer_score = scores[-1]
    assert answer_score == max(scores)
    assert answer_score > max(scores[:-1])


def test_local_reranker_needs_no_model_call(encoder):
    # The point of running locally: zero gateway calls, so the reranker costs nothing per
    # query and two eval runs are comparable.
    fake = RecordingComplete('{"scores": []}')
    outcome = encoder.rerank(_QUERY, _pool(), top_k=3)
    assert outcome.candidates[0].id == "refund-clause"
    assert fake.calls == []


async def test_local_first_uses_the_local_encoder_when_it_works(encoder):
    fake = RecordingComplete('{"scores": [{"id": 0, "score": 9}]}')

    outcome = await rerank_scored_local_first(
        _QUERY, _pool(), complete=fake, top_k=3, local=encoder
    )

    assert outcome.engine == "local"
    assert outcome.candidates[0].id == "refund-clause"
    assert fake.calls == []  # the API reranker was not consulted


# ── the fallback is loud, and it is never "no reranking" ──────────────────────


async def test_local_failure_falls_back_to_the_api_reranker_and_logs_error(caplog):
    # The API reranker grades candidate 5 (the answer) highest, so a successful fallback is
    # visible in the ORDER as well as in the engine label.
    graded = '{"scores": [{"id": 0, "score": 1}, {"id": 5, "score": 9}, {"id": 1, "score": 3}]}'
    fake = RecordingComplete(graded)
    broken = LocalCrossEncoderReranker(model_name=_BROKEN_MODEL)

    with caplog.at_level(logging.ERROR, logger="aegis.retrieval.local_reranker"):
        outcome = await rerank_scored_local_first(
            _QUERY, _pool(), complete=fake, top_k=3, local=broken
        )

    # 1. It fell back to the API reranker — which RAN, rather than the stage being skipped.
    assert outcome.engine == "api"
    assert len(fake.calls) == 1
    assert outcome.graded is True
    # 2. The order is the API reranker's grading, not the fused recall order.
    assert outcome.candidates[0].id == "refund-clause"
    assert "thirty (30) calendar days" in outcome.candidates[0].text
    assert [c.id for c in outcome.candidates] != [c.id for c in _pool()[:3]]
    # 3. It said so, at ERROR, naming the model.
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a silent local-reranker failure is the defect this test exists for"
    assert _BROKEN_MODEL in errors[0].getMessage()
    assert errors[0].exc_info is not None
    # 4. And the outcome carries the reason, so a caller reporting on retrieval can see it.
    assert outcome.reason is not None
    assert "local reranker failed" in outcome.reason


async def test_local_failure_does_not_degrade_to_the_unranked_fused_order(caplog):
    # The specific regression: falling back to "no rerank" costs 12 pp of recall@5 and looks
    # identical from the outside. If the API reranker cannot grade either, the outcome must
    # still say so rather than presenting RRF scores as relevance.
    fake = RecordingComplete("not json at all")
    broken = LocalCrossEncoderReranker(model_name=_BROKEN_MODEL)

    with caplog.at_level(logging.ERROR, logger="aegis.retrieval.local_reranker"):
        outcome = await rerank_scored_local_first(
            _QUERY, _pool(), complete=fake, top_k=3, local=broken
        )

    assert len(fake.calls) == 1  # the fallback was attempted, not skipped
    assert outcome.engine == "api"
    assert outcome.graded is False  # …and its failure is labelled, not laundered
    assert "local reranker failed" in (outcome.reason or "")
    assert "no usable scores" in (outcome.reason or "")


async def test_no_local_reranker_configured_is_not_an_error(caplog):
    # A host that never wired one CHOSE the API reranker. That is a configuration, not an
    # incident, and must not fill the log with ERRORs on every query.
    fake = RecordingComplete('{"scores": [{"id": 5, "score": 9}]}')

    with caplog.at_level(logging.ERROR, logger="aegis.retrieval.local_reranker"):
        outcome = await rerank_scored_local_first(
            _QUERY, _pool(), complete=fake, top_k=3, local=None
        )

    assert outcome.engine == "api"
    assert len(fake.calls) == 1
    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []


# ── the knobs, through the real query path ───────────────────────────────────


def _retriever(config: RetrievalConfig, local: LocalCrossEncoderReranker | None) -> Retriever:
    """A databaseless retriever over the rerank fixtures (offline embedder + :memory: Qdrant)."""
    docs = [(cid, text) for cid, text in _DISTRACTORS]
    docs.append(("refund-clause", _ANSWER_TEXT))
    backend = InMemoryKnowledgeBackend.from_corpus(docs=docs)
    return Retriever(
        backend=backend,
        cache=SemanticCache(InMemoryRedis(), ttl_seconds=60, similarity_threshold=0.985),
        complete=RecordingComplete('{"scores": [{"id": 0, "score": 9}]}'),
        embed=_default_offline_embed,
        config=config,
        local_reranker=local,
    )


async def test_retrieve_reranks_locally_and_reports_the_engine(encoder):
    config = RetrievalConfig(recall_top_k=8, final_top_k=3)
    retriever = _retriever(config, encoder)

    result = await retriever.retrieve(_QUERY, scope=_SCOPE)

    assert result.observability.rerank.ran is True
    assert result.observability.rerank.engine == "local"
    assert result.observability.rerank.graded is True
    # The answering passage came back FIRST — asserted on content. (The assembled context is
    # datamarked, so it is checked on a token the spotlighting does not split.)
    assert result.sources[0].id.startswith("refund-clause")
    assert "thirty (30) calendar days" in result.sources[0].text
    assert "Refunds" in result.answer_context
    # No gateway call was spent on reranking.
    assert retriever.complete.calls == []


async def test_rerank_disabled_skips_the_stage_cleanly(encoder):
    # The knob still means what it always meant: no rerank at all, fused order kept, and the
    # local model is never even loaded. The query path must not break on the way past.
    config = RetrievalConfig(recall_top_k=8, final_top_k=3, rerank_enabled=False)
    unloaded = LocalCrossEncoderReranker()
    retriever = _retriever(config, unloaded)

    result = await retriever.retrieve(_QUERY, scope=_SCOPE)

    assert result.observability.rerank.ran is False
    assert result.observability.rerank.engine == "none"
    assert result.observability.rerank.graded is False
    assert result.sources, "skipping rerank must not empty the result"
    assert len(result.sources) <= config.final_top_k
    # Honest scores: these are RRF scores, and they are small positive floats, not grades.
    assert max(result.observability.rerank.top_scores) < 1.0
    assert unloaded.loaded is False
    assert retriever.complete.calls == []


async def test_local_rerank_kill_switch_demotes_to_the_api_reranker(encoder):
    # RERANK_LOCAL=false: reranking still happens, on the API reranker, and the local model
    # is not loaded. It is a demotion, not an off switch.
    config = RetrievalConfig(recall_top_k=8, final_top_k=3, local_rerank_enabled=False)
    unloaded = LocalCrossEncoderReranker()
    retriever = _retriever(config, unloaded)

    result = await retriever.retrieve(_QUERY, scope=_SCOPE)

    assert result.observability.rerank.ran is True
    assert result.observability.rerank.engine == "api"
    assert unloaded.loaded is False
    assert len(retriever.complete.calls) == 1


# ── the composition roots wire it, so no host has to remember ────────────────


def test_build_local_reranker_honours_the_kill_switch():
    default = build_local_reranker(RetrievalConfig())
    assert isinstance(default, LocalCrossEncoderReranker)
    assert default.model_name == DEFAULT_LOCAL_RERANK_MODEL
    assert default.loaded is False  # construction is free; weights arrive on first use

    custom = build_local_reranker(RetrievalConfig(local_rerank_model="BAAI/bge-reranker-base"))
    assert custom is not None
    assert custom.model_name == "BAAI/bge-reranker-base"

    assert build_local_reranker(RetrievalConfig(local_rerank_enabled=False)) is None


def test_lite_retriever_reranks_locally_too(tmp_path):
    # Lite mode drops the databases, not the reranker: an answer demoed offline must be
    # ordered by the same model as an answer served in production.
    retriever = build_lite_retriever(
        complete=RecordingComplete("{}"),
        embed=_default_offline_embed,
        working_dir=str(tmp_path),
    )
    assert isinstance(retriever.local_reranker, LocalCrossEncoderReranker)
    assert retriever.local_reranker.model_name == DEFAULT_LOCAL_RERANK_MODEL
