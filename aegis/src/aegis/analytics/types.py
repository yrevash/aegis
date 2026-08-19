"""The vocabulary of the Superset seam: a board, a window, a health verdict, a refusal.

Everything here is a value. No HTTP, no config reading, no I/O — so the rules that
decide *what a tenant may see* can be tested without a Superset anywhere near the
process, which is the only way they could be tested at all: Superset runs on the
operator's Windows box and this repository is checked out on macOS.

**A board is a server-side artefact.** The browser names a board by its id and nothing
else. It never sends a datasource, a column, a metric, a row limit or a time range as
free text, because every one of those is a way to ask Superset a question about another
tenant's rows. The catalogue in :mod:`aegis.analytics.catalogue` is the whole set of
questions Aegis is willing to ask on a caller's behalf.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

__all__ = [
    "AGGREGATES",
    "AnalyticsStatus",
    "Board",
    "BoardData",
    "BoardKind",
    "EmbedGrant",
    "HttpResponse",
    "HttpTransport",
    "Metric",
    "SupersetConfig",
    "SupersetRejectedError",
    "SupersetUnavailableError",
    "TimeWindow",
    "WINDOWS",
    "is_safe_identifier",
]

#: What a board is backed by. A ``chart`` board is read server-side through
#: ``POST /api/v1/chart/data`` and drawn by Aegis's own components; a ``dashboard``
#: board is an embedded Superset dashboard. A board may declare both.
BoardKind = Literal["chart", "dashboard"]

#: The **fixed** set of time windows a caller may ask for, mapped to the Superset
#: time-range strings they stand for. A free-text ``time_range`` reaches Superset's
#: date parser, and the point of an enum is that the wire carries a choice from a list
#: this process wrote rather than a string the browser did.
WINDOWS: dict[str, str] = {
    "last_7_days": "Last 7 days",
    "last_30_days": "Last 30 days",
    "last_quarter": "Last quarter",
    "last_year": "Last year",
    "no_filter": "No filter",
}

#: One of :data:`WINDOWS`' keys. Kept as ``str`` rather than a ``Literal`` so the HTTP
#: layer can validate against the single dict instead of a second copy of the names.
TimeWindow = str

#: A SQL identifier Aegis is willing to interpolate into an RLS clause. Deliberately
#: narrower than Postgres allows: no quotes, no dots, no spaces, no leading digit. The
#: tenant column arrives from *configuration*, not from a request, and this is still
#: checked — a config file is a place an injection can be parked just as patiently.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")


def is_safe_identifier(value: str) -> bool:
    """Whether ``value`` may be interpolated into an RLS clause as a column name.

    Args:
        value: The candidate identifier.

    Returns:
        True when it is a bare, unquoted SQL identifier of at most 63 characters.
    """
    return bool(_IDENTIFIER.match(value))


class SupersetUnavailableError(RuntimeError):
    """Superset could not be reached, or is not configured.

    Distinct from :class:`SupersetRejectedError` because the two are different facts
    and only one of them is about the caller: "the BI server is not running" is an
    operator's problem with a command that fixes it, and "Superset refused this
    request" is a question about the request. Collapsing them into one 500 is how a
    page ends up telling a tenant admin to check their permissions when the real
    answer is that nobody started ``superset run``.

    Carries :attr:`action` — the sentence naming what to do — because an unavailable
    state that does not say how to make it available is a shrug.
    """

    def __init__(self, message: str, *, action: str = "") -> None:
        """Create the error.

        Args:
            message: What happened, in one sentence.
            action: What the operator should do about it, in one sentence.
        """
        super().__init__(message)
        self.action = action


class SupersetRejectedError(RuntimeError):
    """Superset answered, and the answer was a refusal.

    Attributes:
        status: The HTTP status Superset returned.
        detail: Superset's own message, when it sent one.
    """

    def __init__(self, message: str, *, status: int, detail: str = "") -> None:
        """Create the error.

        Args:
            message: What happened, in one sentence.
            status: The HTTP status Superset answered with.
            detail: Superset's own error text, verbatim, when there was one.
        """
        super().__init__(message)
        self.status = status
        self.detail = detail


#: The aggregate functions a catalogue metric may name. A fixed list, because the
#: alternative is a free-text expression, and a free-text expression is SQL.
AGGREGATES = frozenset({"SUM", "COUNT", "AVG", "MIN", "MAX", "COUNT_DISTINCT"})


@dataclass(frozen=True)
class Metric:
    """One measure a board asks Superset for.

    Two honest spellings, and deliberately no third:

    * a **saved metric** — the name of a metric already defined on the Superset dataset
      (``name="spend_usd"``). Superset owns the expression; Aegis only names it.
    * an **adhoc metric** — an aggregate over one column
      (``aggregate="SUM", column="cost_usd"``), which Superset assembles itself from
      the structured form.

    What is *not* available is a metric expressed as a SQL string. Superset's query
    context accepts ``expressionType: "SQL"`` with a free-text ``sqlExpression``, and
    a catalogue entry able to carry one would be a place to write a subquery that
    reads past the tenant filter.
    """

    name: str = ""
    aggregate: str = ""
    column: str = ""
    label: str = ""

    @property
    def key(self) -> str:
        """The column name Superset returns this metric's values under."""
        if self.name:
            return self.name
        return self.label or f"{self.aggregate}({self.column})"

    def payload(self) -> str | dict[str, Any]:
        """This metric in the shape Superset's chart-data API takes."""
        if self.name:
            return self.name
        return {
            "expressionType": "SIMPLE",
            "column": {"column_name": self.column},
            "aggregate": self.aggregate,
            "label": self.key,
            "hasCustomLabel": bool(self.label),
        }


