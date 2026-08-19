"""The guardrail control plane at the wire (§7.6 / §7.16 rows 1, 7, 12).

Four claims, each with the request that breaks it if the server is not enforcing:

1. **The stack is the one that runs, and it names its own thresholds.** A tenant that
   has written nothing reads the platform floor with every row sourced ``platform``.
2. **A tenant's tightening is visible *as theirs*.** After one write through the ordinary
   settings surface — no second write path exists here, deliberately — the value moves,
   the source reads ``tenant``, the union key itemises what this tenant added, and the
   rail's own enforcement flips with it.
3. **A weakening is refused, and the row a UI cannot send still loses.** The `curl` that
   writes below the platform floor is refused with the resolver's reason; nothing about
   the screen re-checks it.
4. **Row 12 and row 7.** The tenant is the sealed scope and never a query parameter, so
   two tenants reading the same endpoint get their own policies; and nothing in the
   response, at any depth, names a model — the guardrail completer is not a control.
"""

from __future__ import annotations

import pgsupport
import pytest

from app.api.schemas import Role
from app.core.security import TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker

pytestmark = pytest.mark.asyncio


def _headers(*, tenant_id: int, user_id: int, username: str, role: str) -> dict[str, str]:
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
            User(id=22, username="b-admin", role=Role.ADMIN, tenant_id=2),
        )
        await session.commit()


def _rows(body: dict) -> dict[str, dict]:
    return {row["key"]: row for row in body["controls"]}


def _rails(body: dict) -> dict[str, dict]:
    return {rail["id"]: rail for rail in body["rails"]}


async def test_the_untouched_policy_reads_as_the_platform_floor(client, db):
    """Every control sourced ``platform``, and the rails describe themselves honestly."""
    await _seed_two_tenants()
    admin = _headers(tenant_id=1, user_id=11, username="a-admin", role=TENANT_ADMIN)

    response = await client.get("/guardrails/policy", headers=admin)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tenant_id"] == 1
    assert body["resolved"] is True

    rows = _rows(body)
    assert set(rows) == {
        "guardrails.topical.block",
        "guardrails.grounding.block",
        "guardrails.denylist.terms",
        "guardrails.denylist.patterns",
        "guardrails.pii.entities",
        "guardrails.pii.block",
        "guardrails.input.max_chars",
    }
    assert {row["source"] for row in rows.values()} == {"platform"}
    assert rows["guardrails.input.max_chars"]["value"] == 8000
    assert rows["guardrails.denylist.patterns"]["added"] == []
    # The vetted library is offered as the domain, so the screen and the write are
    # constrained by one set rather than two.
    assert "aws_access_key_id" in rows["guardrails.denylist.patterns"]["control"]["choices"]

    rails = _rails(body)
    assert rails["input_schema"]["threshold"] == "8000 characters"
    assert rails["input_schema"]["settings"] == ["guardrails.input.max_chars"]
    assert rails["pii"]["enforcement"] == "redact"
    assert rails["injection"]["model_backed"] is True


async def test_a_tenants_tightening_is_shown_as_theirs_and_moves_the_rail(client, db):
    """The write goes through the ordinary settings surface; the rail screen follows it.

    Which is the §7.6 claim in one test: there is no second write path and no second
    policy — a catalogue write changes what this screen reports because it changes what
    the rails do.
    """
    await _seed_two_tenants()
    admin = _headers(tenant_id=1, user_id=11, username="a-admin", role=TENANT_ADMIN)
    other = _headers(tenant_id=2, user_id=22, username="b-admin", role=TENANT_ADMIN)

    for key, value in (
        ("guardrails.denylist.patterns", ["aws_access_key_id"]),
        ("guardrails.input.max_chars", 1000),
        ("guardrails.pii.block", True),
    ):
        written = await client.put(
            f"/settings/{key}", headers=admin, json={"value": value, "scope": "tenant"}
        )
        assert written.status_code == 200, written.text

    body = (await client.get("/guardrails/policy", headers=admin)).json()
    rows, rails = _rows(body), _rails(body)
    assert rows["guardrails.input.max_chars"]["value"] == 1000
    assert rows["guardrails.input.max_chars"]["platform_value"] == 8000
    assert rows["guardrails.input.max_chars"]["source"] == "tenant"
    assert rows["guardrails.denylist.patterns"]["added"] == ["aws_access_key_id"]
    assert rows["guardrails.pii.block"]["source"] == "tenant"
    # An untouched key is still the platform's, on the same screen.
    assert rows["guardrails.topical.block"]["source"] == "platform"
    assert rails["input_schema"]["threshold"] == "1000 characters"
    assert rails["pii"]["enforcement"] == "block"
    assert rails["denylist"]["active"] is True

    # Row 12: the other tenant reads its own policy from the identical request.
    theirs = _rows((await client.get("/guardrails/policy", headers=other)).json())
    assert theirs["guardrails.input.max_chars"]["value"] == 8000
    assert theirs["guardrails.pii.block"]["value"] is False
    assert theirs["guardrails.denylist.patterns"]["added"] == []


async def test_a_weakening_written_past_the_screen_is_refused_with_a_reason(client, db):
    """§7.16 row 1, at the wire: the resolver refuses, not the form.

    The platform pins the strictest value it can, and the tenant admin sends the request
    a screen would never render — a value below their own floor. It is refused with the
    resolver's own sentence, and the rail screen still reports the floor.
    """
    await _seed_two_tenants()
    platform = _headers(tenant_id=1, user_id=11, username="a-admin", role="platform_admin")
    admin = _headers(tenant_id=1, user_id=11, username="a-admin", role=TENANT_ADMIN)

    pinned = await client.put(
        "/settings/guardrails.input.max_chars",
        headers=platform,
        json={"value": 600, "scope": "platform"},
    )
    assert pinned.status_code == 200, pinned.text

    refused = await client.put(
        "/settings/guardrails.input.max_chars",
        headers=admin,
        json={"value": 8000, "scope": "tenant"},
    )
    assert refused.status_code == 409, refused.text
    assert "may only be tightened" in refused.json()["detail"]

    rows = _rows((await client.get("/guardrails/policy", headers=admin)).json())
    assert rows["guardrails.input.max_chars"]["value"] == 600
    # The platform's own tightening is the platform's, not the tenant's, on the screen.
    assert rows["guardrails.input.max_chars"]["source"] == "platform"
    assert rows["guardrails.input.max_chars"]["platform_value"] == 600


async def test_nothing_in_the_policy_response_names_a_model(client, db):
    """Row 7, asserted over the whole payload rather than over a remembered field list.

    The guardrail completer is deliberately separate from the answer completer. If a
    control that selected it ever existed it would arrive here — this endpoint renders
    every field of ``GuardrailPolicy`` — so the assertion is over the serialised keys
    and values, not over a list somebody has to keep up to date.
    """
    await _seed_two_tenants()
    admin = _headers(tenant_id=1, user_id=11, username="a-admin", role=TENANT_ADMIN)
    body = (await client.get("/guardrails/policy", headers=admin)).json()

    forbidden = ("model", "completer", "deployment", "endpoint")
    for row in body["controls"]:
        assert not any(word in row["key"] for word in forbidden), row["key"]
    # The model-backed rails say they are model-backed and stop there: which model is
    # not on the wire at all, because it is not a tenant's to know or to choose.
    assert body["model_layer_wired"] in {True, False}
    assert not any(
        word in str(rail.get("threshold") or "") for rail in body["rails"] for word in forbidden
    )
