"""Resolving a skill across the three scopes, and authoring one safely.

Two things happen here, and both of them are deliberately *not* new mechanisms.

**Resolution goes through the Phase 3 settings resolver.** Which skills are in force is
the value of one catalogue key — ``skills.enabled``, ``MergeRule.UNION`` — resolved
``platform u tenant u user`` by :func:`aegis.settings.resolver.resolve`. That is the
whole of the scope precedence, and it is why *"a tenant cannot switch off a platform
safety skill"* needs no check: a union is a superset of every layer in it, so the
platform's members survive by arithmetic. Modelling the scopes a second time here — a
``scope`` column and a hand-written fold — would have produced two answers to one
question, and the day they disagreed the prompt and the screen would each have been
reading a different one.

The resolver answers *which names*. This module answers *which row* each name binds to,
which is a separate question because a name can exist at more than one scope, and it is
where the second half of the no-shadow rule lives:

.. code-block:: text

    a platform row with is_safety=True wins its name outright
    otherwise      user > tenant > platform          (most specific wins)

Without the first line a tenant admin could author a skill named ``safety_floor``, have
it bound in place of the platform's, and thereby *replace* a safety instruction while
the resolved set still contained its name — a shadow that no set-level union can catch,
because the set is identical either way. That is the tighten-only discipline the
guardrail settings run on, applied to the one place a skill can be substituted.

**Authoring is an untrusted-input path, and the rail runs before a row exists.** A skill
body is stored text that reaches a prompt: exactly the surface §7.16 row 11 already
screens for uploaded memory, and for the same reason — it costs nothing at write time,
survives every session, and arrives *inside* the prompt rather than in front of it. So
:func:`write_skill` takes the input rail as a **required** argument. Not optional with a
safe default, because a default is a thing a caller can be unaware of; a required
parameter is a thing a caller must decide about. The screening happens before the row
is constructed, so a blocked body leaves nothing behind to clean up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.core.types import GuardVerdict
from aegis.settings.models import SettingScope
from aegis.settings.resolver import resolve, write_setting
from aegis.skills.document import SkillDocument
from aegis.skills.models import AgentSkill, SkillScope

__all__ = [
    "MAIN_AGENT_ID",
    "SKILLS_ENABLED_KEY",
    "InputRail",
    "ResolvedSkill",
    "SkillBodyRefusedError",
    "SkillNotFoundError",
    "SkillScopeError",
    "delete_skill",
    "list_skills",
    "load_skill",
    "resolve_skills",
    "set_active",
    "write_skill",
]

#: The catalogue key that decides which skills are live. One key, one resolver, three
#: scopes — see the module docstring for why this is not a column on ``agent_skills``.
SKILLS_ENABLED_KEY = "skills.enabled"

#: Name binding order for a non-safety name: most specific scope wins. A platform
#: **safety** row is handled before this tuple is consulted at all.
_BIND_ORDER: tuple[SkillScope, ...] = (
    SkillScope.USER,
    SkillScope.TENANT,
    SkillScope.PLATFORM,
)


class InputRail(Protocol):
    """The guardrail entry point :func:`write_skill` screens authored text with.

    Structural rather than an import of ``aegis.guardrails``: the host binds the rail
    that already carries its tenant's configured screens and its own completer, and a
    second rail constructed here would be a second policy.
    """

    async def __call__(self, text: str) -> Any:  # noqa: ANN401 - the host's GuardResult
        """Screen ``text``, returning a ``GuardResult`` (``verdict`` / ``reason`` / ``text``)."""
        ...


class SkillBodyRefusedError(ValueError):
    """The input rail blocked the authored text, so **no row was written**.

    Attributes:
        reason: The rail's own sentence, safe to show the author. Returning "invalid
            input" here would tell somebody who wrote a legitimate runbook nothing at
            all, and tell an attacker exactly as much.
        field: Which field was refused — ``"body"`` or ``"description"``.
    """

    def __init__(self, reason: str, *, field: str) -> None:
        """Build the refusal, naming the rail's reason and the field it refused."""
        super().__init__(reason)
        self.reason = reason
        self.field = field


