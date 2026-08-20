"""The tenant filter is derived from the sealed scope, and nothing else can produce one.

This is the file that matters. Everything else in `aegis.analytics` is plumbing around
the claim tested here: *a guest token's RLS clause narrows every query Superset runs
under it, and the clause comes from `AuthContext.tenant_scope()`.* If the derivation
fails open, the plumbing faithfully carries an unfiltered query to a browser.

The naive spelling of this function has two outcomes — a clause, or no clause — and
every un-resolvable input lands on "no clause", which in RLS terms is *every tenant*.
So the mutation guards below are the point: `None`, the string `"3"` that a tenant id
becomes after a round trip through a JWT claim, and `True` (which is an `int` in
Python, and would resolve to tenant 1) must all raise rather than return `()`.
"""

from __future__ import annotations

import pytest

from aegis.analytics import chart_data_payload, guest_token_rls, guest_user
from aegis.analytics.rls import (
    GUEST_USERNAME_PLATFORM,
    analytics_connect_options,
    tenant_from_guest_username,
)
from aegis.analytics.types import Board, Metric
from aegis.retrieval.types import ALL_TENANTS, UntenantedPrincipalError


def _board(**kw) -> Board:
    return Board(
        id="spend",
        title="Spend",
        summary="What this tenant spent.",
        audience=frozenset({"tenant_admin"}),
        datasource_id=7,
        metrics=(Metric(aggregate="SUM", column="cost_usd"),),
        groupby=("model",),
        time_column="ts",
        embedded_uuid="dash-uuid",
        dashboard_id=42,
        **kw,
    )


# ── the clause itself ────────────────────────────────────────────────────────


def test_a_tenant_scope_becomes_exactly_one_where_clause():
    assert guest_token_rls(3) == ({"clause": "tenant_id = 3"},)


def test_the_clause_names_the_configured_column():
    assert guest_token_rls(3, column="owner_tenant") == ({"clause": "owner_tenant = 3"},)


def test_a_resolved_platform_authority_is_the_only_way_to_get_no_clause():
    assert guest_token_rls(ALL_TENANTS) == ()


@pytest.mark.parametrize(
    "scope",
    [None, "3", True, False, 3.0, object(), [3]],
    ids=["none", "string-from-a-jwt-claim", "true", "false", "float", "object", "list"],
)
def test_an_unresolved_scope_raises_instead_of_widening(scope):
    """The mutation guard: every one of these returns `()` — every tenant — if the
    resolver is dropped, and `()` is indistinguishable from the platform admin's."""
    with pytest.raises(UntenantedPrincipalError):
        guest_token_rls(scope)


def test_a_tenant_column_that_is_not_an_identifier_is_refused():
    """The column is configuration, not request input — and is still checked, because a
    config file is a place an injection can sit patiently."""
    with pytest.raises(ValueError, match="bare SQL identifier"):
        guest_token_rls(3, column="tenant_id = 1 OR 1=1 --")


# ── the guest username, which is the second isolation layer's only channel ───


def test_the_guest_username_carries_the_tenant_and_round_trips():
    user = guest_user(9)
    assert user["username"] == "aegis-tenant-9"
    assert tenant_from_guest_username(user["username"]) == 9


def test_the_platform_guest_username_does_not_parse_as_a_tenant():
    """`DB_CONNECTION_MUTATOR` must set no GUC for a platform read — not tenant 0, and
    not the string 'platform'."""
    assert guest_user(ALL_TENANTS)["username"] == GUEST_USERNAME_PLATFORM
    assert tenant_from_guest_username(GUEST_USERNAME_PLATFORM) is None


@pytest.mark.parametrize(
    "username", ["admin", "", "aegis-tenant-", "aegis-tenant-x", "aegis-tenant-1;DROP"]
)
def test_a_username_that_does_not_name_a_tenant_yields_none(username):
    assert tenant_from_guest_username(username) is None


def test_an_unresolved_scope_mints_no_guest_user_either():
    with pytest.raises(UntenantedPrincipalError):
        guest_user(None)


