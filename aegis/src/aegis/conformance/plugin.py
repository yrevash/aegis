"""The ``pytest11`` plugin: how ``pytest --pyargs aegis.conformance`` learns its adapter.

Registered as an entry point (``aegis/pyproject.toml``), so it is live in any
environment where ``aegis`` is installed — an integrator adds nothing to their
``conftest.py`` and copies no files. Being globally loaded, it is also deliberately
**inert**: it contributes one namespaced command-line option and one header line, and
does nothing at all to a pytest run that is not checking an adapter.

The header line is there because this suite is demonstrated on a screen. "13 checks
passed" is only evidence if the audience can see *which* adapter they passed against.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from aegis.conformance import ADAPTER_ENV_VAR, ADAPTER_OPTION

if TYPE_CHECKING:
    import pytest

__all__ = ["adapter_name", "pytest_addoption", "pytest_report_header"]

_DEST = "aegis_adapter"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register ``--aegis-adapter``, the import path of the adapter under check."""
    group = parser.getgroup("aegis", "aegis conformance")
    group.addoption(
        ADAPTER_OPTION,
        dest=_DEST,
        default=None,
        metavar="IMPORT_PATH",
        help=(
            "Import path of the domain adapter to check with `pytest --pyargs "
            f"aegis.conformance` (e.g. myapp.adapter). Overrides ${ADAPTER_ENV_VAR}."
        ),
    )


def adapter_name(config: pytest.Config) -> str | None:
    """Return the adapter import path this run was pointed at, if any.

    The command line wins over the environment, so a shell that exports
    ``AEGIS_ADAPTER`` for the running application can still be used to check a second
    adapter without unsetting anything.

    Args:
        config: The active pytest config.

    Returns:
        The adapter import path, or ``None`` when neither source named one.
    """
    chosen = config.getoption(_DEST, default=None) or os.environ.get(ADAPTER_ENV_VAR)
    return chosen.strip() if chosen else None


def pytest_report_header(config: pytest.Config) -> str | None:
    """Name the adapter under check in the pytest header (silent when there is none)."""
    chosen = adapter_name(config)
    return f"aegis conformance: checking adapter {chosen!r}" if chosen else None
