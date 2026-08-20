"""SQLAlchemy ORM for the skills store — one table, the same three scopes as settings.

A skill used to be a Markdown file in a directory named by ``memory_spec.SKILLS_DIR``,
read at recall time and **pasted whole into the prompt**. That is a skills mechanism
with none of the properties that make skills useful: a directory on a container's
filesystem cannot be per-tenant, cannot be per-user, cannot be written from a browser,
and cannot be told apart in a trace from a turn that needed no skill at all.

So a skill is a row here instead, and the shape of the row is deliberately the shape of
:class:`aegis.settings.models.Setting`:

``platform``
    ``tenant_id IS NULL AND user_id IS NULL``. The platform's own skills — including
    the **safety** ones, which is why ``agent_skills`` is registered in
    :data:`aegis.governance.rls._PLATFORM_BASELINE_TABLES` beside ``settings``. A
    platform safety skill that a bound tenant scope could not *read* would resolve to
    nothing while looking perfectly healthy, which is precisely the failure the
    baseline registration exists to prevent.
``tenant``
    ``tenant_id`` set, ``user_id`` NULL. The tenant admin's house style.
``user``
    both set. One person's own way of working, inside their tenant.

Three constraints carry rules that would otherwise be prose in a route:

1. **Scope and ids must agree**, in both directions — the two check constraints copied
   from ``settings``, for the reason given there: a ``tenant``-scoped row with a NULL
   ``tenant_id`` would be world-readable, because NULL is exactly what marks the
   platform baseline.
2. **Only a platform row may be a safety skill.** ``is_safety`` is what makes a name
   un-shadowable by a tenant or a user (see
   :func:`aegis.skills.store.resolve_skills`), so if a tenant could set it, the flag
   would be a privilege-escalation switch rather than a floor. It is refused by the
   database, not by whichever route happens to be in front of it.
3. **One skill per name per scope**, as three *partial* unique indexes rather than one
   composite ``UNIQUE`` — same PostgreSQL-14 NULL-distinctness reasoning as
   ``settings``: ``UNIQUE (scope, tenant_id, user_id, name)`` binds nothing when two of
   the columns are NULL.

Registered in :data:`aegis.governance.rls._TENANT_SCOPED_TABLES` **in the same change**
as the table itself. A tenant-scoped table arriving without its policy is the gap this
project has found five times; the row-level policy is not a follow-up ticket.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text, func, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

# Registration side-effect, and deliberately not a lazy import: the foreign keys below
# reference ``tenants.id`` / ``users.id`` and are resolved by name against the shared
# metadata. Same reasoning as :mod:`aegis.settings.models`.
import aegis.governance.models  # noqa: F401
from aegis.data import AegisBase, JsonB
from aegis.settings.models import SettingScope

__all__ = ["SKILLS_TABLE", "AgentSkill", "SkillScope"]

#: The table name, as one constant shared by the model, the RLS registry and the tests.
SKILLS_TABLE = "agent_skills"

#: The scope vocabulary, **imported rather than restated**. A skill resolves through the
#: settings resolver, so a second three-member enum spelled the same way would be two
#: vocabularies that can drift — and the drift would show up as a skill that resolves at
#: a layer the resolver does not believe in.
SkillScope = SettingScope

#: ``varchar`` + ``CHECK … IN`` rather than a native PostgreSQL enum, and
#: ``values_callable`` so the column stores ``platform``/``tenant``/``user`` rather than
#: SQLAlchemy's default of the member *names*. Both halves are the reasoning in
#: :mod:`aegis.settings.models`, which this table's scope column must agree with byte
#: for byte: the resolver compares the two.
_SKILL_SCOPE = SAEnum(
    SkillScope,
    name="skill_scope",
    native_enum=False,
    create_constraint=True,
    values_callable=lambda enum: [member.value for member in enum],
)


class AgentSkill(AegisBase):
    """One authored skill, at one scope: a name, a description, a body, a trigger.

    The four fields are the ``SKILL.md`` open standard's (agentskills.io) minus its
    filesystem: ``name`` and ``description`` are the frontmatter a compatible authoring
    tool writes, ``body`` is the Markdown under it, and ``triggers`` is the *when* —
    kept as data rather than as a keyword table compiled into a selector function, which
    is what made the file-based selector unable to see a skill it had not been edited
    to know about.

    **A row is content; it is not the answer to "is this in force".** That question has
    exactly one answer and it is the settings resolver's: the ``skills.enabled`` key,
    ``MergeRule.UNION``, resolved ``platform u tenant u user``. There is deliberately no
    ``enabled`` column here — a second flag would be a second mechanism, and the first
    time the two disagreed the screen and the prompt would each be reading a different
    one.

    Attributes:
        is_safety: Platform-only (enforced by a check constraint). A safety skill's
            name cannot be bound to a tenant's or a user's row, which is the same
            tighten-only discipline the guardrail settings run on.
    """

    __tablename__ = SKILLS_TABLE

    id: Mapped[int] = mapped_column(primary_key=True)
    scope: Mapped[SkillScope] = mapped_column(_SKILL_SCOPE, index=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), default=None, index=True
    )

    #: The name the model calls ``load_skill`` with. Short and slug-shaped, because it
    #: is an identifier in a tool argument, not a title.
    name: Mapped[str] = mapped_column(String(64), index=True)

    #: The one sentence that is **always** in the system prompt (tier 1 of progressive
    #: disclosure). It is the entire basis on which the model decides whether the body
    #: is worth a tool call, so it is required and it is bounded.
    description: Mapped[str] = mapped_column(String(280))

    #: The Markdown the ``load_skill`` tool returns. Never in the prompt until it is
    #: asked for; screened by the input rail **at authoring time** (§10.3).
    body: Mapped[str] = mapped_column(Text)

    #: Trigger terms, as data. Empty means "the description alone is the trigger" — the
    #: model may still load it, it simply gets no keyword hint.
    triggers: Mapped[Any] = mapped_column(JsonB, default=list)

    #: Which agent this skill belongs to, or ``NULL`` for "Aegis generally".
    #:
    #: The third targeting axis, and the one the other two could not express. ``scope``
    #: says *who* a skill reaches (platform / tenant / user) and ``triggers`` says *when*
    #: an agent reaches for it; neither can say *which agent it is for* — so a skill
    #: written for the research lane was offered to every lane and to the main persona
    #: besides. NULL is the default and means exactly what it meant before this column
    #: existed: in force for every agent this caller's scope puts it in force for.
    #:
    #: Deliberately a plain string with no foreign key and no enum. The roster is a
    #: **domain adapter's** (``app.adapter.roster``), not this package's, so the
    #: vocabulary is the deployment's to define; validating it against the live roster
    #: is the API layer's job, where the adapter is in scope, and it is done there by
    #: name so a typo is refused rather than stored. A column that named the four
    #: current lanes in a CHECK constraint would have made a domain swap a migration.
    agent_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)

    is_safety: Mapped[bool] = mapped_column(default=False)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_by: Mapped[str | None] = mapped_column(String(255), default=None)

    __table_args__ = (
        CheckConstraint(
            "(scope = 'platform') = (tenant_id IS NULL)",
            name="ck_agent_skills_platform_row_has_no_tenant",
        ),
        CheckConstraint(
            "(scope = 'user') = (user_id IS NOT NULL)",
            name="ck_agent_skills_user_row_has_a_user",
        ),
        # A safety skill is the platform's floor. If a tenant could set the flag, the
        # flag would be the escalation rather than the floor.
        CheckConstraint(
            "NOT is_safety OR scope = 'platform'",
            name="ck_agent_skills_only_platform_declares_safety",
        ),
        Index(
            "uq_agent_skills_platform_name",
            "name",
            unique=True,
            postgresql_where=text("scope = 'platform'"),
            sqlite_where=text("scope = 'platform'"),
        ),
        Index(
            "uq_agent_skills_tenant_name",
            "tenant_id",
            "name",
            unique=True,
            postgresql_where=text("scope = 'tenant'"),
            sqlite_where=text("scope = 'tenant'"),
        ),
        Index(
            "uq_agent_skills_user_name",
            "user_id",
            "name",
            unique=True,
            postgresql_where=text("scope = 'user'"),
            sqlite_where=text("scope = 'user'"),
        ),
    )
