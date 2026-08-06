"""Wave-2 platform surfaces — ``/stack``, ``/stack/patch-check``, ``/risk-map``,
``/savings`` (shape + authz), plus the patch-check offline path, risk-map data
integrity and savings math.

Every test is offline and deterministic: the one network call (PyPI, in the patch
check) is mocked, so nothing here reaches the wire.
"""

from __future__ import annotations

import pytest

import app.platform.patches as patch_check_mod
from app.core.security import create_access_token
from app.platform.patches import RegistryUnreachableError

pytestmark = pytest.mark.asyncio


def _headers(role: str, *, tenant_id=None, user_id=None, username="x") -> dict[str, str]:
    """Auth header for a principal minted from a *fine* role (coarse derived)."""
    token = create_access_token(
        user_id=user_id, username=username, role=role, tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


# ── GET /stack — admin/devops only ───────────────────────────────────────────


async def test_stack_shape_and_real_versions(client, db):
    resp = await client.get("/stack", headers=_headers("platform_admin"))
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["generated_at"], str) and body["generated_at"]
    comps = body["components"]
    assert comps, "stack must not be empty"
    cats = {c["category"] for c in comps}
    assert {"runtime", "backend", "frontend", "infra"} <= cats
    for c in comps:
        assert set(c) == {"name", "category", "package", "version", "aegis_module"}
        assert c["category"] in {"runtime", "backend", "frontend", "infra"}
        assert c["version"] is None or isinstance(c["version"], str)
    # Python runtime is always resolvable to a real version.
    python = next(c for c in comps if c["package"] == "python")
    assert python["category"] == "runtime"
    assert python["version"] and python["version"][0].isdigit()
    # An installed backend dep maps to its branded Aegis module.
    langgraph = next(c for c in comps if c["package"] == "langgraph")
    assert langgraph["aegis_module"] == "Aegis Router"
    assert langgraph["version"] is not None
    # An optional-group dep that isn't installed is honest null, never fabricated.
    litellm = next(c for c in comps if c["package"] == "litellm")
    assert litellm["version"] is None


async def test_stack_devops_allowed_client_forbidden_unauth_401(client, db):
    # devops coarse role is allowed; client is not; no token is 401.
    devops = await client.post("/auth/login", json={"username": "devops", "password": "demo"})
    devops_h = {"Authorization": f"Bearer {devops.json()['token']}"}
    assert (await client.get("/stack", headers=devops_h)).status_code == 200

    clientlogin = await client.post("/auth/login", json={"username": "client", "password": "demo"})
    client_h = {"Authorization": f"Bearer {clientlogin.json()['token']}"}
    assert (await client.get("/stack", headers=client_h)).status_code == 403

    assert (await client.get("/stack")).status_code == 401


# ── POST /stack/patch-check — offline + online, admin/devops only ────────────


