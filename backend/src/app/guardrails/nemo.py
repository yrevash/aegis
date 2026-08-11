"""Backend shim: NeMo Guardrails wiring now lives in ``aegis.guardrails.nemo``.

Re-exports the package's public surface (the config directory, the Colang
refusal constants, and the engine lifecycle / check functions) so existing
importers (``app.guardrails.nemo``) keep working unchanged, including
``config_path()`` — now the bundled ``aegis.guardrails.config`` directory, so the
Colang policy has exactly one copy instead of two divergent ones.

One correction is layered on top. aegis's bundled Colang policy
(``aegis.guardrails.config.rails/*.co``) calls a custom action,
``self_check_injection`` (``aegis.guardrails.config.actions``), which calls
``aegis.guardrails.classifier.detect_injection`` with **no** ``completer`` — a
required keyword-only parameter upstream — so every real invocation of the
Colang input rail raises ``TypeError`` and the engine falls back to a generic
"internal error" turn instead of enforcing the policy (verified empirically: the
rail silently fails open). This shim re-registers a corrected
``self_check_injection`` action, once, after aegis builds the engine,
reproducing the exact same policy (redact PII, then the deterministic + model
injection layers) with the platform's cheap-model completer threaded through —
without modifying the ``aegis`` package itself.

``nemoguardrails`` stays an optional, lazily-imported dependency throughout —
importing this module never requires it (see :mod:`aegis.guardrails.nemo`).
"""

from __future__ import annotations

from typing import Any

import aegis.guardrails.nemo as _aegis_nemo
from aegis.guardrails.classifier import detect_injection
from aegis.guardrails.nemo import (
    _INPUT_REFUSAL,
    _OUTPUT_REFUSAL,
    build_rails,
    config_path,
    get_engine,
    load_rails_config,
    nemo_available,
    nemo_check_input,
    nemo_check_output,
    reset_engine,
)
from aegis.guardrails.pii import redact as _redact_pii

_orig_register_actions = _aegis_nemo.register_actions


async def _self_check_injection(context: dict | None = None) -> bool:
    """Corrected replacement for aegis's bundled ``self_check_injection`` action.

    Args:
        context: The NeMo conversation context (``user_message`` is read).

    Returns:
        ``True`` when safe to proceed, ``False`` to block. Fails closed.
    """
    from app.guardrails import _gateway_completer

    text = (context or {}).get("user_message", "")
    redacted, _ = _redact_pii(text)
    verdict = await detect_injection(redacted, completer=_gateway_completer)
    return not verdict.injection


def register_actions(rails: Any) -> None:  # noqa: ANN401 - nemoguardrails.LLMRails, optional dep
    """Register aegis's bundled actions, then override the broken injection one.

    Args:
        rails: An ``nemoguardrails.LLMRails`` instance.
    """
    _orig_register_actions(rails)
    rails.register_action(_self_check_injection, name="self_check_injection")


# Patch aegis's module-global so ITS OWN ``get_engine()`` — which
# ``nemo_check_input``/``nemo_check_output`` (re-exported above) call
# internally — picks up the corrected action. Confined to the NeMo action
# table; the programmatic rail (``app.guardrails.check_input``/``check_output``)
# is untouched by this.
_aegis_nemo.register_actions = register_actions


__all__ = [
    "_INPUT_REFUSAL",
    "_OUTPUT_REFUSAL",
    "build_rails",
    "config_path",
    "get_engine",
    "load_rails_config",
    "nemo_available",
    "nemo_check_input",
    "nemo_check_output",
    "register_actions",
    "reset_engine",
]
