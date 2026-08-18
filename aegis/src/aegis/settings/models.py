"""SQLAlchemy ORM for the settings store — one table, three scopes.

Every per-tenant control in phases 6, 7 and 10 is a row here rather than a column
somebody added to a bespoke table, which is what makes "0 code change from the
dashboard" true: a new control is a catalogue entry (:mod:`aegis.settings.spec`) plus a
row, never a migration in a project that has no migration tool.

The three scopes and how they are told apart:

``platform``
    ``tenant_id IS NULL AND user_id IS NULL``. The platform's override of the
    catalogue's compiled-in default. **Every tenant can read it and no tenant can write
    it** — see :data:`aegis.governance.rls._PLATFORM_BASELINE_TABLES` for the policy
    that makes that structural. It has to be readable, because a resolver that could not
    see the platform layer would compute a value *weaker than the platform's own choice*
    for a ``tighten_only`` key while looking perfectly healthy.
``tenant``
    ``tenant_id`` set, ``user_id`` NULL. The tenant admin's choice.
``user``
    both set. One person's preference, inside their tenant.

Two things the specification's sketch of this table would not actually have enforced,
fixed here rather than discovered later:

1. **``UNIQUE (scope, tenant_id, user_id, key)`` constrains nothing that matters on
   PostgreSQL 14.** SQL says ``NULL`` is distinct from ``NULL``, so two platform rows
   for the same key — both with NULL tenant and user — satisfy it, and so do two rows
   for the same tenant. ``NULLS NOT DISTINCT`` is PostgreSQL 15; the target cluster is
   14. Three **partial** unique indexes, one per scope, are the version that binds.
2. **A ``user``- or ``tenant``-scoped row with a NULL ``tenant_id`` would be world
   readable**, because NULL is precisely what marks the platform baseline. The check
   constraints below make that row unwritable rather than trusting every caller to pass
   a tenant id.

Registered in :data:`aegis.governance.rls._TENANT_SCOPED_TABLES` like every other
tenant-scoped table, and imports :mod:`aegis.governance.models` for the same
load-bearing reason :mod:`aegis.jobs.models` does — the foreign keys below are resolved
by name against the shared metadata.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, func, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

# Registration side-effect, and deliberately not a lazy import: the foreign keys below
# reference ``tenants.id`` / ``users.id``. See the module docstring.
import aegis.governance.models  # noqa: F401
from aegis.data import AegisBase, JsonB

__all__ = ["SETTINGS_TABLE", "Setting", "SettingScope"]

#: The table name, as one constant shared by the model, the RLS registry and the tests.
SETTINGS_TABLE = "settings"


class SettingScope(StrEnum):
    """Which layer of the resolution chain a row belongs to.

    Stored explicitly rather than inferred from which id columns are NULL. It could be
    inferred — the check constraints below make the two representations equivalent — but
    the scope is what :func:`aegis.settings.resolver.resolve` returns as ``source``, what
    a control renders as its badge, and what an audit row records. A column that three
    surfaces name is worth storing under that name.
    """

    PLATFORM = "platform"
    TENANT = "tenant"
    USER = "user"


#: The scope discriminator's column type: a ``varchar`` with a ``CHECK … IN`` rather
#: than a native PostgreSQL enum, and both halves of that are deliberate.
#:
#: *Not native*, because a native enum is a type in the catalog: adding a scope would be
#: ``ALTER TYPE`` in a project with no migration tool, and dropping one is not possible
#: at all. Three scopes is the whole design, so the cost of the type buys nothing.
#:
#: *With* ``values_callable``, because SQLAlchemy labels an enum column with the member
#: **names** by default — ``PLATFORM``, not ``platform``. The values are what
#: :func:`aegis.settings.resolver.resolve` returns as its ``source``, what the check
#: constraints below compare against and what the specification's DDL wrote, so storing
#: the names would put a translation between the column and every reader of it.
_SETTING_SCOPE = SAEnum(
    SettingScope,
    name="setting_scope",
    native_enum=False,
    create_constraint=True,
    values_callable=lambda enum: [member.value for member in enum],
)


class Setting(AegisBase):
    """One written value, at one scope, for one catalogue key.

    Rows are *writes*, not effective values: nothing here is the answer to "what is in
    force" — :func:`aegis.settings.resolver.resolve` computes that by merging the layers
    under the key's :class:`~aegis.settings.spec.MergeRule`. That separation is what lets
    a tenant's stored value be legal and still lose to a stricter platform default.

    Where the specification was silent, the choices made here and why:

    * ``value`` is ``jsonb`` holding **any** JSON value, not an object: a setting is as
      often a number or a list as a mapping, and wrapping scalars in ``{"value": …}``
      would put a second encoding rule between the catalogue's declared type and the
      column.
    * ``updated_by`` is a string rather than a ``users.id``, matching ``audit_log``: the
      writer may be a platform operator with no row in this database's ``users``, and a
      dangling foreign key would be worse than a name.
    * There is no ``created_at``. The row is the current value at a scope; its history is
      the audit log's job, and a second, partial history here would be the one people
      read by mistake.
    """

    __tablename__ = SETTINGS_TABLE

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[SettingScope] = mapped_column(_SETTING_SCOPE, index=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), default=None, index=True
    )
    key: Mapped[str] = mapped_column(String(128), index=True)
    value: Mapped[Any] = mapped_column(JsonB)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_by: Mapped[str | None] = mapped_column(String(255), default=None)

    __table_args__ = (
        # The scope column and the id columns must agree, in both directions. Written as
        # equivalences rather than as three ORed cases so that each failure names the
        # thing that is wrong: a platform row that carries a tenant, or a tenant row
        # that does not.
        CheckConstraint(
            "(scope = 'platform') = (tenant_id IS NULL)",
            name="ck_settings_platform_row_has_no_tenant",
        ),
        CheckConstraint(
            "(scope = 'user') = (user_id IS NOT NULL)",
            name="ck_settings_user_row_has_a_user",
        ),
        # One row per key per scope — as three partial unique indexes, because the
        # composite UNIQUE the specification sketched does not bind rows with NULLs on
        # PostgreSQL 14. See the module docstring.
        Index(
            "uq_settings_platform_key",
            "key",
            unique=True,
            postgresql_where=text("scope = 'platform'"),
            sqlite_where=text("scope = 'platform'"),
        ),
        Index(
            "uq_settings_tenant_key",
            "tenant_id",
            "key",
            unique=True,
            postgresql_where=text("scope = 'tenant'"),
            sqlite_where=text("scope = 'tenant'"),
        ),
        Index(
            "uq_settings_user_key",
            "user_id",
            "key",
            unique=True,
            postgresql_where=text("scope = 'user'"),
            sqlite_where=text("scope = 'user'"),
        ),
    )
