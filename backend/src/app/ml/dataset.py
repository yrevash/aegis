"""Strangler shim: re-export training-data acquisition from ``aegis.ml``.

:func:`synthesise_frame` / :func:`resolve_training_frame` now live in
:mod:`aegis.ml.dataset`. This module keeps ``app.ml.dataset`` importable for
any existing caller.
"""

from __future__ import annotations

from aegis.ml.dataset import resolve_training_frame, synthesise_frame

__all__ = ["resolve_training_frame", "synthesise_frame"]
