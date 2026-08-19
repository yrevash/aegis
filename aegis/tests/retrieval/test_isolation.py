"""Test that importing aegis.retrieval pulls no heavy dependencies.

All of lightrag, neo4j, redis, pgvector, and asyncpg are lazy-imported inside
methods (`LightRAGBackend._ensure`, `SemanticCache.from_url`, `AnswerCache.from_url`),
so importing the package — including its heaviest module, `lightrag_backend` — must
never require any of them to be installed. This is what makes the whole test suite
pass without the `aegis[retrieval]` extra.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

_SRC = str(Path(__file__).resolve().parents[2] / "src")


def test_importing_retrieval_pulls_no_heavy_deps() -> None:
    """Verify importing aegis.retrieval does not pull heavy/optional-extra deps.

    Covers the platform megadeps (litellm/torch/langgraph/xgboost/fastapi) plus the
    retrieval extra's own heavy packages (lightrag/neo4j/redis/asyncpg/qdrant_client;
    pgvector is checked too, now that it has been dropped from the retrieval extra). The
    subprocess resolves ``aegis`` from the source tree via ``PYTHONPATH`` so the guard
    tests the real import graph deterministically, independent of editable-install
    state.
    """
    code = (
        "import sys; "
        "import aegis.retrieval; "
        "import aegis.retrieval.agentic; "
        "import aegis.retrieval.stream; "
        "import aegis.retrieval.memory; "
        "import aegis.retrieval.lightrag_backend; "
        "import aegis.retrieval.answer_cache; "
        "import aegis.retrieval.cache; "
        "import aegis.retrieval.vector_store; "
        "banned = {'litellm', 'torch', 'langgraph', 'xgboost', 'fastapi', "
        "'redis', 'nemoguardrails', 'lightrag', 'neo4j', 'asyncpg', 'pgvector', "
        "'qdrant_client'}; "
        "hit = banned & set(sys.modules); "
        "print('HIT', hit); assert not hit, hit"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": _SRC},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
