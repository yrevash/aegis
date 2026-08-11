"""Backend shim: the memory READ path now lives in :mod:`aegis.memory.recall`.

The package's :func:`recall` resolves the domain seam from the process-wide
:class:`~aegis.memory.spec.MemorySpec` default, which the backend configures to
``app.adapter.memory_spec`` in :mod:`app.memory` (this package's ``__init__``), so call
sites keep passing no ``spec``.
"""

from __future__ import annotations

from aegis.memory.recall import RecallBundle, load_raw_window, recall

__all__ = ["RecallBundle", "load_raw_window", "recall"]
