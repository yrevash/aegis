"""Platform surfaces — the live "what are we running / what does it save" layer.

Four read endpoints (``/stack``, ``/stack/patch-check``, ``/risk-map``, ``/savings``)
are backed here, deliberately honest throughout.

Everything here is deliberately real and honest:

* :mod:`~app.platform.stack` inventories **actually-installed** package versions via
  :func:`importlib.metadata.version` (null when a package is not installed — honest
  for optional-group dependencies), mapped to the branded Aegis module it powers.
* :mod:`~app.platform.patches` compares those installed versions against a **live**
  PyPI registry query and — when the network is unavailable — refuses to fabricate a
  clean bill of health, returning ``online=False`` with every status ``unknown``.
* :mod:`~app.platform.risk_map` is a typed data module grounded verbatim in
  ``docs/SECURITY_OWASP_AGENTIC.md``: each OWASP-Top-10-for-Agentic risk with its real
  Aegis mitigation and a ``control_ref`` pointing at a real file.
* :mod:`~app.platform.savings` derives the baseline-vs-actual spend from the real
  gateway usage ledger and says plainly which parts are measured vs estimated.

The package is import-light (no heavy runtime dependency) so the API layer and tests
can import it freely.
"""

from __future__ import annotations

from app.platform.patches import (
    RegistryUnreachableError,
    patch_check,
    tracked_packages,
)
from app.platform.risk_map import build_risk_map
from app.platform.savings import build_savings
from app.platform.stack import build_stack

__all__ = [
    "RegistryUnreachableError",
    "build_risk_map",
    "build_savings",
    "build_stack",
    "patch_check",
    "tracked_packages",
]
