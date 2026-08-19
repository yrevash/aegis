"""What a principal is allowed to read, and the three states that decision has.

``tenant_id: int | None`` was carrying two unrelated meanings at once — "a platform
operator, so restrict nothing" and "this principal belongs to no tenant" — and both
arrive as ``None``. ``GET /documents/{id}/ingest`` computed
``None if auth.fine_role == PLATFORM_ADMIN else auth.tenant_id``, so a ``client``-role
account whose ``users.tenant_id`` is NULL reached the *privileged* value down the
*unprivileged* branch. ``progress._load_document`` then added no predicate and
``set_tenant_scope(None)`` bound the empty RLS scope, which the ``tenant_isolation``
predicate deliberately does not restrict: both layers open, on a live endpoint, for a
principal ``python -m app.seed`` creates.

Everything here is asserted against the real routes over the real PostgreSQL the
``db`` fixture provides (a ``NOSUPERUSER NOBYPASSRLS`` role, so a passing isolation
assertion is not an artefact of a privileged connection).
"""

from __future__ import annotations

import pgsupport
import pytest
from aegis.governance.models import Budget, BudgetScope, BudgetWindow
from aegis.jobs import Chunk, Document
from aegis.retrieval.types import (
    ALL_TENANTS,
    GraphEdge,
    GraphNode,
    UntenantedPrincipalError,
)

from app.api.routes import AuthContext, _scope_tenant
from app.api.schemas import Role
from app.core.security import (
    MEMBER,
    PLATFORM_ADMIN,
    TENANT_ADMIN,
    create_access_token,
    decode_access_token,
)
from app.data import Tenant, User, get_sessionmaker

# ``asyncio_mode = "auto"`` (pyproject) already runs the coroutines here; no module-level
# ``pytest.mark.asyncio``, because half of this file is deliberately synchronous.

_TENANT = 8801
_OTHER_TENANT = 8802
_USER = 88011
_ROGUE_USER = 88099

#: A token that exists nowhere else in this repository.
_SECRET = "AUDITA-OKAPI-5591"


async def _seed() -> int:
    """Give tenant A a document plus a chunk carrying the secret. Return its id."""
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=_TENANT, name="Scope tenant A"),
            Tenant(id=_OTHER_TENANT, name="Scope tenant B"),
            User(id=_USER, username="scope-a-admin", role=Role.ADMIN, tenant_id=_TENANT),
            # The rogue: a real, non-admin account with NO tenant. ``app.seed`` mints
            # exactly this shape for the "client" platform principal.
            User(
                id=_ROGUE_USER, username="scope-rogue", role=Role.CLIENT, tenant_id=None
            ),
            Budget(
                tenant_id=_TENANT,
                scope_type=BudgetScope.TENANT,
                scope_id=_TENANT,
                window=BudgetWindow.DAY,
                usd_cap=100.0,
            ),
        )
        await session.commit()
    async with get_sessionmaker()() as session:
        document = Document(
            tenant_id=_TENANT,
            filename=f"{_SECRET}-merger-terms.pdf",
            content_sha256=f"{_TENANT:064d}",
            mime_type="application/pdf",
            size_bytes=1024,
            status="SUCCEEDED",
            completed_stage="graph",
            title=f"Confidential {_SECRET} board pack",
        )
        session.add(document)
        await session.flush()
        session.add(
            Chunk(
                tenant_id=_TENANT,
                document_id=document.id,
                content=f"The {_SECRET} acquisition closes in March.",
                embedding=[],
                meta={
                    "entities": [{"id": "e1", "label": _SECRET, "kind": "organization"}],
                    "relations": [],
                },
            )
        )
        await session.commit()
        return document.id


def _rogue_headers() -> dict[str, str]:
    """A bearer for a real, authenticated, NON-admin principal with no tenant."""
    return {
        "Authorization": "Bearer "
        + create_access_token(
            user_id=_ROGUE_USER, username="scope-rogue", role="client", tenant_id=None
        )
    }


def _owner_headers() -> dict[str, str]:
    """A bearer for tenant A's own admin."""
    return {
        "Authorization": "Bearer "
        + create_access_token(
            user_id=_USER,
            username="scope-a-admin",
            role=TENANT_ADMIN,
            tenant_id=_TENANT,
        )
    }


