"""Second-stage reranking on a **local ONNX cross-encoder**, API reranker behind it.

## Why this exists, and what it replaces

`reranker.py` was locked to API-only on the stated grounds that *"the deploy target is a
16 GB, no-GPU machine"*. That premise was wrong, not merely conservative: `fastembed`'s
:class:`~fastembed.rerank.cross_encoder.TextCrossEncoder` runs a cross-encoder over
**onnxruntime**, which needs no GPU and pulls no torch, and the model this module ships is
33M parameters / ~130 MB on disk. A machine that can hold Neo4j's JVM can hold that.

The value, **external result first and labelled as external**: +12.1 pp recall@5 and
+17.2 pp MRR@3 is the published T2-RAGBench anchor for a cross-encoder of this class, worth
roughly 5.5x what per-chunk LLM enrichment is worth there — on their corpus, not ours.

**Our own ablation says something narrower and more useful.** Arms A3 → A4 (the only
difference is this reranker) over the 53-case gold set move **recall@6 by +0.009** — one
case — because both arms are handed the same 20-candidate pool and a reranker cannot
retrieve what recall missed; it can only reorder. What it moves is the *order*: **MRR@20
0.557 → 0.686, +12.9 pp**, and nDCG@10 0.622 → 0.732
(``runs/eval-goldset-20260819.json``). That is the claim to make — the answer moves toward
rank 1, which is the passage the generator reads first and the citation a human checks —
and it is not the same claim as "+12.1 pp recall". Running it locally also removes the
per-query gateway call the LLM reranker made, which is real money against a fixed credit
budget, and makes the ordering **deterministic**, which is what lets an eval be re-run and
compared at all.

## What it costs, measured — and it is not what the phase doc predicted

Reranking is the one stage of ingestion+retrieval that sits on the **query clock**, so it is
the one number this phase measures rather than assumes. Measured on a 16 GB M3, over a
20-candidate pool of real 400-word chunks (`RetrievalConfig.chunk_size`), it is **1.44 s p50
/ 1.55 s p95** — not the 150-400 ms D6 estimated. The estimate was not wrong about the model; it was
wrong about the **passage**. Cross-encoder cost is linear in total sequence length, so a
figure taken from ~60-word retrieval passages under-reads our 400-word chunks by roughly 4x.
The constant that travels between machines is **~72 ms per 400-word passage**, so a pool of
``recall_top_k`` costs ``72 ms x recall_top_k`` and ``recall_top_k`` is the honest lever if a
slower box needs one. Full numbers in ``docs/dev_new_docs_v2/phase-04-ingestion.md`` §D6;
reproduce with ``spikes/rerank_bench.py``.

That is a real second on the clock and it is stated rather than buried — but it is not a
second **added**, because it replaces an LLM call that graded the same twenty passages
(~12k prompt tokens through the gateway) and was neither faster nor free nor deterministic.

## The fallback is loud, and it is never "no reranking"

A local model is a new failure class in the serving path: the weights can be missing on a box
with no network, the runtime can fail to start, an ONNX session can die mid-query. When that
happens this module logs at **ERROR** and falls back to the API reranker in
:mod:`aegis.retrieval.reranker` — never to the fused RRF order. Dropping the stage silently
would cost 12 pp of recall@5 and say nothing, which is precisely the class of defect the rest
of this system spends its time removing. Which engine actually ran is reported out on
``RerankOutcome.engine`` and lands in ``observability.rerank.engine``.

## Ownership

The encoder is a **collaborator, injected** — :class:`~aegis.retrieval.pipeline.Retriever`
holds one or holds ``None``; it never reaches into a global. Construction is free (no import
of ``fastembed``, no file touched); the model is loaded on first use, or eagerly by a host
that would rather pay the load at boot than on the first question (:meth:`
LocalCrossEncoderReranker.load`).
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aegis.core.lazy import require
from aegis.core.models import ModelRole
from aegis.retrieval.models import Candidate
from aegis.retrieval.protocols import CompleteFn
from aegis.retrieval.reranker import RerankOutcome, rerank_scored

_log = logging.getLogger(__name__)

#: The shipped cross-encoder: 33M parameters, ~130 MB of ONNX weights, 8K context, Apache-2.0.
#: Chosen over every alternative `fastembed` exposes, and measured rather than assumed — on
#: the same 20-passage pool of 400-word chunks (all four at `fastembed`'s default batch of
#: 64, so they are comparable to each other) it is also the **fastest**, which was not the
#: expected result:
#:
#: * ``jinaai/jina-reranker-v1-tiny-en`` — 1.57 s p50 (this one)
#: * ``Xenova/ms-marco-MiniLM-L-6-v2`` — 1.88 s, and it caps at 512 input tokens, so a
#:   400-**word** chunk (~520 tokens) carrying an enriched prefix has its tail truncated
#:   silently: the last lines of a chunk become unrankable and nothing says so.
#: * ``jinaai/jina-reranker-v1-turbo-en`` — 2.38 s, +534 MB
#: * ``Xenova/ms-marco-MiniLM-L-12-v2`` — 3.69 s
#: * ``BAAI/bge-reranker-base`` / ``jinaai/jina-reranker-v2-base-multilingual`` — ~1.05 GB of
#:   weights, not benchmarked: both are eight times the size of a model that already costs a
#:   second and a half, and the v2 one is CC-BY-NC (non-commercial) besides.
#:
#: The 8K window here cannot truncate anything this pipeline produces.
#:
#: Pinned by name because a different reranker is a different answer order: an eval run under
#: one model is not comparable to a run under another.
DEFAULT_LOCAL_RERANK_MODEL = "jinaai/jina-reranker-v1-tiny-en"

#: Execution provider, stated rather than inferred. `fastembed` would otherwise pick whatever
#: onnxruntime build happens to be installed — and `docling[models-onnxruntime]` installs
#: onnxruntime-GPU on Windows, so "whatever is installed" is not a constant across our two
#: target machines.
_CPU_PROVIDER = "CPUExecutionProvider"


def _is_under_temp(path: Path) -> bool:
    """Whether ``path`` sits inside the system temp directory.

    Args:
        path: The resolved model-cache directory.

    Returns:
        ``True`` if the weights are somewhere a reboot may delete.
    """
    try:
        return path.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())
    except OSError:  # an unresolvable path is not a temp path we can warn about
        return False


@dataclass
class LocalCrossEncoderReranker:
    """A cross-encoder reranker running locally on onnxruntime.

    Scores each ``(query, passage)`` pair jointly — which is what makes a cross-encoder
    better than the bi-encoder that produced the recall vectors, and also what makes it
    O(pool) rather than O(1) per query. Hence the pool cap: this reorders the ~20 fused
    candidates, never the corpus.

    The instance is cheap to construct and safe to share across threads and event loops: the
    model loads once under a lock, and onnxruntime sessions are themselves thread-safe.

    Attributes:
        model_name: The `fastembed` cross-encoder to load.
        cache_dir: Where the ONNX weights are cached. ``None`` uses `fastembed`'s default,
            which is ``FASTEMBED_CACHE_PATH`` if set and otherwise
            ``<system temp>/fastembed_cache`` — a directory a reboot may empty, so
            :meth:`load` warns when the weights end up there.
        threads: onnxruntime intra-op thread count. ``None`` lets the runtime decide, which
            measured fastest (1.5 s vs 3.7 s pinned to one thread on an 8-core M3); a host
            running several workers on one box may still want to pin it.
        batch_size: How many (query, passage) pairs go through the ONNX session at once.
            **4, not `fastembed`'s default of 64**, and the reason is memory rather than
            speed: measured on a 20-passage pool of 400-word chunks, batch 64 settles at
            **867 MB** RSS against **470 MB** at batch 4, because onnxruntime's CPU arena
            sizes itself to the largest batch it ever saw and does not give it back. Latency
            is unchanged (1.39 s vs 1.47 s, batch 4 marginally *ahead*) and the scores are
            identical to 1e-7, so the 400 MB is bought for nothing. On a 16 GB box also
            holding Neo4j's JVM that is the difference that matters.
    """

    model_name: str = DEFAULT_LOCAL_RERANK_MODEL
    cache_dir: str | None = None
    threads: int | None = None
    batch_size: int = 4
    _encoder: Any = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    @property
    def loaded(self) -> bool:
        """Whether the ONNX weights are resident in this process."""
        return self._encoder is not None

    def load(self) -> None:
        """Load the model, once. Idempotent and thread-safe.

        Hosts may call this at boot to move the (small, but non-zero) load off the first
        user's question. Nothing calls it implicitly except :meth:`rerank`.

        Warns (at WARNING) when the weights live in the system temp directory, which is
        `fastembed`'s default and is a real demo-day trap: ``/tmp`` is cleared on reboot on
        most Linux boxes, so the first query after a restart re-downloads 134 MB — and on a
        venue with no network it does not re-download at all, it fails to the API reranker.
        Setting ``FASTEMBED_CACHE_PATH`` (or ``cache_dir``) to somewhere durable is a
        one-line fix that nobody makes if nothing says it out loud.

        Raises:
            ImportError: If ``fastembed`` is not installed (names ``aegis[retrieval]``).
            Exception: Whatever `fastembed`/onnxruntime raise on a missing or corrupt model —
                deliberately not swallowed here. The caller
                (:func:`rerank_scored_local_first`) is the layer that decides to fall back,
                and it says so at ERROR when it does.
        """
        if self._encoder is not None:
            return
        with self._lock:
            if self._encoder is not None:  # another thread won the race
                return
            module = require("aegis[retrieval]", "fastembed.rerank.cross_encoder")
            encoder = module.TextCrossEncoder(
                model_name=self.model_name,
                cache_dir=self.cache_dir,
                threads=self.threads,
                providers=[_CPU_PROVIDER],
            )
            self._encoder = encoder
            cache = self._resolved_cache_dir()
            _log.info(
                "local cross-encoder reranker loaded: model=%s provider=%s cache=%s",
                self.model_name,
                _CPU_PROVIDER,
                cache,
            )
            if _is_under_temp(cache):
                _log.warning(
                    "the reranker's ONNX weights are cached under the system temp directory "
                    "(%s), which is cleared on reboot on most machines. Set "
                    "FASTEMBED_CACHE_PATH to a durable path, or the first query after a "
                    "restart re-downloads ~134 MB — and falls back to the API reranker if "
                    "there is no network.",
                    cache,
                )

    def _resolved_cache_dir(self) -> Path:
        """Return the directory `fastembed` will actually read the weights from.

        Mirrors ``fastembed.common.utils.define_cache_dir``: an explicit ``cache_dir``, else
        ``FASTEMBED_CACHE_PATH``, else ``<system temp>/fastembed_cache``.

        Returns:
            The resolved cache directory.
        """
        if self.cache_dir:
            return Path(self.cache_dir)
        override = os.getenv("FASTEMBED_CACHE_PATH")
        if override:
            return Path(override)
        return Path(tempfile.gettempdir()) / "fastembed_cache"

    def score(self, query: str, texts: list[str]) -> list[float]:
        """Score every ``(query, text)`` pair, loading the model if needed.

        Args:
            query: The user query.
            texts: Candidate passages, in any order.

        Returns:
            One raw relevance logit per text, positionally aligned with ``texts``. The scale
            is the model's own (roughly -11..+11 for this checkpoint) and is **not**
            normalised — an unnormalised score that is genuinely the model's beats a
            prettier number that is not.
        """
        self.load()
        return list(self._encoder.rerank(query, texts, batch_size=self.batch_size))

    def rerank(
        self, query: str, candidates: list[Candidate], *, top_k: int
    ) -> RerankOutcome:
        """Reorder ``candidates`` by cross-encoder relevance and keep the best ``top_k``.

        Synchronous and CPU-bound by design — :func:`rerank_scored_local_first` is what puts
        it on a worker thread so it does not block the event loop.

        Args:
            query: The user query.
            candidates: The fused wide-recall pool.
            top_k: How many survivors to keep.

        Returns:
            A :class:`~aegis.retrieval.reranker.RerankOutcome` whose survivors carry the
            model's scores. Every candidate is graded — a cross-encoder has no partial
            outcome the way a JSON-parsing LLM does — so ``graded`` is ``True`` and
            ``ungraded`` is ``0`` whenever anything survives.
        """
        if not candidates or top_k <= 0:
            return RerankOutcome(candidates=[], graded=False, ungraded=0, engine="local")
        scores = self.score(query, [c.text for c in candidates])
        ranked = sorted(
            zip(scores, range(len(candidates)), strict=True),
            key=lambda pair: (pair[0], -pair[1]),
            reverse=True,
        )
        out = [
            candidates[idx].model_copy(update={"score": float(score)})
            for score, idx in ranked[:top_k]
        ]
        return RerankOutcome(candidates=out, graded=True, ungraded=0, engine="local")


async def rerank_scored_local_first(
    query: str,
    candidates: list[Candidate],
    *,
    complete: CompleteFn,
    top_k: int,
    local: LocalCrossEncoderReranker | None = None,
    role: ModelRole = ModelRole.CHEAP,
) -> RerankOutcome:
    """Rerank on the local cross-encoder, falling **loudly** back to the API reranker.

    The two failure modes are separated because they read differently in a log: a host that
    passed ``local=None`` chose the API reranker, and that is not an incident; a local model
    that was configured and then failed **is** one, and it is logged at ERROR with the
    traceback. Neither path returns the fused RRF order — that would be the retrieval stage
    quietly switching itself off.

    Args:
        query: The user query.
        candidates: The fused wide-recall pool.
        complete: The injected chat-completion function, used only by the fallback.
        top_k: How many survivors to keep.
        local: The local cross-encoder, or ``None`` to go straight to the API reranker.
        role: Model role for the API fallback.

    Returns:
        A :class:`~aegis.retrieval.reranker.RerankOutcome` whose ``engine`` says which
        reranker actually produced the order, and whose ``reason`` carries the local failure
        when there was one.
    """
    if local is None:
        return await rerank_scored(
            query, candidates, complete=complete, top_k=top_k, role=role
        )
    try:
        return await asyncio.to_thread(local.rerank, query, candidates, top_k=top_k)
    except Exception as exc:  # noqa: BLE001 — any local failure must reach the fallback
        _log.error(
            "local cross-encoder reranker failed (model=%s); falling back to the API "
            "reranker. Retrieval is NOT running unranked.",
            local.model_name,
            exc_info=True,
        )
        outcome = await rerank_scored(
            query, candidates, complete=complete, top_k=top_k, role=role
        )
        local_failure = (
            f"local reranker failed ({type(exc).__name__}: {exc}); used the API reranker"
        )
        reason = f"{local_failure}; {outcome.reason}" if outcome.reason else local_failure
        return RerankOutcome(
            candidates=outcome.candidates,
            graded=outcome.graded,
            ungraded=outcome.ungraded,
            reason=reason,
            engine="api",
        )
