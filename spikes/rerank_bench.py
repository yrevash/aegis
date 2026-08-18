"""Task 4.9 — measure the local cross-encoder reranker on this machine.

Reranking is the one stage of Phase 4 that sits on the **query clock**: everything else is
paid once at ingest, this is paid on every question a jury asks. D6 says so and says to
benchmark rather than assume, so this script produces the four numbers that decide whether
the model is shippable on a given box:

* **load** — cold (weights fetched over the network) and warm (weights already cached), so a
  demo machine can be primed while there is still network.
* **latency** — p50/p95 to rerank a pool of ``recall_top_k`` passages of realistic length.
  This is the number that lands in front of a user.
* **peak RSS** — the delta the model adds to a process that is already holding Neo4j's
  driver, Chroma and a gateway client.
* **ordering** — a smoke check that the thing actually reranks, because a model that loads,
  answers in 70 ms and returns the input order is the failure this benchmark would otherwise
  miss entirely.

Run (from the repo root, with the backend venv)::

    PYTHONPATH=aegis/src backend/.venv/bin/python spikes/rerank_bench.py
    PYTHONPATH=aegis/src backend/.venv/bin/python spikes/rerank_bench.py --cold
    PYTHONPATH=aegis/src backend/.venv/bin/python spikes/rerank_bench.py --model Xenova/ms-marco-MiniLM-L-6-v2

``--cold`` downloads into a fresh temporary directory and reports the wall clock and the
on-disk size — the "first run on the demo box, with the venue wifi" number. It leaves the
shared cache alone.
"""

from __future__ import annotations

import argparse
import resource
import statistics
import sys
import tempfile
import time
from pathlib import Path

from aegis.retrieval.local_reranker import (
    DEFAULT_LOCAL_RERANK_MODEL,
    LocalCrossEncoderReranker,
)
from aegis.retrieval.models import Candidate
from aegis.retrieval.pipeline import RetrievalConfig

#: The filler a passage is built from. `RetrievalConfig.chunk_size` is 400 **words**, and
#: cross-encoder latency is driven by total sequence length, so benchmarking on one-line toy
#: documents reports a number four times too optimistic — which is exactly how D6 got its
#: 150-400 ms estimate.
_FILLER = (
    "The supplier shall maintain records of every shipment for a period of seven years "
    "from the date of delivery, and shall make those records available for inspection on "
    "reasonable notice. Records include the bill of lading, the customs declaration, the "
    "certificate of origin and any inspection reports produced at the point of dispatch. "
) * 7

_QUERY = "what does clause 7.3.2 say about the refund window"

#: The passage that should win. Deliberately placed LAST in the input order, so a run that
#: reports it first has genuinely reordered rather than passed the list through.
_ANSWER = (
    "Clause 7.3.2 (Refunds). A customer may request a refund within thirty (30) calendar "
    "days of delivery. Refunds are issued to the original payment method within ten "
    "business days of approval. " + _FILLER
)


def peak_rss_mb() -> float:
    """Return this process's peak resident set size in MB.

    Returns:
        The peak RSS. ``ru_maxrss`` is bytes on macOS and kilobytes on Linux, so the value
        is normalised by platform rather than reported in whichever unit the OS chose.
    """
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 1e6 if sys.platform == "darwin" else peak / 1e3


def dir_size_mb(path: Path) -> float:
    """Return the total size of every file under ``path``, in MB.

    Symlinks are skipped: the HuggingFace cache stores each file once under ``blobs/`` and
    links it from ``snapshots/``, so following the links double-counts every weight and
    reports a model twice its real size.

    Args:
        path: Directory to measure.

    Returns:
        Size in MB (0.0 for a directory that does not exist).
    """
    if not path.exists():
        return 0.0
    return sum(
        f.stat().st_size
        for f in path.rglob("*")
        if f.is_file() and not f.is_symlink()
    ) / 1e6


def build_pool(size: int, words: int) -> list[Candidate]:
    """Build a realistic rerank pool: ``size`` passages of ``words`` words, answer last.

    Args:
        size: Pool size (use ``RetrievalConfig.recall_top_k`` for the real one).
        words: Words per passage (use ``RetrievalConfig.chunk_size`` for the real one).
            Latency is linear in this, which is the single most important thing the phase
            doc's original estimate got wrong.

    Returns:
        Candidates in "fused RRF" order, i.e. the answer is not first.
    """

    def clip(text: str) -> str:
        return " ".join(text.split()[:words])

    pool = [
        Candidate(id=f"c{i}", text=clip(f"Section {i + 1}. Shipping and inspection. {_FILLER}"))
        for i in range(size - 1)
    ]
    pool.append(Candidate(id="answer", text=clip(_ANSWER)))
    return pool


def main() -> int:
    """Run the benchmark and print the numbers.

    Returns:
        Process exit code: ``1`` if the model failed to reorder the pool, else ``0``.
    """
    config = RetrievalConfig()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_LOCAL_RERANK_MODEL)
    parser.add_argument("--pool", type=int, default=config.recall_top_k)
    parser.add_argument("--words", type=int, default=config.chunk_size)
    parser.add_argument("--batch", type=int, default=LocalCrossEncoderReranker.batch_size)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument(
        "--cold",
        action="store_true",
        help="download into an empty temp cache and report the first-run cost",
    )
    args = parser.parse_args()

    rss_before = peak_rss_mb()
    cache_dir = tempfile.mkdtemp(prefix="rerank-cold-") if args.cold else None

    t0 = time.perf_counter()
    reranker = LocalCrossEncoderReranker(
        model_name=args.model, cache_dir=cache_dir, batch_size=args.batch
    )
    reranker.load()
    load_s = time.perf_counter() - t0
    rss_after_load = peak_rss_mb()

    pool = build_pool(args.pool, args.words)
    chars = sum(len(c.text) for c in pool) / len(pool)

    outcome = reranker.rerank(_QUERY, pool, top_k=config.final_top_k)
    latencies_ms = []
    for _ in range(args.runs):
        start = time.perf_counter()
        reranker.rerank(_QUERY, pool, top_k=config.final_top_k)
        latencies_ms.append((time.perf_counter() - start) * 1000)
    latencies_ms.sort()

    def pct(p: float) -> float:
        return latencies_ms[min(len(latencies_ms) - 1, int(len(latencies_ms) * p))]

    print(f"model            {args.model}")
    print(f"pool             {args.pool} passages x {args.words} words (~{chars:.0f} chars each)")
    print(f"batch size       {args.batch}")
    print(f"{'cold' if args.cold else 'warm'} load        {load_s:.2f} s")
    if cache_dir:
        print(f"weights on disk  {dir_size_mb(Path(cache_dir)):.0f} MB ({cache_dir})")
    print(f"latency p50      {statistics.median(latencies_ms):.1f} ms  (n={args.runs})")
    print(f"latency p95      {pct(0.95):.1f} ms")
    print(f"latency min/max  {latencies_ms[0]:.1f} / {latencies_ms[-1]:.1f} ms")
    print(f"peak RSS @load   {rss_after_load:.0f} MB (+{rss_after_load - rss_before:.0f} MB)")
    print(f"peak RSS @end    {peak_rss_mb():.0f} MB")
    print(f"per-passage      {statistics.median(latencies_ms) / args.pool:.0f} ms")

    winner = outcome.candidates[0].id
    moved = [c.id for c in outcome.candidates]
    print(f"top-{config.final_top_k} order     {moved}")
    if winner != "answer":
        print(f"FAIL: the answer passage did not reach rank 1 (got {winner!r})")
        return 1
    print("reordering       OK — the answer moved from last place to rank 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
