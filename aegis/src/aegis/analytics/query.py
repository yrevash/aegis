"""Building the Superset query context — server-side, with the tenant filter welded in.

Superset's ``POST /api/v1/chart/data`` takes a *query context*: which dataset, which
columns, which metrics, which filters, how many rows. This module is the only place
that builds one, and it builds it from a :class:`~aegis.analytics.types.Board` out of
the catalogue plus a sealed :data:`~aegis.retrieval.types.TenantScope` — never from a
request body.

**Two filters, not one, and they are different mechanisms.** The guest token's ``rls``
clause (see :mod:`aegis.analytics.rls`) is compiled into the ``WHERE`` by Superset
itself and cannot be removed by whoever holds the token. The ``filters`` list built
here is part of the query Aegis asked for. They are both derived from the same sealed
scope, and having both means a Superset release that regressed guest-token RLS — this
is 6.1.0, whose wheel has already shipped three broken paths — does not silently become
a cross-tenant read, because the query Aegis sent was narrow to begin with.

**What is *not* here.** No raw SQL, no ``extras.where``, no free-text time range. The
window is a key from :data:`~aegis.analytics.types.WINDOWS`, translated on this side.
"""

from __future__ import annotations

from typing import Any

from aegis.analytics.rls import resolved_scope
from aegis.analytics.types import WINDOWS, Board, TimeWindow, is_safe_identifier
from aegis.retrieval.types import ALL_TENANTS, TenantScope

__all__ = ["chart_data_payload", "resolve_window", "rows_from_chart_data"]


def resolve_window(window: TimeWindow | None, board: Board) -> str:
    """Translate a window key into the Superset time-range string it stands for.

    Args:
        window: A key of :data:`~aegis.analytics.types.WINDOWS`, or ``None`` for the
            board's own default.
        board: The board being read, which carries that default.

    Returns:
        The Superset time-range string.

    Raises:
        ValueError: If ``window`` is not one of the fixed keys. Refused rather than
            defaulted: silently substituting a different range would draw a chart whose
            axis disagrees with the control that produced it.
    """
    key = window or board.default_window
    if key not in WINDOWS:
        raise ValueError(
            f"{key!r} is not a time window this server offers. Choose one of "
            f"{sorted(WINDOWS)}."
        )
    return WINDOWS[key]


def chart_data_payload(
    board: Board,
    scope: TenantScope,
    *,
    tenant_column: str = "tenant_id",
    window: TimeWindow | None = None,
) -> dict[str, Any]:
    """Build the ``POST /api/v1/chart/data`` body for one board and one authority.

    Args:
        board: The catalogue entry. Supplies the datasource, metrics and grouping —
            all of them server-side facts.
        scope: The sealed authority from :meth:`AuthContext.tenant_scope`.
        tenant_column: The tenant column on the dataset, from configuration.
        window: A key of :data:`~aegis.analytics.types.WINDOWS`, or ``None``.

    Returns:
        The query context, ready to serialise.

    Raises:
        UntenantedPrincipalError: If ``scope`` is not a resolved authority — so a
            principal whose tenant is unknown produces no query at all, rather than an
            unfiltered one.
        ValueError: If the board is not chart-backed, the tenant column is not an
            identifier, or the window is unknown.
    """
    if not board.supports("chart"):
        raise ValueError(
            f"board '{board.id}' has no server-side data path — it is an embedded "
            "dashboard only."
        )
    if not is_safe_identifier(tenant_column):
        raise ValueError(
            f"the Superset tenant column is configured as {tenant_column!r}, which is not "
            "a bare SQL identifier."
        )
    resolved = resolved_scope(scope)

    filters: list[dict[str, Any]] = []
    if resolved is not ALL_TENANTS:
        filters.append({"col": tenant_column, "op": "==", "val": int(resolved)})

    query: dict[str, Any] = {
        "columns": list(board.groupby),
        "metrics": [metric.payload() for metric in board.metrics],
        "filters": filters,
        "row_limit": board.row_limit,
        "order_desc": True,
        "orderby": [[metric.payload(), False] for metric in board.metrics[:1]],
    }
    if board.time_column:
        query["granularity"] = board.time_column
        query["time_range"] = resolve_window(window, board)

    payload: dict[str, Any] = {
        "datasource": {"id": board.datasource_id, "type": board.datasource_type},
        "queries": [query],
        "force": False,
        "result_format": "json",
        "result_type": "full",
    }
    if board.dashboard_id is not None:
        # **This is an authorisation field, not a hint.** Superset's
        # ``raise_for_access`` lets a guest read a dataset only when the request body
        # carries ``form_data.dashboardId`` *and* that row resolves by
        # ``Dashboard.id == dashboard_id``. Naming the dashboard in the guest token is
        # necessary and not sufficient: without this key every board answered
        # 403 DATASOURCE_SECURITY_ACCESS_ERROR while holding a token that named it.
        payload["form_data"] = {"dashboardId": board.dashboard_id}
    return payload


def rows_from_chart_data(body: Any) -> tuple[tuple[str, ...], tuple[dict[str, Any], ...]]:  # noqa: ANN401
    """Pull ``(columns, rows)`` out of a Superset chart-data response.

    Superset answers ``{"result": [{"data": [...], "colnames": [...]}]}``. Anything
    else — an empty ``result``, a body that is not a mapping — yields empty columns and
    rows, so a surprising shape renders an honest "no rows" rather than a stack trace.

    Args:
        body: The decoded response body.

    Returns:
        The column names and the row dicts.
    """
    if not isinstance(body, dict):
        return (), ()
    result = body.get("result")
    if not isinstance(result, list) or not result:
        return (), ()
    first = result[0]
    if not isinstance(first, dict):
        return (), ()
    data = first.get("data")
    rows = tuple(row for row in data if isinstance(row, dict)) if isinstance(data, list) else ()
    colnames = first.get("colnames")
    if isinstance(colnames, list) and colnames:
        columns = tuple(str(name) for name in colnames)
    else:
        columns = tuple(rows[0]) if rows else ()
    return columns, rows
