"""``python -m aegis.dbadmin`` — print the database console's provisioning SQL.

Prints rather than executes, on purpose. Creating a login role against a production
database is an operator action, and a reviewable ``.sql`` file a DBA can read before it
runs is a better artefact than a migration that ran once on somebody's laptop. Nothing in
:mod:`aegis.dbadmin` runs at Aegis boot.

::

    python -m aegis.dbadmin --role aegis_readonly --password '…' > readonly.sql
    psql -d aegis -f readonly.sql

Re-run it after every schema change: a table created since the last run is not readable by
the console's role until its ``GRANT SELECT`` exists, and the browser will simply not list
it — which is the correct behaviour and an easy one to misread as a missing table.
"""

from __future__ import annotations

import argparse

from aegis.dbadmin.provision import READONLY_ROLE, provisioning_sql, revocation_statements


def main() -> None:
    """Parse arguments and print the SQL."""
    parser = argparse.ArgumentParser(
        prog="python -m aegis.dbadmin",
        description=(
            "Print the SQL that provisions the read-only Postgres role the Aegis database "
            "console connects as. Run the output as the OWNER of the Aegis tables."
        ),
    )
    parser.add_argument("--role", default=READONLY_ROLE, help="the read-only Postgres role")
    parser.add_argument(
        "--password",
        default=None,
        help="set the role's password; omit to leave the role's password alone",
    )
    parser.add_argument(
        "--revoke",
        action="store_true",
        help="print the SQL that takes the console's access away again",
    )
    args = parser.parse_args()
    if args.revoke:
        print("\n".join(revocation_statements(args.role)))
        return
    print(provisioning_sql(args.role, password=args.password), end="")


if __name__ == "__main__":  # pragma: no cover - a CLI entry point
    main()
