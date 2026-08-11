"""Backend shim: working-memory assembly now lives in :mod:`aegis.memory.working`.

Re-exports the public API plus the section-header constants some tests assert on. The
package's :func:`assemble_working_memory` resolves the domain seam from the process-wide
:class:`~aegis.memory.spec.MemorySpec` default configured in :mod:`app.memory`.
"""

from __future__ import annotations

from aegis.memory.working import (
    _EPISODIC_HEADER,
    _FACTS_HEADER,
    _PROFILE_HEADER,
    _RAW_HEADER,
    _SKILLS_HEADER,
    _SUMMARY_HEADER,
    AssembledMemory,
    assemble_working_memory,
    build_working_text,
)

__all__ = [
    "AssembledMemory",
    "assemble_working_memory",
    "build_working_text",
    "_EPISODIC_HEADER",
    "_FACTS_HEADER",
    "_PROFILE_HEADER",
    "_RAW_HEADER",
    "_SKILLS_HEADER",
    "_SUMMARY_HEADER",
]
