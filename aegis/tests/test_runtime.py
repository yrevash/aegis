"""``Aegis.from_env`` — the one way up (§8.3).

Three claims, and nothing else. The task this replaces is an *ordered ten-call ritual*,
so the tests are about the ritual being gone, not about each seam's own behaviour (every
one of those already has its own suite):

1. **One call brings up what the ten did.** Every seam is *read back through its own
   public accessor* — ``get_default_spec()``, ``get_default_index()``,
   ``new_default_store()``, ``render_floor_prompt()``, a real token round-trip — because
   asserting that ``from_env`` called something proves only that ``from_env`` has a line
   in it. Reading the global back is what fails when the line is deleted.
2. **A missing adapter member is named up front**, before a single global is written.
3. **A missing environment variable fails loud**, naming the variable.

Plus the one design decision that is only visible under a second call: a second domain in
one process is refused rather than merged.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import TYPE_CHECKING, Any

import pytest

from aegis.adapter import adapter_members
from aegis.runtime import (
    AdapterContractError,
    Aegis,
    AegisAlreadyConfiguredError,
    MissingConfigurationError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.asyncio


# ─────────────────────────────────────────────────────────────── a fake domain ──
class _FakeMemorySpec:
    """The pieces of a ``MemorySpec`` the runtime and the tests actually read."""

    FACT_TYPES = ["preference", "constraint"]
    PROFILE_FIELDS = ["name"]
    FACT_EXTRACTION_PROMPT = "extract"
    IMPORTANCE_HINTS = "hints"
    SKILLS_DIR = "/tmp/skills"  # noqa: S108 - a string the runtime only prints
    FactSchema = dict
    FactExtraction = dict


class _FakePrompts:
    def render_system_prompt(self, persona: Any, *, extra_context: str | None = None) -> str:  # noqa: ANN401, ARG002
        return f"floor for {persona}"

    def render_platform_floor(self, persona: Any) -> str:  # noqa: ANN401
        return f"floor for {persona}"


class _FakePersonas:
    DEFAULT_PERSONA_ID = "analyst"
    PERSONAS: dict[str, Any] = {}

    def get_persona(self, persona_id: str | None) -> str:
        return persona_id or self.DEFAULT_PERSONA_ID


def _fake_adapter(
    name: str, *, domain_id: str = "widgets", omit: str | None = None
) -> ModuleType:
    """Build an importable module satisfying :class:`~aegis.adapter.DomainAdapter`.

    Registered in :data:`sys.modules` so the dotted-path branch of ``from_env`` is what
    the tests exercise — that is the spelling the docs promise, and the one an integrator
    types.

    Args:
        name: The dotted module path to register it under.
        domain_id: The ``DOMAIN_ID`` it declares.
        omit: A member to leave off, for the contract-failure test.
    """
    module = ModuleType(name)
    members: dict[str, Any] = {
        "DOMAIN_ID": domain_id,
        "DOMAIN_DESCRIPTION": "Widgets, and the making of widgets.",
        "schema": object(),
        "ml_spec": object(),
        "generator": object(),
        "tools": object(),
        "personas": _FakePersonas(),
        "prompts": _FakePrompts(),
        "memory_spec": _FakeMemorySpec(),
        "roster": object(),
        "corpus": object(),
    }
    assert set(members) == set(adapter_members()), "the fake must track the Protocol"
    for member, value in members.items():
        if member != omit:
            setattr(module, member, value)
    sys.modules[name] = module
    return module


# ─────────────────────────────────────────────────────────────────── isolation ──
#: Every global ``from_env`` writes, as ``(module path, attribute)``. Snapshotted and
#: restored around each test — these are process-wide by design, and a bring-up test that
#: leaked its wiring into the rest of the suite would be a worse defect than the one this
#: module exists to prevent.
_GLOBALS = [
    ("aegis.runtime", "_ACTIVE"),
    ("aegis.memory.spec", "_default_spec"),
    ("aegis.memory.vector_ops", "_DEFAULT_INDEX"),
    ("aegis.retrieval.vector_store", "_STORE_FACTORY"),
    ("aegis.governance.security", "_config"),
    ("aegis.gateway.llm", "_config"),
    ("aegis.gateway.llm", "_governance"),
    ("aegis.gateway.llm", "_observability"),
    ("aegis.ops.config", "_render_floor_prompt"),
    ("aegis.ops.config", "_session_factory"),
    ("aegis.ops.config", "_set_tenant_scope"),
    ("aegis.guardrails.nemo", "_allowed_topics"),
    ("aegis.jobs.scope", "_session_factory"),
]


@pytest.fixture(autouse=True)
def _isolated_process(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Restore every process global ``from_env`` writes, and clear the ``AEGIS_`` env."""
    import importlib

    saved = []
    for path, attr in _GLOBALS:
        module = importlib.import_module(path)
        saved.append((module, attr, getattr(module, attr)))
    for key in [k for k in dict(__import__("os").environ) if k.startswith("AEGIS_")]:
        monkeypatch.delenv(key, raising=False)
    yield
    for module, attr, value in saved:
        setattr(module, attr, value)


