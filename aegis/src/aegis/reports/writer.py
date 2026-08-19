"""CSV writing for an export that has to survive leaving the browser (§7.12).

Three decisions live here, and each one is about a file that will be opened somewhere
this product cannot see:

**The file states its own scope and window.** A CSV called ``aegis-audit.csv`` in a
downloads folder is evidence of nothing — nobody can tell whose rows it holds, over
what window, or who took it. :class:`ReportMeta` is written into the file as a
key/value preamble above the table, so the answer travels with the bytes. A filename
is a convenience; the preamble is the record.

**The file says when it ended.** The body is streamed, so a truncated download — a
dropped connection, a proxy timeout — produces a shorter file that still parses. The
trailer names the row count actually written, so "is this the whole export?" has an
answer inside the file rather than a comparison against a screen nobody kept open.

**A field is never executable.** A spreadsheet treats a cell beginning ``=``, ``+``,
``-`` or ``@`` as a formula, and an audit trail is full of strings an *attacker* chose:
an actor name, an action, a tenant name. Exporting them verbatim turns the compliance
export into the delivery vehicle (CSV injection / DDE). Every **string** field that
starts with one of those characters is prefixed with an apostrophe, which spreadsheets
strip on display and every CSV parser reads as part of the text. Numbers are untouched,
because they are produced here, not typed by a caller.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime

__all__ = [
    "BOM",
    "CSV_MEDIA_TYPE",
    "CRLF",
    "ReportMeta",
    "content_disposition",
    "csv_row",
    "preamble",
    "report_filename",
    "trailer",
]

#: RFC 4180's line terminator. Excel on Windows is the target reader; ``\n`` alone is
#: read correctly by most tools and wrongly by some of the ones that matter.
CRLF = "\r\n"

#: A UTF-8 byte-order mark, emitted once at the head of the stream. Without it Excel
#: on Windows decodes the file as the system code page, and a tenant named ``Ünal``
#: arrives mojibake in the one document that is supposed to be the record.
BOM = "\ufeff"

#: What these responses are, said precisely: the charset is not optional next to a BOM.
CSV_MEDIA_TYPE = "text/csv; charset=utf-8"

#: The characters a spreadsheet may read as the start of a formula.
_FORMULA_LEADERS = ("=", "+", "-", "@", "\t", "\r")


def _safe(value: object) -> object:
    """Return ``value`` rendered as a cell that a spreadsheet cannot execute.

    Args:
        value: The cell value. ``None`` becomes an empty field; numbers pass through
            unchanged; a string that could be read as a formula is prefixed with an
            apostrophe.

    Returns:
        The value to hand to :mod:`csv`.
    """
    if value is None:
        return ""
    if isinstance(value, str) and value.startswith(_FORMULA_LEADERS):
        return f"'{value}"
    return value


def csv_row(values: Iterable[object]) -> str:
    """Render one RFC 4180 row, CRLF-terminated, with formula-safe fields.

    Args:
        values: The cells, in column order.

    Returns:
        The encoded row including its line terminator.
    """
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator=CRLF, quoting=csv.QUOTE_MINIMAL).writerow(
        [_safe(value) for value in values]
    )
    return buffer.getvalue()


@dataclass(frozen=True)
class ReportMeta:
    """What an export is, written into the export itself.

    Every field is a statement the file has to be able to make on its own, weeks
    later, in a folder full of other CSVs:

    Attributes:
        report: The report id (``audit`` / ``tenant`` / ``budget`` / ``forecast``).
        title: Human title, e.g. ``Audit trail``.
        scope: Whose rows these are, in words — a tenant, or the platform.
        window: The time span covered, or the honest ``All time`` when unbounded.
        source: The table or accessor the rows came from, so the report and the
            screen can be checked against each other.
        generated_at: When the export ran (UTC).
        exported_by: The principal who took it.
        exported_by_role: That principal's RBAC tier — the authority the export used.
        filters: The query parameters that narrowed it, verbatim.
        caveats: Sentences a reader must have before quoting a number in this file.
            Never optional decoration: the ledger's missing outcome column and an
            uncalibrated cumulative envelope both live here.
    """

    report: str
    title: str
    scope: str
    window: str
    source: str
    generated_at: datetime
    exported_by: str
    exported_by_role: str
    filters: Mapping[str, str] = field(default_factory=dict)
    caveats: tuple[str, ...] = ()


def preamble(meta: ReportMeta) -> Iterator[str]:
    """Yield the key/value block that opens the file, ending with a blank row.

    Args:
        meta: What this export is.

    Yields:
        Encoded CSV rows, in the order they are written.
    """
    yield csv_row(["Aegis export", meta.title])
    yield csv_row(["Report", meta.report])
    yield csv_row(["Generated at (UTC)", meta.generated_at.isoformat()])
    yield csv_row(["Scope", meta.scope])
    yield csv_row(["Window", meta.window])
    yield csv_row(["Source", meta.source])
    yield csv_row(["Exported by", f"{meta.exported_by} ({meta.exported_by_role})"])
    for name, value in meta.filters.items():
        yield csv_row([f"Filter: {name}", value])
    for caveat in meta.caveats:
        yield csv_row(["Read this before quoting a number", caveat])
    yield CRLF


def trailer(rows: int) -> Iterator[str]:
    """Yield the closing rows that make a truncated download detectable.

    Args:
        rows: How many data rows were actually written.

    Yields:
        A blank row and the end marker.
    """
    yield CRLF
    yield csv_row(["End of export", f"{rows} data rows"])


def report_filename(report: str, *, scope: str, generated_at: datetime) -> str:
    """Return the download filename for a report.

    Args:
        report: The report id.
        scope: ``platform`` or ``tenant-<id>`` — the scope slug.
        generated_at: When the export ran.

    Returns:
        A filename with no characters that need quoting in a header.
    """
    stamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    return f"aegis-{report}-{scope}-{stamp}.csv"


def content_disposition(filename: str) -> str:
    """Return the ``Content-Disposition`` value that makes a browser save the file.

    The server sends the file; the browser writes it to disk. That is the whole
    mechanism, and it is why no part of this export is assembled in the page: a
    script-built blob is capped by the tab's memory and is inert inside a sandboxed
    frame, while this header works in every browser and in ``curl`` alike.

    Args:
        filename: The name to save as. :func:`report_filename` produces one that needs
            no escaping; anything else is quoted defensively.

    Returns:
        The header value.
    """
    safe = filename.replace('"', "").replace("\\", "")
    return f'attachment; filename="{safe}"'