# ── the server-built query context ───────────────────────────────────────────


def test_the_query_context_carries_the_tenant_filter():
    payload = chart_data_payload(_board(), 4)
    assert payload["queries"][0]["filters"] == [
        {"col": "tenant_id", "op": "==", "val": 4}
    ]


def test_two_tenants_get_two_different_queries():
    """Mutation guard on the line above: drop the filter and these are equal."""
    assert chart_data_payload(_board(), 4) != chart_data_payload(_board(), 5)


def test_the_query_context_is_built_from_the_board_not_the_request():
    board = _board()
    payload = chart_data_payload(board, 4, window="last_7_days")
    query = payload["queries"][0]
    assert payload["datasource"] == {"id": 7, "type": "table"}
    assert query["metrics"] == [
        {
            "expressionType": "SIMPLE",
            "column": {"column_name": "cost_usd"},
            "aggregate": "SUM",
            "label": "SUM(cost_usd)",
            "hasCustomLabel": False,
        }
    ]
    assert query["columns"] == ["model"]
    assert query["row_limit"] == board.row_limit
    assert query["time_range"] == "Last 7 days"
    # No seam a caller could pour SQL through.
    assert "extras" not in query
    assert "query" not in query


def test_an_unresolved_scope_produces_no_query_at_all():
    with pytest.raises(UntenantedPrincipalError):
        chart_data_payload(_board(), None)


def test_an_unknown_window_is_refused_rather_than_defaulted():
    with pytest.raises(ValueError, match="not a time window"):
        chart_data_payload(_board(), 4, window="all of time")


def test_a_dashboard_only_board_has_no_server_side_query():
    board = Board(
        id="ops",
        title="Ops",
        summary="Embedded only.",
        kinds=frozenset({"dashboard"}),
        audience=frozenset({"platform_admin"}),
        embedded_uuid="abc",
        dashboard_id=42,
    )
    with pytest.raises(ValueError, match="no server-side data path"):
        chart_data_payload(board, 4)


def test_a_board_with_no_embedded_uuid_can_serve_nothing():
    """Superset derives a guest token's dataset access from the dashboards it grants, so
    a board naming none has no credential that could read it — chart path included."""
    board = Board(
        id="orphan",
        title="Orphan",
        summary="No dashboard registered.",
        audience=frozenset({"tenant_admin"}),
        datasource_id=7,
        metrics=(Metric(aggregate="SUM", column="cost_usd"),),
        groupby=("model",),
    )
    assert not board.supports("chart")
    assert not board.supports("dashboard")


# ── the mutator's decision, which otherwise lives only in a config file ──────


def test_the_connection_options_set_nothing_for_a_username_that_names_no_tenant():
    """The whole of the fail-closed design, on the Aegis side of it.

    Superset's service account, and any username format that drifts after an upgrade,
    produce **no** GUC — and the views' predicate then returns zero rows. The failure
    mode this rules out is the one worth ruling out: a hook that quietly stops firing and
    a database layer that quietly stops narrowing while the runbook still claims it.
    """
    assert analytics_connect_options("admin") == ""
    assert analytics_connect_options("aegis_tenant_1") == ""
    assert analytics_connect_options("") == ""


def test_a_tenant_guest_sets_the_same_guc_the_base_policy_reads():
    """One value, so the base table's policy and the view's predicate cannot be pointed
    at two different tenants."""
    assert analytics_connect_options("aegis-tenant-7") == "-c app.tenant_id=7"


def test_reading_every_tenant_needs_its_own_guc_not_an_absent_one():
    """Default-deny with a deliberate opt-out. The platform read sets a GUC in its own
    name and, in particular, does **not** set an empty ``app.tenant_id`` — that is the
    value `set_tenant_scope` writes on reset, i.e. one a session returns to rather than
    one anybody chose."""
    options = analytics_connect_options(GUEST_USERNAME_PLATFORM)
    assert options == "-c app.analytics_all_tenants=on"
    assert "app.tenant_id" not in options