class SkillScopeError(ValueError):
    """The scope and the ids handed in do not describe a writable layer."""


class SkillNotFoundError(LookupError):
    """No skill of that name is in force for this caller."""


@dataclass(frozen=True)
class ResolvedSkill:
    """One skill in force for a run, with the layer that provided it.

    Attributes:
        name: The slug ``load_skill`` is called with.
        description: Tier 1 — always in the system prompt.
        body: Tier 2 — returned only by a ``load_skill`` tool call.
        triggers: The stored trigger terms.
        scope: Which layer's row won the name (``platform`` / ``tenant`` / ``user``).
        is_safety: Whether this is a platform safety skill, i.e. one whose name no
            other layer may rebind.
        agent_id: The agent this skill was assigned to, or ``None`` for every agent.
    """

    name: str
    description: str
    body: str
    triggers: tuple[str, ...]
    scope: str
    is_safety: bool
    agent_id: str | None = None

    def card(self) -> str:
        """Return the one-line tier-1 card the system prompt carries.

        The *whole* of what a skill costs before it is loaded. Naming the scope is not
        decoration: a user reading their own prompt should be able to tell their own
        skill from the platform's, for the same reason the settings screen badges which
        layer decided a value.
        """
        return f"- {self.name} ({self.scope}): {self.description}"


#: The lane a run is on when nothing says otherwise: the main persona, the one agent
#: every deployment has. It is the default of :func:`resolve_skills`'s ``agent_id``, so
#: every existing caller keeps asking the question it was already asking — "what is in
#: force for the main lane?" — and a fan-out lane is the caller that passes something
#: else. Named here rather than in the adapter because it is not a domain fact: a
#: deployment can rewrite its whole sub-agent roster and still have a main lane.
MAIN_AGENT_ID = "main"


def _for_agent(row_agent_id: str | None, agent_id: str) -> bool:
    """Return whether a row assigned to ``row_agent_id`` is in force for ``agent_id``.

    ``NULL`` means unassigned — the skill belongs to Aegis generally and reaches every
    agent, which is what every row meant before the column existed. Anything else
    reaches that one agent and no other. There is no partial match and no prefix rule:
    a skill either names your lane or it is not yours.
    """
    return row_agent_id is None or str(row_agent_id) == agent_id


def _rows_stmt(names: tuple[str, ...], *, tenant_id: int | None, user_id: int | None):  # noqa: ANN202
    """Build the SELECT for every row that could bind one of ``names``.

    The app-level ``WHERE`` is the belt over the database's ``tenant_isolation`` policy
    that every governed read in this codebase carries. The platform disjunct is only
    correct because ``agent_skills`` is registered in
    :data:`aegis.governance.rls._PLATFORM_BASELINE_TABLES`; without it a bound tenant
    scope would silently lose the platform layer — including its safety skills.
    """
    layers = [AgentSkill.scope == SkillScope.PLATFORM]
    if tenant_id is not None:
        layers.append(
            (AgentSkill.scope == SkillScope.TENANT)
            & (AgentSkill.tenant_id == tenant_id)
        )
        if user_id is not None:
            layers.append(
                (AgentSkill.scope == SkillScope.USER)
                & (AgentSkill.tenant_id == tenant_id)
                & (AgentSkill.user_id == user_id)
            )
    return select(AgentSkill).where(AgentSkill.name.in_(names)).where(or_(*layers))


def _bind(name: str, rows: dict[tuple[SkillScope, str], AgentSkill]) -> AgentSkill | None:
    """Return the one row that ``name`` binds to, or ``None`` if no layer has it.

    **This function is the no-shadow rule.** A platform row flagged ``is_safety`` wins
    its name outright; every other name goes to the most specific scope that declares
    it. Deleting the first branch would leave the union unchanged and the *content*
    replaced, which is the shadow attack exactly — so the branch is what
    ``test_a_tenant_skill_cannot_shadow_a_platform_safety_skill`` mutates.
    """
    platform = rows.get((SkillScope.PLATFORM, name))
    if platform is not None and platform.is_safety:
        return platform
    for scope in _BIND_ORDER:
        row = rows.get((scope, name))
        if row is not None:
            return row
    return None