def _ctx(*, role: Role, fine_role: str, tenant_id: object) -> AuthContext:
    return AuthContext(
        username="u",
        role=role,
        persona="client",
        fine_role=fine_role,
        tenant_id=tenant_id,  # type: ignore[arg-type]
        user_id=1,
    )


# ─────────────────────────────────────────────────────────────────────────────
# The three states, at the type
# ─────────────────────────────────────────────────────────────────────────────


def test_a_platform_admin_resolves_to_the_named_all_tenants_authority() -> None:
    """The privileged answer is a sentinel a caller has to name, never an omission."""
    scope = _ctx(role=Role.ADMIN, fine_role=PLATFORM_ADMIN, tenant_id=None).tenant_scope()
    assert scope is ALL_TENANTS


def test_a_tenant_bound_principal_resolves_to_its_own_tenant() -> None:
    assert _ctx(role=Role.ADMIN, fine_role=TENANT_ADMIN, tenant_id=7).tenant_scope() == 7


def test_a_tenant_less_client_resolves_to_no_authority_at_all() -> None:
    """The third state. It is an exception, so it cannot be passed on as a value."""
    with pytest.raises(UntenantedPrincipalError):
        _ctx(role=Role.CLIENT, fine_role=MEMBER, tenant_id=None).tenant_scope()


def test_a_tenant_id_that_is_not_an_integer_resolves_to_no_authority() -> None:
    """A claim that lost its type upstream names no scope — it does not name a wide one."""
    with pytest.raises(UntenantedPrincipalError):
        _ctx(role=Role.CLIENT, fine_role=MEMBER, tenant_id="7").tenant_scope()


def test_scope_tenant_refuses_a_tenant_less_client_rather_than_returning_none() -> None:
    """``_scope_tenant``'s ``None`` now provably means ALL_TENANTS and nothing else.

    It backs ``/audit``, ``/jobs``, ``POST /documents``, the admin listings and the
    forecasts, several of which admit ``devops``/``ai_team`` as well as admins — so the
    same conflation was reachable from more than one endpoint.
    """
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        _scope_tenant(_ctx(role=Role.CLIENT, fine_role=MEMBER, tenant_id=None), None)
    assert excinfo.value.status_code == 403


# ─────────────────────────────────────────────────────────────────────────────
# The endpoints
# ─────────────────────────────────────────────────────────────────────────────


async def test_an_untenanted_non_admin_cannot_read_another_tenants_ingest_log(
    client, db
) -> None:
    """The 404 is deliberate: "deleted", "not yours" and "you have no tenant" are one."""
    document_id = await _seed()

    owner = await client.get(
        f"/documents/{document_id}/ingest", headers=_owner_headers()
    )
    assert owner.status_code == 200, owner.text

    rogue = await client.get(
        f"/documents/{document_id}/ingest", headers=_rogue_headers()
    )

    assert rogue.status_code == 404, (
        "a non-admin principal with no tenant read tenant "
        f"{_TENANT}'s ingest log: {rogue.text[:400]}"
    )
    assert _SECRET not in rogue.text


async def test_the_graph_endpoint_does_not_serve_another_tenants_entities(
    client, db, monkeypatch
) -> None:
    """``GET /graph`` read the whole Neo4j graph with no tenant scope at all.

    Neo4j has no RLS and LightRAG's ``get_knowledge_graph("*")`` takes no predicate, so
    the durable half arrives holding every tenant. Phase 4's ``index`` stage is what
    started writing every tenant's document into that one graph, which turned a dormant
    gap into a live one. The elements below carry no provenance, which is exactly the
    state a graph written before tagging is in — and unknown provenance is refused.
    """
    await _seed()

    async def _whole_graph(*, max_nodes: int = 500):
        return (
            [
                GraphNode(id="own", label="Tenant A Ltd", kind="organization"),
                GraphNode(id="foreign", label=_SECRET, kind="organization"),
            ],
            [GraphEdge(source="own", target="foreign", relation="acquired")],
        )

    import app.api.routes as routes_module

    monkeypatch.setattr("app.retrieval.knowledge_graph", _whole_graph, raising=False)
    monkeypatch.setattr(routes_module, "_resolve_persona", lambda requested, auth: "client")

    res = await client.get("/graph", headers=_owner_headers())
    assert res.status_code == 200, res.text
    labels = [node["label"] for node in res.json()["nodes"]]

    assert _SECRET not in labels, (
        "GET /graph served an entity extracted from another tenant's corpus to a "
        f"tenant-scoped caller: {labels}"
    )


