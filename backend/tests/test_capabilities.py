"""Tests for the Aegis capabilities manifest and its platform endpoints.

Guards the product's single source of truth (:mod:`app.capabilities`) and the
honest ``/platform/capabilities`` + ``/about`` surfaces:

* every declared ``module_path`` really imports (the manifest stays factual),
* every module pairs a branded name with real tech, an honest summary and a valid
  category/status (branding, never hiding),
* the endpoint returns every module and honours the read-auth convention.
"""

from __future__ import annotations

import importlib

import pytest

from app.capabilities import (
    AEGIS_MODULES,
    PRODUCT_NAME,
    PRODUCT_VERSION,
    module_count,
)

_CATEGORIES = {"runtime", "knowledge", "trust", "ops", "platform"}
_STATUSES = {"live", "optional"}


def test_manifest_is_non_empty_and_keys_unique() -> None:
    assert AEGIS_MODULES, "the Aegis manifest must declare at least one module"
    keys = [m.key for m in AEGIS_MODULES]
    assert len(keys) == len(set(keys)), "module keys must be unique"
    assert module_count() == len(AEGIS_MODULES)


@pytest.mark.parametrize("module", AEGIS_MODULES, ids=lambda m: m.key)
def test_module_path_importable(module) -> None:  # noqa: ANN001 - Pydantic model
    """Every declared ``module_path`` must import — the manifest stays factual."""
    importlib.import_module(module.module_path)


@pytest.mark.parametrize("module", AEGIS_MODULES, ids=lambda m: m.key)
def test_module_is_honest_and_well_formed(module) -> None:  # noqa: ANN001
    """Branded name is always paired with real tech; fields are valid and honest."""
    assert module.name.startswith("Aegis"), "branded names are 'Aegis <Module>'"
    assert module.tech.strip(), "branding never stands without its underlying tech"
    assert module.summary.strip(), "each module needs an honest one-line summary"
    assert module.category in _CATEGORIES
    assert module.status in _STATUSES
    assert module.module_path.startswith("app."), "module_path points at real code"


def test_optional_modules_are_marked() -> None:
    """The MCP tool server is gated on the optional MCP SDK — declared optional."""
    by_key = {m.key: m for m in AEGIS_MODULES}
    assert by_key["mcp"].status == "optional"


async def test_capabilities_endpoint_returns_all_modules(client, user_headers) -> None:
    """`GET /platform/capabilities` returns the whole manifest to an authed caller."""
    resp = await client.get("/platform/capabilities", headers=user_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["product"] == PRODUCT_NAME
    assert body["module_count"] == len(AEGIS_MODULES)
    assert len(body["modules"]) == len(AEGIS_MODULES)
    returned_keys = {row["key"] for row in body["modules"]}
    assert returned_keys == {m.key for m in AEGIS_MODULES}
    # Each row carries the branded name AND its honest tech (never one without the other).
    for row in body["modules"]:
        assert row["name"].startswith("Aegis")
        assert row["tech"].strip()
        assert row["module_path"].startswith("app.")


async def test_capabilities_endpoint_is_public(client) -> None:
    """The manifest is public: the pre-login landing page at ``/`` renders it.

    This deliberately reverses the endpoint's original read-auth guard. The body is
    product identity — module names, honest tech, summaries, import paths — the same
    material published in ``README.md``, carrying no tenant, user, usage or
    credential data. See ``tests/api/test_public_surfaces.py`` for the wider
    public-surface contract.
    """
    resp = await client.get("/platform/capabilities")
    assert resp.status_code == 200
    assert resp.json()["module_count"] == len(AEGIS_MODULES)


async def test_about_endpoint_is_public_identity_card(client) -> None:
    """`GET /about` is a trivial, public product-identity card."""
    resp = await client.get("/about")
    assert resp.status_code == 200
    body = resp.json()
    assert body["product"] == PRODUCT_NAME
    assert body["version"] == PRODUCT_VERSION
    assert body["modules"] == len(AEGIS_MODULES)