@runtime_checkable
class HttpResponse(Protocol):
    """The shape of an HTTP response this package reads.

    Structural, not nominal: ``httpx.Response`` satisfies it, and so does a three-line
    dataclass in a test. That is the whole reason it exists — no test in this package
    may open a socket, and a protocol is how that is enforced by construction rather
    than by everyone remembering.
    """

    @property
    def status_code(self) -> int:
        """The HTTP status."""
        ...

    @property
    def text(self) -> str:
        """The raw response body."""
        ...

    def json(self) -> Any:  # noqa: ANN401 - the vendor's own untyped body
        """The parsed JSON body."""
        ...


@runtime_checkable
class HttpTransport(Protocol):
    """The single method :class:`~aegis.analytics.client.SupersetClient` calls out on.

    Matches ``httpx.AsyncClient.request`` so the composition root can hand one over
    directly, and matches a fake in three lines so no test needs a server.
    """

    async def request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,  # noqa: ANN401 - an arbitrary JSON body
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        """Issue one HTTP request and return the response."""
        ...


@dataclass(frozen=True)
class SupersetConfig:
    """Everything Aegis needs to talk to one Superset, and nothing it must not hold.

    The service credentials live here, on the server, and are the *only* way Aegis
    authenticates to Superset. They are never rendered, never returned by an endpoint
    and never reach the browser: a Superset admin JWT in a tenant's browser is the
    whole BI instance, every tenant's rows included.
    """

    #: Where Superset is served, e.g. ``http://localhost:8088``. Empty means the
    #: feature is not configured, which is a first-class, honest state.
    base_url: str = ""
    #: The Superset account Aegis logs in as to *mint* guest tokens. It needs enough
    #: privilege to call the guest-token endpoint and nothing else.
    username: str = ""
    password: str = ""
    #: Superset's auth provider name. ``db`` for the local metadata-DB accounts the
    #: ``superset fab create-admin`` flow produces.
    provider: str = "db"
    #: The column every tenant-scoped Superset dataset carries. One name, configured
    #: once, used by the guest-token RLS clause *and* by the server-built query filter.
    tenant_column: str = "tenant_id"
    #: Whether the operator has turned the feature on at all. Off is the default: a BI
    #: tool that is not installed must not make Aegis look broken.
    enabled: bool = False
    #: Whether ``EMBEDDED_SUPERSET`` is expected to work on this instance. Separate
    #: from :attr:`enabled` because the server-side data path and the iframe embed fail
    #: independently — 6.1.0's wheel has already shipped three broken paths, and one
    #: of them being the embed must not take the charts down with it.
    embed_enabled: bool = False
    #: Seconds a minted guest token is asked to live. Short on purpose: the token is
    #: the only credential that ever reaches a browser.
    guest_token_ttl_seconds: int = 300

    def configured(self) -> bool:
        """Whether there is enough here to attempt a call at all."""
        return bool(self.enabled and self.base_url and self.username and self.password)

    def url(self, path: str) -> str:
        """Join ``path`` onto the base URL.

        Args:
            path: An absolute path beginning with ``/``.

        Returns:
            The absolute URL.
        """
        return f"{self.base_url.rstrip('/')}{path}"


