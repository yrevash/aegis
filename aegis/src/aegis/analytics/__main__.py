"""``python -m aegis.analytics`` — print the analytics provisioning SQL.

Prints rather than executes, on purpose. Provisioning Superset is an operator action
against a production database, and a reviewable ``.sql`` file that a DBA can read before
it runs is a better artefact than a migration that ran once on somebody's laptop.
Nothing in :mod:`aegis.analytics` ever runs at Aegis boot.

::

    python -m aegis.analytics --role aegis_superset --password '…' > analytics.sql
    psql -d aegis -f analytics.sql
"""

from __future__ import annotations

import argparse

from aegis.analytics.provision import READ_ONLY_ROLE, provisioning_sql, revocation_statements


def main() -> None:
    """Parse arguments and print the SQL."""
    parser = argparse.ArgumentParser(
        prog="python -m aegis.analytics",
        description=(
            "Print the SQL that provisions Aegis's Superset datasets and the read-only "
            "role Superset connects as. Run the output as the OWNER of the Aegis tables."
        ),
    )
    parser.add_argument("--role", default=READ_ONLY_ROLE, help="the read-only Postgres role")
    parser.add_argument(
        "--password",
        default=None,
        help="create the role with this password; omit to leave role creation to you",
    )
    parser.add_argument(
        "--revoke",
        action="store_true",
        help="print the SQL that removes the views and grants again",
    )
    args = parser.parse_args()
    if args.revoke:
        print("\n".join(revocation_statements(args.role)))
        return
    print(provisioning_sql(args.role, password=args.password), end="")


if __name__ == "__main__":  # pragma: no cover - a CLI entry point
    main()
