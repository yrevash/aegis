"""The ablation runner: it runs end to end, and it *is* the shipped retrieval path.

No PDFs and no model weights here — a synthetic corpus, the deterministic offline
embedder and a fake reranker, so the mechanics are provable in milliseconds. The run over
the real fixtures lives in ``scripts/eval_goldset.py``; what this file protects is that
the thing that run drives is not a lookalike of the pipeline.
"""

from __future__ import annotations

import pytest

from aegis.evals.ablation import (
    ABLATION_ARMS,
    AblationRun,
    Chunking,
    Signal,
    compare,
    markdown_table,
)
from aegis.evals.goldset import GoldCase, GoldKind
from aegis.retrieval.cache import SemanticCache
from aegis.retrieval.graph_extract import NoOpExtractor
from aegis.retrieval.memory import (
    InMemoryKnowledgeBackend,
    InMemoryRedis,
    _local_embed,
)
from aegis.retrieval.models import Candidate, Chunk
from aegis.retrieval.pipeline import RetrievalConfig, Retriever
from aegis.retrieval.reranker import RerankOutcome
from aegis.retrieval.types import RetrievalScope

SCOPE = RetrievalScope(tenant_id=None, persona="eval")

#: Sentences the gold cases are anchored to, each in its own document.
FACTS = {
    "encoder": "The encoder is composed of a stack of six identical layers.",
    "poverty": "The official poverty rate in 2022 was 11.5 percent nationwide.",
    "deduction": "The standard deduction for married filing jointly is thirty one thousand.",
}

FILLER = [
    "Recurrent models factor computation along the symbol positions of the sequence.",
    "Poverty thresholds are updated each year for inflation by the Census Bureau.",
    "Taxpayers may request an automatic extension of time to file their return.",
    "Attention weights are computed with a softmax over compatibility scores.",
    "Noncash benefits are counted as resources under the supplemental measure.",
    "Private delivery services are designated by the agency for timely filing.",
]


def _corpus(prefix: str) -> list[Chunk]:
    """Build one corpus whose chunk ids are unique to ``prefix``.

    Different chunk ids per strategy is the whole reason the gold set anchors to spans:
    nothing here could be graded by id across two of these corpora.
    """
    texts = [*FACTS.values(), *FILLER]
    return [
        Chunk(id=f"{prefix}#{i}", doc_id=f"doc-{i % 3}", ordinal=i, text=text)
        for i, text in enumerate(texts)
    ]


CASES = (
    GoldCase("g-001", "How many layers are in the encoder?", "doc-0",
             FACTS["encoder"], GoldKind.HANDWRITTEN, "test"),
    GoldCase("g-002", "What was the official poverty rate in 2022?", "doc-1",
             FACTS["poverty"], GoldKind.HANDWRITTEN, "test"),
    GoldCase("g-003", "What is the standard deduction?", "doc-2",
             FACTS["deduction"], GoldKind.KNOWN_ITEM, "test"),
    GoldCase("g-004", "What is the capital of Peru?", "", "",
             GoldKind.UNANSWERABLE, "test"),
)


class _ReverseReranker:
    """A deterministic fake: reverses the pool. Enough to prove reranking is applied."""

    def rerank(
        self, query: str, candidates: list[Candidate], *, top_k: int
    ) -> RerankOutcome:
        ordered = list(reversed(candidates))[:top_k]
        return RerankOutcome(candidates=ordered, engine="fake", graded=True, ungraded=0)


async def _embed(texts: list[str]) -> list[list[float]]:
    return [_local_embed(text) for text in texts]


@pytest.fixture
def backends():
    return {
        kind: InMemoryKnowledgeBackend(
            _corpus(str(kind)), embed=_embed, extractor=NoOpExtractor()
        )
        for kind in Chunking
    }


@pytest.fixture
def run(backends):
    return AblationRun(
        backends=backends,
        chunk_counts={kind: len(backends[kind]._chunks) for kind in Chunking},
        scope=SCOPE,
        reranker=_ReverseReranker(),
    )