@dataclass(frozen=True)
class Board:
    """One question Aegis is willing to ask Superset on a caller's behalf.

    Args:
        id: The slug the browser names. The *only* board field that crosses the wire
            inbound.
        title: What the section header says.
        summary: One sentence naming what the operator is looking at.
        kinds: Which paths this board supports — ``chart`` (server-side data, drawn by
            Aegis), ``dashboard`` (embedded Superset), or both.
        audience: The fine roles allowed to select it. A client must not reach an
            operator's dashboards, and this is where that is decided.
        datasource_id: Superset's dataset id for the ``chart`` path.
        datasource_type: ``table`` for a physical/virtual dataset.
        metrics: The measures to request — see :class:`Metric`.
        groupby: The dimension columns to group by. Also the x axis, in order.
        row_limit: Hard ceiling on rows returned, so a board cannot become an export.
        embedded_uuid: Superset's **embedded dashboard UUID** — the value
            ``POST /api/v1/dashboard/{id}/embedded`` returns, *not* the numeric id in
            the dashboard's URL. Required on every board, not only embeddable ones:
            it is the ``resources[].id`` of the guest token, and a guest token is the
            credential **both** paths authenticate with. Passing a numeric id here
            mints a token that silently authorises nothing.
        default_window: Which of :data:`WINDOWS` this board opens on.
        time_column: The temporal column the window filters, when the board has one.
    """

    id: str
    title: str
    summary: str
    kinds: frozenset[BoardKind] = field(default_factory=lambda: frozenset({"chart"}))
    audience: frozenset[str] = field(default_factory=frozenset)
    datasource_id: int | None = None
    datasource_type: str = "table"
    metrics: tuple[Metric, ...] = ()
    groupby: tuple[str, ...] = ()
    row_limit: int = 500
    embedded_uuid: str = ""
    default_window: TimeWindow = "last_30_days"
    time_column: str = ""

    def supports(self, kind: BoardKind) -> bool:
        """Whether this board can be served through ``kind``.

        Both kinds need :attr:`embedded_uuid`, which is not an oversight. Superset
        derives a guest token's *datasource* access from the dashboards named in its
        ``resources``, so a token granting no dashboard has access to no dataset and the
        chart-data call it authenticates would be refused. One rule, no special case.
        """
        if not self.embedded_uuid:
            return False
        if kind == "chart":
            return "chart" in self.kinds and self.datasource_id is not None
        return "dashboard" in self.kinds

    def visible_to(self, fine_role: str) -> bool:
        """Whether a principal holding ``fine_role`` may select this board."""
        return fine_role in self.audience


@dataclass(frozen=True)
class BoardData:
    """The rows one chart board returned, already narrowed to the caller's tenant."""

    board_id: str
    window: TimeWindow
    columns: tuple[str, ...]
    rows: tuple[dict[str, Any], ...]
    #: Whether the rows were filtered to a single tenant. False only for a resolved
    #: platform-wide authority — never as a fallback, and never by default.
    tenant_scoped: bool = True


@dataclass(frozen=True)
class EmbedGrant:
    """A minted guest token and the page it may be used on.

    The token is short-lived and carries the RLS clause. It is the only Superset
    credential that ever leaves this process toward a browser.
    """

    board_id: str
    token: str
    supersetDomain: str  # noqa: N815 - the embedded SDK's own option name
    uuid: str
    expires_in_seconds: int


@dataclass(frozen=True)
class AnalyticsStatus:
    """What the analytics page should say about itself before it draws anything.

    Never raises and never lies: every field is measured or configured, and
    :attr:`action` is an instruction rather than an apology.
    """

    enabled: bool
    configured: bool
    reachable: bool
    embed_enabled: bool
    #: What is true right now, in one sentence.
    detail: str
    #: What the operator should do next, in one sentence. Empty when nothing is wrong.
    action: str = ""
    #: The Superset origin, so the page can name the address that was tried. Never a
    #: credential.
    base_url: str = ""
