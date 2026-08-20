"""The Superset HTTP surface, as a contract — because there is no Superset to test against.

Superset runs on the operator's Windows machine; this package is developed and tested
on a machine that cannot reach it. So this module is written against Superset's
documented API shape and **every test drives it through a fake transport**. Nothing in
this package opens a socket, ever. What that buys is precision about what is proven:
the request Aegis builds, the credential it attaches, and how it behaves when the
answer is a refusal or nothing at all are all proven here. Whether Superset 6.1.0
honours those requests is proven only by ``docs/operations/superset-embedded.md`` being
walked on the Windows box.

**The credential rule, and it is the security property.** Aegis logs in with the
service account for exactly one purpose: to *mint guest tokens*. The service JWT never
leaves this process. Every request that can touch tenant rows —
``POST /api/v1/chart/data`` included — is authenticated with a **guest token** that
carries the tenant's RLS clause, so both the server-side chart path and the browser's
embedded iframe are narrowed by the same mechanism. There is no configuration switch
that swaps the guest token for the service token on the data path, because that switch
would be a way to lose the row-level filter without anything looking different.

**Observed on a live Superset 6.1.0**, and the shapes below are written against it
rather than against the documentation:

* ``POST /api/v1/security/login`` with ``{username, password, provider: "db",
  refresh: true}`` → 200 and an ``access_token``.
* ``POST /api/v1/dashboard/{id}/embedded`` with ``{"allowed_domains": [...]}`` → 200 and
  the ``uuid`` that every board must carry. The embed registration endpoint is present
  and working in 6.1.0.
* ``POST /api/v1/security/guest_token/`` with ``{user, resources, rls}`` → 200 and a
  token whose decoded payload carries the clause under **``rls_rules``**. The request
  field is ``rls``; the token field is ``rls_rules``. The clause is inside a *signed*
  token, which is the property this whole design rests on.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from aegis.analytics.query import chart_data_payload, rows_from_chart_data
from aegis.analytics.rls import guest_token_rls, guest_user
from aegis.analytics.types import (
    Board,
    BoardData,
    HttpResponse,
    HttpTransport,
    SupersetConfig,
    SupersetRejectedError,
    SupersetUnavailableError,
    TimeWindow,
)
from aegis.retrieval.types import ALL_TENANTS, TenantScope

__all__ = [
    "CHART_DATA_PATH",
    "CSRF_PATH",
    "GUEST_TOKEN_PATH",
    "HEALTH_PATH",
    "LOGIN_PATH",
    "SupersetClient",
]

logger = logging.getLogger(__name__)

#: Superset's own paths. Named here so a version bump that moves one is a one-line
#: change with a test that fails, rather than four string literals in three files.
LOGIN_PATH = "/api/v1/security/login"
CSRF_PATH = "/api/v1/security/csrf_token/"
GUEST_TOKEN_PATH = "/api/v1/security/guest_token/"
CHART_DATA_PATH = "/api/v1/chart/data"

#: The header a guest token must arrive in. Superset's ``GUEST_TOKEN_HEADER_NAME``
#: defaults to this; a deployment that changes it must change this too.
GUEST_TOKEN_HEADER = "X-GuestToken"
HEALTH_PATH = "/health"

#: How long before a service token's nominal expiry it is refreshed. Superset's access
#: token defaults to a short life and a clock skew of a few seconds between two hosts
#: is normal, so the margin is generous.
_REFRESH_MARGIN_SECONDS = 30.0

#: The sentence an operator gets when Superset is not answering. Names the command that
#: starts it, because "unavailable" with no next step is a shrug.
START_SUPERSET = (
    "Start Superset on the analytics host with "
    "`superset run -p 8088 --with-threads` (SUPERSET_CONFIG_PATH must point at "
    "superset_config.py), then reload this page."
)


class SupersetClient:
    """One Superset instance, behind the four calls Aegis makes to it.

    Args:
        config: Where Superset is and who Aegis logs in as.
        transport: Anything with ``httpx.AsyncClient``'s ``request`` signature. Injected
            rather than constructed so tests can supply a fake, and so the composition
            root owns connection pooling and TLS verification.
        now: Clock seam, for testing token refresh without sleeping.
    """

    def __init__(
        self,
        config: SupersetConfig,
        transport: HttpTransport,
        *,
        now: Any = time.monotonic,  # noqa: ANN401 - a zero-arg clock
    ) -> None:
        """Create the client."""
        self._config = config
        self._transport = transport
        self._now = now
        self._service_token = ""
        self._service_token_expires_at = 0.0
        self._csrf_token = ""

    # ── transport ────────────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,  # noqa: ANN401 - an arbitrary JSON body
        token: str = "",
        guest_token: str = "",
        csrf: str = "",
    ) -> HttpResponse:
        """Issue one request, turning every transport failure into a named error.

        ``token`` is the **service** JWT and rides ``Authorization: Bearer``.
        ``guest_token`` is not a Bearer token and must not be sent as one: Superset
        validates a guest token against ``GUEST_TOKEN_JWT_SECRET``, and only when it
        arrives in ``GUEST_TOKEN_HEADER_NAME``. Sent as ``Authorization``, FAB's JWT
        manager tries to verify it as a service token against ``SECRET_KEY`` and
        answers **422 "Signature verification failed"** — which reads like a rotated
        secret and is really a token in the wrong header.
        """
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if guest_token:
            headers[GUEST_TOKEN_HEADER] = guest_token
        if csrf:
            headers["X-CSRFToken"] = csrf
        url = self._config.url(path)
        try:
            return await self._transport.request(method, url, json=json, headers=headers)
        except Exception as exc:  # noqa: BLE001 - every transport failure is the same fact
            raise SupersetUnavailableError(
                f"Aegis could not reach Superset at {self._config.base_url}.",
                action=START_SUPERSET,
            ) from exc

    @staticmethod
    def _detail(response: HttpResponse) -> str:
        """Superset's own error text, when it sent one worth repeating."""
        try:
            body = response.json()
        except Exception:  # noqa: BLE001 - a non-JSON error body is common and fine
            return response.text[:400]
        if isinstance(body, dict):
            for key in ("message", "msg", "error", "detail"):
                value = body.get(key)
                if isinstance(value, str) and value:
                    return value
        return response.text[:400]

    # ── health ───────────────────────────────────────────────────────────────

    async def healthy(self) -> bool:
        """Whether Superset answers its own health endpoint.

        Never raises: this is the call the analytics page makes before it decides what
        to render, and a page that 500s because the BI tool is down is exactly the
        coupling this feature is not allowed to introduce.
        """
        if not self._config.configured():
            return False
        try:
            response = await self._request("GET", HEALTH_PATH)
        except SupersetUnavailableError:
            return False
        return 200 <= response.status_code < 300

    # ── credentials ──────────────────────────────────────────────────────────

    async def service_token(self) -> str:
        """Log in as the service account and return its access token, cached.

        The returned token stays inside this process. It is the credential that may
        mint guest tokens; it is **not** the credential any data request uses, and it is
        never returned by any Aegis endpoint.

        Raises:
            SupersetUnavailableError: If Superset is unconfigured or unreachable.
            SupersetRejectedError: If Superset refused the login.
        """
        if not self._config.configured():
            raise SupersetUnavailableError(
                "Superset analytics is not configured on this deployment.",
                action=(
                    "Set AEGIS_SUPERSET_ENABLED=true and AEGIS_SUPERSET_BASE_URL / "
                    "AEGIS_SUPERSET_USERNAME / AEGIS_SUPERSET_PASSWORD in the backend "
                    "environment, then restart the backend."
                ),
            )
        if self._service_token and self._now() < self._service_token_expires_at:
            return self._service_token

        response = await self._request(
            "POST",
            LOGIN_PATH,
            json={
                "username": self._config.username,
                "password": self._config.password,
                "provider": self._config.provider,
                "refresh": True,
            },
        )
        if response.status_code >= 400:
            raise SupersetRejectedError(
                "Superset refused the Aegis service account's sign-in.",
                status=response.status_code,
                detail=self._detail(response),
            )
        body = response.json()
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise SupersetRejectedError(
                "Superset accepted the sign-in but returned no access token.",
                status=response.status_code,
                detail=self._detail(response),
            )
        self._service_token = token
        self._csrf_token = await self._fetch_csrf(token)
        # Superset does not report the lifetime on this body, so the cache is held for a
        # deliberately short, fixed period rather than a guessed one. A stale token
        # costs one extra round trip; a token cached past its life costs a 401 on a
        # user-visible request.
        self._service_token_expires_at = self._now() + 240.0 - _REFRESH_MARGIN_SECONDS
        return token

    async def _fetch_csrf(self, service: str) -> str:
        """Fetch a CSRF token for the service session, or return ``""``.

        Superset protects its POST endpoints with flask-wtf CSRF unless
        ``WTF_CSRF_ENABLED`` is off. Rather than telling an operator to turn a security
        control off so Aegis works — which is the shape of every "temporary" hole this
        codebase keeps removing — this asks Superset for the token and sends it back on
        every POST. When the control *is* off the endpoint 404s or returns nothing, and
        the empty string simply means no header is added: harmless either way.

        An empty result is never mistaken for success: the POST that follows carries no
        ``X-CSRFToken``, and if Superset wanted one it refuses in its own words, which
        :class:`SupersetRejectedError` passes through verbatim.
        """
        try:
            response = await self._request("GET", CSRF_PATH, token=service)
        except SupersetUnavailableError:
            return ""
        if response.status_code >= 400:
            return ""
        try:
            body = response.json()
        except Exception:  # noqa: BLE001 - a non-JSON body means no token, not a crash
            return ""
        value = body.get("result") if isinstance(body, dict) else None
        return value if isinstance(value, str) else ""

    async def guest_token(self, board: Board, scope: TenantScope) -> str:
        """Mint a guest token for ``board``, narrowed to ``scope``.

        The token's ``rls`` clause is derived from the sealed scope by
        :func:`~aegis.analytics.rls.guest_token_rls`, and its ``user.username`` carries
        the tenant so Superset's ``DB_CONNECTION_MUTATOR`` can set ``app.tenant_id`` on
        Superset's own Postgres connection.

        Args:
            board: The board the token grants. ``resources`` names exactly one dashboard
                — by the **embedded uuid**, which is what the SDK's ``id`` expects and
                what Superset's guest-token endpoint accepts — so a token minted for one
                board cannot open another.
            scope: The sealed authority.

        Returns:
            The guest token.

        Raises:
            UntenantedPrincipalError: If ``scope`` is not a resolved authority.
            SupersetUnavailableError: If Superset is unconfigured or unreachable.
            SupersetRejectedError: If Superset refused to mint the token.
        """
        if not board.embedded_uuid:
            raise ValueError(
                f"board '{board.id}' names no embedded dashboard UUID, so there is no "
                "resource a guest token could grant. Superset would mint a token that "
                "silently authorises nothing — see aegis.analytics.catalogue for how to "
                "register the dashboard and obtain the uuid."
            )
        rls = guest_token_rls(scope, column=self._config.tenant_column)
        user = guest_user(scope)
        resource_uuid = board.embedded_uuid
        service = await self.service_token()
        response = await self._request(
            "POST",
            GUEST_TOKEN_PATH,
            token=service,
            csrf=self._csrf_token,
            json={
                "user": user,
                "resources": [{"type": "dashboard", "id": resource_uuid}],
                "rls": [dict(clause) for clause in rls],
            },
        )
        if response.status_code >= 400:
            raise SupersetRejectedError(
                "Superset refused to mint a guest token for this board.",
                status=response.status_code,
                detail=self._detail(response),
            )
        body = response.json()
        token = body.get("token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise SupersetRejectedError(
                "Superset answered the guest-token request without a token.",
                status=response.status_code,
                detail=self._detail(response),
            )
        return token

    # ── data ─────────────────────────────────────────────────────────────────

    async def board_data(
        self, board: Board, scope: TenantScope, *, window: TimeWindow | None = None
    ) -> BoardData:
        """Read one chart board's rows, authenticated as the tenant's guest.

        Two independent narrowings apply and both come from ``scope``: the guest token's
        RLS clause, which Superset compiles into the ``WHERE``, and the ``filters`` list
        in the query context Aegis built.

        Args:
            board: The catalogue entry.
            scope: The sealed authority.
            window: A key of :data:`~aegis.analytics.types.WINDOWS`, or ``None``.

        Returns:
            The rows, already narrowed.

        Raises:
            UntenantedPrincipalError: If ``scope`` is not a resolved authority.
            SupersetUnavailableError: If Superset is unconfigured or unreachable.
            SupersetRejectedError: If Superset refused the query.
        """
        payload = chart_data_payload(
            board, scope, tenant_column=self._config.tenant_column, window=window
        )
        token = await self.guest_token(board, scope)
        response = await self._request(
            "POST", CHART_DATA_PATH, guest_token=token, csrf=self._csrf_token, json=payload
        )
        if response.status_code >= 400:
            raise SupersetRejectedError(
                f"Superset refused the query behind '{board.title}'.",
                status=response.status_code,
                detail=self._detail(response),
            )
        columns, rows = rows_from_chart_data(response.json())
        return BoardData(
            board_id=board.id,
            window=window or board.default_window,
            columns=columns,
            rows=rows,
            tenant_scoped=scope is not ALL_TENANTS,
        )
