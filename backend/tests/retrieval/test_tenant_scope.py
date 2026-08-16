"""The backend's retrieval entry points reconcile the caller's scope with governance.

``app.retrieval.retrieve``/``ingest`` sit in front of a **process-wide** retriever
shared by every request, so they are the last place the request's own tenant can be
applied. The governance context bound at the request edge is the authority; these tests
pin all three outcomes — threaded through, left alone, and refused — because the middle
one being wrong is a wrong answer and the third being wrong is a data leak.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from aegis.governance.context import reset_governance_context, set_governance_context
from aegis.governance.types import GovernanceContext

from app.retrieval import RetrievalScope, TenantScopeMismatch
from app.retrieval.pipeline import _governed_scope


@contextmanager
def _governed(tenant_id: int | None):
    """Bind a governance context for the block, restoring the previous one after.

    Set and reset happen in the same (synchronous) context, which is what the
    ``ContextVar`` token requires — a token reset from a different task raises.
    """
    token = set_governance_context(GovernanceContext(tenant_id=tenant_id))
    try:
        yield
    finally:
        reset_governance_context(token)


def test_unbound_governance_leaves_the_callers_scope_alone():
    """The offline / single-tenant path is unchanged: no context, no reconciliation."""
    scope = RetrievalScope(tenant_id=None, persona="ops")
    assert _governed_scope(scope) is scope


def test_governance_tenant_is_threaded_into_an_unscoped_call():
    """Narrowing an unscoped request to the tenant it belongs to is the fix, not a fallback."""
    with _governed(7):
        effective = _governed_scope(
            RetrievalScope(tenant_id=None, persona="ops", corpus_version=3)
        )
    assert effective.tenant_id == 7
    assert effective.persona == "ops"  # the rest of the scope is preserved
    assert effective.corpus_version == 3


def test_a_matching_scope_passes_through():
    scope = RetrievalScope(tenant_id=7)
    with _governed(7):
        assert _governed_scope(scope) == scope


def test_a_conflicting_tenant_is_refused_not_reconciled():
    """Guessing which side is right means either a leak or a wrong answer. Refuse instead."""
    with _governed(7), pytest.raises(TenantScopeMismatch):
        _governed_scope(RetrievalScope(tenant_id=8))


def test_a_context_without_a_tenant_does_not_widen_a_scoped_call():
    """A system/unscoped context must not strip a tenant the caller deliberately set."""
    scope = RetrievalScope(tenant_id=7)
    with _governed(None):
        assert _governed_scope(scope) is scope
