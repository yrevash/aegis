"""Superset is optional, and Aegis says so in a sentence an operator can act on.

The decision being tested: *Aegis must never fail because a BI tool is not running.*
So `status()` has no failure mode — every state it can be in is a value, each one
naming what is true and what to do next. A page that renders a spinner forever, or a
grid of zeros, or a 500, would each be a different way of lying about the same fact.

Also tested here: the catalogue's audience rule, which is what stops a client reaching
an operator's dashboard. It is asserted through `get()` rather than through the list,
because hiding a board in the nav is a courtesy and refusing it on the server is the
enforcement.
"""

from __future__ import annotations

import json

import pytest

from aegis.analytics import (
    AnalyticsService,
    BoardCatalogue,
    CatalogueError,
    SupersetClient,
    SupersetConfig,
    load_catalogue,
    parse_boards,
)
from aegis.analytics.types import SupersetUnavailableError
from tests.analytics.test_superset_contract import BOARD, FakeResponse, FakeSuperset

CATALOGUE = BoardCatalogue([BOARD])


def _service(config: SupersetConfig, *, up: bool = True, boards=(BOARD,)) -> AnalyticsService:
    fake = FakeSuperset(responses={"/health": FakeResponse(200, {}, text="OK")}, explode=not up)
    client = SupersetClient(config, fake) if config.configured() else None
    return AnalyticsService(config, BoardCatalogue(boards), client)


ON = SupersetConfig(
    base_url="http://localhost:8088",
    username="aegis-service",
    password="s3cret",
    enabled=True,
    embed_enabled=True,
)


# ── the four honest states ───────────────────────────────────────────────────


async def test_the_feature_off_says_so_and_names_the_switch():
    status = await _service(SupersetConfig()).status()
    assert (status.enabled, status.reachable) == (False, False)
    assert "turned off" in status.detail
    assert "AEGIS_SUPERSET_ENABLED" in status.action


async def test_turned_on_but_unconfigured_names_the_variables():
    status = await _service(SupersetConfig(enabled=True)).status()
    assert (status.enabled, status.configured) == (True, False)
    assert "AEGIS_SUPERSET_BASE_URL" in status.action


async def test_superset_down_names_the_address_and_the_command():
    status = await _service(ON, up=False).status()
    assert status.configured is True
    assert status.reachable is False
    assert "localhost:8088" in status.detail
    assert "superset run" in status.action


async def test_reachable_with_no_boards_is_reported_as_itself():
    """A fresh install has no boards. Reported as 'no boards configured', never as an
    empty chart — an empty chart reads as 'you have no data'."""
    status = await _service(ON, boards=()).status()
    assert status.reachable is True
    assert "no boards are configured" in status.detail
    assert "AEGIS_SUPERSET_BOARDS" in status.action


async def test_everything_up_reports_no_action():
    status = await _service(ON).status()
    assert (status.enabled, status.configured, status.reachable) == (True, True, True)
    assert status.action == ""


async def test_reading_a_board_with_superset_down_refuses_with_an_instruction():
    with pytest.raises(SupersetUnavailableError) as caught:
        await _service(ON, up=False).board_data(BOARD, 3)
    assert "superset run" in caught.value.action


async def test_the_embed_being_off_does_not_take_the_charts_with_it():
    """6.1.0 ships broken paths. `EMBEDDED_SUPERSET` being one of them must cost the
    iframe and nothing else."""
    service = _service(SupersetConfig(**{**ON.__dict__, "embed_enabled": False}))
    with pytest.raises(SupersetUnavailableError, match="embedded Superset dashboard"):
        await service.embed_grant(BOARD, 3)
    data = await _service(
        SupersetConfig(**{**ON.__dict__, "embed_enabled": False})
    ).status()
    assert data.reachable is True


# ── who may select which board ───────────────────────────────────────────────


def test_a_role_outside_the_audience_gets_nothing_not_a_different_error():
    """Same answer for 'no such board' and 'not yours', so a 404 cannot be used to
    enumerate the boards a caller is not allowed to open."""
    assert CATALOGUE.get("spend", fine_role="tenant_admin") is BOARD
    assert CATALOGUE.get("spend", fine_role="client") is None
    assert CATALOGUE.get("no-such-board", fine_role="tenant_admin") is None


def test_the_listing_is_narrowed_by_role_too():
    assert CATALOGUE.for_role("tenant_admin") == (BOARD,)
    assert CATALOGUE.for_role("client") == ()


# ── the catalogue file ───────────────────────────────────────────────────────


_GOOD = [
    {
        "id": "spend",
        "title": "Spend by model",
        "summary": "What this tenant spent, per model.",
        "audience": ["tenant_admin", "platform_admin"],
        "datasourceId": 7,
        "metrics": [{"aggregate": "SUM", "column": "cost_usd", "label": "Spend (USD)"}],
        "groupby": ["model"],
        "timeColumn": "ts",
        "embeddedUuid": "dash-uuid",
        "dashboardId": 42,
    }
]


