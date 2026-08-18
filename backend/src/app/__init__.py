"""TAIF S2 agentic platform — the domain-agnostic backend core.

The stable core (``api``, ``agent``, ``retrieval``, ``ml``, ``guardrails``,
``data``, ``observability``) is problem-independent; only ``adapter`` changes
when the hackathon problem is revealed. See ``docs/architecture/backend.md``.

**One process-wide setting, made here because it must happen before anything else
loads.** This deployment holds two OpenMP runtimes: ``torch`` ships its own
``libomp.dylib`` (pulled in by Docling for ingestion, and imported opportunistically by
``presidio-analyzer``'s device detector whenever it is installed), and ``xgboost`` /
``scikit-learn`` ship another. Loading both into one process on macOS/arm64 breaks —
**measured 2026-08-18**: with torch loaded first, an xgboost fit **segfaults** inside
``set_label``; with xgboost first, the next ``torch`` operation **deadlocks**. Neither is
catchable, and this application puts both in one process (the ML spine is warmed in the
lifespan; the ingest worker runs in-process by default).

``OMP_NUM_THREADS=1`` is the only value that fixes it — 2, 4 and 8 all still segfault —
and ``KMP_DUPLICATE_LIB_OK=TRUE`` does not help. It costs +5% on a Docling parse and, on
the small models this platform fits, is *faster* (the aegis ML suite: 12.1 s -> 5.2 s).
``setdefault``, so a deployment that has chosen a value keeps it.
"""

from __future__ import annotations

import os

os.environ.setdefault("OMP_NUM_THREADS", "1")
