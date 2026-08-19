"""The database console's refusals, where they are decided: before any I/O.

Everything here is about what the package will *build*, not what a cluster does with it.
The live proofs — a write refused by privilege, a tenant filter that holds on a real
``NOSUPERUSER NOBYPASSRLS`` role, and the unbound-scope control case — need the whole
schema and live in ``backend/tests/api/test_db_console.py``. What is decided here is
decided without a database, so it is tested without one.

The three claims that carry the surface, in the order they fail if they are wrong:

1. **No statement can leave this package without a bind parameter**, because a statement
   with one travels the extended protocol, which refuses multiple commands in a single
   message. That is the §7.9 finding-2 control, and it is enforced in a constructor so no
   caller can skip it.
2. **No generated statement over a tenant-scoped relation can omit the tenant predicate**,
   because there is no field in which to write a ``WHERE`` that replaces it — asserted over
   every inspection in the catalogue, so an inspection added next month is covered by a
   test that already exists.
3. **No identifier is escaped; every one is matched against the catalog.** A column a grant
   withholds is not in the catalog, so it cannot be ordered by, filtered on or projected.
"""

from __future__ import annotations

import pytest

from aegis.dbadmin import (
    INSPECTIONS,
    MAX_ROW_LIMIT,
    TENANT_PREDICATE,
    Column,
    DbAdminError,
    ReadOnlyPosture,
    ReadQuery,
    TableInfo,
    binding_for,
    browse_query,
    count_query,
    inspection_named,
    narrow_to,
    provisioning_statements,
    resolve_limit,
    table_named,
)
from aegis.governance.rls import _TENANT_ISOLATION_PREDICATE
from aegis.retrieval.types import ALL_TENANTS, UntenantedPrincipalError

_LEDGER = TableInfo(
    name="usage_ledger",
    columns=(
        Column(name="id", data_type="integer", nullable=False, is_primary_key=True),
        Column(name="tenant_id", data_type="integer", nullable=True),
        Column(name="cost_usd", data_type="double precision", nullable=False),
    ),
    primary_key=("id",),
    tenant_scoped=True,
)

#: A relation with no ``tenant_id`` column. There is no predicate that can narrow it, which
#: is why it is readable only under a platform-wide authority.
_TENANTS = TableInfo(
    name="tenants",
    columns=(
        Column(name="id", data_type="integer", nullable=False, is_primary_key=True),
        Column(name="name", data_type="character varying", nullable=False),
    ),
    primary_key=("id",),
    tenant_scoped=False,
)

#: ``users`` as the console's role sees it once ``password_hash`` is withheld by a
#: column-level grant: the column is simply not in the catalog.
_USERS = TableInfo(
    name="users",
    columns=(
        Column(name="id", data_type="integer", nullable=False, is_primary_key=True),
        Column(name="tenant_id", data_type="integer", nullable=True),
        Column(name="username", data_type="character varying", nullable=False),
    ),
    primary_key=("id",),
    tenant_scoped=True,
    withheld_columns=("password_hash",),
)


# ── 1. Every statement travels the extended protocol ──────────────────────────


def test_a_query_with_no_bind_parameter_cannot_be_constructed():
    """The §7.9 finding-1 defence, enforced where it cannot be skipped.

    asyncpg's simple protocol runs every command in a string and reports only the first
    one's status tag. A statement carrying a bind parameter takes the extended protocol,
    which refuses multi-statement outright. So "carries a parameter" is an invariant of the
    type, not a habit of its callers.
    """
    with pytest.raises(ValueError, match="extended protocol"):
        ReadQuery(sql="SELECT 1", params={}, label="no binds")


@pytest.mark.parametrize("inspection", INSPECTIONS, ids=[item.id for item in INSPECTIONS])
def test_every_generated_statement_carries_a_bind_parameter(inspection):
    """Every inspection in the closed set, and the browse, satisfy the invariant."""
    table = TableInfo(
        name=inspection.source,
        columns=(Column(name="tenant_id", data_type="integer", nullable=True),),
        tenant_scoped=inspection.tenant_scoped,
    )
    assert inspection.build(table).params


# ── 2. The tenant predicate cannot be left out ────────────────────────────────


def test_the_console_predicate_is_not_the_fail_open_one():
    """The trap this whole design exists to avoid, stated as an assertion.

    ``aegis.governance.rls``'s predicate begins ``substring(...) IS NULL OR ...`` — an
    unset ``app.tenant_id`` therefore does **not** restrict, which is right for the request
    path and catastrophic for a page that reads every table. The console's predicate has no
    such branch: nothing bound yields ``tenant_id = NULL``, which is never true.
    """
    assert "IS NULL" in _TENANT_ISOLATION_PREDICATE
    assert "IS NULL" not in TENANT_PREDICATE
    assert "app.dbadmin_all_tenants" in TENANT_PREDICATE


