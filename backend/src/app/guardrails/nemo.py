"""Backend shim: NeMo Guardrails wiring now lives in ``aegis.guardrails.nemo``.

Re-exports the package's public surface (the config directory, the Colang
refusal constants, and the engine lifecycle / check functions) so existing
importers (``app.guardrails.nemo``) keep working unchanged, including
``config_path()`` — now the bundled ``aegis.guardrails.config`` directory, so the
Colang policy has exactly one copy instead of two divergent ones.

``nemoguardrails`` stays an optional, lazily-imported dependency throughout —
importing this module never requires it (see :mod:`aegis.guardrails.nemo`).
"""

from __future__ import annotations

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
    register_actions,
    reset_engine,
)

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
