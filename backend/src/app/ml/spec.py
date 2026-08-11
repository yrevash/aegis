"""Strangler shim: re-export the ML spec contract from ``aegis.ml``.

The spine's contract (``MLSpec`` protocol, ``ResolvedSpec``, ``TaskType``,
``FALLBACK_SPEC``) lives in :mod:`aegis.ml.spec` now, adapter-free. This module
keeps ``app.ml.spec`` importable for any existing caller and preserves the
legacy no-argument ``resolve_spec()`` behaviour: defensively probing
``app.adapter.ml_spec`` before falling back, so callers such as
``python -m app.ml`` keep working unchanged.
"""

from __future__ import annotations

from aegis.ml.spec import FALLBACK_SPEC, MLSpec, ResolvedSpec, TaskType
from aegis.ml.spec import resolve_spec as _resolve_spec

__all__ = ["FALLBACK_SPEC", "MLSpec", "ResolvedSpec", "TaskType", "resolve_spec"]


def resolve_spec(spec: MLSpec | None = None) -> ResolvedSpec:
    """Resolve the active ML spec, defensively importing the domain adapter's.

    Resolution order:

    1. An explicit ``spec`` argument (used by tests and callers).
    2. ``app.adapter.ml_spec`` if it exists and exposes a feature/target contract.
    3. :data:`FALLBACK_SPEC` (via :func:`aegis.ml.spec.resolve_spec`).

    Args:
        spec: Optional explicit spec object satisfying :class:`MLSpec`.

    Returns:
        A validated :class:`ResolvedSpec` ready for training/inference.
    """
    if spec is None:
        try:
            from app.adapter import ml_spec as spec  # type: ignore[no-redef]
        except (ImportError, AttributeError):
            spec = None
    return _resolve_spec(spec)