async def test_patch_check_offline_reports_unknown(client, db, monkeypatch):
    """A network failure ⇒ online:false and every status 'unknown' (honest)."""
    patch_check_mod.reset_cache()

    def _boom(_name: str) -> str | None:
        raise RegistryUnreachableError("simulated offline")

    monkeypatch.setattr(patch_check_mod, "_fetch_latest", _boom)

    resp = await client.post(
        "/stack/patch-check",
        json={"packages": ["fastapi", "pydantic"]},
        headers=_headers("platform_admin"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["online"] is False
    assert "NOT a clean bill of health" in body["note"]
    assert {r["name"] for r in body["results"]} == {"fastapi", "pydantic"}
    for r in body["results"]:
        assert r["status"] == "unknown"
        assert r["latest"] is None


async def test_patch_check_online_current_and_outdated(client, db, monkeypatch):
    """A successful registry answer ⇒ online:true with real current/outdated verdicts."""
    patch_check_mod.reset_cache()

    # fastapi installed >= "latest" → current; a fake package with a higher latest →
    # outdated. Drive both purely through the mocked registry answer.
    installed = {
        "fastapi": patch_check_mod._installed_version("fastapi"),
        "langgraph": patch_check_mod._installed_version("langgraph"),
    }
    assert installed["fastapi"] and installed["langgraph"]

    def _fake_latest(name: str) -> str | None:
        if name == "fastapi":
            return installed["fastapi"]  # equal → current
        if name == "langgraph":
            return "9999.0.0"  # newer → outdated
        return None

    monkeypatch.setattr(patch_check_mod, "_fetch_latest", _fake_latest)

    resp = await client.post(
        "/stack/patch-check",
        json={"packages": ["fastapi", "langgraph"]},
        headers=_headers("platform_admin"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["online"] is True
    by_name = {r["name"]: r for r in body["results"]}
    assert by_name["fastapi"]["status"] == "current"
    assert by_name["langgraph"]["status"] == "outdated"
    assert by_name["langgraph"]["latest"] == "9999.0.0"


async def test_patch_check_partial_success_mixed_statuses(client, db, monkeypatch):
    """FIX 3: one package resolves + one times out in the SAME call ⇒ per-package
    best-effort — the resolved package keeps its real verdict, the failing one is
    'unknown' (registry unreachable), and ``online`` stays true (at least one answer)."""
    patch_check_mod.reset_cache()

    installed_fastapi = patch_check_mod._installed_version("fastapi")
    assert installed_fastapi

    def _fetch(name: str) -> str | None:
        if name == "fastapi":
            return installed_fastapi  # equal → current (a real registry answer)
        raise RegistryUnreachableError("simulated per-package timeout")  # pydantic times out

    monkeypatch.setattr(patch_check_mod, "_fetch_latest", _fetch)

    resp = await client.post(
        "/stack/patch-check",
        json={"packages": ["fastapi", "pydantic"]},
        headers=_headers("platform_admin"),
    )
    assert resp.status_code == 200
    body = resp.json()
    # At least one package answered ⇒ online is true (NOT degraded to the offline set).
    assert body["online"] is True
    by_name = {r["name"]: r for r in body["results"]}
    # The reachable package keeps its real verdict…
    assert by_name["fastapi"]["status"] == "current"
    assert by_name["fastapi"]["latest"] == installed_fastapi
    # …while the timed-out package is honestly unknown, with no fabricated 'latest'.
    assert by_name["pydantic"]["status"] == "unknown"
    assert by_name["pydantic"]["latest"] is None
    assert "unreachable" in (by_name["pydantic"]["note"] or "")
    # The top-level note is honest that this is not a complete clean bill of health.
    assert "NOT a complete clean bill of health" in body["note"]


async def test_patch_check_partial_success_is_not_cached_as_last_success(client, db, monkeypatch):
    """FIX 3 cache: a partial run must NOT become the cached last-successful baseline —
    only a fully-reachable check does. So a subsequent fully-offline run falls back to the
    honest unknown set, never a stale partial masquerading as 'last known good'."""
    patch_check_mod.reset_cache()

    def _partial(name: str) -> str | None:
        if name == "fastapi":
            return patch_check_mod._installed_version("fastapi")
        raise RegistryUnreachableError("offline for pydantic")

    monkeypatch.setattr(patch_check_mod, "_fetch_latest", _partial)
    first = await client.post(
        "/stack/patch-check",
        json={"packages": ["fastapi", "pydantic"]},
        headers=_headers("platform_admin"),
    )
    assert first.json()["online"] is True

    # Now go fully offline. If the partial had been cached, we'd see fastapi 'current';
    # instead we get the honest all-unknown offline set (nothing cached to fall back to).
    def _boom(_name: str) -> str | None:
        raise RegistryUnreachableError("fully offline")

    monkeypatch.setattr(patch_check_mod, "_fetch_latest", _boom)
    second = await client.post(
        "/stack/patch-check",
        json={"packages": ["fastapi", "pydantic"]},
        headers=_headers("platform_admin"),
    )
    body = second.json()
    assert body["online"] is False
    assert all(r["status"] == "unknown" for r in body["results"])
    assert "NOT a clean bill of health" in body["note"]


async def test_patch_check_authz(client, db, monkeypatch):
    patch_check_mod.reset_cache()
    monkeypatch.setattr(patch_check_mod, "_fetch_latest", lambda name: None)

    devops = await client.post("/auth/login", json={"username": "devops", "password": "demo"})
    devops_h = {"Authorization": f"Bearer {devops.json()['token']}"}
    assert (await client.post("/stack/patch-check", json={}, headers=devops_h)).status_code == 200

    clientlogin = await client.post("/auth/login", json={"username": "client", "password": "demo"})
    client_h = {"Authorization": f"Bearer {clientlogin.json()['token']}"}
    assert (await client.post("/stack/patch-check", json={}, headers=client_h)).status_code == 403

    assert (await client.post("/stack/patch-check", json={})).status_code == 401


# ── GET /risk-map — admin/client only, data integrity ────────────────────────


async def test_risk_map_shape_and_integrity(client, db):
    resp = await client.get("/risk-map", headers=_headers("platform_admin"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["scale"] == {"likelihood": [1, 2, 3, 4, 5], "impact": [1, 2, 3, 4, 5]}
    assert body["note"]
    risks = body["risks"]
    assert len(risks) >= 6
    ids = {r["id"] for r in risks}
    assert len(ids) == len(risks), "risk ids must be unique"
    for r in risks:
        assert 1 <= r["likelihood"] <= 5
        assert 1 <= r["impact"] <= 5
        assert r["control_ref"].strip(), "control_ref must point at a real file"
        assert r["mitigation"].strip()
        assert r["residual"] in {"low", "medium", "high"}
    # Injection is never marked fully resolved (honest posture).
    injection = next(r for r in risks if "injection" in r["title"].lower())
    assert injection["residual"] != "low"


async def test_risk_map_authz(client, db):
    clientlogin = await client.post("/auth/login", json={"username": "client", "password": "demo"})
    client_h = {"Authorization": f"Bearer {clientlogin.json()['token']}"}
    assert (await client.get("/risk-map", headers=client_h)).status_code == 200

    # ai_team and devops are not on the assurance surface.
    devops = await client.post("/auth/login", json={"username": "devops", "password": "demo"})
    devops_h = {"Authorization": f"Bearer {devops.json()['token']}"}
    assert (await client.get("/risk-map", headers=devops_h)).status_code == 403

    assert (await client.get("/risk-map")).status_code == 401


# ── GET /savings — any authenticated principal, math ─────────────────────────


async def test_savings_shape_and_math(client, db):
    resp = await client.get("/savings", headers=_headers("member", username="c"))
    assert resp.status_code == 200
    body = resp.json()
    baseline = body["baseline_cost_usd"]
    actual = body["actual_cost_usd"]
    saved = body["saved_usd"]
    # saved == baseline - actual (clamped at 0), within float tolerance.
    assert saved == pytest.approx(max(0.0, baseline - actual), abs=1e-6)
    if baseline > 0:
        assert body["saved_pct"] == pytest.approx(saved / baseline, abs=1e-6)
    else:
        assert body["saved_pct"] == 0.0
    sources = {b["source"] for b in body["breakdown"]}
    assert "Small-model routing" in sources
    assert len(body["breakdown"]) >= 3
    for b in body["breakdown"]:
        assert b["explanation"].strip()
    assert body["note"].strip()


async def test_savings_reachable_for_every_role(client, db):
    """Overview's savings figure must load for every portal role (require_auth)."""
    for username in ("admin", "ai", "devops", "client"):
        login = await client.post("/auth/login", json={"username": username, "password": "demo"})
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        assert (await client.get("/savings", headers=headers)).status_code == 200, username

    assert (await client.get("/savings")).status_code == 401


# ── /metrics reachability relaxed for the Overview surface (every role) ───────


async def test_metrics_reachable_for_every_role(client, db):
    """Overview polls /metrics in every portal; the guard was relaxed to require_auth."""
    for username in ("admin", "ai", "devops", "client"):
        login = await client.post("/auth/login", json={"username": username, "password": "demo"})
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        resp = await client.get("/metrics", headers=headers)
        assert resp.status_code == 200, username
        body = resp.json()
        assert "cache_hit_rate" in body and "cost_saved_usd" in body

    assert (await client.get("/metrics")).status_code == 401