async def resolve_skills(
    session: AsyncSession,
    *,
    tenant_id: int | None = None,
    user_id: int | None = None,
    query: str = "",
    limit: int | None = None,
    agent_id: str = MAIN_AGENT_ID,
) -> list[ResolvedSkill]:
    """Return the skills in force for this caller **on this agent**, most relevant first.

    Args:
        session: The async session. On PostgreSQL the caller is expected to have bound
            the tenant scope; the query filters by tenant as well, so the two agree.
        tenant_id: The tenant to resolve for, or ``None`` for the platform layer alone.
        user_id: The user to resolve for. Ignored without a tenant, because a user row
            is only meaningful inside its tenant.
        query: This turn's question. Used **only** to order the cards — a skill whose
            trigger term appears in the query sorts first — never to filter, because a
            trigger that silently withholds a skill is indistinguishable in the trace
            from a skill that does not exist.
        limit: Cap on the number of cards returned. ``None`` for all of them.
        agent_id: Which agent is asking. A row assigned to another agent is dropped
            here — the one place skills are selected for a run, so there is no second
            path that could disagree with it. An **unassigned** row is in force for
            every agent, which is what every row was before the column existed:
            leaving this argument alone reproduces the old behaviour exactly.

    Returns:
        The resolved skills. Empty when the tenant has enabled none, which is the
        default state and not an error.
    """
    enabled, _source = await resolve(
        session, SKILLS_ENABLED_KEY, tenant_id=tenant_id, user_id=user_id
    )
    names = tuple(dict.fromkeys(str(n) for n in (enabled or ())))
    if not names:
        return []
    result = await session.execute(
        _rows_stmt(names, tenant_id=tenant_id, user_id=user_id)
    )
    rows = {(row.scope, row.name): row for row in result.scalars()}
    resolved: list[ResolvedSkill] = []
    for name in names:
        row = _bind(name, rows)
        if row is None:
            # Enabled by name, but no layer this caller can see holds the row. A stale
            # name in the set is not an error — a platform skill may have been deleted
            # while a tenant's list still names it — and it is silently absent rather
            # than raising into a hot path.
            continue
        if not _for_agent(getattr(row, "agent_id", None), agent_id):
            # Enabled, visible, and addressed to a different agent. Dropped here rather
            # than at the prompt, so a lane never carries a card it cannot act on and
            # ``load_skill`` cannot reach a body by naming it either — tier 1 and tier 2
            # answer from this one function.
            continue
        resolved.append(
            ResolvedSkill(
                name=row.name,
                description=row.description,
                body=row.body,
                triggers=tuple(row.triggers or ()),
                scope=str(row.scope.value if hasattr(row.scope, "value") else row.scope),
                is_safety=bool(row.is_safety),
                agent_id=getattr(row, "agent_id", None),
            )
        )
    resolved.sort(key=lambda s: (not _triggered(s, query), not s.is_safety, s.name))
    return resolved[:limit] if limit is not None else resolved


def _triggered(skill: ResolvedSkill, query: str) -> bool:
    """Return whether one of ``skill``'s trigger terms appears in ``query``."""
    lowered = query.lower()
    return any(term and term in lowered for term in skill.triggers)


async def load_skill(
    session: AsyncSession,
    name: str,
    *,
    tenant_id: int | None = None,
    user_id: int | None = None,
    agent_id: str = MAIN_AGENT_ID,
) -> ResolvedSkill:
    """Return one skill's full body — tier 2, reached only by a real tool call.

    Resolution runs in full rather than querying the row by name, which is the point:
    a skill that is not in force for this caller cannot be loaded by naming it, and a
    name that binds to a platform safety row loads *that* row's body whoever asks.

    Args:
        session: The async session, tenant scope already bound.
        name: The skill to load.
        tenant_id: The caller's tenant.
        user_id: The caller's user id.
        agent_id: Which agent is asking. A skill assigned to another agent is not in
            force here, so naming it loads nothing — the refusal a caller gets is the
            same one it gets for a skill nobody authored.

    Returns:
        The resolved skill, body included.

    Raises:
        SkillNotFoundError: If no skill of that name is in force for this caller.
    """
    for skill in await resolve_skills(
        session, tenant_id=tenant_id, user_id=user_id, agent_id=agent_id
    ):
        if skill.name == name:
            return skill
    raise SkillNotFoundError(
        f"No skill named {name!r} is in force for you. A skill must be authored and "
        "enabled at the platform, tenant or user layer before it can be loaded."
    )


