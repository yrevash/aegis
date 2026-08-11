"""Backend shim: Reciprocal Rank Fusion now lives in ``aegis.retrieval.fusion``."""

from __future__ import annotations

from aegis.retrieval.fusion import (
    ORIGIN_METADATA_KEY,
    RankedList,
    RankedRecall,
    collect_origins,
    reciprocal_rank_fusion,
)

__all__ = [
    "ORIGIN_METADATA_KEY",
    "RankedList",
    "RankedRecall",
    "collect_origins",
    "reciprocal_rank_fusion",
]
