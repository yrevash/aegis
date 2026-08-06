"""Lazy resolvers for the shared LLM gateway (`app.core.llm`).

`app.core.llm` is built by another agent in parallel and may not be importable yet, and
must never be imported at module load time here (it would break unit tests, which mock
the gateway). These thin resolvers import it **only when first called at runtime**, so
the retrieval package imports cleanly with or without the gateway present.
"""

from __future__ import annotations

from .protocols import CompleteFn, EmbedFn


def default_complete() -> CompleteFn:
    """Return the process-wide `complete` function from `app.core.llm` (lazy import)."""
    from app.core.llm import complete

    return complete


def default_embed() -> EmbedFn:
    """Return the process-wide `embed` function from `app.core.llm` (lazy import)."""
    from app.core.llm import embed

    return embed
