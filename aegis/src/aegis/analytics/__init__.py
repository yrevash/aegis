"""Aegis Analytics — embedded Apache Superset, inside Aegis, scoped by a WHERE clause.

The operator never leaves Aegis to see a chart. Two paths reach the same Superset and
are narrowed by the same sealed authority:

* **the server-side data path** — Aegis builds the query context, calls
  ``POST /api/v1/chart/data`` with a tenant-scoped guest token, and the rows are drawn
  by Aegis's own components in Aegis's light theme;
* **the embed** — a short-lived guest token is handed to Superset's embedded SDK, which
  renders an embedded dashboard in an iframe on an Aegis page.

The interesting part is the same in both: the tenant filter is a ``WHERE`` clause the
browser cannot remove. It is derived from
:meth:`AuthContext.tenant_scope` — the sealed authority — and never from anything the
request carried. See :mod:`aegis.analytics.rls`, which is the whole safety property in
one small file.

Superset is **optional**. Nothing here runs at import or at boot, and
:meth:`~aegis.analytics.service.AnalyticsService.status` reports an honest state rather
than raising, so a deployment with no Superset is a deployment with one page that
explains itself and no other difference at all.
"""

from __future__ import annotations

from aegis.analytics.catalogue import BoardCatalogue, CatalogueError, load_catalogue, parse_boards
from aegis.analytics.client import SupersetClient
from aegis.analytics.query import chart_data_payload, resolve_window, rows_from_chart_data
from aegis.analytics.rls import (
    guest_token_rls,
    guest_user,
    resolved_scope,
    tenant_from_guest_username,
)
from aegis.analytics.service import AnalyticsService
from aegis.analytics.types import (
    WINDOWS,
    AnalyticsStatus,
    Board,
    BoardData,
    EmbedGrant,
    Metric,
    SupersetConfig,
    SupersetRejectedError,
    SupersetUnavailableError,
)

__all__ = [
    "WINDOWS",
    "AnalyticsService",
    "AnalyticsStatus",
    "Board",
    "BoardCatalogue",
    "BoardData",
    "CatalogueError",
    "EmbedGrant",
    "Metric",
    "SupersetClient",
    "SupersetConfig",
    "SupersetRejectedError",
    "SupersetUnavailableError",
    "chart_data_payload",
    "guest_token_rls",
    "guest_user",
    "load_catalogue",
    "parse_boards",
    "resolve_window",
    "resolved_scope",
    "rows_from_chart_data",
    "tenant_from_guest_username",
]
