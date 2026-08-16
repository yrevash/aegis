"""SECURITY: `/vision/analyse` is budget-enforced and ledgered, like every other call.

The regression this pins. ``/vision/analyse`` issues **two** paid
``ModelRole.VISION`` calls — the injection screen and the analyst — and the platform's
governance hook (``app.core.llm._GovernanceHook``) gates *both* budget enforcement and
the usage-ledger write on a bound governance context: ``_governed`` returns ``None``
when nothing is bound, and enforcement and recording are then skipped entirely. The
route bound nothing. Any authenticated user could therefore loop images for spend that
no cap limited and no ledger row recorded — uncapped, unattributed, and invisible on
the token dashboard.

Nothing is stubbed between the ends here: the real route → the real ``aegis.vision``
pipeline → the real ``app.core.llm.complete`` → the real governance hook → the real
``record_usage`` → ``UsageLedger`` rows in the real PostgreSQL database bound by ``db``.
Only ``litellm`` is faked (the gateway credential is a placeholder, so no network call
is possible) and the output rails are replaced, since the real ones reach a
content-safety model and would fail closed offline.
"""

from __future__ import annotations

import base64
import json
import sys
from types import SimpleNamespace

import aegis.gateway.llm as llm_mod
import pgsupport
import pytest
from sqlalchemy import select

from app.core.security import create_access_token
from app.data import (
    Budget,
    BudgetScope,
    BudgetWindow,
    Tenant,
    UsageLedger,
    User,
    get_sessionmaker,
)

pytestmark = pytest.mark.asyncio

TENANT_ID = 1
USER_ID = 2

#: A real 1×1 PNG — valid magic bytes and a readable IHDR, so hygiene clears it.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGA"
    "hKmMIQAAAABJRU5ErkJggg=="
)

CLEAN_SCREEN = json.dumps(
    {
        "contains_text": True,
        "injection": False,
        "reason": "Ordinary invoice text; nothing addressed to an AI.",
    }
)
ANSWER = "An invoice for 1,200 rupees dated 12 August."


def _body() -> dict:
    """The JSON request body for a clean image."""
    return {
        "image_base64": base64.b64encode(PNG_1X1).decode("ascii"),
        "mime_type": "image/png",
        "question": "What is this?",
        "filename": "invoice.png",
    }


def _tenant_headers() -> dict[str, str]:
    """A bearer header for a **tenant-bound** principal.

    Tenant-bound is the whole point: ``_resolve_governance`` yields an empty context
    for an unscoped principal, and the gateway then correctly enforces nothing. The
    defect was that a *scoped* caller was treated as unscoped too.
    """
    token = create_access_token(
        user_id=USER_ID, username="vision-user", role="client", tenant_id=TENANT_ID
    )
    return {"Authorization": f"Bearer {token}"}


def _completion(content: str, *, prompt_tokens: int, completion_tokens: int):
    """A litellm-shaped chat completion response."""
    message = SimpleNamespace(content=content, tool_calls=[])
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
        model="genailab-maas-Llama-3.2-90B-Vision-Instruct",
    )


class _FakeLiteLLM:
    """A litellm stand-in that answers the screen call and then the analyst call."""

    def __init__(self) -> None:
        self.ssl_verify = None
        self.calls: list[dict] = []

    async def acompletion(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs.get("response_format") is not None:
            return _completion(CLEAN_SCREEN, prompt_tokens=400, completion_tokens=30)
        return _completion(ANSWER, prompt_tokens=812, completion_tokens=64)

    def completion_cost(self, *, completion_response):
        # A custom gateway deployment id is not in LiteLLM's cost map — the normal
        # path here, so the cost comes from the configured per-role rates instead.
        return 0.0


@pytest.fixture
def fake_litellm(monkeypatch):
    fake = _FakeLiteLLM()
    monkeypatch.setitem(sys.modules, "litellm", fake)
    monkeypatch.setattr(llm_mod, "_ssl_configured", False)
    monkeypatch.setattr(llm_mod, "_tally", llm_mod._UsageTally())
    return fake


@pytest.fixture
def passing_rails(monkeypatch):
    """Clear the output rails — the real ones need a content-safety model call."""

    async def _pass(text: str, contexts=None):  # noqa: ANN001
        from aegis.core.types import GuardResult, GuardVerdict

        return GuardResult(
            verdict=GuardVerdict.PASS, reason="clean", text=text, layer="pipeline"
        )

    monkeypatch.setattr("app.guardrails.check_output", _pass)


async def _seed(*rows) -> None:
    """Seed the acting principal, then ``rows``, parent-first.

    The ``Tenant``/``User`` pair is not boilerplate: ``usage_ledger`` carries real foreign
    keys to ``tenants.id`` and ``users.id``, so the principal that ``_tenant_headers``
    authenticates as has to exist before the route can ledger a single call against it.
    Under the suite's former SQLite binding those keys were unenforced (SQLite ignores
    foreign keys unless ``PRAGMA foreign_keys=ON``), so the spend recorded here was
    attributed to a tenant and a user that were not in the database — which is precisely
    the unattributable state this file exists to prove impossible.
    """
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=TENANT_ID, name="vision-tenant"),
            User(id=USER_ID, username="vision-user", tenant_id=TENANT_ID),
            *rows,
        )
        await session.commit()


