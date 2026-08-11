"""Strangler shim: ``app.ops.diagnose`` delegates to :mod:`aegis.ops.diagnose`.

The Diagnose stage (cluster failing evals → write an improved-prompt DRAFT) now lives in
the standalone ``aegis.ops`` package; re-exported here under its historical names. The
prompt *floor* (adapter default when no active version exists) is injected into
``aegis.ops`` once at :mod:`app.ops` import via ``configure_ops``.
"""

from __future__ import annotations

from aegis.ops.diagnose import DiagnoseResult, diagnose

__all__ = ["DiagnoseResult", "diagnose"]
