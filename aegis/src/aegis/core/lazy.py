"""Fail-loud optional imports.

The single sanctioned way to reach an optional dependency. A missing module
raises an :class:`ImportError` naming the exact ``pip install`` command — never a
silent ``except ImportError: pass``.
"""

from __future__ import annotations

import importlib
from types import ModuleType


def require(extra: str, module: str) -> ModuleType:
    """Import ``module`` or raise an ImportError telling the user how to install it.

    Args:
        extra: The install target to suggest, e.g. ``"aegis[nemo]"``.
        module: The importable module name, e.g. ``"nemoguardrails"``.

    Returns:
        The imported module.

    Raises:
        ImportError: If ``module`` cannot be imported.
    """
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise ImportError(
            f"This feature needs '{module}'. Run: pip install {extra}"
        ) from exc
