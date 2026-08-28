"""Two tenants do not share one desk.

Two independent auditors reached this from two different surfaces — A2A and MCP — and
got the same result: `northwind.admin` (tenant 1) and `vertex.admin` (tenant 2) each
asked for open service requests and received the **identical 25 ids** out of the
identical "40 matching requests" set. The cause was a single process-wide
`_shared_store`, so every tenant's agent acted on one synthetic desk.

Nothing real leaked — the records are synthetic demo data. That is exactly why it is
worth a test rather than a shrug: the platform's loudest claim is tenant isolation, and
"the isolation holds except in the data we demonstrate it with" is not a sentence anyone
wants to say to a jury that tries two logins side by side.

The narrower invariant the shared global was protecting is preserved: one store *per
tenant*, so the MCP front door and the agent loop still act on the same records for the
same tenant.
"""

from __future__ import annotations

import pytest

from aegis.governance.context import (
    GovernanceContext,
    reset_governance_context,
    set_governance_context,
)

from app.agent import deps as agent_deps


@pytest.fixture(autouse=True)
def _fresh_stores(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test gets an empty map, so ordering cannot decide the outcome."""
    monkeypatch.setattr(agent_deps, "_shared_stores", {})


def _store_for(tenant_id: int | None) -> object:
    ctx = GovernanceContext(tenant_id=tenant_id)
    token = set_governance_context(ctx)
    try:
        return agent_deps.shared_record_store()
    finally:
        reset_governance_context(token)


def test_two_tenants_get_two_different_desks() -> None:
    """The defect, stated as an assertion."""
    one = _store_for(1)
    two = _store_for(2)
    assert one is not two, (
        "both tenants received the same record store — this is the shared-desk bug that "
        "made tenant 1 and tenant 2 return an identical set of request ids"
    )


def test_one_tenant_keeps_exactly_one_desk() -> None:
    """The invariant the shared global existed to protect, narrowed rather than dropped.

    The MCP front door and the agent loop must act on the *same* records for the same
    tenant, or a note added over MCP is invisible to the agent looking at that request.
    """
    assert _store_for(1) is _store_for(1)


def test_a_platform_principal_does_not_borrow_a_tenants_desk() -> None:
    """`None` is a scope of its own, not an alias for whichever tenant ran first.

    Platform-scoped principals and the synchronous seams that run before any context is
    bound both resolve to `None`; giving that key its own store keeps them from writing
    into a tenant's records.
    """
    platform = _store_for(None)
    assert platform is not _store_for(1)
    assert platform is not _store_for(2)
    assert platform is _store_for(None)