async def test_the_ablation_runs_every_arm_and_produces_the_table(run):
    results = [await run.run(arm, CASES) for arm in ABLATION_ARMS]

    assert [r.arm.name for r in results] == ["A0", "A1", "A2", "A3", "A4", "L1", "L2"]
    for result in results:
        # The unanswerable case is not retrieval-gradeable and must not be scored.
        assert [o.case_id for o in result.outcomes] == ["g-001", "g-002", "g-003"]
        assert result.chunks == len(FACTS) + len(FILLER)
        for outcome in result.outcomes:
            assert 0.0 <= outcome.recall_6 <= outcome.recall_20 <= 1.0

    table = markdown_table(results)
    for name in ("A0", "A1", "A2", "A3", "A4", "L1", "L2"):
        assert f"**{name}**" in table
    assert "recall@20" in table and "n = 3" in table


async def test_the_arms_actually_find_the_answers(run):
    """The apparatus is only meaningful if it can score above zero on a solvable set."""
    shipped = await run.run(ABLATION_ARMS[4], CASES)
    assert shipped.mean("recall_20") == 1.0


async def test_reranking_reorders_the_pool_and_cannot_add_to_it(run):
    """A4's recall@20 is A3's by construction — the property the whole gap argument rests on."""
    a3 = await run.run(ABLATION_ARMS[3], CASES)
    a4 = await run.run(ABLATION_ARMS[4], CASES)

    assert a3.scores("recall_20") == a4.scores("recall_20")
    # …and the reverse reranker genuinely changed the order inside that pool.
    assert a3.scores("mrr_20") != a4.scores("mrr_20")


async def test_an_arm_that_reranks_with_no_reranker_fails_loudly(backends):
    run = AblationRun(
        backends=backends,
        chunk_counts=dict.fromkeys(Chunking, 0),
        scope=SCOPE,
        reranker=None,
    )
    with pytest.raises(RuntimeError, match="refusing to report an un-reranked pool"):
        await run.run(ABLATION_ARMS[4], CASES)


async def test_the_leave_one_out_probes_drop_exactly_one_arm():
    by_name = {arm.name: arm for arm in ABLATION_ARMS}
    assert by_name["A4"].signals - by_name["L1"].signals == {Signal.GRAPH}
    assert by_name["A4"].signals - by_name["L2"].signals == {Signal.BM25}
    assert by_name["L1"].rerank and by_name["L2"].rerank


async def test_compare_refuses_two_arms_that_did_not_answer_the_same_questions(run):
    a3 = await run.run(ABLATION_ARMS[3], CASES)
    partial = await run.run(ABLATION_ARMS[4], CASES[:2])

    with pytest.raises(ValueError, match="same cases in the same order"):
        compare(a3, partial, metric="recall_6", seed=1)


async def test_compare_reports_the_discordant_pairs(run):
    a0 = await run.run(ABLATION_ARMS[0], CASES)
    a4 = await run.run(ABLATION_ARMS[4], CASES)
    comparison = compare(a0, a4, metric="recall_6", seed=20260830)

    assert comparison.baseline == "A0"
    assert comparison.arm == "A4"
    assert comparison.favouring_arm + comparison.favouring_baseline <= len(a0.outcomes)
    assert 0.0 <= comparison.p_value <= 1.0


async def test_arm_a3_is_the_shipped_retrieval_path(backends):
    """The bridge: A3's ordering must equal what ``Retriever.retrieve`` returns.

    A3 is hybrid recall with reranking off, which is exactly ``RetrievalConfig``'s
    ``rerank_enabled=False`` over the same backend. If the arm runner ever drifts from the
    pipeline — a different fusion, a different arm, a different k — this fails, and every
    number the ablation prints stops being a claim about the shipped system.
    """
    backend = backends[Chunking.STRUCTURAL_PREFIXED]
    config = RetrievalConfig(rerank_enabled=False, recall_top_k=20, final_top_k=6)
    retriever = Retriever(
        backend=backend,
        cache=SemanticCache(InMemoryRedis(), similarity_threshold=0.99),
        complete=None,
        embed=_embed,
        config=config,
    )
    run = AblationRun(
        backends=backends,
        chunk_counts=dict.fromkeys(Chunking, 0),
        scope=SCOPE,
        reranker=_ReverseReranker(),
    )

    for case in CASES:
        shipped = await retriever.retrieve(case.query, scope=SCOPE)
        arm_texts = await run._ranked_texts(ABLATION_ARMS[3], case.query)

        assert [source.text for source in shipped.sources] == arm_texts[: config.final_top_k]
        assert shipped.num_candidates == len(arm_texts)
