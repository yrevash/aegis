"""NeMo Guardrails config package (the Colang security-policy artifact).

This directory is a standard NeMo Guardrails configuration:
``config.yml`` (rails + models), ``prompts.yml`` (optional native self-checks),
``rails/*.co`` (the Colang flows), and ``actions.py`` (custom Python actions).

It is loaded via :func:`aegis.guardrails.nemo.load_rails_config`. Kept as a package
so :mod:`aegis.guardrails.nemo` can import :mod:`actions` for explicit
registration; the module is import-light on purpose (no top-level imports that
would pull in ``nemoguardrails``).
"""

from __future__ import annotations