# ───────────────────────────────────────────────────── 1. one call, ten seams ──
async def test_one_call_brings_up_what_the_ten_configures_did() -> None:
    """Every seam is readable through its own accessor after a single ``from_env``."""
    from aegis.governance.security import create_access_token, decode_access_token
    from aegis.guardrails.nemo import get_allowed_topics
    from aegis.memory import get_default_index
    from aegis.memory.spec import get_default_spec
    from aegis.ops.config import render_floor_prompt
    from aegis.retrieval.vector_store import new_default_store

    adapter = _fake_adapter("fake_widgets_adapter")
    aegis = await Aegis.from_env(adapter="fake_widgets_adapter", mode="lite")

    # 1. aegis.memory.set_default_spec — from the adapter's piece 7.
    assert get_default_spec() is adapter.memory_spec
    # 2. aegis.memory.set_default_index — the seam that used to degrade silently.
    assert get_default_index() is not None
    # 3. aegis.retrieval.configure_vector_store — the other one.
    assert new_default_store() is not None
    # 4. aegis.governance.security.configure_security — a real signed round-trip.
    token = create_access_token(
        user_id=7, username="u1", role="client", tenant_id=3
    )
    claims = decode_access_token(token)
    assert claims.username == "u1"
    # 5. aegis.ops.configure_ops — the floor renderer, derived from pieces 5 + 6.
    assert render_floor_prompt("analyst") == "floor for analyst"
    # 6. aegis.guardrails.nemo.set_allowed_topics — a control input, from the adapter.
    assert get_allowed_topics() == adapter.DOMAIN_DESCRIPTION

    # And the handle says what it chose, including every non-durable choice.
    assert aegis.domain_id == "widgets"
    assert aegis.mode.value == "lite"
    ephemeral = {seam.target for seam in aegis.ephemeral_seams}
    assert "aegis.memory.set_default_index" in ephemeral
    assert "aegis.retrieval.configure_vector_store" in ephemeral
    assert "EPHEMERAL" in aegis.describe()


async def test_a_host_session_factory_reaches_governance_ops_and_jobs() -> None:
    """One ``session_factory=`` argument replaces four separate wiring calls."""
    import aegis.governance.audit as audit
    import aegis.governance.enforcement as enforcement
    import aegis.jobs.scope as scope
    import aegis.ops.config as ops_config

    _fake_adapter("fake_widgets_adapter")

    def factory() -> Any:  # noqa: ANN401 - a stand-in; nothing here opens it
        raise AssertionError("no test may open a real session through this")

    await Aegis.from_env(
        adapter="fake_widgets_adapter", mode="lite", session_factory=factory
    )

    assert enforcement._session_factory is factory
    assert audit._session_factory is factory
    assert ops_config._session_factory is factory
    assert scope._session_factory is factory


