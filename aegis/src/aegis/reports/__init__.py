"""Aegis reports — the record, streamed out as a file an operator can take away.

An operator who can only *look* at the audit trail through a console cannot hand it to
an auditor, diff it against last quarter, or keep it after the demo box is gone. This
package is the take-away half: the CSV encoding, the self-describing preamble every
export carries, and the streaming reader for the one table with no natural size bound.

What is *not* here, deliberately: authorisation and tenant scoping. Both belong to the
HTTP layer that knows who is asking (``app.api.routes_reports``), and every reader here
takes the tenant filter as an argument it does not compute. A module that guessed its
own scope would be a second place for the isolation rule to live.
"""

from __future__ import annotations

from aegis.reports.audit_export import AUDIT_COLUMNS, audit_cells, stream_audit_rows
from aegis.reports.writer import (
    BOM,
    CRLF,
    CSV_MEDIA_TYPE,
    ReportMeta,
    content_disposition,
    csv_row,
    preamble,
    report_filename,
    trailer,
)

__all__ = [
    "AUDIT_COLUMNS",
    "BOM",
    "CRLF",
    "CSV_MEDIA_TYPE",
    "ReportMeta",
    "audit_cells",
    "content_disposition",
    "csv_row",
    "preamble",
    "report_filename",
    "stream_audit_rows",
    "trailer",
]
