"""The CSV a compliance reviewer opens has to be honest about itself, and inert.

Two claims, both of which are about the file *after* it has left this product:

1. A cell is data, never a program. An audit trail is full of strings an attacker
   chose; a spreadsheet reads a leading ``=`` as a formula.
2. The file says what it is and when it ended. A truncated stream still parses, so
   "is this the whole export?" must be answerable from the bytes.
"""

from __future__ import annotations

from datetime import UTC, datetime

from aegis.reports import ReportMeta, content_disposition, csv_row, preamble, trailer


def _meta(**over) -> ReportMeta:
    base = {
        "report": "audit",
        "title": "Audit trail",
        "scope": "Tenant 7 only — no other tenant's rows are in this file",
        "window": "2026-08-01T00:00:00+00:00 to now (UTC, inclusive)",
        "source": "audit_log",
        "generated_at": datetime(2026, 8, 19, 10, 30, tzinfo=UTC),
        "exported_by": "alice",
        "exported_by_role": "platform_admin",
        "filters": {"actor": "bob"},
        "caveats": ("The ledger records no outcome for a call.",),
    }
    base.update(over)
    return ReportMeta(**base)


def test_a_string_that_looks_like_a_formula_is_neutralised():
    # The classic CSV-injection payload, arriving as an audit actor name.
    row = csv_row(["=cmd|'/c calc'!A1", "ok"])
    assert row.startswith("'=cmd") or row.startswith('"\'=cmd')
    assert "\r\n" in row


def test_the_neutralisation_does_not_mangle_a_real_negative_number():
    """Mutation guard: quoting every field beginning ``-`` would corrupt the data.

    ``-12.5`` is produced by this codebase, not typed by a caller, so it must survive
    as a number. Only *strings* are neutralised.
    """
    assert csv_row([-12.5]).strip() == "-12.5"


def test_the_preamble_states_scope_window_and_caveats_inside_the_file():
    text = "".join(preamble(_meta()))
    assert "Tenant 7 only" in text
    assert "2026-08-01T00:00:00+00:00 to now" in text
    assert "alice (platform_admin)" in text
    assert "Filter: actor,bob" in text
    assert "no outcome for a call" in text


def test_the_trailer_names_the_row_count_so_truncation_is_visible():
    assert "End of export,10 data rows" in "".join(trailer(10))


def test_the_disposition_header_makes_the_browser_save_the_file():
    value = content_disposition("aegis-audit-tenant-7-20260819T103000Z.csv")
    assert value.startswith("attachment; filename=")
    assert "aegis-audit-tenant-7" in value