async def _ledger_rows() -> list[UsageLedger]:
    async with get_sessionmaker()() as session:
        return list(
            (
                await session.execute(
                    select(UsageLedger)
                    .where(UsageLedger.tenant_id == TENANT_ID)
                    .order_by(UsageLedger.id)
                )
            )
            .scalars()
            .all()
        )


# ── the ledger ──────────────────────────────────────────────────────────────


async def test_every_paid_vision_call_lands_in_the_usage_ledger(
    client, db, fake_litellm, passing_rails
):
    """Both VISION calls are attributed to the calling tenant AND user.

    Two rows, not one: the injection screen is a paid model call too, and a surface
    that ledgers only the "real" one under-reports the spend an image analysis costs
    by roughly half.
    """
    await _seed(
        Budget(
            tenant_id=TENANT_ID,
            scope_type=BudgetScope.TENANT,
            scope_id=TENANT_ID,
            window=BudgetWindow.DAY,
            usd_cap=100.0,
        )
    )

    resp = await client.post(
        "/vision/analyse", headers=_tenant_headers(), json=_body()
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["analysis"]["outcome"] == "answered"
    assert len(fake_litellm.calls) == 2, "screen + analyst both reached the provider"

    rows = await _ledger_rows()
    assert len(rows) == 2, "an unbound governance context writes ZERO rows — the bug"
    assert [r.user_id for r in rows] == [USER_ID, USER_ID]
    assert [r.model for r in rows] == [
        "genailab-maas-Llama-3.2-90B-Vision-Instruct"
    ] * 2
    # The image is the billable unit a vision call is priced on; a row that records
    # only tokens cannot price it.
    assert [r.images for r in rows] == [1, 1]
    assert [r.prompt_tokens for r in rows] == [400, 812]


async def test_an_unscoped_principal_is_still_a_clean_no_op(
    client, user_headers, db, fake_litellm, passing_rails
):
    """Binding the context must not start governing the ungoverned demo principal.

    The demo/platform operators carry no tenant, so ``_resolve_governance`` yields an
    empty context and the chokepoint enforces nothing and writes nothing — unchanged.
    """
    resp = await client.post("/vision/analyse", headers=user_headers, json=_body())

    assert resp.status_code == 200, resp.text
    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(UsageLedger))).scalars().all()
    assert rows == []


# ── enforcement ─────────────────────────────────────────────────────────────


async def test_a_tenant_over_its_usd_cap_cannot_analyse_images(
    client, db, fake_litellm, passing_rails
):
    """SECURITY: the cap binds BEFORE the spend, so no paid call is ever issued.

    ``fake_litellm.calls == []`` is the assertion that matters. A route that called
    the model and then noticed the cap cannot satisfy it — and a route that never
    bound the context does not notice at all.
    """
    await _seed(
        Budget(
            tenant_id=TENANT_ID,
            scope_type=BudgetScope.TENANT,
            scope_id=TENANT_ID,
            window=BudgetWindow.DAY,
            usd_cap=0.01,
        ),
        UsageLedger(tenant_id=TENANT_ID, user_id=USER_ID, cost_usd=5.0, images=3),
    )

    resp = await client.post(
        "/vision/analyse", headers=_tenant_headers(), json=_body()
    )

    assert resp.status_code == 200, resp.text
    analysis = resp.json()["analysis"]
    # The screen fails closed when its model call cannot be made, so the image is
    # blocked rather than waved through to the analyst.
    assert analysis["outcome"] == "blocked"
    assert analysis["blocked_stage"] == "injection_screen"
    assert fake_litellm.calls == [], "an over-budget tenant reached the provider"

    # No new ledger row: nothing was spent, so nothing is recorded.
    assert len(await _ledger_rows()) == 1


async def test_the_governance_context_does_not_leak_to_the_next_request(
    client, db, fake_litellm, passing_rails, user_headers
):
    """The binding is reset in a ``finally``, so a worker never reuses a tenant.

    A leaked context would attribute the *next* caller's spend to the previous
    caller's tenant — a cross-tenant accounting error, and on a shared worker a
    cross-tenant budget one.
    """
    await _seed(
        Budget(
            tenant_id=TENANT_ID,
            scope_type=BudgetScope.TENANT,
            scope_id=TENANT_ID,
            usd_cap=100.0,
        )
    )

    await client.post("/vision/analyse", headers=_tenant_headers(), json=_body())
    tenant_rows_after_first = len(await _ledger_rows())

    # An unscoped principal follows on the same worker; it must record nothing.
    await client.post("/vision/analyse", headers=user_headers, json=_body())

    assert len(await _ledger_rows()) == tenant_rows_after_first


# ── the binding itself ──────────────────────────────────────────────────────


async def test_the_route_binds_and_resets_the_context_like_voice_does():
    """Pins the shape, so a future edit cannot drop the reset and leave a leak."""
    import inspect

    from app.api import routes

    source = inspect.getsource(routes.vision_analyse)
    assert "_resolve_governance(auth)" in source
    assert "set_governance_context(governance)" in source
    assert "finally:" in source
    assert "reset_governance_context(token)" in source
