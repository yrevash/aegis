"""Tiny, dependency-free vector helpers.

Kept separate so the semantic cache and any similarity logic share one implementation
without pulling numpy into the import path of the always-loaded modules.
"""

from __future__ import annotations

import math
from collections.abc import Sequence


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Return the cosine similarity of two equal-length vectors.

    Args:
        a: First vector.
        b: Second vector.

    Returns:
        Cosine similarity in ``[-1.0, 1.0]``; ``0.0`` if either vector is zero-length or
        has zero magnitude.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))
