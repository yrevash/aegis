"""Two seed documents that share one id."""

from __future__ import annotations

from app.adapter.corpus import load_seed_corpus as _load_reference_corpus


def load_seed_corpus() -> list:
    docs = _load_reference_corpus()[:2]
    docs[1] = docs[1].model_copy(update={"id": docs[0].id})
    return docs
