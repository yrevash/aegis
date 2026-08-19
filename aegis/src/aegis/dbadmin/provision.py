"""The role the console connects as — created as SQL, reviewable, and idempotent.

**Why a third role.** ``aegis_app`` (``scripts/sql/aegis-app-role.sql``) serves requests
and holds ``SELECT, INSERT, UPDATE, DELETE`` plus ``CREATE`` on the schema; the owner role
runs DDL and, on a stock local cluster, bypasses row security. Neither may be the console's
connection: the whole claim of §7.9 is that the page *cannot write*, and a claim that
depends on the application never sending a write is not a claim, it is a hope.

**Why not ``SET ROLE`` on the application connection.** §7.9 finding 3, measured: ``RESET
ROLE`` is a single legal statement, and the probe used it to walk from the read-only role
back to the superuser and read ``pg_authid``. A role assumed inside a session is not a
boundary. A separately authenticated connection is, so this role has its own password, its
own DSN and its own pool.

**What the column grants buy.** ``users.password_hash`` is withheld by granting SELECT on
every *other* column rather than by a denylist in application code. §7.9 finding 5,
measured: ``SELECT *`` is then refused, a ``WHERE password_hash LIKE …`` predicate is
refused, **and ``information_schema`` stops listing the column**. So the permission model
is the schema browser's source of truth and there is nothing to drift — which is exactly
what a denylist cannot promise.

**The role settings are guard rails, not boundaries.** ``default_transaction_read_only`` is
user-settable; §7.9 finding 4 watched the read-only role turn its own copy off, and what
stopped the write was the absent grant. It is set here anyway, because it turns a mistake
into a clean error rather than a write, and because
:class:`~aegis.dbadmin.types.ReadOnlyPosture` reports it to the operator.

Emitted as SQL rather than executed, deliberately: this never runs at Aegis boot. The
console is optional, provisioning a role is an operator action, and a reviewable ``.sql``
file is a better artefact than a migration that ran once on somebody's laptop::

    python -m aegis.dbadmin --role aegis_readonly --password '…' > readonly.sql
    psql -d aegis -f readonly.sql
"""

from __future__ import annotations

import re

__all__ = [
    "IDLE_IN_TRANSACTION_TIMEOUT",
    "READONLY_ROLE",
    "STATEMENT_TIMEOUT",
    "WITHHELD_COLUMNS",
    "provisioning_sql",
    "provisioning_statements",
    "revocation_statements",
]

#: The default name of the role the database console connects as. Never the table owner,
#: and never ``aegis_app``.
READONLY_ROLE = "aegis_readonly"

#: Columns no console connection may read, expressed as ``{table: (column, ...)}``. The
#: table is granted column-by-column instead, so the withheld column disappears from the
#: catalog as well as from every projection.
#:
#: Deliberately short. This is not a denylist of *sensitive* data — every row on this page
#: is sensitive, which is why the page is ``require_platform_admin`` and audited. It is the
#: list of columns that are **never** legitimately looked at by a human, where the only
#: reason to read one is to attack it. A password hash is that; an email address is not.
WITHHELD_COLUMNS: dict[str, tuple[str, ...]] = {
    "users": ("password_hash",),
}

#: Per-statement wall clock on the role. §7.9 control 4, verified: ``pg_sleep(10)`` under
#: this raises *canceling statement due to statement timeout*.
STATEMENT_TIMEOUT = "10s"

#: How long one of this role's transactions may sit idle before PostgreSQL kills it, so a
#: console tab left open cannot pin a connection or hold back vacuum.
IDLE_IN_TRANSACTION_TIMEOUT = "30s"

#: A role name this module is willing to interpolate. ``CREATE ROLE`` and ``GRANT`` take no
#: bind parameter for their subject, so the name is interpolated — and anything
#: interpolated is validated rather than trusted. Deliberately narrower than PostgreSQL
#: allows: a name that would need escaping is refused, not escaped.
_SAFE_ROLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,62}$")


def _check_role(role: str) -> str:
    """Return ``role`` if it is a bare SQL identifier, else raise.

    Args:
        role: The proposed role name.

    Returns:
        The same name.

    Raises:
        ValueError: If it is not a bare SQL identifier.
    """
    if not _SAFE_ROLE.match(role):
        raise ValueError(
            f"{role!r} is not a bare SQL identifier. It is interpolated into CREATE ROLE "
            "and GRANT, which take no bind parameter for their subject, so it is refused "
            "rather than quoted."
        )
    return role