async def list_skills(
    session: AsyncSession,
    *,
    scope: SkillScope | None = None,
    tenant_id: int | None = None,
    user_id: int | None = None,
) -> list[AgentSkill]:
    """Return the authored rows a management screen shows, in force or not.

    Distinct from :func:`resolve_skills` on purpose: an authoring surface must be able
    to show a skill that exists and is switched off, and a run must never see one.

    Args:
        session: The async session.
        scope: Restrict to one layer, or ``None`` for every layer this caller can see.
        tenant_id: The tenant whose rows to include.
        user_id: The user whose rows to include.

    Returns:
        The rows, ordered by scope then name.
    """
    layers = [AgentSkill.scope == SkillScope.PLATFORM]
    if tenant_id is not None:
        layers.append(
            (AgentSkill.scope == SkillScope.TENANT) & (AgentSkill.tenant_id == tenant_id)
        )
        if user_id is not None:
            layers.append(
                (AgentSkill.scope == SkillScope.USER)
                & (AgentSkill.tenant_id == tenant_id)
                & (AgentSkill.user_id == user_id)
            )
    stmt = select(AgentSkill).where(or_(*layers))
    if scope is not None:
        stmt = stmt.where(AgentSkill.scope == scope)
    rows = list((await session.execute(stmt)).scalars())
    rows.sort(key=lambda r: (str(r.scope), r.name))
    return rows


async def _screen(rail: InputRail, text: str, *, field: str) -> str:
    """Run the input rail over authored text and refuse a BLOCK.

    Returns the rail's ``text`` rather than the argument, because that is where a
    REDACT verdict's redaction lives: storing the original would make the rail
    decorative, which is the failure mode the memory-write path already names.

    Args:
        rail: The bound input rail.
        text: The authored text.
        field: Which field this is, for the refusal message.

    Returns:
        The text to store.

    Raises:
        SkillBodyRefusedError: If the rail blocked it.
    """
    result = await rail(text)
    if result.verdict is GuardVerdict.BLOCK:
        raise SkillBodyRefusedError(
            f"The guardrails refused this skill's {field}, so nothing was stored: "
            f"{result.reason} A skill body is replayed into a later prompt as trusted "
            "instructions, so it is screened exactly like a live question — and it is "
            "screened when it is written, not when it is used.",
            field=field,
        )
    return str(result.text)


def _check_scope(
    scope: SkillScope, *, tenant_id: int | None, user_id: int | None, is_safety: bool
) -> None:
    """Refuse a scope whose ids do not describe it, or a non-platform safety skill.

    Mirrors the check constraints on the table. Both exist: the constraint is the one
    that binds, and this one gives a sentence rather than an ``IntegrityError``.
    """
    if scope is SkillScope.PLATFORM and (tenant_id is not None or user_id is not None):
        raise SkillScopeError("a platform skill carries no tenant and no user")
    if scope is SkillScope.TENANT and (tenant_id is None or user_id is not None):
        raise SkillScopeError(
            "a tenant skill needs a tenant and carries no user; write at user scope"
        )
    if scope is SkillScope.USER and (tenant_id is None or user_id is None):
        raise SkillScopeError(
            "a user skill needs both a tenant and a user — a row with no tenant would "
            "be readable by every tenant"
        )
    if is_safety and scope is not SkillScope.PLATFORM:
        raise SkillScopeError(
            "only the platform declares a safety skill. A safety skill's name cannot "
            "be rebound by any other layer, so a tenant that could set the flag would "
            "be granting itself a floor nobody above it chose."
        )


