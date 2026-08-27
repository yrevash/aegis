"""The live eval suite spends money. The platform has to be able to see it.

`POST /v1/evals/live-run` runs the real Ragas metrics, and both the API response and the
evals screen tell the reader every judge call is *"budget-checked, rate-limited, traced
and written to the usage ledger"*.

That was false for a release. The adapters really do call `aegis.gateway.complete`, but
the route bound no `GovernanceContext`, so every call ran with `ctx=None`: no budget
check, no tenant attribution, no ledger row. Measured before the fix — seven HTTP
invocations, ~108 model calls, ~$0.088 of real spend, **zero rows in `usage_ledger`**.

The claim being *rendered to the reader* is what makes it worth a dedicated test. An
unmetered path is a bug; an unmetered path with a metering badge over it is the failure
mode this repo's `no-dishonest-fallbacks` rule exists for.

The suite itself is replaced with a spy. What is under test is the route's binding, not
Ragas — and a test that actually scored the corpus would cost several dollars a run,
which is precisely the property that makes the missing binding matter.
"""

from __future__ import annotations

import pgsupport
import pytest

from app.api.schemas import Role
from app.core.security import create_access_token
from app.data import Tenant, User, get_sessionmaker

pytestmark = pytest.mark.asyncio


async def _seed() -> None:
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=1, name="Tenant A"),
            User(id=41, username="a-ai", role=Role.AI_TEAM, tenant_id=1),
        )
        await session.commit()


async def test_the_live_run_binds_a_governance_context(client, db, monkeypatch) -> None:
    """Whatever the suite calls, it calls with the caller's identity attached.

    Asserting on the context rather than on ledger rows keeps the test offline and
    deterministic while still failing for the exact defect: delete the
    `set_governance_context` binding from the route and `seen["ctx"]` is `None`.
    """
    await _seed()

    seen: dict[str, object] = {}

    async def _spy(*, complete, embed, limit):  # noqa: ANN001, ANN202
        from aegis.governance.context import get_governance_context

        seen["ctx"] = get_governance_context()
        seen["limit"] = limit
        return []

    import aegis.evals.libs.ragas_suite as suite

    monkeypatch.setattr(suite, "run_ragas_suite", _spy)

    token = create_access_token(
        user_id=41, username="a-ai", role="ai_team", tenant_id=1
    )
    res = await client.post(
        "/evals/live-run?limit=1", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200, res.text

    ctx = seen.get("ctx")
    assert ctx is not None, (
        "the judge calls ran with no governance context — unattributed, unbudgeted and "
        "absent from usage_ledger, while the response says they are metered"
    )
    assert getattr(ctx, "tenant_id", None) == 1
    assert getattr(ctx, "user_id", None) == 41


async def test_the_context_does_not_leak_past_the_request(client, db, monkeypatch) -> None:
    """The binding is reset in a `finally`, so a later unrelated call is not attributed here.

    A context bound and never reset would be the mirror failure of the one above: instead
    of spend nobody can see, spend attributed to whoever happened to run an eval.
    """
    await _seed()

    async def _spy(*, complete, embed, limit):  # noqa: ANN001, ANN202
        return []

    import aegis.evals.libs.ragas_suite as suite

    monkeypatch.setattr(suite, "run_ragas_suite", _spy)

    token = create_access_token(
        user_id=41, username="a-ai", role="ai_team", tenant_id=1
    )
    res = await client.post(
        "/evals/live-run?limit=1", headers={"Authorization": f"Bearer {token}"}
    )
    # Without this the test passes whether or not the route ran at all — a 404 would
    # leave the context unbound and the assertion below would be about nothing.
    assert res.status_code == 200, res.text

    from aegis.governance.context import get_governance_context

    assert get_governance_context() is None
