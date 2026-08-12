"""Backend shim: guard result types now live in ``aegis.core.types``.

These models are deliberately tiny and dependency-free so they can be imported
anywhere (the agent graph, the API layer, tests) without pulling in the LLM
gateway or NeMo Guardrails. :class:`GuardResult` is the shared contract named in
``docs/module/MODULE_REFERENCE.md`` and is what :func:`app.guardrails.check_input` /
:func:`app.guardrails.check_output` return. Re-exported here, unchanged, from
``aegis.core.types`` so this module and the ``aegis`` package can never define
diverging copies.
"""

from __future__ import annotations

from aegis.core.types import FormatCheck, GuardResult, InjectionVerdict, PIIMatch

__all__ = ["FormatCheck", "GuardResult", "InjectionVerdict", "PIIMatch"]
