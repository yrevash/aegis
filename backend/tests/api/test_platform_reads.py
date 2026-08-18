"""Thin platform read-surface routes over the ``aegis.*`` accessors (Phase-3 · Task 3).

Each route is a read-only projection of a domain-agnostic accessor that backs one
of the platform dashboards (MLOps / evals / LLMOps / token-opt / harness /
governance / security / latency / red-team). These tests assert:

- every route returns 200 with the accessor's shape;
- the RBAC guards hold (role-scoped routes reject the wrong role; the governance
  route is tenant-scoped — a tenant-admin never reads another tenant, and a
  non-admin is refused);
- ``POST /redteam/run`` returns a report with a real block rate and audits the run;
- offline empty/zero states are honest (latency empty, gateway zeros).
"""

from __future__ import annotations

import pytest
from tests.conftest import login_as

from app.api.schemas import Role
from app.core.security import PLATFORM_ADMIN, TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker

pytestmark = pytest.mark.asyncio


def _headers(role: str, *, tenant_id=None, user_id=None, username="u") -> dict[str, str]:
    """Build a bearer header for a fine ``role`` (coarse is derived on the token)."""
    token = create_access_token(
        user_id=user_id, username=username, role=role, tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


# ── MLOps · model card ───────────────────────────────────────────────────────


@pytest.fixture
def fitted_spine(monkeypatch):
    """Install a genuinely domain-fitted spine as the process-wide model.

    Explicit, because ``app.ml.get_model`` no longer trains one on demand: the
    removed fallback fitted the built-in noise synthesiser whenever the domain
    adapter was unavailable and served the result as this platform's model. The
    endpoint's *presence* of a model is now a precondition the test states, not
    something the handler manufactures behind the operator's back.
    """
    import app.ml as ml
    from app.adapter import training_frame
    from app.ml.model import TrustworthyModel
    from app.ml.spec import resolve_spec

    model = TrustworthyModel.train(
        resolve_spec(), training_frame(num_requests=200), path=None
    )
    monkeypatch.setattr(ml, "_MODEL", model)
    return model


@pytest.fixture
def no_ml_artifact(monkeypatch, tmp_path):
    """A cold process with no persisted artifact — the pre-training state."""
    import app.ml as ml

    monkeypatch.setattr(ml, "_MODEL", None)
    monkeypatch.setattr(ml, "DEFAULT_ARTIFACT_PATH", tmp_path / "absent.joblib")


async def test_model_card_returns_measured_shape(client, db, fitted_spine):
    r = await client.get("/ml/model-card", headers=await login_as(client, "ai"))
    assert r.status_code == 200
    body = r.json()
    assert body["task"] == "regression"
    assert body["ensemble_members"], "ensemble members are read off the fitted spine"
    # data_source is the honesty label — a real domain frame, not the synthesiser.
    assert body["data_source"] == "provided"


async def test_model_card_is_503_when_no_model_has_been_trained(
    client, db, no_ml_artifact
):
    """A model card describes a model; with none fitted the honest answer is "none".

    It used to train one inside this request — on the domain spec if the adapter
    imported, and on the built-in **noise synthesiser** if it did not — and return a
    fully-populated card for it. The card looked identical either way.
    """
    r = await client.get("/ml/model-card", headers=await login_as(client, "ai"))

    assert r.status_code == 503
    assert "python -m app.ml" in r.json()["detail"]


async def test_ml_explain_is_503_when_no_model_has_been_trained(
    client, db, admin_headers, no_ml_artifact
):
    """SAME for the prediction surface: refuse rather than serve a noise interval."""
    r = await client.post(
        "/ml/explain", json={"features": {"priority": "urgent"}}, headers=admin_headers
    )

    assert r.status_code == 503
    assert "python -m app.ml" in r.json()["detail"]


async def test_ml_explain_runs_the_fit_off_the_event_loop(client, db, admin_headers):
    """The blocking spine call must not be awaited inline in the handler.

    A first request with no cached model pays a joblib load (and previously a full
    XGBoost + MAPIE fit) — seconds of pure CPU. Run in the handler's own coroutine
    that froze the single event loop for every other in-flight request, every SSE
    stream and the health check alongside it.
    """
    import inspect

    from app.api import routes

    assert "asyncio.to_thread(predict" in inspect.getsource(routes.ml_explain)
    assert "asyncio.to_thread(get_model)" in inspect.getsource(routes.ml_model_card)


async def test_model_card_rejects_client_role(client, db):
    r = await client.get("/ml/model-card", headers=await login_as(client, "client"))
    assert r.status_code == 403


# ── Evals · offline regression-gate rollup ───────────────────────────────────


async def test_evals_report_returns_gate_rollup(client, db):
    r = await client.get("/evals/report", headers=await login_as(client, "ai"))
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["passed"], bool)
    assert body["source"] == "offline_regression_gate"
    names = {m["name"] for m in body["metrics"]}
    assert "context_recall" in names  # a real seed-corpus metric
    assert 0.0 <= body["overall"] <= 1.0


async def test_evals_report_rejects_devops_role(client, db):
    # evals is admin/ai_team only — a devops principal is refused.
    r = await client.get("/evals/report", headers=await login_as(client, "devops"))
    assert r.status_code == 403


# ── LLMOps · loop params ─────────────────────────────────────────────────────