@pytest.mark.parametrize("inspection", INSPECTIONS, ids=[item.id for item in INSPECTIONS])
def test_no_inspection_can_replace_the_tenant_filter(inspection):
    """Every tenant-scoped inspection carries the predicate, and carries it in the WHERE.

    Parametrised over the catalogue rather than written per entry, so an inspection added
    next month is covered by a test that already exists — which is the only kind of
    coverage that survives contact with a deadline.
    """
    table = TableInfo(
        name=inspection.source,
        columns=(Column(name="tenant_id", data_type="integer", nullable=True),),
        tenant_scoped=inspection.tenant_scoped,
    )
    sql = inspection.build(table).sql
    if inspection.tenant_scoped:
        assert TENANT_PREDICATE.format(alias="t") in sql
        where, _, rest = sql.partition("WHERE ")
        assert rest.startswith("(current_setting"), "the predicate must lead the WHERE"
    assert sql.count("WHERE") == 1, "an inspection may add conditions, never a second WHERE"


def test_browsing_a_tenant_scoped_table_welds_the_predicate_in():
    """The browse is generated the same way, so it cannot drift from the inspections."""
    query = browse_query(_LEDGER, platform_wide=True, limit=10)
    assert TENANT_PREDICATE.format(alias="t") in query.sql
    assert query.tenant_filtered is True


def test_a_table_with_no_tenant_column_is_refused_to_a_tenant_scoped_reader():
    """"No predicate" must never quietly mean "no restriction".

    ``tenants`` has no column to narrow on, so a caller whose authority is one tenant is
    refused it outright rather than handed every row with ``WHERE TRUE``.
    """
    assert browse_query(_TENANTS, platform_wide=True).tenant_filtered is False
    with pytest.raises(DbAdminError, match="platform-wide authority"):
        browse_query(_TENANTS, platform_wide=False)
    with pytest.raises(DbAdminError, match="platform-wide authority"):
        count_query(_TENANTS, platform_wide=False)


# ── 3. Identifiers are matched, never escaped ─────────────────────────────────


def test_a_withheld_column_cannot_be_named_anywhere():
    """A column-level grant is the whole denylist, and this is what that buys.

    ``password_hash`` is absent from the catalog because the console's role holds no grant
    on it, so it cannot be projected, ordered by or filtered on — and the refusal names
    what *is* readable rather than leaking the shape of what is not.
    """
    with pytest.raises(DbAdminError, match="no readable column"):
        browse_query(_USERS, platform_wide=True, order_by="password_hash")
    with pytest.raises(DbAdminError, match="no readable column"):
        browse_query(_USERS, platform_wide=True, filter_column="password_hash", filter_value="x")
    assert "password_hash" not in browse_query(_USERS, platform_wide=True).sql


def test_an_injection_attempt_in_an_identifier_is_refused_not_escaped():
    """The identifier gate is a lookup, so a hostile name has nothing to escape into."""
    with pytest.raises(DbAdminError, match="no readable column"):
        browse_query(_LEDGER, platform_wide=True, order_by="id; DROP TABLE users --")
    with pytest.raises(DbAdminError, match="no readable table"):
        table_named((_LEDGER,), "users; DROP TABLE users")


def test_a_filter_value_is_bound_and_never_interpolated():
    """A value is data. It reaches the statement as a parameter or it does not reach it."""
    query = browse_query(
        _LEDGER, platform_wide=True, filter_column="cost_usd", filter_value="1' OR '1'='1"
    )
    assert "OR '1'='1" not in query.sql
    assert query.params["filter_value"] == "1' OR '1'='1"


def test_an_undeclared_inspection_parameter_is_refused_not_dropped():
    """A silently dropped filter answers a different question than the one asked."""
    inspection = inspection_named("audit_by_actor")
    table = TableInfo(
        name="audit_log",
        columns=(Column(name="tenant_id", data_type="integer", nullable=True),),
        tenant_scoped=True,
    )
    with pytest.raises(DbAdminError, match="takes no parameter"):
        inspection.build(table, values={"tenant_id": 9})


def test_there_is_no_inspection_that_is_not_in_the_closed_set():
    """There is no fallback to a free-form statement, because there is none to fall to."""
    with pytest.raises(DbAdminError, match="closed set"):
        inspection_named("../../etc/passwd")


