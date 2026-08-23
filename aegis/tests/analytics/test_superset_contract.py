"""What Aegis sends Superset, and what it does when Superset is not there.

Superset runs on the operator's Windows box; this repository is tested on a machine
that cannot reach it, and **no test here opens a socket**. So what is proven is the
Aegis half of the contract — the request, the credential on it, and the behaviour when
the answer is a refusal or nothing at all. Whether Superset 6.1.0 honours the request
is proven by walking `docs/operations/superset-embedded.md` on the Windows box, and
nothing in this file may be read as evidence that it does.

The load-bearing test in the file is
`test_the_data_request_is_authenticated_with_the_guest_token`: the service account's
JWT owns the whole BI instance, and if it were ever the credential on a data request,
the RLS clause — the only thing narrowing that query to one tenant — would not be in
force.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from aegis.analytics import SupersetClient, SupersetConfig, SupersetRejectedError
from aegis.analytics.client import (
    CHART_DATA_PATH,
    CSRF_PATH,
    GUEST_TOKEN_PATH,
    LOGIN_PATH,
)
from aegis.analytics.types import Board, Metric, SupersetUnavailableError
from aegis.retrieval.types import UntenantedPrincipalError

SERVICE_JWT = "service-jwt-that-owns-the-whole-instance"
GUEST_JWT = "guest-jwt-scoped-to-one-tenant"


@dataclass
class FakeResponse:
    status_code: int
    payload: Any = None
    text: str = ""

    def json(self) -> Any:
        if self.payload is None:
            raise ValueError("not json")
        return self.payload


@dataclass
class FakeSuperset:
    """A Superset-shaped HTTP surface. Records every call; opens no socket."""

    responses: dict[str, FakeResponse] = field(default_factory=dict)
    calls: list[dict[str, Any]] = field(default_factory=list)
    explode: bool = False

    async def request(self, method, url, *, json=None, headers=None):
        if self.explode:
            raise ConnectionRefusedError("no listener on 8088")
        path = url.split("8088", 1)[-1]
        self.calls.append(
            {"method": method, "path": path, "json": json, "headers": headers or {}}
        )
        return self.responses.get(path, FakeResponse(200, {}))

    def call(self, path: str) -> dict[str, Any]:
        for entry in self.calls:
            if entry["path"] == path:
                return entry
        seen = [c["path"] for c in self.calls]
        raise AssertionError(f"{path} was never called; calls were {seen}")

    def body_of(self, path: str) -> Any:
        return self.call(path)["json"]

    def bearer(self, path: str) -> str:
        return self.call(path)["headers"].get("Authorization", "")

    def header(self, path: str, name: str) -> str:
        return self.call(path)["headers"].get(name, "")


CONFIG = SupersetConfig(
    base_url="http://localhost:8088",
    username="aegis-service",
    password="s3cret",
    enabled=True,
    embed_enabled=True,
)

BOARD = Board(
    id="spend",
    title="Spend by model",
    summary="What this tenant spent, per model.",
    kinds=frozenset({"chart", "dashboard"}),
    audience=frozenset({"tenant_admin"}),
    datasource_id=7,
    metrics=(Metric(aggregate="SUM", column="cost_usd"),),
    groupby=("model",),
    embedded_uuid="1a2b3c4d-embed-uuid",
    dashboard_id=42,
    time_column="ts",
)


def _wired(**overrides) -> tuple[SupersetClient, FakeSuperset]:
    fake = FakeSuperset(
        responses={
            LOGIN_PATH: FakeResponse(200, {"access_token": SERVICE_JWT}),
            GUEST_TOKEN_PATH: FakeResponse(200, {"token": GUEST_JWT}),
            CHART_DATA_PATH: FakeResponse(
                200,
                {
                    "result": [
                        {
                            "colnames": ["model", "SUM(cost_usd)"],
                            "data": [{"model": "gpt-4o", "SUM(cost_usd)": 12.5}],
                        }
                    ]
                },
            ),
            "/health": FakeResponse(200, {}, text="OK"),
            CSRF_PATH: FakeResponse(200, {"result": "csrf-abc"}),
        }
        | overrides
    )
    return SupersetClient(CONFIG, fake), fake


# ── the credential rule ──────────────────────────────────────────────────────


async def test_the_data_request_is_authenticated_with_the_guest_token():
    """The service JWT owns every tenant's rows. Only the guest token, which carries the
    RLS clause, may authenticate a request that reads any of them.

    **And it travels in its own header.** This assertion used to read
    ``bearer(CHART_DATA_PATH) == f"Bearer {GUEST_JWT}"`` — it asserted the bug. Superset
    validates a guest token against ``GUEST_TOKEN_JWT_SECRET`` and only when it arrives
    in ``GUEST_TOKEN_HEADER_NAME``; sent as ``Authorization``, FAB's JWT manager tries
    to verify it as a *service* token against ``SECRET_KEY`` and answers
    **422 "Signature verification failed"**. Every board was dead against a real
    Superset while this suite was green, because the fake never verified anything —
    which is the failure mode of a fake that answers 200 to whatever it is sent.
    """
    client, fake = _wired()
    await client.board_data(BOARD, 3)

    assert fake.header(CHART_DATA_PATH, "X-GuestToken") == GUEST_JWT
    # Not as a Bearer token, and never the service token, on the data path.
    assert fake.bearer(CHART_DATA_PATH) == ""
    assert SERVICE_JWT not in str(fake.call(CHART_DATA_PATH)["headers"])
    # The service token is used for exactly one thing: minting the guest token.
    assert fake.bearer(GUEST_TOKEN_PATH) == f"Bearer {SERVICE_JWT}"


async def test_the_data_request_names_the_dashboard_that_authorises_it():
    """Naming the dashboard in the guest token is necessary and **not sufficient**.

    Superset's ``raise_for_access`` grants a guest access to a dataset only when the
    chart-data body carries ``form_data.dashboardId`` resolving to a real dashboard.
    Without it the call answers 403 DATASOURCE_SECURITY_ACCESS_ERROR while holding a
    token that names that very dashboard — an authorisation failure that reads like a
    permissions misconfiguration.
    """
    client, fake = _wired()
    await client.board_data(BOARD, 3)

    body = fake.body_of(CHART_DATA_PATH)
    assert body["form_data"]["dashboardId"] == 42
    # The token names the same dashboard, by UUID. Two identifiers, both required.
    assert fake.body_of(GUEST_TOKEN_PATH)["resources"][0]["id"] == "1a2b3c4d-embed-uuid"


async def test_the_guest_token_request_carries_the_tenants_rls_clause():
    client, fake = _wired()
    await client.board_data(BOARD, 3)
    body = fake.call(GUEST_TOKEN_PATH)["json"]

    assert body["rls"] == [{"clause": "tenant_id = 3"}]
    assert body["user"]["username"] == "aegis-tenant-3"
    # The token grants one dashboard, so it cannot be replayed against another board.
    assert body["resources"] == [{"type": "dashboard", "id": "1a2b3c4d-embed-uuid"}]


async def test_the_query_sent_to_superset_is_also_narrowed():
    """Defence in depth on the wire: even if a Superset release regressed guest-token
    RLS, the query Aegis asked for was narrow to begin with."""
    client, fake = _wired()
    await client.board_data(BOARD, 3)
    query = fake.call(CHART_DATA_PATH)["json"]["queries"][0]
    assert query["filters"] == [{"col": "tenant_id", "op": "==", "val": 3}]


async def test_no_request_is_sent_at_all_for_an_unresolved_scope():
    """Fail closed, and fail *early*: not one call reaches Superset."""
    client, fake = _wired()
    with pytest.raises(UntenantedPrincipalError):
        await client.board_data(BOARD, None)
    assert fake.calls == []


async def test_the_service_password_never_appears_in_a_data_request():
    client, fake = _wired()
    await client.board_data(BOARD, 3)
    for entry in fake.calls:
        if entry["path"] == LOGIN_PATH:
            continue
        assert "s3cret" not in repr(entry)


# ── the answer comes back ────────────────────────────────────────────────────


async def test_rows_come_back_shaped_for_a_chart():
    client, _fake = _wired()
    data = await client.board_data(BOARD, 3, window="last_7_days")
    assert data.columns == ("model", "SUM(cost_usd)")
    assert data.rows == ({"model": "gpt-4o", "SUM(cost_usd)": 12.5},)
    assert data.window == "last_7_days"
    assert data.tenant_scoped is True


# ── Superset is not there, or says no ────────────────────────────────────────


async def test_an_unreachable_superset_is_an_unavailable_error_naming_the_fix():
    fake = FakeSuperset(explode=True)
    client = SupersetClient(CONFIG, fake)
    with pytest.raises(SupersetUnavailableError) as caught:
        await client.board_data(BOARD, 3)
    assert "superset run" in caught.value.action


async def test_health_reports_false_rather_than_raising_when_superset_is_down():
    """The page asks this before it decides what to render, so it must not throw."""
    client = SupersetClient(CONFIG, FakeSuperset(explode=True))
    assert await client.healthy() is False


async def test_health_is_false_when_the_feature_is_not_configured():
    client = SupersetClient(SupersetConfig(enabled=True), FakeSuperset())
    assert await client.healthy() is False


async def test_a_refusal_keeps_supersets_own_words():
    client, _fake = _wired(
        **{CHART_DATA_PATH: FakeResponse(403, {"message": "Forbidden: guest role"})}
    )
    with pytest.raises(SupersetRejectedError) as caught:
        await client.board_data(BOARD, 3)
    assert caught.value.status == 403
    assert caught.value.detail == "Forbidden: guest role"


async def test_a_login_that_returns_no_token_is_a_refusal_not_an_empty_bearer():
    client, _fake = _wired(**{LOGIN_PATH: FakeResponse(200, {"nope": 1})})
    with pytest.raises(SupersetRejectedError, match="no access token"):
        await client.board_data(BOARD, 3)


async def test_the_service_token_is_fetched_once_and_reused():
    client, fake = _wired()
    await client.board_data(BOARD, 3)
    await client.board_data(BOARD, 3)
    assert sum(1 for c in fake.calls if c["path"] == LOGIN_PATH) == 1


async def test_the_csrf_token_superset_offers_is_sent_back_on_every_post():
    """Superset guards its POST endpoints with flask-wtf CSRF unless it is turned off.

    Aegis asks for the token and returns it, rather than the alternative on offer —
    telling an operator to set `WTF_CSRF_ENABLED = False` so the integration works, which
    is a security control disabled to paper over a client that would not send a header.
    """
    client, fake = _wired()
    await client.board_data(BOARD, 3)
    assert fake.call(GUEST_TOKEN_PATH)["headers"].get("X-CSRFToken") == "csrf-abc"
    assert fake.call(CHART_DATA_PATH)["headers"].get("X-CSRFToken") == "csrf-abc"


async def test_no_csrf_token_available_is_not_an_error():
    """With `WTF_CSRF_ENABLED` off the endpoint has nothing to give, and the POSTs go
    through without the header. If Superset did want one, it refuses in its own words."""
    client, fake = _wired(**{CSRF_PATH: FakeResponse(404, None, text="Not Found")})
    await client.board_data(BOARD, 3)
    assert "X-CSRFToken" not in fake.call(CHART_DATA_PATH)["headers"]


async def test_the_guest_token_names_the_embedded_uuid_not_the_board_id():
    """`resources[].id` is the uuid `POST /api/v1/dashboard/{id}/embedded` returns.

    Anything else mints a token that Superset accepts and that authorises nothing — a
    200 followed by an unexplained empty chart, which is the worst failure shape there
    is. A board with no uuid is refused before a request is sent.
    """
    client, fake = _wired()
    await client.guest_token(BOARD, 3)
    assert fake.body_of(GUEST_TOKEN_PATH)["resources"][0]["id"] == "1a2b3c4d-embed-uuid"

    orphan = Board(
        id="orphan",
        title="Orphan",
        summary="No dashboard registered.",
        audience=frozenset({"tenant_admin"}),
        datasource_id=7,
    )
    with pytest.raises(ValueError, match="no embedded dashboard UUID"):
        await client.guest_token(orphan, 3)


async def test_the_login_matches_the_call_that_was_observed_working():
    client, fake = _wired()
    await client.service_token()
    assert fake.body_of(LOGIN_PATH) == {
        "username": "aegis-service",
        "password": "s3cret",
        "provider": "db",
        "refresh": True,
    }


# ── a stale cached session ───────────────────────────────────────────────────


@dataclass
class FlakyFirstCall:
    """Superset that refuses the first request to ``path`` and accepts the rest.

    The shape of a cached service token outliving its session: Superset restarts, or
    the fixed cache window (Superset does not report a lifetime) guesses too long, and
    the *next* call is refused even though the credentials are still good.
    """

    inner: FakeSuperset
    path: str
    status: int = 401
    refusals: int = 1
    message: str = "Token has expired"

    async def request(self, method, url, *, json=None, headers=None):
        response = await self.inner.request(method, url, json=json, headers=headers)
        if url.endswith(self.path) and self.refusals > 0:
            self.refusals -= 1
            return FakeResponse(self.status, {"message": self.message})
        return response


async def test_a_stale_session_re_logs_in_once_instead_of_failing_the_board():
    """One expired cached token must not take the whole analytics screen down.

    The page issues a board request per tile — ~18 in parallel — all sharing one cached
    service token. Before this, a token that expired early refused every one of them
    and the screen stayed broken until the fixed cache window elapsed, which is a
    screen-wide outage reported as eighteen separate failures.
    """
    client, fake = _wired()
    flaky = FlakyFirstCall(inner=fake, path=CHART_DATA_PATH)
    client._transport = flaky  # noqa: SLF001 - swapping the seam is the point

    data = await client.board_data(BOARD, 3)

    assert data.rows, "the retry must return the tenant's real rows"
    logins = [c["path"] for c in fake.calls].count(LOGIN_PATH)
    assert logins == 2, "the stale session must be discarded and re-established once"


async def test_a_genuine_refusal_is_retried_once_and_then_raised():
    """The retry must not turn a real refusal into an infinite loop or a silent pass."""
    client, fake = _wired()
    flaky = FlakyFirstCall(inner=fake, path=CHART_DATA_PATH, refusals=99)
    client._transport = flaky  # noqa: SLF001 - swapping the seam is the point

    with pytest.raises(SupersetRejectedError):
        await client.board_data(BOARD, 3)

    attempts = [c["path"] for c in fake.calls].count(CHART_DATA_PATH)
    assert attempts == 2, "exactly one retry, then the refusal is reported"


async def test_a_csrf_mismatch_is_treated_as_a_stale_session_and_recovers():
    """The 400 that actually took analytics down, on a Superset that was up.

    A CSRF token is only meaningful against the Flask session cookie it was minted for,
    so a mismatch IS a stale session — but Superset reports it as a plain 400, which
    fell through a predicate that only knew 401/403/422. Every board then reported
    "Superset refused to mint a guest token" indefinitely while ``/health`` answered
    200, because nothing ever invalidated the poisoned pair.
    """
    client, fake = _wired()
    flaky = FlakyFirstCall(
        inner=fake,
        path=GUEST_TOKEN_PATH,
        status=400,
        message="400 Bad Request: The CSRF tokens do not match.",
    )
    client._transport = flaky  # noqa: SLF001 - swapping the seam is the point

    data = await client.board_data(BOARD, 3)
    assert data.rows, "a CSRF mismatch must re-establish the session, not fail the board"
    assert [c["path"] for c in fake.calls].count(LOGIN_PATH) == 2


async def test_an_ordinary_bad_request_is_still_reported():
    """Only a CSRF 400 is a stale session. A malformed query is a real refusal."""
    client, fake = _wired(
        **{GUEST_TOKEN_PATH: FakeResponse(400, {"message": "datasource 7 does not exist"})}
    )
    with pytest.raises(SupersetRejectedError):
        await client.board_data(BOARD, 3)
    # One attempt, not two: this must not be retried.
    assert [c["path"] for c in fake.calls].count(LOGIN_PATH) == 1