# ────────────────────────────────────── 2. a missing member, named up front ──
async def test_a_missing_adapter_member_is_named_before_anything_is_wired() -> None:
    """The exact failure ``missing_members(['memory_spec'])`` describes, caught at bring-up."""
    import aegis.runtime as runtime
    from aegis.memory.spec import get_default_spec

    _fake_adapter("fake_broken_adapter", omit="memory_spec")
    runtime._ACTIVE = None
    import aegis.memory.spec as spec_module

    spec_module._default_spec = None

    with pytest.raises(AdapterContractError) as excinfo:
        await Aegis.from_env(adapter="fake_broken_adapter", mode="lite")

    message = str(excinfo.value)
    assert "memory_spec" in message
    assert "fake_broken_adapter" in message
    # Up front means up front: not one global was written on the way to the error.
    assert runtime.active() is None
    with pytest.raises(RuntimeError, match="set_default_spec"):
        get_default_spec()


# ─────────────────────────────── 3. missing environment, named and refused ──
async def test_full_mode_names_the_unset_backend_variable() -> None:
    """``AEGIS_MODE=full`` with no vector-store path names the variable, not "config error"."""
    _fake_adapter("fake_widgets_adapter")
    with pytest.raises(RuntimeError, match="AEGIS_VECTOR_STORE_PATH"):
        await Aegis.from_env(adapter="fake_widgets_adapter", mode="full")


async def test_full_mode_names_the_unset_jwt_secret_and_its_value_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any  # noqa: ANN401 - pathlib.Path
) -> None:
    """The signing secret is named, its shape is named, and the process does not come up."""
    _fake_adapter("fake_widgets_adapter")
    monkeypatch.setenv("AEGIS_REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("AEGIS_DATABASE_URL", "postgresql+asyncpg://localhost/x")
    monkeypatch.setenv("AEGIS_VECTOR_STORE_PATH", str(tmp_path))

    with pytest.raises(MissingConfigurationError) as excinfo:
        await Aegis.from_env(adapter="fake_widgets_adapter", mode="full")

    message = str(excinfo.value)
    assert "AEGIS_JWT_SECRET" in message
    assert "32" in message  # the value shape, not just the name


async def test_full_mode_refuses_to_invent_a_session_factory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any  # noqa: ANN401 - pathlib.Path
) -> None:
    """It will not build an engine from ``AEGIS_DATABASE_URL``; it names the parameter."""
    _fake_adapter("fake_widgets_adapter")
    monkeypatch.setenv("AEGIS_REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("AEGIS_DATABASE_URL", "postgresql+asyncpg://localhost/x")
    monkeypatch.setenv("AEGIS_VECTOR_STORE_PATH", str(tmp_path))
    monkeypatch.setenv("AEGIS_JWT_SECRET", "x" * 48)

    with pytest.raises(MissingConfigurationError, match="session_factory"):
        await Aegis.from_env(adapter="fake_widgets_adapter", mode="full")


# ──────────────────────────────────────────────── the second-call decision ──
async def test_a_second_domain_in_one_process_is_refused_not_merged() -> None:
    """Same adapter re-wires; a different one raises and names both domains."""
    _fake_adapter("fake_widgets_adapter", domain_id="widgets")
    _fake_adapter("fake_gadgets_adapter", domain_id="gadgets")

    await Aegis.from_env(adapter="fake_widgets_adapter", mode="lite")
    # Idempotent for the same domain — a host that brings up twice is not punished.
    again = await Aegis.from_env(adapter="fake_widgets_adapter", mode="lite")
    assert again.domain_id == "widgets"

    with pytest.raises(AegisAlreadyConfiguredError) as excinfo:
        await Aegis.from_env(adapter="fake_gadgets_adapter", mode="lite")
    assert "widgets" in str(excinfo.value)
    assert "gadgets" in str(excinfo.value)

    # The escape hatch is explicit, never implicit.
    replaced = await Aegis.from_env(
        adapter="fake_gadgets_adapter", mode="lite", replace=True
    )
    assert replaced.domain_id == "gadgets"