async def test_the_graph_endpoint_shows_a_tenant_its_own_entities(
    client, db, monkeypatch
) -> None:
    """Non-vacuity for the test above: the filter is provenance, not a blanket empty."""
    await _seed()

    async def _tagged_graph(*, max_nodes: int = 500):
        return (
            [
                GraphNode(
                    id="own",
                    label="Tenant A Ltd",
                    kind="organization",
                    owners=(f"t{_TENANT}",),
                ),
                GraphNode(
                    id="foreign",
                    label=_SECRET,
                    kind="organization",
                    owners=(f"t{_OTHER_TENANT}",),
                ),
            ],
            [],
        )

    import app.api.routes as routes_module

    monkeypatch.setattr("app.retrieval.knowledge_graph", _tagged_graph, raising=False)
    monkeypatch.setattr(routes_module, "_resolve_persona", lambda requested, auth: "client")

    res = await client.get("/graph", headers=_owner_headers())
    assert res.status_code == 200, res.text
    assert [n["label"] for n in res.json()["nodes"]] == ["Tenant A Ltd"]


async def test_a_platform_admin_still_reads_the_whole_graph(
    client, db, monkeypatch
) -> None:
    """The platform-wide read survives, because it is now something the route names."""
    await _seed()

    async def _whole_graph(*, max_nodes: int = 500):
        return (
            [
                GraphNode(id="a", label="Tenant A Ltd", kind="organization"),
                GraphNode(id="b", label=_SECRET, kind="organization"),
            ],
            [],
        )

    import app.api.routes as routes_module

    monkeypatch.setattr("app.retrieval.knowledge_graph", _whole_graph, raising=False)
    monkeypatch.setattr(routes_module, "_resolve_persona", lambda requested, auth: "client")

    headers = {
        "Authorization": "Bearer "
        + create_access_token(
            user_id=None, username="root", role=PLATFORM_ADMIN, tenant_id=None
        )
    }
    res = await client.get("/graph", headers=headers)
    assert res.status_code == 200, res.text
    assert {n["label"] for n in res.json()["nodes"]} == {"Tenant A Ltd", _SECRET}


# ─────────────────────────────────────────────────────────────────────────────
# The token claim itself
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("claim", ["7", "../t2", 7.0, [7], True])
def test_a_tenant_id_claim_that_is_not_an_integer_invalidates_the_token(claim) -> None:
    """The value reaches a path segment and an RLS GUC, and neither coerces it.

    ``DocumentStore._tenant_dir`` interpolates it into a filesystem path unchecked while
    the digest beside it *is* checked; ``set_tenant_scope`` writes it into
    ``app.tenant_id``, where the policy's ``substring(... from '^[0-9]+$')`` yields NULL
    for anything non-numeric — which is the deliberate "no scope bound" branch, so a
    non-integer tenant **disengages RLS entirely** rather than merely matching nothing.
    ``RetrievalScope.resolved_tenant_id`` already refused this shape; the auth side now
    agrees. ``True`` is refused with the rest: it is an ``int`` in Python and would
    resolve to tenant 1, which is a real tenant.
    """
    import jwt as pyjwt

    token = create_access_token(
        user_id=1, username="forged", role=MEMBER, tenant_id=claim  # type: ignore[arg-type]
    )
    with pytest.raises(pyjwt.InvalidTokenError):
        decode_access_token(token)


async def test_a_forged_tenant_claim_is_a_401_not_an_unscoped_read(client, db) -> None:
    document_id = await _seed()
    token = create_access_token(
        user_id=_ROGUE_USER,
        username="scope-rogue",
        role=MEMBER,
        tenant_id="8801",  # type: ignore[arg-type]
    )
    res = await client.get(
        f"/documents/{document_id}/ingest",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 401, res.text