async def write_skill(
    session: AsyncSession,
    document: SkillDocument,
    *,
    screen: InputRail,
    scope: SkillScope,
    actor_role: str,
    tenant_id: int | None = None,
    user_id: int | None = None,
    actor_user_id: int | None = None,
    updated_by: str | None = None,
    is_safety: bool = False,
    enable: bool = True,
    agent_id: str | None = None,
) -> AgentSkill:
    """Author one skill at one scope, screening it **before** a row exists.

    The order of operations is the security property, so it is stated rather than
    implied: the rail runs first, on both fields that reach a prompt; only then is a
    row constructed. There is no path through this function that writes and then
    screens, and no argument that turns the screening off.

    The caller owns the transaction: rows are flushed, not committed, so a route can
    write the skill, its activation and its audit entry atomically.

    Args:
        session: The async session, tenant scope already bound.
        document: The parsed ``SKILL.md``.
        screen: The bound input rail. **Required** — see the module docstring.
        scope: The layer to author at.
        actor_role: The writer's fine RBAC role, passed through to the resolver when
            the activation is written.
        tenant_id: The tenant the row belongs to.
        user_id: The user the row belongs to.
        actor_user_id: The writer's own user id.
        updated_by: Who to record as the author; defaults to ``actor_role``.
        is_safety: Platform-only. A safety skill's name cannot be rebound.
        enable: Whether to put the skill in force by adding its name to
            ``skills.enabled`` at this scope, in the same transaction.
        agent_id: The agent this skill belongs to, or ``None`` for every agent. Stored
            as given: the roster it must be a member of belongs to the domain adapter,
            so the *name* is validated one layer up (``app.api.routes_skills``) where
            the live roster is in scope. This function will not invent a default.

    Returns:
        The inserted or updated :class:`~aegis.skills.models.AgentSkill`.

    Raises:
        SkillBodyRefusedError: If the rail blocked the body or the description.
        SkillScopeError: If the scope and the ids disagree, or a non-platform caller
            asked for a safety skill.
        SettingError: If the activation write is not the caller's to make.
    """
    _check_scope(scope, tenant_id=tenant_id, user_id=user_id, is_safety=is_safety)

    # ── the rail, before anything is constructed ────────────────────────────────
    description = await _screen(screen, document.description, field="description")
    body = await _screen(screen, document.body, field="body")

    # Authority before content. ``set_active`` writes ``skills.enabled`` through the
    # settings resolver, which is where "may this role write at this layer" is decided —
    # so running it first means a caller who is not entitled to the layer is refused
    # before a body is ever constructed. The whole call is one transaction the caller
    # owns, so an ordering that wrote the row first would still leave nothing behind;
    # this way there is nothing to leave behind in the first place.
    await set_active(
        session,
        document.name,
        active=enable,
        scope=scope,
        actor_role=actor_role,
        tenant_id=tenant_id,
        user_id=user_id,
        actor_user_id=actor_user_id,
        updated_by=updated_by,
    )

    row = await _existing(session, document.name, scope=scope, tenant_id=tenant_id, user_id=user_id)
    if row is None:
        row = AgentSkill(
            scope=scope,
            tenant_id=tenant_id,
            user_id=user_id,
            name=document.name,
            description=description,
            body=body,
            triggers=list(document.triggers),
            is_safety=is_safety,
            agent_id=agent_id,
            updated_by=updated_by or actor_role,
        )
        session.add(row)
    else:
        row.description = description
        row.body = body
        row.triggers = list(document.triggers)
        row.is_safety = is_safety
        row.agent_id = agent_id
        row.updated_by = updated_by or actor_role
        row.updated_at = datetime.now(UTC)
    await session.flush()
    return row


