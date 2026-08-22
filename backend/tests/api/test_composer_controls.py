"""The three surfaces the composer's control row needs, and the claims they stand on.

Deliberately few tests. Each one fails on a real regression and on nothing else:

* **The width reaches the run.** ``aegis.agent.run_agent`` has accepted ``depth_mode``
  and ``requested_fanout`` since Phase 5 and honours an explicit value exactly — but the
  host wrapper dropped both from its signature, ``QueryRequest`` carried neither, and the
  route passed neither, so nothing in a browser could reach the mechanism. The capture
  test asserts the value arrives at the **core** call, which is the only place that
  proves all three layers threaded it.
* **A width nobody can honour is refused, not swallowed.** Pydantic drops an unknown body
  field in silence, so posting ``depth_mode`` at a model without it returned 200 and ran
  in Auto. ``extra="forbid"`` plus the two validators turn every one of those into a 422
  naming the field.
* **A setting says which scope decided it.** ``resolve`` returns ``(value, source)`` and
  the source half is the point: "Team (your setting)" and "Team (your tenant's default)"
  render identically without it.
* **The tenant boundary holds on the settings table**, asserted against the database with
  no application predicate in the query — the only way to show Postgres is filtering.
* **A weakening is refused with its reason**, by the resolver rather than by a second
  copy of the rule in the route.
* **The tool roster names the layer that decided each row**, and a tenant's own gate floor
  visibly moves it — which is the same write from the settings surface changing what the
  roster reports.
"""

from __future__ import annotations

import pgsupport
import pytest
from aegis.gateway.routing import tenant_selectable_deployments
from aegis.settings.models import Setting
from sqlalchemy import select

from app.api.schemas import Role
from app.core.security import create_access_token
from app.data import Tenant, User, get_sessionmaker, set_tenant_scope

#: A real deployment from the platform's allowed set. ``agent.model`` used to accept any
#: string because nothing validated it and nothing read it; it now carries a domain the
#: platform declares (§7.16 row 6), so an arbitrary value here would be testing the
#: refusal rather than the scope resolution these tests are about.
_CHOSEN_MODEL = tenant_selectable_deployments()[1]

pytestmark = pytest.mark.asyncio


def _headers(*, tenant_id: int, user_id: int, username: str, role: str) -> dict[str, str]:
    """A bearer for one tenant's user at one **fine** RBAC tier.

    ``role`` is the fine tier the settings catalogue is written against
    (``tenant_admin`` / ``client`` / …); the coarse four-valued role the persona guard
    reads is derived from it by :func:`aegis.governance.security.coarse_role_from_fine`.
    """
    token = create_access_token(
        user_id=user_id, username=username, role=role, tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_two_tenants() -> None:
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=1, name="Tenant A"),
            Tenant(id=2, name="Tenant B"),
            User(id=11, username="a-admin", role=Role.ADMIN, tenant_id=1),
            User(id=12, username="a-user", role=Role.CLIENT, tenant_id=1),
            User(id=22, username="b-user", role=Role.CLIENT, tenant_id=2),
        )
        await session.commit()


# ── Gap 1: the width reaches the run ─────────────────────────────────────────


