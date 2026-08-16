"""``python -m app.data.rls_check`` — is tenant isolation actually switched on?

A one-line, no-side-effects answer to the only question that decides whether the 13
``tenant_isolation`` policies mean anything: **can the role that serves requests bypass
them?** Postgres skips row security entirely for a ``SUPERUSER``/``BYPASSRLS`` role, and
``FORCE ROW LEVEL SECURITY`` does not change that — so a platform connected as
``postgres`` shows a perfect ``pg_policies`` listing and isolates nothing.

It exists as a module rather than as a copy of the query inside each preflight script so
the readiness board and the running application ask the database the *same* question,
through the same code path (:func:`aegis.governance.rls.audit_rls_enforcement`). A
day-of check that drifts from the boot-time check is a check that lies on exactly the
day it matters.

Read-only: it opens one connection, reads ``pg_roles``, and exits.

Exit codes (so a script can branch on them):
    0: the serving role is subject to RLS — tenant isolation is live.
    1: the serving role bypasses RLS — every tenant policy is inert.
    2: the question could not be answered (database unreachable, or a non-Postgres
       DSN such as the SQLite one lite mode uses). Deliberately distinct from 0: an
       unverified control must never be reported as a healthy one.
"""

from __future__ import annotations

import asyncio
import sys

from aegis.governance.rls import audit_rls_enforcement
from sqlalchemy.exc import SQLAlchemyError

from app.data.session import get_engine, serving_role_name


async def _check() -> int:
    """Audit the serving role and print one line describing the result.

    Returns:
        The process exit code documented in the module docstring.
    """
    engine = get_engine()
    if engine.dialect.name != "postgresql":
        print(f"UNVERIFIED  serving DSN is {engine.dialect.name}, which has no row security")
        return 2
    try:
        enforcement = await audit_rls_enforcement(engine)
    except (SQLAlchemyError, OSError) as exc:
        print(f"UNVERIFIED  cannot reach the serving database: {type(exc).__name__}")
        return 2
    finally:
        await engine.dispose()
    if enforcement.bypassed:
        print(
            f"BYPASSED    serving role '{enforcement.role}' has {enforcement.cause} — "
            "row-level security is inert; every tenant policy is bypassed"
        )
        return 1
    split = (
        "owner DSN split"
        if serving_role_name() is not None
        else "NO separate owner role — DDL shares this connection"
    )
    print(f"ENFORCED    serving role '{enforcement.role}' is subject to RLS ({split})")
    return 0


def main() -> int:
    """Entry point: run the check and return its exit code."""
    return asyncio.run(_check())


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())
