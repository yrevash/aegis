"""The platform actually wires the local cross-encoder, and `RERANK_LOCAL` actually works.

The package proves the reranker reorders (``aegis/tests/retrieval/test_local_reranker.py``).
What is only provable here is that **this deployment reaches it**: a composition root that
quietly forgets to pass ``local_reranker`` would leave every production query on the API
reranker — 12 pp of recall@5 gone, with nothing in the logs, because falling back to the API
reranker is a legitimate configuration and looks like one.

No model is loaded by any of this: constructing the encoder touches no file, and these tests
assert on the wiring rather than on a rerank.
"""

from __future__ import annotations

from aegis.retrieval.local_reranker import (
    DEFAULT_LOCAL_RERANK_MODEL,
    LocalCrossEncoderReranker,
)

from app.config import Settings
from app.retrieval.memory import build_lite_retriever
from app.retrieval.pipeline import _config_from_settings


def test_settings_default_to_the_local_reranker():
    # The default posture is the good one; running on the API reranker is opt-in.
    assert Settings().rerank_local is True
    assert _config_from_settings(Settings()).local_rerank_enabled is True


def test_rerank_local_env_switch_reaches_the_retrieval_config():
    config = _config_from_settings(Settings(rerank_local=False))
    assert config.local_rerank_enabled is False
    # …and it is a demotion to the API reranker, NOT reranking switched off.
    assert config.rerank_enabled is True


def test_lite_mode_still_reranks_locally():
    # STORES=off drops the databases. It must not quietly drop retrieval quality with them.
    retriever = build_lite_retriever(Settings(stores="off"))
    assert isinstance(retriever.local_reranker, LocalCrossEncoderReranker)
    assert retriever.local_reranker.model_name == DEFAULT_LOCAL_RERANK_MODEL
    assert retriever.local_reranker.loaded is False  # nothing loaded just by building


def test_lite_mode_honours_the_kill_switch():
    retriever = build_lite_retriever(Settings(stores="off", rerank_local=False))
    assert retriever.local_reranker is None
    assert retriever.config.rerank_enabled is True
