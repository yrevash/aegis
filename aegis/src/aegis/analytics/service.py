"""The analytics entry point: honest states out, no coupling in.

Aegis does not depend on Superset being up. That is a decision, and this class is where
it is implemented: :meth:`AnalyticsService.status` never raises and never guesses. It
returns what is configured, what answered, and — when something is wrong — the sentence
naming what the operator should do about it. Every other surface in Aegis is unaffected
by any of it: nothing here runs at import time, nothing here runs at boot, and no route
outside :mod:`app.api.routes_analytics` calls it.

The three states an operator can be in, and what each says:

``off``
    The feature flag is off. "Superset analytics is turned off on this deployment."
``configured but not answering``
    "Superset is not answering at http://…" plus the command that starts it.
``on``
    Boards render.

A fourth, quieter one matters just as much: **on, reachable, and no boards configured**.
That is the state a fresh install is in, and it is reported as itself rather than as an
empty chart, because an empty chart reads as "you have no data".
"""

from __future__ import annotations

import logging

from aegis.analytics.catalogue import BoardCatalogue
from aegis.analytics.client import START_SUPERSET, SupersetClient
from aegis.analytics.types import (
    AnalyticsStatus,
    Board,
    BoardData,
    EmbedGrant,
    SupersetConfig,
    SupersetUnavailableError,
    TimeWindow,
)
from aegis.retrieval.types import TenantScope

__all__ = ["AnalyticsService"]

logger = logging.getLogger(__name__)

#: What the page says when the operator has not turned the feature on.
_OFF = (
    "Superset analytics is turned off on this deployment, so Aegis is drawing nothing "
    "from it."
)
_OFF_ACTION = (
    "Set AEGIS_SUPERSET_ENABLED=true in the backend environment and restart the "
    "backend to turn it on."
)

#: What the page says when the flag is on but the credentials are not filled in.
_UNCONFIGURED = "Superset analytics is turned on but not configured."
_UNCONFIGURED_ACTION = (
    "Set AEGIS_SUPERSET_BASE_URL, AEGIS_SUPERSET_USERNAME and AEGIS_SUPERSET_PASSWORD "
    "in the backend environment, then restart the backend."
)

#: What the page says when everything is wired and there is simply nothing to show yet.
_NO_BOARDS_ACTION = (
    "Point AEGIS_SUPERSET_BOARDS at a JSON board catalogue — see "
    "docs/operations/superset-embedded.md for a worked example — and restart the backend."
)