async def test_an_explicit_width_reaches_the_core_run(client, db, admin_headers, monkeypatch):
    """`depth_mode` and `requested_fanout` posted to /query arrive at `aegis.agent`.

    Captured at ``_core_run_agent`` rather than at the host wrapper on purpose: that is
    the seam three separate layers had to thread the value through, and asserting at the
    wrapper would still pass with the core call dropping it.
    """
    seen: dict[str, object] = {}

    async def _capture(query, **kwargs):
        seen.update(kwargs)
        seen["query"] = query
        return
        yield  # pragma: no cover - makes this an async generator

    from app.agent import orchestrator

    monkeypatch.setattr(orchestrator, "_core_run_agent", _capture)

    resp = await client.post(
        "/query",
        json={
            "query": "compare the two policies and tell me what changed",
            "persona": "operations_lead",
            "depth_mode": "team",
            "requested_fanout": 3,
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200, resp.text
    assert seen["depth_mode"] == "team", (
        "POST /query did not carry the requested width into aegis.agent.run_agent — the "
        f"core was called with {seen.get('depth_mode')!r}"
    )
    assert seen["requested_fanout"] == 3


async def test_a_width_the_platform_cannot_honour_is_refused_not_swallowed(
    client, db, admin_headers, make_deps
):
    """Every malformed width is a 422 naming the field, never a silent Auto run.

    The last case is the trap this endpoint spent a phase in: with pydantic's default,
    a body naming a field the model does not carry is accepted and the field discarded.

    Fake deps are injected even though a 422 never reaches the agent: the assertion is
    that these bodies are *refused*, and the run they would otherwise start must not be
    able to reach a gateway to prove the negative.
    """
    from app.api import routes as api_routes
    from app.main import app

    app.dependency_overrides[api_routes.get_agent_deps] = lambda: make_deps()

    base = {"query": "hello", "persona": "operations_lead"}
    unknown_mode = await client.post(
        "/query", json={**base, "depth_mode": "deep"}, headers=admin_headers
    )
    negative_width = await client.post(
        "/query",
        json={**base, "depth_mode": "team", "requested_fanout": -1},
        headers=admin_headers,
    )
    width_without_a_team = await client.post(
        "/query",
        json={**base, "depth_mode": "single", "requested_fanout": 4},
        headers=admin_headers,
    )
    unknown_field = await client.post(
        "/query", json={**base, "dpeth_mode": "team"}, headers=admin_headers
    )
    assert unknown_mode.status_code == 422, unknown_mode.text
    assert "depth_mode" in unknown_mode.text
    assert negative_width.status_code == 422, negative_width.text
    assert width_without_a_team.status_code == 422, width_without_a_team.text
    assert unknown_field.status_code == 422, (
        "a body naming a field QueryRequest does not carry was accepted and the field "
        "dropped in silence — which is exactly how depth_mode would have stayed dark"
    )


# ── Gap 2: the settings catalogue over HTTP ──────────────────────────────────


async def test_a_setting_reports_the_scope_that_decided_it(client, db):
    """An untouched key reads `platform`; the caller's own write makes it read `user`.

    This is the whole reason the endpoint returns ``source``. Hardcode it — or resolve
    without the user layer — and the second half of this test fails while the value
    keeps looking right.
    """
    await _seed_two_tenants()
    user = _headers(tenant_id=1, user_id=12, username="a-user", role="client")

    before = await client.get("/settings/agent.model", headers=user)
    assert before.status_code == 200, before.text
    assert before.json()["source"] == "platform"
    assert before.json()["value"] == "default"

    written = await client.put(
        "/settings/agent.model", headers=user, json={"value": _CHOSEN_MODEL, "scope": "user"}
    )
    assert written.status_code == 200, written.text
    assert (written.json()["value"], written.json()["source"]) == (_CHOSEN_MODEL, "user")

    after = await client.get("/settings/agent.model", headers=user)
    assert (after.json()["value"], after.json()["source"]) == (_CHOSEN_MODEL, "user")

    # The list surface answers with the same pair, so a screen and a single-key read
    # cannot disagree about what is in force.
    rows = {row["key"]: row for row in (await client.get("/settings", headers=user)).json()["rows"]}
    assert (rows["agent.model"]["value"], rows["agent.model"]["source"]) == (_CHOSEN_MODEL, "user")
    assert rows["agent.mode"]["source"] == "platform"


async def test_a_model_a_tenant_may_not_select_is_refused_by_the_server(client, db):
    """§7.16 row 6: *"a UI enum is not enforcement"*, asserted where the curl lands.

    Three shapes, all refused by the catalogue's own validation rather than by anything
    this route re-checks: a model that does not exist, a real deployment the platform
    keeps for itself (the injection classifier's own tier), and a bare provider name.
    The refusal names the set, so the operator is told what they *may* pick.
    """
    await _seed_two_tenants()
    user = _headers(tenant_id=1, user_id=12, username="a-user", role="client")

    for refused in ("a-model-nobody-approved", "genailab-maas-gpt-4o-mini", "gpt-4o"):
        written = await client.put(
            "/settings/agent.model", headers=user, json={"value": refused, "scope": "user"}
        )
        assert written.status_code == 422, f"{refused} was accepted: {written.text}"
        assert _CHOSEN_MODEL in written.json()["detail"]

    # Nothing was stored on the way through: the key still resolves to the platform's.
    after = await client.get("/settings/agent.model", headers=user)
    assert (after.json()["value"], after.json()["source"]) == ("default", "platform")


async def test_the_control_offers_exactly_the_platforms_allowed_set(client, db):
    """The enum a screen renders is the server's set, not a list beside it.

    If the two were separate declarations, this is the assertion that would catch them
    drifting — and drift is the whole failure mode row 6 names, because the day the
    dropdown says more than the validator accepts is the day the control starts lying.
    """
    await _seed_two_tenants()
    user = _headers(tenant_id=1, user_id=12, username="a-user", role="client")
    controls = {c["key"]: c for c in (await client.get("/settings", headers=user)).json()["rows"]}
    descriptor = controls["agent.model"]["control"]
    assert descriptor["control"] == "select"
    assert descriptor["choices"] == ["default", *tenant_selectable_deployments()]
    assert descriptor.get("inert_reason") is None
    assert descriptor["effective"] is True


async def test_a_selected_model_is_what_the_run_routes_and_prices(client, db, monkeypatch):
    """The resolved preference reaches the gateway, and the ledger follows it there.

    Asserted through the route's own resolution seam and the gateway's real routing
    functions — never a live call (the key is ``replace-me`` and every call 403s). What
    would regress here is the seam being wired but not entered: a preference that
    resolves, validates, and then routes nothing is ``agent.model`` inert again with
    more code.
    """
    from aegis.core.models import ModelRole
    from aegis.gateway.routing import model_for, selected_deployment, unit_cost

    from app.api.routes import AuthContext, _resolve_model_choice

    await _seed_two_tenants()
    user = _headers(tenant_id=1, user_id=12, username="a-user", role="client")
    written = await client.put(
        "/settings/agent.model", headers=user, json={"value": _CHOSEN_MODEL, "scope": "user"}
    )
    assert written.status_code == 200, written.text

    auth = AuthContext(
        username="a-user",
        role=Role.CLIENT,
        persona="",
        fine_role="client",
        tenant_id=1,
        user_id=12,
    )
    chosen = await _resolve_model_choice(auth)
    assert chosen == _CHOSEN_MODEL

    platform_model = model_for(ModelRole.GENERATION)
    assert chosen != platform_model, "the fixture picked the default; it proves nothing"
    with selected_deployment(chosen):
        assert model_for(ModelRole.GENERATION) == chosen
        # …and the cost is the chosen model's, not the tier it replaced.
        assert unit_cost(
            ModelRole.GENERATION, prompt_tokens=1000, deployment=model_for(ModelRole.GENERATION)
        ) != unit_cost(ModelRole.GENERATION, prompt_tokens=1000, deployment=platform_model)
    assert model_for(ModelRole.GENERATION) == platform_model


async def test_a_preference_the_platform_has_withdrawn_refuses_the_run(client, db, monkeypatch):
    """A row that was legal when written must not take effect after a withdrawal.

    The run is refused, naming the preference — not quietly served on the platform's
    model under the tenant's chosen name, which would tell them a model is in force that
    is not. This is the second of row 6's two gates and the only one that can fire.
    """
    from fastapi import HTTPException

    from app.api.routes import AuthContext, _resolve_model_choice

    await _seed_two_tenants()
    user = _headers(tenant_id=1, user_id=12, username="a-user", role="client")
    assert (
        await client.put(
            "/settings/agent.model", headers=user, json={"value": _CHOSEN_MODEL, "scope": "user"}
        )
    ).status_code == 200

    # The platform withdraws it from the tenant-selectable set after the fact.
    import aegis.gateway.routing as routing

    monkeypatch.setitem(
        routing._FLEET,
        _CHOSEN_MODEL,
        routing.Deployment(
            _CHOSEN_MODEL,
            routing._FLEET[_CHOSEN_MODEL].role,
            routing._FLEET[_CHOSEN_MODEL].input_rate,
            routing._FLEET[_CHOSEN_MODEL].output_rate,
            tenant_selectable=False,
        ),
    )
    auth = AuthContext(
        username="a-user",
        role=Role.CLIENT,
        persona="",
        fine_role="client",
        tenant_id=1,
        user_id=12,
    )
    with pytest.raises(HTTPException) as caught:
        await _resolve_model_choice(auth)
    assert caught.value.status_code == 409
    assert _CHOSEN_MODEL in caught.value.detail


async def test_the_guardrail_completer_runs_on_a_role_no_tenant_may_redirect(monkeypatch):
    """§7.16 row 7 held against row 6, at the wiring that actually decides it.

    ``aegis.gateway.routing`` reserves the roles the host's safety layers call, but the
    reservation is only true if the host really calls one of them. This reads the role
    off ``app.guardrails._gateway_completer`` — the single place the rails reach the
    gateway — rather than trusting the constant. Point that completer at ``GENERATION``
    and this fails, which is precisely the day a tenant's model becomes the model their
    own injection classifier is run on.
    """
    from aegis.gateway.routing import guardrail_reserved_roles

    from app.guardrails import _gateway_completer

    seen: list[object] = []

    async def _fake_complete(role, messages, **kwargs):  # noqa: ANN001, ANN003, ANN202
        seen.append(role)

        class _R:
            content = "{}"

        return _R()

    import app.core.llm as core_llm

    monkeypatch.setattr(core_llm, "complete", _fake_complete)
    await _gateway_completer([{"role": "user", "content": "hi"}])

    assert seen and seen[0] in guardrail_reserved_roles(), (
        f"the guardrail completer runs on {seen[0]!r}, which a tenant may redirect with "
        "agent.model; the injection classifier would then be judged by the model it is "
        "supposed to be judging"
    )


async def test_a_preference_is_invisible_to_another_tenant(client, db):
    """One tenant's written preference is neither read nor listed by another's.

    The final assertion carries no application predicate at all, so the only thing that
    can return zero rows is the ``tenant_isolation`` policy engaging for the bound scope.
    Drop ``settings`` from the RLS registry and it fails while every HTTP assertion above
    still passes.
    """
    await _seed_two_tenants()
    mine = _headers(tenant_id=1, user_id=12, username="a-user", role="client")
    theirs = _headers(tenant_id=2, user_id=22, username="b-user", role="client")

    await client.put("/settings/agent.model", headers=mine, json={"value": _CHOSEN_MODEL})

    stranger = await client.get("/settings/agent.model", headers=theirs)
    assert stranger.status_code == 200, stranger.text
    assert (stranger.json()["value"], stranger.json()["source"]) == ("default", "platform")

    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, 2)
        assert (await session.execute(select(Setting.key))).scalars().all() == [], (
            "the settings table is readable across tenants with tenant 2's scope bound — "
            "one tenant's preferences are visible to another at the row level"
        )


async def test_a_weakening_is_refused_by_the_resolver_with_its_reason(client, db):
    """A tighten-only key may be tightened and never loosened, and says so.

    The refusals are the resolver's — the route only chooses the status code. A client
    who may not write the key at all gets a 403 carrying ``writable_by``; a user whose
    tenant has already tightened the floor gets a 409 quoting the value that stands.
    """
    await _seed_two_tenants()
    admin = _headers(tenant_id=1, user_id=11, username="a-admin", role="tenant_admin")
    user = _headers(tenant_id=1, user_id=12, username="a-user", role="client")

    # A business user tunes their own preferences, not their tenant's safety posture.
    refused_role = await client.put(
        "/settings/agent.gate_min_risk", headers=user, json={"value": "low"}
    )
    assert refused_role.status_code == 403, refused_role.text
    assert "writable by" in refused_role.json()["detail"]

    # The tenant admin tightens the gate for the whole tenant: high → medium.
    tightened = await client.put(
        "/settings/agent.gate_min_risk",
        headers=admin,
        json={"value": "medium", "scope": "tenant"},
    )
    assert tightened.status_code == 200, tightened.text
    assert (tightened.json()["value"], tightened.json()["source"]) == ("medium", "tenant")

    # …and nobody inside the tenant can then loosen it. The floor a user write is
    # checked against is what the ENCLOSING scopes already resolve to — platform ∩
    # tenant — so the admin's own preference loses to the tenant default they just set.
    loosened = await client.put(
        "/settings/agent.gate_min_risk",
        headers=admin,
        json={"value": "high", "scope": "user"},
    )
    assert loosened.status_code == 409, loosened.text
    assert "may only be tightened" in loosened.json()["detail"]
    assert "medium" in loosened.json()["detail"]

    # The tenant may still withdraw its OWN tightening — the floor a tenant write is
    # checked against is the platform's, not its own previous value. Withdrawing a
    # tightening is not weakening the platform.
    withdrawn = await client.put(
        "/settings/agent.gate_min_risk",
        headers=admin,
        json={"value": "high", "scope": "tenant"},
    )
    assert withdrawn.status_code == 200, withdrawn.text

    unknown = await client.get("/settings/agent.no_such_knob", headers=admin)
    assert unknown.status_code == 404, unknown.text


# ── Gap 3: the effective tool roster ─────────────────────────────────────────


async def test_the_tool_roster_names_the_layer_that_decided_each_row(client, db):
    """Allowed-ness comes from `is_allowed`; the gate floor comes from the tenant.

    Two mutations fail this and nothing else does: widening ``ALLOWLIST['client']``
    changes the count and the ``persona`` verdicts, and ignoring the tenant's resolved
    ``agent.gate_min_risk`` leaves ``add_case_note`` reading ``platform`` after the write
    below — a roster claiming a tool runs unattended when the tenant has said a human
    must see it first.
    """
    await _seed_two_tenants()
    admin = _headers(tenant_id=1, user_id=11, username="a-admin", role="tenant_admin")
    user = _headers(tenant_id=1, user_id=12, username="a-user", role="client")

    mine = (await client.get("/tools", headers=user)).json()
    assert (mine["allowed_count"], mine["total"]) == (1, 4)
    rows = {row["name"]: row for row in mine["rows"]}
    assert rows["add_case_note"]["allowed"] is True
    assert rows["add_case_note"]["decided_by"] == "platform"
    assert rows["add_case_note"]["requires_approval"] is False
    assert rows["update_request_status"]["allowed"] is False
    assert rows["update_request_status"]["decided_by"] == "persona"
    # The read tool is registered but NOT granted to the client persona: its scope is
    # OWN-on-customer_id and nothing pins the filter to the authenticated subject yet,
    # so an enumeration tool here would be a scope change, not a roster line.
    assert rows["find_requests"]["allowed"] is False
    assert rows["find_requests"]["decided_by"] == "persona"

    # The operator persona reaches all four, and the HIGH-risk one already stops at the
    # platform's default gate — which is the tenant layer, at its default value.
    theirs = (await client.get("/tools", headers=admin)).json()
    assert (theirs["allowed_count"], theirs["total"]) == (4, 4)
    operator = {row["name"]: row for row in theirs["rows"]}
    assert operator["update_request_status"]["decided_by"] == "tenant"
    assert operator["update_request_status"]["requires_approval"] is True

    # Now the tenant admin tightens the gate to LOW through the settings surface: the
    # roster must move, because it reads the floor a run reads.
    written = await client.put(
        "/settings/agent.gate_min_risk", headers=admin, json={"value": "low", "scope": "tenant"}
    )
    assert written.status_code == 200, written.text

    after = {row["name"]: row for row in (await client.get("/tools", headers=user)).json()["rows"]}
    assert after["add_case_note"]["allowed"] is True
    assert after["add_case_note"]["requires_approval"] is True
    assert after["add_case_note"]["decided_by"] == "tenant", (
        "the roster still reports add_case_note as unconstrained after its tenant "
        "lowered agent.gate_min_risk to 'low' — the tenant layer is not being read"
    )