def test_no_catalogue_is_zero_boards_but_a_missing_one_fails_loudly(tmp_path):
    """Unset means "no boards", which is a state. Set-but-absent means an operator meant
    something and it is not there — serving zero boards silently would read as
    'Superset has no data'."""
    assert load_catalogue(None) == ()
    assert load_catalogue("") == ()
    with pytest.raises(CatalogueError, match="no file there"):
        load_catalogue(tmp_path / "nope.json")


def test_a_board_with_no_audience_is_refused():
    entry = dict(_GOOD[0])
    entry.pop("audience")
    with pytest.raises(CatalogueError, match="audience"):
        parse_boards([entry])


def test_a_groupby_that_is_not_an_identifier_is_refused():
    entry = dict(_GOOD[0]) | {"groupby": ["model; DROP TABLE usage_ledger"]}
    with pytest.raises(CatalogueError, match="bare SQL identifier"):
        parse_boards([entry])


def test_a_metric_written_as_sql_is_refused():
    """The catalogue is a config file on a server, which is exactly the kind of place a
    subquery reading past the tenant filter would sit quietly."""
    entry = dict(_GOOD[0]) | {
        "metrics": ["SUM(cost_usd) FROM usage_ledger WHERE 1=1 --"]
    }
    with pytest.raises(CatalogueError, match="never accepted as a SQL string"):
        parse_boards([entry])


def test_the_placeholder_datasource_id_is_refused_by_name():
    """The shipped catalogue ships `0`. An operator who forgets to paste the real ids
    gets a sentence naming the board, not a 400 from Superset three screens later."""
    entry = dict(_GOOD[0]) | {"datasourceId": 0}
    with pytest.raises(CatalogueError, match="placeholder"):
        parse_boards([entry])


def test_an_unknown_aggregate_is_refused():
    entry = dict(_GOOD[0]) | {"metrics": [{"aggregate": "EXEC", "column": "cost_usd"}]}
    with pytest.raises(CatalogueError, match="not one of"):
        parse_boards([entry])


def test_a_board_with_no_embedded_uuid_is_refused_by_name(tmp_path):
    """Every board needs one, chart-only included — and the message says how to get it,
    including that it is not the numeric id in the dashboard's URL."""
    entry = dict(_GOOD[0])
    entry.pop("embeddedUuid")
    path = tmp_path / "boards.json"
    path.write_text(json.dumps([entry]))
    with pytest.raises(CatalogueError, match="dashboard/<numeric id>/embedded"):
        load_catalogue(path)


def test_a_chart_board_with_no_datasource_is_refused_at_load(tmp_path):
    entry = dict(_GOOD[0])
    entry.pop("datasourceId")
    path = tmp_path / "boards.json"
    path.write_text(json.dumps([entry]))
    with pytest.raises(CatalogueError, match="names no datasourceId"):
        load_catalogue(path)


# ── the version-controlled Superset artefacts ────────────────────────────────


def test_every_shipped_dataset_names_a_view_that_exists_with_the_columns_it_claims():
    """`docs/operations/superset/` is the reproducible half of the integration.

    A dashboard that exists only as clicks in somebody's `superset.db` is gone the
    moment that file is, so the datasets are committed. Committed artefacts rot, and
    they rot invisibly — a renamed column in `provision.py` leaves a dataset YAML
    pointing at a column Superset will happily import and then fail to query. This is
    the guard, and it is a regex rather than a YAML parse on purpose: `pyyaml` is not a
    declared dependency of either package, and adding one to police a doc directory
    would be the wrong trade.
    """
    import re
    from pathlib import Path

    from aegis.analytics.provision import ANALYTICS_VIEWS

    root = Path(__file__).resolve().parents[3] / "docs" / "operations" / "superset"
    datasets = sorted((root / "datasets" / "Aegis").glob("*.yaml"))
    assert datasets, f"no dataset artefacts under {root}"

    by_name = {view.name: view for view in ANALYTICS_VIEWS}
    for path in datasets:
        text = path.read_text()
        table = re.search(r"^table_name:\s*(\S+)", text, re.M)
        assert table is not None, f"{path.name} declares no table_name"
        view = by_name.get(table.group(1))
        assert view is not None, (
            f"{path.name} is a dataset over '{table.group(1)}', which "
            "aegis.analytics.provision no longer creates"
        )
        columns = set(re.findall(r"^- column_name:\s*(\w+)", text, re.M))
        assert "tenant_id" in columns, f"{path.name} has no tenant_id for the RLS clause"
        missing = sorted(c for c in columns if f"AS {c}" not in view.sql)
        assert not missing, f"{path.name} declares columns the view does not select: {missing}"