async def set_active(
    session: AsyncSession,
    name: str,
    *,
    active: bool,
    scope: SkillScope,
    actor_role: str,
    tenant_id: int | None = None,
    user_id: int | None = None,
    actor_user_id: int | None = None,
    updated_by: str | None = None,
) -> list[str]:
    """Put one skill in force at ``scope``, or take it out, and return the new list.

    Writes ``skills.enabled`` through :func:`aegis.settings.resolver.write_setting`, so
    every refusal a settings write already makes — the role, the scope's reach, the
    value's type — is made here too, once.

    **Taking a name out of one layer's list does not take it out of the resolved set**
    when another layer still names it. That is the union doing its job, and it is
    exactly why a tenant admin cannot deactivate a platform safety skill by clearing
    their own list.

    Args:
        session: The async session.
        name: The skill's name.
        active: Whether it should be in force at this layer.
        scope: The layer to write at.
        actor_role: The writer's fine RBAC role.
        tenant_id: The tenant the row belongs to.
        user_id: The user the row belongs to.
        actor_user_id: The writer's own user id.
        updated_by: Who to record as the writer.

    Returns:
        The list now stored at ``scope``.

    Raises:
        SettingError: If the write is not the caller's to make.
    """
    setting_scope = SettingScope(scope.value if hasattr(scope, "value") else scope)
    current = await _enabled_at(session, scope=setting_scope, tenant_id=tenant_id, user_id=user_id)
    names = [n for n in current if n != name] + ([name] if active else [])
    await write_setting(
        session,
        SKILLS_ENABLED_KEY,
        names,
        scope=setting_scope,
        actor_role=actor_role,
        tenant_id=tenant_id,
        user_id=user_id,
        actor_user_id=actor_user_id,
        updated_by=updated_by,
    )
    return names


async def delete_skill(
    session: AsyncSession,
    name: str,
    *,
    scope: SkillScope,
    actor_role: str,
    tenant_id: int | None = None,
    user_id: int | None = None,
    actor_user_id: int | None = None,
    updated_by: str | None = None,
) -> None:
    """Delete one authored skill and take its name out of force, in one transaction.

    Args:
        session: The async session.
        name: The skill to delete.
        scope: The layer it was authored at.
        actor_role: The deleter's fine RBAC role.
        tenant_id: The tenant the row belongs to.
        user_id: The user the row belongs to.
        actor_user_id: The deleter's own user id.
        updated_by: Who to record on the activation write.

    Raises:
        SkillNotFoundError: If no row of that name exists at that layer.
        SettingError: If the deactivation is not the caller's to write.
    """
    row = await _existing(session, name, scope=scope, tenant_id=tenant_id, user_id=user_id)
    if row is None:
        raise SkillNotFoundError(f"No skill named {name!r} is authored at {scope} scope.")
    await session.delete(row)
    await set_active(
        session,
        name,
        active=False,
        scope=scope,
        actor_role=actor_role,
        tenant_id=tenant_id,
        user_id=user_id,
        actor_user_id=actor_user_id,
        updated_by=updated_by,
    )
    await session.flush()


async def _enabled_at(
    session: AsyncSession,
    *,
    scope: SettingScope,
    tenant_id: int | None,
    user_id: int | None,
) -> list[str]:
    """Return the ``skills.enabled`` list written at exactly one layer (not merged)."""
    from aegis.settings.models import Setting

    stmt = select(Setting.value).where(
        Setting.key == SKILLS_ENABLED_KEY, Setting.scope == scope
    )
    stmt = stmt.where(
        Setting.tenant_id.is_(None) if tenant_id is None else Setting.tenant_id == tenant_id
    )
    stmt = stmt.where(
        Setting.user_id.is_(None) if user_id is None else Setting.user_id == user_id
    )
    value = (await session.execute(stmt)).scalars().one_or_none()
    return [str(n) for n in (value or ())]


async def _existing(
    session: AsyncSession,
    name: str,
    *,
    scope: SkillScope,
    tenant_id: int | None,
    user_id: int | None,
) -> AgentSkill | None:
    """Return the row already authored at this exact layer, if there is one."""
    stmt = select(AgentSkill).where(AgentSkill.name == name, AgentSkill.scope == scope)
    stmt = stmt.where(
        AgentSkill.tenant_id.is_(None)
        if tenant_id is None
        else AgentSkill.tenant_id == tenant_id
    )
    stmt = stmt.where(
        AgentSkill.user_id.is_(None) if user_id is None else AgentSkill.user_id == user_id
    )
    return (await session.execute(stmt)).scalars().one_or_none()
