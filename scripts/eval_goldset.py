"""Task 4.11 — run the span-anchored gold set through every ablation arm.

Produces the table that goes on the slide, plus a JSON artifact that pins everything
needed to re-derive it: the git sha, the embedder and reranker model ids, a content hash
of each corpus, the gold set's hash, the bootstrap seed, every arm's configuration, and
every case's ranks. **Reproducibility is a property of the artifact, not of the code** — a
run that cannot be re-derived from its own JSON does not go on a slide.

Run from the repo root, with the backend venv (which is where `docling` and `fastembed`
live)::

    AEGIS_DOCLING_SLOW_FIXTURES=1 PYTHONPATH=aegis/src \\
      backend/.venv/bin/python scripts/eval_goldset.py --out runs/eval.json

The first run parses all four fixtures (~10 minutes, dominated by the 126-page IRS
document) and caches the parses under ``--parse-cache`` so every later run is seconds of
embedding plus the reranker's real per-query second. Without
``AEGIS_DOCLING_SLOW_FIXTURES=1`` only the two small papers are used, which is a smoke
test of the apparatus and **not** a result: it is labelled as such in the artifact.

**No gateway call is made anywhere in this script.** The embedder and the reranker are
both local ONNX models, so the run costs $0.00 and is deterministic — which is what makes
two runs comparable at all.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import pickle  # a local parse cache this script wrote itself
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date
from pathlib import Path

from aegis.evals.ablation import (
    ABLATION_ARMS,
    AblationRun,
    ArmResult,
    Chunking,
    compare,
    markdown_table,
)
from aegis.evals.goldset import GoldCase, gold_set_hash, load_gold_set
from aegis.ingestion import ParsedDocument, parse_pdf, probe_page_text
from aegis.retrieval.chunker import DocumentContext, chunk_sections, chunk_text
from aegis.retrieval.graph_extract import NoOpExtractor
from aegis.retrieval.local_reranker import (
    DEFAULT_LOCAL_RERANK_MODEL,
    LocalCrossEncoderReranker,
)
from aegis.retrieval.memory import InMemoryKnowledgeBackend
from aegis.retrieval.models import Chunk
from aegis.retrieval.protocols import EmbedFn
from aegis.retrieval.types import RetrievalScope

REPO = Path(__file__).resolve().parents[1]
PDF_DIR = REPO / "tests" / "fixtures" / "pdfs"

#: The local bi-encoder every arm embeds with — the same model for A0 as for A4, which is
#: the first of the two rules that keep the baseline honest. 384 dimensions, 33M
#: parameters, ONNX on CPU, no torch and no network after the first download.
EMBED_MODEL = "BAAI/bge-small-en-v1.5"

#: Chunk geometry, identical in every arm including the naive one.
CHUNK_SIZE = 400
CHUNK_OVERLAP = 60

#: What the uploader would have supplied at ``POST /documents``. ``doc_date`` is left
#: ``None`` for the IRS instructions on purpose: that document names a tax *year*, not an
#: issue date, and stamping one on it would be inventing precision the prefix would then
#: embed into every chunk.
DOCUMENTS: dict[str, tuple[str, date | None]] = {
    "transformer-single-column.pdf": ("research paper", date(2017, 6, 12)),
    "bert-two-column.pdf": ("research paper", date(2019, 5, 24)),
    "census-income-tables.pdf": ("government statistical report", date(2023, 9, 12)),
    "irs-1040-instructions-tables.pdf": ("tax form instructions", None),
}

#: The two documents that parse in seconds. The other two are minutes, hence the gate.
FAST_FIXTURES = ("transformer-single-column.pdf", "bert-two-column.pdf")


def _git_sha() -> str:
    """Return the current commit, or ``"unknown"`` outside a checkout."""
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip()


def _load_parse(name: str, cache: Path) -> tuple[ParsedDocument, tuple[str, ...]]:
    """Parse ``name``, or read it back from the on-disk cache.

    Args:
        name: Fixture file name.
        cache: Directory holding cached parses.

    Returns:
        ``(parsed document, per-page text layer)``.
    """
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{name}.pkl"
    if target.exists():
        with target.open("rb") as handle:
            payload = pickle.load(handle)
        return payload["doc"], tuple(payload["pages"])
    started = time.perf_counter()
    document = parse_pdf(PDF_DIR / name)
    pages = probe_page_text(PDF_DIR / name)
    with target.open("wb") as handle:
        pickle.dump({"doc": document, "pages": pages}, handle)
    print(f"  parsed {name}: {document.page_count}p in {time.perf_counter()-started:.0f}s")
    return document, pages


def _naive_chunks(name: str, pages: Sequence[str]) -> list[Chunk]:
    """Cut the raw text layer into fixed word windows — arm A0's corpus.

    This is the whole of the naive baseline's ingestion: no layout model, no headings, no
    tables, no prefix. It keeps the shipped overlap (see :mod:`aegis.evals.ablation` for
    why the baseline is given that advantage).

    Args:
        name: The document id.
        pages: The per-page text layer.

    Returns:
        The chunks.
    """
    windows = chunk_text(
        "\n".join(pages), chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP
    )
    return [
        Chunk(id=f"{name}#naive-{i}", doc_id=name, ordinal=i, text=text)
        for i, text in enumerate(windows)
    ]


def _structural_chunks(
    name: str, document: ParsedDocument, *, prefixed: bool
) -> list[Chunk]:
    """Pack the parsed document with ``chunk_sections`` — arms A1 (bare) and A2+ (prefixed).

    Args:
        name: The document id.
        document: The layout-aware parse.
        prefixed: Whether the D7 prefix is folded into the embedded text.

    Returns:
        The chunks.
    """
    doc_type, doc_date = DOCUMENTS[name]
    context = DocumentContext(
        title=DocumentContext.from_parsed(document).title,
        doc_type=doc_type,
        doc_date=doc_date,
    )
    pieces = chunk_sections(
        document, context=context, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP
    )
    return [
        Chunk(
            id=f"{name}#{'ctx' if prefixed else 'sec'}-{piece.ordinal}",
            doc_id=name,
            ordinal=piece.ordinal,
            text=piece.contextualized() if prefixed else piece.text,
            metadata={"section": piece.section},
        )
        for piece in pieces
    ]


def _corpus_hash(chunks: Sequence[Chunk]) -> str:
    """Content hash over a corpus, so an artifact pins the exact text that was searched."""
    digest = hashlib.sha256()
    for chunk in chunks:
        digest.update(chunk.id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(chunk.text.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _build_embedder(model_name: str) -> EmbedFn:
    """Return an async embedder over a local ONNX bi-encoder.

    Args:
        model_name: The `fastembed` model id.

    Returns:
        An ``EmbedFn`` — the *same* one handed to every arm.
    """
    # Imported here rather than at module scope: heavy, and only this run needs it.
    from fastembed import TextEmbedding

    encoder = TextEmbedding(model_name=model_name, providers=["CPUExecutionProvider"])

    async def embed(texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in encoder.embed(texts)]

    return embed


async def main() -> int:
    """Run the ablation and write the artifact. Returns a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=REPO / "runs" / "eval-goldset.json")
    parser.add_argument("--parse-cache", type=Path, default=REPO / ".parse-cache")
    parser.add_argument("--seed", type=int, default=20260830)
    parser.add_argument("--embed-model", default=EMBED_MODEL)
    parser.add_argument("--rerank-model", default=DEFAULT_LOCAL_RERANK_MODEL)
    args = parser.parse_args()

    full = bool(os.environ.get("AEGIS_DOCLING_SLOW_FIXTURES"))
    fixtures = tuple(DOCUMENTS) if full else FAST_FIXTURES
    missing = [name for name in fixtures if not (PDF_DIR / name).exists()]
    if missing:
        print(f"missing fixtures: {missing} — run {PDF_DIR}/fetch.sh", file=sys.stderr)
        return 2

    cases: tuple[GoldCase, ...] = tuple(
        case for case in load_gold_set() if not case.doc_id or case.doc_id in fixtures
    )
    gradeable = [case for case in cases if case.gradeable]
    print(f"gold set: {len(gradeable)} gradeable cases over {len(fixtures)} documents")

    print("parsing…")
    corpora: dict[Chunking, list[Chunk]] = {k: [] for k in Chunking}
    for name in fixtures:
        document, pages = _load_parse(name, args.parse_cache)
        corpora[Chunking.NAIVE_WINDOWS] += _naive_chunks(name, pages)
        corpora[Chunking.STRUCTURAL] += _structural_chunks(name, document, prefixed=False)
        corpora[Chunking.STRUCTURAL_PREFIXED] += _structural_chunks(
            name, document, prefixed=True
        )
    for kind, chunks in corpora.items():
        print(f"  {kind}: {len(chunks)} chunks")

    embed = _build_embedder(args.embed_model)
    scope = RetrievalScope(tenant_id=None, persona="eval")
    backends: dict[Chunking, InMemoryKnowledgeBackend] = {}
    for kind, chunks in corpora.items():
        started = time.perf_counter()
        backend = InMemoryKnowledgeBackend(
            list(chunks), embed=embed, extractor=NoOpExtractor()
        )
        await backend.recall_ranked("warm the index", top_k=1, scope=scope)
        backends[kind] = backend
        print(f"  indexed {kind} in {time.perf_counter()-started:.0f}s")

    reranker = LocalCrossEncoderReranker(model_name=args.rerank_model)
    reranker.load()
    run = AblationRun(
        backends=backends,
        chunk_counts={kind: len(chunks) for kind, chunks in corpora.items()},
        scope=scope,
        reranker=reranker,
    )

    results: list[ArmResult] = []
    for arm in ABLATION_ARMS:
        result = await run.run(arm, cases)
        results.append(result)
        print(
            f"  {arm.name}: recall@20={result.mean('recall_20'):.3f} "
            f"recall@6={result.mean('recall_6'):.3f} "
            f"MRR@20={result.mean('mrr_20'):.3f} ({result.seconds:.0f}s)"
        )

    by_name = {result.arm.name: result for result in results}
    ladder = [("A0", "A1"), ("A1", "A2"), ("A2", "A3"), ("A3", "A4"), ("A0", "A4")]
    probes = [("L1", "A4"), ("L2", "A4")]
    comparisons = [
        compare(by_name[a], by_name[b], metric=metric, seed=args.seed)
        for a, b in [*ladder, *probes]
        for metric in ("recall_20", "recall_6")
    ]

    table = markdown_table(results)
    print("\n" + table)
    for comparison in comparisons:
        print(
            f"{comparison.baseline}→{comparison.arm} {comparison.metric}: "
            f"Δ={comparison.delta:+.3f} "
            f"(95% CI {comparison.interval.low:+.3f} to {comparison.interval.high:+.3f}; "
            f"{comparison.favouring_arm + comparison.favouring_baseline} discordant, "
            f"{comparison.favouring_arm} favouring {comparison.arm}; "
            f"p={comparison.p_value:.4f})"
        )

    artifact = {
        "kind": "aegis.phase4.ablation",
        "complete_corpus": full,
        "note": (
            "every fixture" if full
            else "SMOKE RUN — two small fixtures only; not a result"
        ),
        "git_sha": _git_sha(),
        "embedding_model": args.embed_model,
        "rerank_model": args.rerank_model,
        "recall_top_k": run.recall_top_k,
        "final_top_k": run.final_top_k,
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
        "bootstrap_seed": args.seed,
        "gold_set_hash": gold_set_hash(cases),
        "gold_cases": len(gradeable),
        "gold_kinds": {
            kind: sum(1 for case in cases if str(case.kind) == kind)
            for kind in sorted({str(case.kind) for case in cases})
        },
        "documents": list(fixtures),
        "corpora": {
            str(kind): {"chunks": len(chunks), "sha256": _corpus_hash(chunks)}
            for kind, chunks in corpora.items()
        },
        "spend_usd": 0.0,
        "arms": [
            {
                "name": result.arm.name,
                "label": result.arm.label,
                "chunking": str(result.arm.chunking),
                "signals": sorted(str(s) for s in result.arm.signals),
                "rerank": result.arm.rerank,
                "chunks": result.chunks,
                "seconds": round(result.seconds, 2),
                "means": {
                    metric: result.mean(metric)
                    for metric in (
                        "recall_20", "recall_6", "precision_6", "mrr_20", "ndcg_10"
                    )
                },
                "wilson95": {
                    metric: [result.interval(metric).low, result.interval(metric).high]
                    for metric in ("recall_20", "recall_6")
                },
                "cases": [asdict(outcome) for outcome in result.outcomes],
            }
            for result in results
        ],
        "comparisons": [
            {
                **{k: v for k, v in asdict(comparison).items() if k != "interval"},
                "ci95": [comparison.interval.low, comparison.interval.high],
                "significant": comparison.significant,
            }
            for comparison in comparisons
        ],
        "table_markdown": table,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(artifact, indent=2), encoding="utf-8")
    print(f"\nartifact: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