# ── The bounds, and the authority ─────────────────────────────────────────────


def test_the_row_limit_is_clamped_and_the_statement_asks_for_one_more():
    """Truncation is observed, not inferred: the extra row is what makes it a fact."""
    assert resolve_limit(None) > 0
    assert resolve_limit(10_000) == MAX_ROW_LIMIT
    assert browse_query(_LEDGER, platform_wide=True, limit=10_000).params["row_limit"] == (
        MAX_ROW_LIMIT + 1
    )
    with pytest.raises(DbAdminError, match="at least one row"):
        resolve_limit(0)


def test_an_authority_has_three_outcomes_and_one_of_them_raises():
    """The collapse that caused five cross-tenant leaks in this project, refused."""
    assert binding_for(ALL_TENANTS).all_tenants is True
    assert binding_for(7).tenant_id == 7
    assert binding_for(7).all_tenants is False
    # ``True`` is an ``int`` in Python and would otherwise bind tenant 1, a real tenant.
    for bad in (None, True, "3", 3.0):
        with pytest.raises(UntenantedPrincipalError):
            binding_for(bad)


def test_the_tenant_selector_can_only_narrow():
    """Impersonation is the demo, and the obvious place to hand out a wider authority."""
    assert narrow_to(ALL_TENANTS, 2) == 2
    assert narrow_to(ALL_TENANTS, None) is ALL_TENANTS
    assert narrow_to(5, 5) == 5
    assert narrow_to(5, None) == 5
    with pytest.raises(UntenantedPrincipalError, match="only narrow"):
        narrow_to(5, 6)


def test_an_unbound_scope_never_becomes_a_platform_wide_read():
    """The empty ``app.tenant_id`` that resets a scope must not mean "show everything"."""
    binding = binding_for(3)
    assert binding.tenant_value == "3"
    assert binding.all_tenants_value == "off"
    platform = binding_for(ALL_TENANTS)
    assert platform.tenant_value == ""
    assert platform.all_tenants_value == "on"


# ── Provisioning ──────────────────────────────────────────────────────────────


def test_every_provisioning_statement_is_a_single_command():
    """The provisioning script obeys the rule that protects the console.

    A caller executing these one at a time goes over the extended protocol, which refuses
    two commands in one message — the same refusal §7.9 finding 2 relies on. This was found
    the expensive way: the first draft emitted a ``REVOKE`` and a ``DO`` block as one
    string and the whole provisioning run died on it.
    """
    for statement in provisioning_statements("aegis_readonly", password="pw"):
        body = "\n".join(
            line for line in statement.splitlines() if not line.strip().startswith("--")
        )
        # A `DO $$ ... $$;` block is one command whose body legitimately contains `;`.
        if body.strip().startswith("DO $$"):
            continue
        assert body.count(";") <= 1, f"more than one command in: {body}"


def test_provisioning_withholds_the_password_hash_and_grants_no_write():
    """The two properties the role is for, read off the DDL it emits."""
    script = "\n".join(provisioning_statements("aegis_readonly", password="pw"))
    assert "NOSUPERUSER NOBYPASSRLS" in script
    assert "REVOKE SELECT ON TABLE users" in script
    assert "'password_hash'" in script
    assert "GRANT INSERT" not in script
    assert "GRANT UPDATE" not in script
    assert "GRANT CREATE ON SCHEMA" not in script


def test_a_role_name_that_would_need_escaping_is_refused():
    """``GRANT`` takes no bind parameter for its grantee, so the name is validated."""
    with pytest.raises(ValueError, match="bare SQL identifier"):
        provisioning_statements('ro"; DROP ROLE aegis_app; --')
    with pytest.raises(ValueError, match="single quote"):
        provisioning_statements("aegis_readonly", password="pw'; ALTER ROLE x SUPERUSER; --")


def test_a_connection_that_can_write_is_refused_with_a_sentence_naming_why():
    """The posture is a verdict an operator can act on, not a boolean."""
    safe = ReadOnlyPosture(role="aegis_readonly", is_superuser=False, bypasses_rls=False)
    assert safe.is_safe and safe.refusal() == ""
    unsafe = ReadOnlyPosture(
        role="postgres",
        is_superuser=True,
        bypasses_rls=False,
        writable_tables=("users", "usage_ledger"),
    )
    assert not unsafe.is_safe
    assert "SUPERUSER" in unsafe.refusal()
    assert "users" in unsafe.refusal()
    assert "AEGIS_DB_CONSOLE_DSN" in unsafe.refusal()
