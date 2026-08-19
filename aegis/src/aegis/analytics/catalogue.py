"""The board catalogue — the finite set of questions Aegis will ask Superset.

**Why a catalogue rather than a pass-through.** Superset's ``POST /api/v1/chart/data``
takes a *query context*: a datasource, columns, metrics, filters, a row limit. If any
of that came off the wire, a caller could name another tenant's dataset, drop the
tenant filter, or raise the row limit until the board became an export. So the wire
carries a board **id** and a window key, and this module turns those into a query that
this process wrote.

**Why it is loaded from a file rather than hardcoded.** A Superset dataset id and an
embedded-dashboard UUID are facts about one Superset installation. Shipping invented
ones would produce a page that looks configured and returns nothing — the exact
dishonesty this codebase keeps removing. With no catalogue file the catalogue is
**empty**, and the analytics page says so and names the environment variable, which is
a state an operator can act on.

The file is JSON, a list of objects; see ``docs/operations/superset-embedded.md`` for a
worked example against Aegis's own ``usage_ledger`` and ``audit_log``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from aegis.analytics.types import AGGREGATES, WINDOWS, Board, BoardKind, Metric, is_safe_identifier

__all__ = [
    "BoardCatalogue",
    "CatalogueError",
    "load_catalogue",
    "parse_boards",
]

logger = logging.getLogger(__name__)

#: The fine roles a board may be shown to. A board naming anything else is a typo that
#: would otherwise silently hide the board from everybody.
_FINE_ROLES = frozenset(
    {"platform_admin", "tenant_admin", "ai_team", "devops", "client"}
)


class CatalogueError(ValueError):
    """The board catalogue file is malformed, and says which entry and why.

    Raised at load time rather than swallowed: a board that silently fails to parse is
    a section that is mysteriously empty, and "mysteriously empty" is the state this
    whole feature is trying to stop producing.
    """


def _require_str(entry: Mapping[str, Any], key: str, *, where: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CatalogueError(f"board {where}: '{key}' must be a non-empty string")
    return value.strip()


def _kinds(entry: Mapping[str, Any], where: str) -> frozenset[BoardKind]:
    raw = entry.get("kinds", ["chart"])
    if not isinstance(raw, list) or not raw:
        raise CatalogueError(f"board {where}: 'kinds' must be a non-empty list")
    unknown = sorted(k for k in raw if k not in ("chart", "dashboard"))
    if unknown:
        raise CatalogueError(
            f"board {where}: unknown kinds {unknown} — only 'chart' and 'dashboard' exist"
        )
    return frozenset(raw)


def _audience(entry: Mapping[str, Any], where: str) -> frozenset[str]:
    raw = entry.get("audience")
    if not isinstance(raw, list) or not raw:
        raise CatalogueError(
            f"board {where}: 'audience' must list the fine roles allowed to see it. A "
            "board with no audience is reachable by nobody, which is never what was meant."
        )
    unknown = sorted(str(role) for role in raw if role not in _FINE_ROLES)
    if unknown:
        raise CatalogueError(
            f"board {where}: 'audience' names roles that do not exist: {unknown}"
        )
    return frozenset(str(role) for role in raw)


def _columns(entry: Mapping[str, Any], key: str, where: str) -> tuple[str, ...]:
    raw = entry.get(key, [])
    if not isinstance(raw, list):
        raise CatalogueError(f"board {where}: '{key}' must be a list")
    out: list[str] = []
    for value in raw:
        if not isinstance(value, str) or not is_safe_identifier(value):
            raise CatalogueError(
                f"board {where}: '{key}' entry {value!r} is not a bare SQL identifier. "
                "These names are sent to Superset as columns and metrics, so anything "
                "else is refused rather than quoted."
            )
        out.append(value)
    return tuple(out)


def _metrics(entry: Mapping[str, Any], where: str) -> tuple[Metric, ...]:
    """Parse the ``metrics`` list into :class:`Metric` values.

    Two shapes are accepted and a third is refused. A plain string names a metric the
    Superset dataset already defines. An object names an aggregate over one column.
    Anything else — in particular a SQL expression — is rejected, because a catalogue
    file that could carry SQL is a catalogue file that could carry a subquery reading
    past the tenant filter.
    """
    raw = entry.get("metrics", [])
    if not isinstance(raw, list) or not raw:
        raise CatalogueError(f"board {where}: 'metrics' must be a non-empty list")
    out: list[Metric] = []
    for value in raw:
        if isinstance(value, str):
            if not is_safe_identifier(value):
                raise CatalogueError(
                    f"board {where}: metric {value!r} is not the name of a saved Superset "
                    "metric. To aggregate a column, write "
                    '{"aggregate": "SUM", "column": "cost_usd"} instead — a metric is '
                    "never accepted as a SQL string."
                )
            out.append(Metric(name=value))
            continue
        if not isinstance(value, Mapping):
            raise CatalogueError(f"board {where}: metric {value!r} is neither a name nor an object")
        aggregate = str(value.get("aggregate", "")).upper()
        column = str(value.get("column", ""))
        if aggregate not in AGGREGATES:
            raise CatalogueError(
                f"board {where}: metric aggregate {aggregate!r} is not one of "
                f"{sorted(AGGREGATES)}"
            )
        if not is_safe_identifier(column):
            raise CatalogueError(
                f"board {where}: metric column {column!r} is not a bare SQL identifier"
            )
        label = str(value.get("label", ""))
        out.append(Metric(aggregate=aggregate, column=column, label=label))
    return tuple(out)


def _parse_board(entry: Mapping[str, Any], index: int) -> Board:
    """Turn one JSON object into a :class:`Board`, or explain why it cannot."""
    where = f"#{index}"
    board_id = _require_str(entry, "id", where=where)
    where = f"'{board_id}'"
    kinds = _kinds(entry, where)
    window = entry.get("defaultWindow", "last_30_days")
    if window not in WINDOWS:
        raise CatalogueError(
            f"board {where}: defaultWindow {window!r} is not one of {sorted(WINDOWS)}"
        )
    time_column = entry.get("timeColumn", "")
    if time_column and not is_safe_identifier(str(time_column)):
        raise CatalogueError(f"board {where}: timeColumn {time_column!r} is not an identifier")

    datasource_id = entry.get("datasourceId")
    if datasource_id is not None and (
        not isinstance(datasource_id, int) or isinstance(datasource_id, bool)
    ):
        raise CatalogueError(f"board {where}: datasourceId must be an integer")
    if datasource_id is not None and datasource_id <= 0:
        # The shipped catalogue ships ``0`` as a deliberate placeholder. Refusing it
        # here is what turns "I forgot to paste the dataset ids" into a sentence naming
        # the board, instead of a 400 from Superset three screens later.
        raise CatalogueError(
            f"board {where}: datasourceId is {datasource_id}, which is a placeholder. "
            "Paste the Superset dataset id — Superset → Datasets → the dataset's edit "
            "URL, /tablemodelview/edit/<id> — for each board."
        )

    board = Board(
        id=board_id,
        title=_require_str(entry, "title", where=where),
        summary=_require_str(entry, "summary", where=where),
        kinds=kinds,
        audience=_audience(entry, where),
        datasource_id=datasource_id,
        datasource_type=str(entry.get("datasourceType", "table")),
        metrics=_metrics(entry, where),
        groupby=_columns(entry, "groupby", where),
        row_limit=int(entry.get("rowLimit", 500)),
        embedded_uuid=str(entry.get("embeddedUuid", "")),
        default_window=window,
        time_column=str(time_column),
    )
    if not board.embedded_uuid:
        raise CatalogueError(
            f"board {where}: names no embeddedUuid. Every board needs one, not only the "
            "embeddable ones: it is the guest token's resources[].id, and Superset "
            "derives a guest token's dataset access from the dashboards it names — a "
            "token granting no dashboard can read no chart either. Register the "
            "dashboard for embedding to get it:\n"
            "  POST /api/v1/dashboard/<numeric id>/embedded "
            '{"allowed_domains": ["<the Aegis origin>"]}\n'
            "and use the `uuid` it returns. NOT the numeric id in the dashboard's URL — "
            "that mints a token which silently authorises nothing."
        )
    if "chart" in kinds and board.datasource_id is None:
        raise CatalogueError(
            f"board {where}: declares kind 'chart' but names no datasourceId, so the "
            "server-side data path has nothing to query."
        )
    if board.row_limit <= 0 or board.row_limit > 10_000:
        raise CatalogueError(
            f"board {where}: rowLimit {board.row_limit} is outside 1..10000. A board is a "
            "chart, not an export."
        )
    return board


def parse_boards(payload: Any) -> tuple[Board, ...]:  # noqa: ANN401 - parsed JSON
    """Parse a decoded catalogue document into boards.

    Args:
        payload: The decoded JSON — a list of board objects, or an object with a
            ``boards`` key holding one.

    Returns:
        The boards, in file order.

    Raises:
        CatalogueError: If the document, or any board in it, is malformed.
    """
    if isinstance(payload, Mapping):
        payload = payload.get("boards")
    if not isinstance(payload, list):
        raise CatalogueError(
            "the board catalogue must be a JSON list of boards, or an object with a "
            "'boards' list"
        )
    boards = tuple(_parse_board(entry, i) for i, entry in enumerate(payload))
    seen: set[str] = set()
    for board in boards:
        if board.id in seen:
            raise CatalogueError(f"board '{board.id}' is defined twice")
        seen.add(board.id)
    return boards


def load_catalogue(path: str | Path | None) -> tuple[Board, ...]:
    """Load the catalogue from ``path``.

    Args:
        path: The catalogue file. ``None`` or empty means *no boards configured*,
            which is an honest, first-class state and not an error.

    Returns:
        The boards, or an empty tuple when nothing is configured.

    Raises:
        CatalogueError: If a path was given and the file is missing or malformed. A
            configured-but-broken catalogue fails loudly: an operator who pointed at a
            file meant it, and silently serving zero boards would read as "Superset has
            no data".
    """
    if not path:
        return ()
    file = Path(path)
    if not file.is_file():
        raise CatalogueError(
            f"the Superset board catalogue was configured as {file} and there is no file "
            "there. Point AEGIS_SUPERSET_BOARDS at the JSON catalogue, or unset it to run "
            "with no boards."
        )
    try:
        payload = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CatalogueError(
            f"the Superset board catalogue at {file} is not valid JSON: {exc}"
        ) from exc
    return parse_boards(payload)


class BoardCatalogue:
    """The loaded boards, queried by id and narrowed by role.

    Immutable after construction. The role narrowing lives here rather than in the HTTP
    layer so "a client cannot select an operator's dashboard" is one rule in one place,
    checked identically by the list endpoint, the data endpoint and the embed endpoint.
    """

    def __init__(self, boards: Iterable[Board] = ()) -> None:
        """Create the catalogue.

        Args:
            boards: The boards, in nav order.
        """
        self._boards: tuple[Board, ...] = tuple(boards)
        self._by_id = {board.id: board for board in self._boards}

    def __len__(self) -> int:
        """How many boards are configured."""
        return len(self._boards)

    def all(self) -> tuple[Board, ...]:
        """Every configured board, in file order."""
        return self._boards

    def for_role(self, fine_role: str) -> tuple[Board, ...]:
        """The boards a principal holding ``fine_role`` may select."""
        return tuple(board for board in self._boards if board.visible_to(fine_role))

    def get(self, board_id: str, *, fine_role: str) -> Board | None:
        """Return the board ``board_id`` **if** ``fine_role`` may select it.

        Returns ``None`` both for a board that does not exist and for one this role may
        not see — deliberately the same answer, so the endpoint's 404 does not become a
        directory of the boards a caller is not allowed to open.
        """
        board = self._by_id.get(board_id)
        if board is None or not board.visible_to(fine_role):
            return None
        return board