async def test_ops_params_returns_knobs(client, db):
    r = await client.get("/ops/params", headers=await login_as(client, "admin"))
    assert r.status_code == 200
    body = r.json()
    assert body["auto_promote_ceiling"] == "low"
    assert isinstance(body["safety_terms"], list) and body["safety_terms"]
    assert "temperature" in body["tunable_max_delta"]


# ── Token-optimization ───────────────────────────────────────────────────────


async def test_gateway_optimization_shape_and_honest_zeros(client, db):
    # require_auth: any authenticated principal (incl. client) may read efficiency figures.
    r = await client.get("/gateway/optimization", headers=await login_as(client, "client"))
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"summary", "config"}
    # Offline, before any metered call, the savings figures are honest zeros / None.
    assert body["summary"]["total_calls"] == 0
    assert body["summary"]["small_model_share"] is None
    assert body["config"]["routing"], "the effective routing table is always present"


async def test_gateway_optimization_requires_auth(client, db):
    r = await client.get("/gateway/optimization")
    assert r.status_code == 401


# ── Harness · tweakable config ───────────────────────────────────────────────


async def test_harness_config_shape(client, db):
    r = await client.get("/harness/config", headers=await login_as(client, "ai"))
    assert r.status_code == 200
    body = r.json()
    assert body["knobs"] and body["effective"]
    keys = {k["key"] for k in body["knobs"]}
    assert "gate_min_risk" in keys


async def test_harness_config_rejects_client_role(client, db):
    r = await client.get("/harness/config", headers=await login_as(client, "client"))
    assert r.status_code == 403


# ── Security posture ─────────────────────────────────────────────────────────


async def test_security_posture_shape_and_status_vocab(client, db):
    r = await client.get("/security/posture", headers=await login_as(client, "devops"))
    assert r.status_code == 200
    body = r.json()
    assert body["entries"], "one posture entry per major threat"
    for e in body["entries"]:
        assert e["status"] in {"enforced", "partial", "not_covered"}
        assert e["refs"], "every entry names importable backing symbols"
    assert "rls_fail_closed" in body["signals"]


async def test_security_posture_rejects_client_role(client, db):
    r = await client.get("/security/posture", headers=await login_as(client, "client"))
    assert r.status_code == 403


# ── Latency ──────────────────────────────────────────────────────────────────


async def test_latency_reports_honest_empty_state(client, db):
    # No runs recorded → an honest empty summary, not fake zeros. The latency window
    # is process-global, so clear anything other tests recorded before asserting empty.
    from aegis.observability import reset_latency_window

    reset_latency_window()
    r = await client.get("/latency", headers=await login_as(client, "devops"))
    assert r.status_code == 200
    body = r.json()
    assert body["empty"] is True
    assert body["per_node"] == []
    assert body["run_p50_ms"] is None


# ── Red-team ─────────────────────────────────────────────────────────────────


async def test_redteam_run_reports_block_rate(client, db):
    r = await client.post("/redteam/run", headers=await login_as(client, "admin"))
    assert r.status_code == 200
    body = r.json()
    assert body["overall"]["attacksTotal"] > 0
    assert 0.0 <= body["overall"]["blockRate"] <= 1.0
    assert isinstance(body["passed"], bool)
    assert body["attacks"], "every attack's verdict is reported"
    # camelCase alias is preserved on the wire (mirrors RedTeamReport.as_dict).
    assert "falsePositiveDetail" in body


async def test_redteam_run_rejects_client_role(client, db):
    r = await client.post("/redteam/run", headers=await login_as(client, "client"))
    assert r.status_code == 403


# ── Governance dashboard (RBAC + tenant scope) ───────────────────────────────


async def _seed_two_tenants() -> None:
    async with get_sessionmaker()() as session:
        session.add_all(
            [
                Tenant(id=1, name="Tenant A"),
                Tenant(id=2, name="Tenant B"),
                User(id=11, username="a-user", role=Role.CLIENT, tenant_id=1),
                User(id=22, username="b-user", role=Role.CLIENT, tenant_id=2),
            ]
        )
        await session.commit()


async def test_governance_dashboard_platform_admin_sees_all(client, db):
    await _seed_two_tenants()
    r = await client.get("/governance/dashboard", headers=_headers(PLATFORM_ADMIN))
    assert r.status_code == 200
    body = r.json()
    # Platform view (tenant_id None) contains every tenant.
    assert body["tenant_id"] is None
    assert {t["id"] for t in body["tenants"]} == {1, 2}


async def test_governance_dashboard_tenant_admin_scoped_to_own(client, db):
    await _seed_two_tenants()
    r = await client.get(
        "/governance/dashboard", headers=_headers(TENANT_ADMIN, tenant_id=1)
    )
    assert r.status_code == 200
    body = r.json()
    # Omitted tenant_id defaults to the caller's own; never Tenant B.
    assert body["tenant_id"] == 1
    assert {t["id"] for t in body["tenants"]} == {1}


async def test_governance_dashboard_cross_tenant_is_forbidden(client, db):
    await _seed_two_tenants()
    r = await client.get(
        "/governance/dashboard?tenant_id=2",
        headers=_headers(TENANT_ADMIN, tenant_id=1),
    )
    assert r.status_code == 403


async def test_governance_dashboard_rejects_non_admin(client, db):
    r = await client.get("/governance/dashboard", headers=await login_as(client, "client"))
    assert r.status_code == 403


async def test_governance_dashboard_requires_auth(client, db):
    r = await client.get("/governance/dashboard")
    assert r.status_code == 401
