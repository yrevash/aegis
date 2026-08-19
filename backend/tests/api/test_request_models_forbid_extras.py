"""Every request model refuses a field it does not carry, and says which one.

Pydantic's default is to **drop** an unrecognised field, in silence, with a 200. In this
project that default has now swallowed a request field four separate times — most
recently ``session_id``, and before it ``depth_mode`` — and every time it presented the
same way: "the backend ignored what I sent", nothing in any log, and an afternoon spent
looking at the wrong side of the wire. ``QueryRequest`` and ``SettingWriteRequest``
carried ``extra="forbid"`` precisely because they had already been bitten; the other
thirteen models were one typo away from the same afternoon.

So the rule is the file's, not one model's, and it is asserted as a rule. The static
half below is what makes it a rule — a *new* request model added tomorrow without
``forbid`` fails here, which no single 422 test could catch. The live half is what gives
it teeth: a static check on ``model_config`` proves nothing about what the server
actually returns.

Response models deliberately keep the permissive default: an extra key on the way *out*
breaks nobody, and forbidding them would turn every additive API change into a
deployment-order problem.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.api.schemas import Role
from app.core.security import create_access_token


def _request_models() -> dict[str, type[BaseModel]]:
    """Every ``*Request`` Pydantic model the API's two schema modules declare."""
    import app.main  # noqa: F401 - routes_console may only be reached through the app
    from app.api import routes_console, schemas

    found: dict[str, type[BaseModel]] = {}
    for module in (schemas, routes_console):
        for name in dir(module):
            if not name.endswith("Request"):
                continue
            obj = getattr(module, name)
            if isinstance(obj, type) and issubclass(obj, BaseModel) and obj is not BaseModel:
                found[name] = obj
    return found


def test_every_request_model_forbids_a_field_it_does_not_carry() -> None:
    """The rule, over the real class objects rather than a list somebody maintains.

    Drop ``model_config = ConfigDict(extra="forbid")`` from any request model in
    ``app.api.schemas`` or ``app.api.routes_console`` and this names it.
    """
    models = _request_models()
    assert len(models) >= 17, f"the discovery found too few models to be trusted: {models}"
    permissive = sorted(
        name for name, model in models.items() if model.model_config.get("extra") != "forbid"
    )
    assert permissive == [], (
        "these request models silently drop a field they do not recognise and answer "
        f"200, which is how a request field goes dark for a whole phase: {permissive}"
    )


@pytest.mark.asyncio
async def test_an_unknown_field_is_a_422_that_names_it(client, db) -> None:
    """The live half: ``POST /sessions`` with a stray key is refused, by name.

    ``ChatSessionCreateRequest`` is the one to drive because it is the model that would
    have swallowed the *next* one — the chat surface is where ``session_id`` went dark.
    """
    token = create_access_token(
        user_id=41, username="forbid-probe", role=Role.CLIENT.value, tenant_id=1
    )
    response = await client.post(
        "/sessions",
        headers={"Authorization": f"Bearer {token}"},
        json={"title": "Q3 refunds", "titel": "typo that used to vanish"},
    )
    assert response.status_code == 422, response.text
    assert "titel" in response.text, response.text
