"""Sub-agent prompts live in the LLM-Ops registry — asserted on the prompt actually sent.

§5.9b. Improving a sub-agent's system prompt has to be *promoting a version through the
existing eval gate*, not editing a string in a roster file and hoping. So the lane
resolves its prompt exactly as the main persona prompt already does: the ACTIVE
:class:`~aegis.ops.models.PromptVersion` if there is one, and the adapter's shipped
string as the **floor** if there is not.

Both halves are asserted on the text the model was handed — not on the resolver's return
value, because a resolver that agrees with itself proves nothing about what ran. The
registry here is the real one, over a real PostgreSQL, promoted through the real
:func:`aegis.ops.registry.promote`, so what is being tested is the whole chain and not a
double of it.
"""

from __future__ import annotations

from aegis.ops import registry

from ..agent.test_team_fanout import DEMO_QUERY, _drive, _roster, build_team_deps

#: The registry key a roster entry with no explicit ``prompt_key`` is versioned under.
RESEARCH_KEY = "subagent:research"


def _wire(deps):  # noqa: ANN001, ANN202 - AgentDeps -> (deps, sent)
    """Bind the REAL registry cache as the prompt seam and capture the lane prompts."""
    deps.active_prompt = registry.get_cached_active
    sent: dict[str, str] = {}
    inner = deps.complete

    async def complete(role, messages, **kwargs):  # noqa: ANN001, ANN003
        system = messages[0]["content"] if messages else ""
        if "You are ONE agent in a concurrent team" in system:
            for agent in ("research", "knowledge", "data", "policy"):
                if f"the {agent} agent" in system or f"[{agent}]" in system:
                    sent[agent] = system
            sent.setdefault("?", system)
        return await inner(role, messages, **kwargs)

    deps.complete = complete
    return deps, sent


async def test_a_subagent_with_an_active_version_is_sent_that_version(db):
    async with db() as session:
        version = await registry.create_draft(
            session,
            prompt_key=RESEARCH_KEY,
            system_prompt="[research] PROMOTED v1: search widely, cite everything.",
        )
        await registry.promote(session, version.id)
        await session.commit()
    assert registry.get_cached_active(RESEARCH_KEY) is not None

    deps, _ = build_team_deps(roster=_roster(2))
    deps, sent = _wire(deps)
    await _drive(deps, DEMO_QUERY)

    assert "PROMOTED v1" in sent["research"], (
        "the promoted version never reached the model; the lane sent something else"
    )
    # And only that lane's prompt moved: the knowledge agent has no active version.
    assert "You are the knowledge agent." in sent["knowledge"]
    assert "PROMOTED v1" not in sent["knowledge"]


async def test_with_no_active_version_the_adapter_prompt_is_the_floor(db):  # noqa: ARG001
    """A registry outage degrades to the shipped prompt, never to none."""
    registry.clear_cache()

    deps, _ = build_team_deps(roster=_roster(2))
    deps, sent = _wire(deps)
    await _drive(deps, DEMO_QUERY)

    assert "You are the research agent." in sent["research"]
    assert "You are the knowledge agent." in sent["knowledge"]


async def test_a_raising_registry_degrades_to_the_floor_rather_than_failing_the_run(db):  # noqa: ARG001
    """The registry is a read on the hot path; it may not be a way for a run to die."""

    def _explode(_key: str):  # noqa: ANN202 - registry double
        raise RuntimeError("registry is down")

    deps, _ = build_team_deps(roster=_roster(2))
    deps, sent = _wire(deps)
    deps.active_prompt = _explode
    events = await _drive(deps, DEMO_QUERY)

    assert "You are the research agent." in sent["research"]
    assert [e for e in events if e["type"] == "run_finished"][-1]["status"] == "completed"


async def test_rolling_a_version_back_puts_the_prior_prompt_back_on_the_wire(db):
    """Promotion is reversible, and the lane is what the reversal has to reach."""
    async with db() as session:
        first = await registry.create_draft(
            session, prompt_key=RESEARCH_KEY, system_prompt="[research] version one."
        )
        await registry.promote(session, first.id)
        second = await registry.create_draft(
            session, prompt_key=RESEARCH_KEY, system_prompt="[research] version two."
        )
        await registry.promote(session, second.id)
        await session.commit()

    deps, _ = build_team_deps(roster=_roster(2))
    deps, sent = _wire(deps)
    await _drive(deps, DEMO_QUERY)
    assert "version two" in sent["research"]

    async with db() as session:
        await registry.rollback(session, RESEARCH_KEY)
        await session.commit()

    deps, _ = build_team_deps(roster=_roster(2))
    deps, sent = _wire(deps)
    await _drive(deps, DEMO_QUERY)
    assert "version one" in sent["research"], "the rollback never reached the lane"
