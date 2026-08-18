"""Host wiring for ingestion — the upload, the stages, and when the parser is loaded.

The parsing itself lives in :mod:`aegis.ingestion` and the chunking in
:mod:`aegis.retrieval.chunker`, neither of which knows anything about this application:
no settings, no orchestrator, no event loop. What is left over is the host's, and it is
this package:

* :mod:`app.ingestion.upload` — ``POST /documents``: store the bytes, deduplicate on
  them, admit the job against the tenant's budget, start the workflow.
* :mod:`app.ingestion.store` — where those bytes live, content-addressed and partitioned
  by tenant, and where the parse artifact is written beside them.
* :mod:`app.ingestion.artifacts` — the codec that carries a parsed document from the
  ``parse`` stage to the ``chunk`` stage.
* :mod:`app.ingestion.stages` — the six stage handlers, and the registration a process
  entry point calls to bind them to Phase 3's substrate.

The rest of this module is the two host decisions about the *parser*, and they are both
here.

**Which process pays for the models.** Docling's layout and table models are ~730 MB on
disk and hold a little under a gigabyte resident once loaded. Only a process that
actually runs the ``parse`` stage should carry that, which is the worker serving the CPU
queue — never an API-only process and never a test worker. So the warm-up is gated on a
setting a deployment turns on deliberately, rather than on "docling happens to be
importable".

**When they are loaded (D4).** At worker startup, off the request path, in a thread —
because model loading is blocking, CPU-bound work and the event loop still has a worker
to start. Measured on an M3 with the model cache primed, that is ~3 s; on a machine whose
cache is cold it is a 730 MB download, which is the real risk D4 was written about and
the reason ``spikes/docling_spike.py --prefetch`` exists. Prime the cache on the demo box
while there is still network.

Best-effort, deliberately: a failed warm-up logs loudly and the worker starts anyway. The
first parse then pays the cost it would have paid without this module at all.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import get_settings

logger = logging.getLogger(__name__)

__all__ = ["warm_parser"]


async def warm_parser() -> float:
    """Load the Docling models now, if this deployment says it parses.

    Returns:
        Seconds spent warming; ``0.0`` when the warm-up was skipped or failed, so a
        caller can log the difference between "warmed" and "did not warm".
    """
    settings = get_settings()
    if not settings.docling_warm_on_start:
        logger.info(
            "Docling warm-up skipped: DOCLING_WARM_ON_START is off, so the first parse "
            "in this process will load the models itself."
        )
        return 0.0
    try:
        from aegis.ingestion import warm_converter  # noqa: PLC0415 - optional extra

        return await asyncio.to_thread(warm_converter)
    except Exception:  # noqa: BLE001 - a worker that cannot warm must still start
        logger.warning(
            "Docling warm-up failed; the parse stage will load its models on first use "
            "(or fail there, visibly).",
            exc_info=True,
        )
        return 0.0
