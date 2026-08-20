"""``load_skill`` — tier 2 of progressive disclosure, as a real tool call (§10.2).

The whole of §10.2 is one design decision: **a skill body is fetched by a tool call, not
by a retrieval heuristic upstream of the prompt.** Everything else follows from it.

*The mechanism.* The system prompt carries one line per skill in force — name, scope and
description (:meth:`aegis.skills.store.ResolvedSkill.card`). The bodies are not there. If
the model decides one of those descriptions is worth reading, it calls ``load_skill`` and
the body comes back as a tool result.

*The visibility, which is the same thing.* Because the load is an ordinary tool call it
travels the ordinary path: the ``act`` node emits ``tool_call`` with the tool name, the
argument and the risk tier, a ``tool.load_skill`` span opens with
``TOOL_NAME``/``TOOL_RISK`` attributes, the result passes the ``TOOL_RESULT`` rail like
any other third-party text, and a ``tool_result`` event carries the outcome. So the
console shows a user the agent deciding it needed a skill, with the same treatment as any
other action — rather than a skill silently appearing in a prompt nobody sees.

*The risk tier, and why it is LOW.* ``ToolSpec.risk`` is the only input to the human
gate, so this is not a label. ``load_skill`` reads one row that this caller's own
resolution already put in force, changes nothing, reaches no network and can return no
skill the caller was not already entitled to. It is also on the hot path of the feature:
tiering it at or above ``gate_min_risk`` would stop every skill load at a human approval,
which is not a safety property, it is the feature not working. The two things that make
LOW honest are elsewhere and both already exist — the body was screened by the input rail
**at authoring time** (§10.3), and the returned text is screened again by the
``TOOL_RESULT`` rail before it reaches the generation prompt.

Registered here rather than in ``app.adapter.tools`` because it is a *platform* tool: it
must survive a retarget, and the on-the-day adapter swap must not be able to take the
skills mechanism with it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from aegis.core.types import RiskLevel

logger = logging.getLogger(__name__)

__all__ = [
    "LOAD_SKILL_RISK",
    "LOAD_SKILL_TOOL",
    "load_skill_definition",
    "run_load_skill",
]

#: The reserved tool name. A domain adapter that registered its own ``load_skill`` would
#: shadow the platform's; :func:`app.agent.deps._default_tool_definitions_for` appends
#: this one last and the registry lookup prefers the platform's, so the collision is
#: resolved in favour of the tool that cannot be swapped out.
LOAD_SKILL_TOOL = "load_skill"

#: The declared tier. See the module docstring — this is the gate's only input.
LOAD_SKILL_RISK = RiskLevel.LOW


@dataclass(frozen=True)
class _Outcome:
    """A structural :class:`aegis.agent.deps.ToolOutcome` (``ok`` + ``summary``)."""

    ok: bool
    summary: str


def load_skill_definition() -> dict[str, Any]:
    """Return the OpenAI/MCP ``function`` definition offered to the planner.

    Returns:
        The tool definition. The description names the constraint that matters — a skill
        must already be listed in the prompt — because a model that invents a plausible
        name gets a refusal, and one refusal per turn is a wasted round trip.
    """
    return {
        "type": "function",
        "function": {
            "name": LOAD_SKILL_TOOL,
            "description": (
                "Read the full body of one skill listed under 'Skills available' in "
                "your context. Call it when a skill's description says it covers what "
                "you are about to do. Only names from that list can be loaded."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "The skill's name, exactly as listed.",
                    }
                },
                "required": ["name"],
                "additionalProperties": False,
            },
        },
    }


async def run_load_skill(args: dict[str, Any]) -> _Outcome:
    """Load one skill's body for the caller's own resolved scope.

    The tenant and the user are read from the request's governance context, never from
    ``args``. That is not defensive tidiness: the argument comes from a model that has
    just read attacker-influenced text, so a ``tenant_id`` on the wire here would be a
    prompt-injectable cross-tenant read — the shape of five leaks this repository has
    already fixed.

    Args:
        args: The model's arguments; only ``name`` is read.

    Returns:
        A structural ``ToolOutcome``. A refusal is ``ok=False`` with a sentence, not an
        exception: the graph turns an exception into "Tool error: …" and the model
        learns nothing it can act on.
    """
    from aegis.agent.subagent import current_agent_id
    from aegis.skills.store import SkillNotFoundError, load_skill

    from app.agent.deps import _current_tenant_id, _current_user_id
    from app.data.session import get_sessionmaker, set_tenant_scope

    name = str(args.get("name") or "").strip()
    if not name:
        return _Outcome(ok=False, summary="load_skill needs the name of a skill to load.")

    tenant_id = _current_tenant_id()
    user_id = _current_user_id()
    # Which lane is asking, read the same way the tenant is: from the context this call
    # is running in, never from ``args``. A skill assigned to another agent is not in
    # force here, so tier 2 refuses exactly what tier 1 never offered — one answer to
    # "is this skill mine", not two.
    agent_id = current_agent_id()
    try:
        async with get_sessionmaker()() as session:
            await set_tenant_scope(session, tenant_id)
            skill = await load_skill(
                session, name, tenant_id=tenant_id, user_id=user_id, agent_id=agent_id
            )
            await session.rollback()
    except SkillNotFoundError as exc:
        return _Outcome(ok=False, summary=str(exc))
    except Exception:  # noqa: BLE001 - a skills outage must not fail the run
        logger.warning("load_skill(%r) could not read the skills store", name, exc_info=True)
        return _Outcome(
            ok=False,
            summary=(
                f"The skills store is unreachable, so {name!r} could not be loaded. "
                "Answer without it rather than guessing at its contents."
            ),
        )
    return _Outcome(ok=True, summary=f"# skill: {skill.name} ({skill.scope})\n\n{skill.body}")
