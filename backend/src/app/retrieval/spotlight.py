"""Backend shim: Azure Spotlighting now lives in ``aegis.retrieval.spotlight``."""

from __future__ import annotations

from aegis.retrieval.spotlight import (
    DATAMARK_TOKEN,
    build_spotlighted_context,
    datamark,
    spotlight,
    spotlight_system_instruction,
)

__all__ = [
    "DATAMARK_TOKEN",
    "build_spotlighted_context",
    "datamark",
    "spotlight",
    "spotlight_system_instruction",
]