class AnalyticsService:
    """Boards, data and embed grants — or an honest reason there are none.

    Args:
        config: The Superset connection settings.
        catalogue: The configured boards.
        client: The Superset HTTP client, or ``None`` when the feature is off or
            unconfigured (in which case every call reports that state rather than
            constructing a client that could not work).
        catalogue_error: Why the catalogue could not be read, when it could not. Passed
            in rather than raised: a malformed config file must not stop Aegis serving
            every other page, and it must not silently degrade to "no boards" either —
            "no boards are configured" and "your catalogue is wrong on line 4" are
            different facts, and the operator is owed whichever one is true.
    """

    def __init__(
        self,
        config: SupersetConfig,
        catalogue: BoardCatalogue,
        client: SupersetClient | None = None,
        *,
        catalogue_error: str = "",
    ) -> None:
        """Create the service."""
        self._config = config
        self._catalogue = catalogue
        self._client = client
        self._catalogue_error = catalogue_error

    @property
    def catalogue(self) -> BoardCatalogue:
        """The configured boards."""
        return self._catalogue

    async def status(self) -> AnalyticsStatus:
        """Describe what the analytics page can do right now. Never raises.

        Returns:
            The state, with a sentence of detail and — when there is something to fix —
            a sentence of instruction.
        """
        if not self._config.enabled:
            return AnalyticsStatus(
                enabled=False,
                configured=False,
                reachable=False,
                embed_enabled=False,
                detail=_OFF,
                action=_OFF_ACTION,
                base_url=self._config.base_url,
            )
        if not self._config.configured() or self._client is None:
            return AnalyticsStatus(
                enabled=True,
                configured=False,
                reachable=False,
                embed_enabled=False,
                detail=_UNCONFIGURED,
                action=_UNCONFIGURED_ACTION,
                base_url=self._config.base_url,
            )
        reachable = await self._client.healthy()
        if not reachable:
            return AnalyticsStatus(
                enabled=True,
                configured=True,
                reachable=False,
                embed_enabled=self._config.embed_enabled,
                detail=f"Superset is not answering at {self._config.base_url}.",
                action=START_SUPERSET,
                base_url=self._config.base_url,
            )
        if self._catalogue_error:
            return AnalyticsStatus(
                enabled=True,
                configured=True,
                reachable=True,
                embed_enabled=self._config.embed_enabled,
                detail="Superset is running, and the board catalogue could not be read.",
                action=self._catalogue_error,
                base_url=self._config.base_url,
            )
        if len(self._catalogue) == 0:
            return AnalyticsStatus(
                enabled=True,
                configured=True,
                reachable=True,
                embed_enabled=self._config.embed_enabled,
                detail="Superset is running, and no boards are configured yet.",
                action=_NO_BOARDS_ACTION,
                base_url=self._config.base_url,
            )
        return AnalyticsStatus(
            enabled=True,
            configured=True,
            reachable=True,
            embed_enabled=self._config.embed_enabled,
            detail=f"Superset is answering at {self._config.base_url}.",
            action="",
            base_url=self._config.base_url,
        )

    def boards_for(self, fine_role: str) -> tuple[Board, ...]:
        """The boards a principal holding ``fine_role`` may select."""
        return self._catalogue.for_role(fine_role)

    def board(self, board_id: str, *, fine_role: str) -> Board | None:
        """One board, if this role may select it. ``None`` otherwise."""
        return self._catalogue.get(board_id, fine_role=fine_role)

    def _live_client(self) -> SupersetClient:
        """Return the client, or refuse with the state the caller should render."""
        if not self._config.enabled:
            raise SupersetUnavailableError(_OFF, action=_OFF_ACTION)
        if self._client is None or not self._config.configured():
            raise SupersetUnavailableError(_UNCONFIGURED, action=_UNCONFIGURED_ACTION)
        return self._client

    async def board_data(
        self, board: Board, scope: TenantScope, *, window: TimeWindow | None = None
    ) -> BoardData:
        """Read one chart board's rows for ``scope``.

        Args:
            board: A board this caller has already been shown to be allowed to select.
            scope: The sealed authority from :meth:`AuthContext.tenant_scope`.
            window: A key of :data:`~aegis.analytics.types.WINDOWS`, or ``None``.

        Returns:
            The rows, narrowed to the caller's tenant.

        Raises:
            SupersetUnavailableError: If the feature is off, unconfigured or unreachable.
            SupersetRejectedError: If Superset refused the query.
            UntenantedPrincipalError: If ``scope`` is not a resolved authority.
        """
        return await self._live_client().board_data(board, scope, window=window)

    async def embed_grant(self, board: Board, scope: TenantScope) -> EmbedGrant:
        """Mint a guest token for the embedded dashboard behind ``board``.

        Args:
            board: A dashboard-backed board this caller may select.
            scope: The sealed authority.

        Returns:
            The grant the browser hands to Superset's embedded SDK.

        Raises:
            SupersetUnavailableError: If the feature is off, unconfigured, unreachable,
                or the embed is not enabled on this deployment.
            SupersetRejectedError: If Superset refused to mint the token.
            UntenantedPrincipalError: If ``scope`` is not a resolved authority.
            ValueError: If ``board`` has no embedded dashboard behind it.
        """
        if not self._config.embed_enabled:
            raise SupersetUnavailableError(
                "The embedded Superset dashboard is turned off on this deployment.",
                action=(
                    "Turn on EMBEDDED_SUPERSET in superset_config.py and set "
                    "AEGIS_SUPERSET_EMBED_ENABLED=true in the backend environment. The "
                    "charts on this page do not need it."
                ),
            )
        if not board.supports("dashboard"):
            raise ValueError(
                f"board '{board.id}' is not backed by an embedded Superset dashboard."
            )
        client = self._live_client()
        token = await client.guest_token(board, scope)
        return EmbedGrant(
            board_id=board.id,
            token=token,
            supersetDomain=self._config.base_url,
            uuid=board.embedded_uuid,
            expires_in_seconds=self._config.guest_token_ttl_seconds,
        )