def provisioning_statements(
    role: str = READONLY_ROLE, *, password: str | None = None
) -> tuple[str, ...]:
    """Return the ordered DDL that provisions the console's read-only role.

    Idempotent end to end, which matters because it is re-run after every schema change —
    a new table is not readable by this role until it is.

    The order is the argument:

    1. create the role, with every attribute that makes row security apply to it stated
       explicitly rather than inherited from ``CREATE``'s defaults — so a re-run also
       *repairs* a role somebody granted ``SUPERUSER`` by hand;
    2. bound it: statement timeout, idle-in-transaction timeout, read-only transactions;
    3. let it reach the schema, and nothing beyond it — no ``CREATE``, so it can make
       nothing and own nothing;
    4. ``GRANT SELECT`` on everything, then **revoke the tables that carry a withheld
       column and re-grant them column by column**. Ordered this way so a table added
       since the last run is covered by step 4's bulk grant and a withheld column is
       re-withheld on top of it;
    5. default privileges, so a table the owner creates later is readable without a
       re-run — and an explicit ``REVOKE`` of everything but ``SELECT``, in case a
       broader default privilege was ever set;
    6. print the proof: a row saying ``t`` for superuser or bypassrls means the whole
       page is unsafe and :func:`aegis.dbadmin.runner.verify_posture` will refuse it.

    Args:
        role: The role the console connects as.
        password: Its password. ``None`` skips ``CREATE ROLE``/``ALTER ROLE … PASSWORD``
            entirely, for a deployment that provisions roles elsewhere.

    Returns:
        The statements, in order. **Each element is exactly one SQL command** (leading
        ``--`` comments aside), because a caller executing them one at a time goes over
        the extended protocol, which refuses multiple commands in one message —
        ``cannot insert multiple commands into a prepared statement``. That refusal is
        §7.9 finding 2 and it is a feature everywhere else in this package; here it means
        the provisioning script has to respect the same rule that protects the console.

    Raises:
        ValueError: If ``role`` is not a bare SQL identifier, or the password could
            terminate the literal it is inlined into.
    """
    _check_role(role)
    out: list[str] = [
        "-- The Aegis database console's read-only role.\n"
        "-- Generated by `python -m aegis.dbadmin`. Run as the OWNER of the Aegis\n"
        "-- tables (granting requires ownership, which is what makes the split real).\n"
        "-- Idempotent: safe and expected to be re-run after every schema change."
    ]
    if password is not None:
        if "'" in password:
            raise ValueError(
                "the console role's password may not contain a single quote: it is inlined "
                "into CREATE ROLE, which takes no bind parameter for it"
            )
        out.append(
            f"DO $$ BEGIN\n"
            f"  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN\n"
            f"    CREATE ROLE {role} LOGIN PASSWORD '{password}';\n"
            f"  END IF;\n"
            f"END $$;"
        )
        out.append(
            f"ALTER ROLE {role} LOGIN PASSWORD '{password}'\n"
            f"  NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT;"
        )
    else:
        out.append(
            f"ALTER ROLE {role}\n"
            f"  NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT;"
        )
    out.append(f"ALTER ROLE {role} SET statement_timeout = '{STATEMENT_TIMEOUT}';")
    out.append(
        f"ALTER ROLE {role} SET idle_in_transaction_session_timeout = "
        f"'{IDLE_IN_TRANSACTION_TIMEOUT}';"
    )
    out.append(
        f"-- A guard rail, not the boundary: this role can turn it off itself (finding 4).\n"
        f"ALTER ROLE {role} SET default_transaction_read_only = on;"
    )
    out.append(f"GRANT USAGE ON SCHEMA public TO {role};")
    out.append(
        f"-- No CREATE on the schema: this role owns nothing and can make nothing.\n"
        f"REVOKE CREATE ON SCHEMA public FROM {role};"
    )
    out.append(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role};")
    for table, columns in sorted(WITHHELD_COLUMNS.items()):
        withheld = ", ".join(columns)
        excluded = ", ".join(f"'{column}'" for column in columns)
        out.append(
            f"-- Withhold {table}.{withheld}: revoke the table-wide SELECT and re-grant\n"
            f"-- every other column. information_schema then stops listing it, so the\n"
            f"-- schema browser and every generated projection lose it with nothing to\n"
            f"-- drift (finding 5).\n"
            f"REVOKE SELECT ON TABLE {table} FROM {role};"
        )
        out.append(
            f"DO $$\n"
            f"DECLARE cols text;\n"
            f"BEGIN\n"
            f"  SELECT string_agg(format('%I', attname), ', ' ORDER BY attnum)\n"
            f"    INTO cols\n"
            f"    FROM pg_attribute\n"
            f"   WHERE attrelid = '{table}'::regclass\n"
            f"     AND attnum > 0 AND NOT attisdropped\n"
            f"     AND attname <> ALL (ARRAY[{excluded}]);\n"
            f"  EXECUTE format('GRANT SELECT (%s) ON TABLE {table} TO {role}', cols);\n"
            f"END $$;"
        )
    out.append(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO {role};"
    )
    out.append(
        f"-- Belt: take back anything a broader default privilege may have granted.\n"
        f"REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER\n"
        f"  ON ALL TABLES IN SCHEMA public FROM {role};"
    )
    out.append(
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public\n"
        f"  REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLES\n"
        f"  FROM {role};"
    )
    out.append(
        f"-- Prove it. `t` in either flag column means the console will refuse to run:\n"
        f"-- aegis.dbadmin.runner.verify_posture re-reads exactly this before every query.\n"
        f"SELECT rolname AS console_role,\n"
        f"       rolsuper     AS is_superuser_MUST_BE_f,\n"
        f"       rolbypassrls AS bypasses_rls_MUST_BE_f\n"
        f"  FROM pg_roles WHERE rolname = '{role}';"
    )
    return tuple(out)


def revocation_statements(role: str = READONLY_ROLE) -> tuple[str, ...]:
    """Return the DDL that takes the console's access away again.

    Kept beside the provisioning so "turn this off" is a documented operation rather than
    an archaeology exercise. Does **not** drop the role: a role may own objects in another
    database, and a provisioning script that drops roles is one that eventually drops the
    wrong one.

    Args:
        role: The role to revoke from.

    Returns:
        The statements, in order.

    Raises:
        ValueError: If ``role`` is not a bare SQL identifier.
    """
    _check_role(role)
    return (
        f"ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT ON TABLES FROM {role};",
        f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role};",
        f"REVOKE USAGE ON SCHEMA public FROM {role};",
        f"ALTER ROLE {role} NOLOGIN;",
    )


def provisioning_sql(role: str = READONLY_ROLE, *, password: str | None = None) -> str:
    """The provisioning DDL as one runnable script.

    Args:
        role: The role the console connects as.
        password: Its password, or ``None`` to leave the role's password alone.

    Returns:
        The script, newline-terminated.
    """
    return "\n\n".join(provisioning_statements(role, password=password)) + "\n"
