"""Strangler shim: re-export the trustworthy-ML model from ``aegis.ml``.

``TrustworthyModel`` (ensemble + MAPIE conformal + SHAP) now lives in
:mod:`aegis.ml.model`. This module keeps ``app.ml.model`` importable — for any
existing caller and for the module-availability check in
:mod:`app.capabilities` (``module_path="app.ml.model"``).
"""

from __future__ import annotations

from aegis.ml.model import DEFAULT_ARTIFACT_PATH, TrustworthyModel

__all__ = ["DEFAULT_ARTIFACT_PATH", "TrustworthyModel"]
