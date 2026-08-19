"""Fixtures for the conformance run: the adapter under check, and the pieces it exposes.

The adapter is imported **once per session**. Every check reads it; none of them mutate
it, and none of them call anything that reaches a database, a queue or a model — see
each check's docstring for exactly what it touches.

This file also carries the fallback registration of ``--aegis-adapter``, for the case
where ``aegis`` is on ``PYTHONPATH`` rather than installed and the ``pytest11`` entry
point therefore is not live. Without it, that run would die on "unrecognized argument"
— which is a confusing first impression of a suite whose whole job is to make wiring
mistakes legible.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from aegis.conformance import AdapterNotSelectedError, load_adapter, plugin

if TYPE_CHECKING:
    from collections.abc import Callable

#: The ``pytest11`` entry-point name declared in ``aegis/pyproject.toml``.
PLUGIN_NAME = "aegis_conformance"


def pytest_addoption(parser: pytest.Parser, pluginmanager: pytest.PytestPluginManager) -> None:
    """Register ``--aegis-adapter`` unless the installed entry-point plugin already did.

    The option is added directly rather than by registering :mod:`~aegis.conformance.plugin`
    here: ``pytest_addoption`` is not re-called for a plugin registered while it is
    running, so a late registration would leave the option undefined.
    """
    if pluginmanager.hasplugin(PLUGIN_NAME):
        return
    plugin.pytest_addoption(parser)


def pytest_report_header(config: pytest.Config) -> str | None:
    """Name the adapter under check when the entry-point plugin is not there to do it."""
    if config.pluginmanager.hasplugin(PLUGIN_NAME):
        return None
    return plugin.pytest_report_header(config)


def _import_adapter(config: pytest.Config) -> object:
    """Import the adapter this run was pointed at, as a usage error when it cannot be.

    Args:
        config: The active pytest config.

    Returns:
        The imported adapter module.

    Raises:
        UsageError: If no adapter was named, or the one named could not be imported.
    """
    try:
        return load_adapter(plugin.adapter_name(config))
    except AdapterNotSelectedError as exc:
        raise pytest.UsageError(str(exc)) from exc


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Resolve the adapter as soon as conformance checks are collected.

    Deliberately here rather than only in the fixture: a fixture that cannot resolve the
    adapter reports the same "you did not say which adapter" message once per check,
    and thirteen copies of one instruction is a worse first impression than one. Raised
    during collection, it is a single ``ERROR:`` line and a non-zero exit.
    """
    here = Path(__file__).parent
    if any(item.path is not None and here in item.path.parents for item in items):
        _import_adapter(config)


@pytest.fixture(scope="session")
def adapter(pytestconfig: pytest.Config) -> object:
    """The imported domain adapter under check (imported once per session)."""
    return _import_adapter(pytestconfig)


@pytest.fixture(scope="session")
def piece(adapter: object) -> Callable[[str], object]:
    """Return a reader for one adapter piece, failing the check when it is absent.

    Every check that reads a piece goes through this, so an adapter missing a member
    fails each dependent check with the same actionable message rather than an
    ``AttributeError`` from somewhere inside the check body.

    Args:
        adapter: The adapter under check.

    Returns:
        A callable taking the member name and returning that member.
    """
    from aegis.conformance._report import fail

    def read(member: str) -> object:
        found = getattr(adapter, member, None)
        if found is None:
            fail(
                member=member,
                problem=f"the adapter has no {member!r} member",
                what=(
                    f"{adapter.__name__!r} does not expose {member!r}, so this check "
                    f"cannot run. aegis.adapter.DomainAdapter requires it."
                ),
                fix=(
                    f"Implement {member} and make sure the adapter package imports it — "
                    f"a submodule becomes an attribute of its package only once "
                    f"something imports it, so `from myapp.adapter import {member}` (or "
                    f"an import of a name inside it) must appear in the package's "
                    f"__init__.py."
                ),
                if_not=(
                    "The platform reads this member through the adapter package; an "
                    "absent one is not an ImportError, it is a piece of the domain that "
                    "is never consulted."
                ),
                scar=(
                    "missing_members(app.adapter) returned ['memory_spec'] on this "
                    "repository's own reference adapter: the file was on disk and in "
                    "the manifest, but nothing imported it, so it was never an "
                    "attribute of the package. Nine of ten pieces reachable, no error "
                    "anywhere."
                ),
            )
        return found

    return read
