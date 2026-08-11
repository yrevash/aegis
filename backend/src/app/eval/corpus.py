"""Strangler shim: ``app.eval.corpus`` delegates to :mod:`aegis.evals.corpus`.

The seed corpus + labelled eval cases now live in the standalone, importable
``aegis.evals`` package; this module re-exports them under their historical names so every
``from app.eval.corpus import SEED_CASES`` call site is unchanged.
"""

from __future__ import annotations

from aegis.evals.corpus import (
    SEED_CASES,
    SEED_CORPUS,
    EvalCase,
    SeedDoc,
    corpus_chunks,
)

__all__ = [
    "SEED_CASES",
    "SEED_CORPUS",
    "EvalCase",
    "SeedDoc",
    "corpus_chunks",
]
