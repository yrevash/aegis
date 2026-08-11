"""Aegis — modular, importable agentic-AI platform components.

Import only what you need: ``from aegis.guardrails import Guardrails``. The
:mod:`aegis.core` package is dependency-free (pydantic + stdlib); each component
declares its own optional dependencies as an extra (``pip install aegis[nemo]``).
"""

from __future__ import annotations

__version__ = "0.1.0"
__all__ = ["__version__"]
