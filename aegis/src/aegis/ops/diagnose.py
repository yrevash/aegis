"""Diagnose — cluster failing evals + propose an improved system prompt (a DRAFT).

The **Diagnose** stage of the LLM-Ops closed loop (see ``docs/learn/40-pipelines.md``
Gap 2). It reads the recent *failing* :class:`~aegis.ops.models.EvalResult` rows that
:mod:`aegis.ops.trace_eval` wrote, tallies **which metrics fail most** (answer vs the
per-step ``step:retrieval`` / ``step:tool`` / ``step:guardrail`` facets), then feeds the
current base prompt + the worst failure critiques + that breakdown to a Reflexion-style
**prompt optimizer** (``ModelRole.REASONING``, JSON-mode). The optimizer's improved prompt
is written back **only as a DRAFT** :class:`~aegis.ops.models.PromptVersion` — it never goes
live here; promotion is the sole responsibility of :mod:`aegis.ops.release` behind the eval
gate + tiered approval. Diagnose is deliberately conservative and total:

* **No failures → no draft.** Nothing to fix ⇒ ``draft_version_id=None``.
* **Defensive parsing.** A malformed / empty optimizer response yields *no draft*
  (``draft_version_id=None``) rather than a crash or a garbage prompt.
* **Injected prompt floor.** When no active version exists for the key, the base prompt
  is the injected ``render_floor_prompt(prompt_key)`` (the adapter/persona baseline the
  host wires in via :func:`aegis.ops.config.configure_ops`).
* **Injected model call.** ``complete`` is a parameter, so tests run fully offline.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from aegis.core.models import ModelRole
from aegis.ops import config, registry
from aegis.ops.models import EvalResult

logger = logging.getLogger(__name__)

#: The eval metrics Diagnose tallies (mirrors the ``trace_eval`` namespaces). Any other
#: metric a failing row carries is still counted, but these are the ones the optimizer is
#: told about explicitly.
_KNOWN_METRICS: tuple[str, ...] = (
    "answer",
    "step:retrieval",
    "step:tool",
    "step:guardrail",
)

#: How many worst-offending failure critiques to show the optimizer.
_MAX_EXAMPLES = 8

_OPTIMIZER_SYSTEM = (
    "You are a prompt optimizer for an enterprise agent. You are given the agent's CURRENT "
    "SYSTEM PROMPT, a BREAKDOWN of which evaluation metrics are failing most, and concrete "
    "FAILURE EXAMPLES (judge critiques from failing runs). Rewrite the system prompt so the "
    "agent stops making these mistakes, while preserving all existing safety, guardrail, "
    "tool, and scope instructions. Make the smallest change that plausibly fixes the "
    "failures; do not remove constraints. Respond with ONLY a JSON object of the form "
    '{"system_prompt": "<the full improved system prompt>", "rationale": "<one sentence>"}.'
)


@dataclass
class DiagnoseResult:
    """The outcome of one diagnose pass.

    Attributes:
        draft_version_id: The id of the DRAFT :class:`PromptVersion` written, or ``None``
            when there was nothing to fix (no failures) or the optimizer response could
            not be used.
        failure_summary: A short human-readable summary (also stored as the draft's notes).
        failures_considered: How many failing ``EvalResult`` rows were read.
        metric_breakdown: ``metric name → failing-row count`` (the failure tally).
    """

    draft_version_id: int | None
    failure_summary: str
    failures_considered: int
    metric_breakdown: dict[str, int] = field(default_factory=dict)


def _critique(row: EvalResult) -> str:
    """Best-effort extract of a human-readable critique from a failing eval row's detail."""
    detail = row.detail or {}
    for key in ("critique", "reason", "explanation", "rationale", "note"):
        value = detail.get(key)
        if value:
            return str(value)
    # Fall back to a compact rendering of the detail so the optimizer still gets signal.
    node = detail.get("node") or detail.get("tool") or detail.get("verdict")
    tail = f" ({node})" if node else ""
    return f"{row.metric} scored {row.score:.2f}{tail}"


def _build_summary(breakdown: dict[str, int], considered: int) -> str:
    """Render a one-line summary of the failure tally."""
    if not breakdown:
        return "No failing evals."
    top = sorted(breakdown.items(), key=lambda kv: kv[1], reverse=True)
    parts = ", ".join(f"{metric}={count}" for metric, count in top)
    return f"{considered} failing evals: {parts}"


def _parse_optimized_prompt(content: str) -> str | None:
    """Parse the optimizer's JSON response into an improved prompt, or ``None`` if unusable.

    Defensive: a non-JSON body, a missing/blank ``system_prompt`` key, or a wrong type all
    yield ``None`` (⇒ no draft) rather than raising.
    """
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        logger.warning("diagnose: optimizer response was not valid JSON; no draft written")
        return None
    if not isinstance(data, dict):
        return None
    prompt = data.get("system_prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        logger.warning("diagnose: optimizer response missing a usable system_prompt")
        return None
    return prompt.strip()


async def diagnose(
    session: Any,  # noqa: ANN401 - AsyncSession, kept loose for offline testability
    *,
    prompt_key: str,
    complete: Any,  # noqa: ANN401 - async complete(role, messages, ...) -> .content
    limit: int = 50,
    render_floor_prompt: Callable[[str], str] | None = None,
) -> DiagnoseResult:
    """Cluster recent failures for ``prompt_key`` and write an improved-prompt DRAFT.

    Reads up to ``limit`` most-recent failing ``EvalResult`` rows, tallies the failing
    metrics, and — when there are failures — asks the injected ``complete`` (the REASONING
    role, JSON mode) to rewrite the current base prompt. The rewrite is persisted **only as
    a draft** (parented to the current active version, if any), never promoted.

    Args:
        session: An async SQLAlchemy session.
        prompt_key: The prompt whose active version is the base to improve (also the
            key passed to the injected floor renderer when nothing is active).
        complete: Async ``complete(role, messages, *, response_format=None) -> result``
            with ``.content: str``. Injected so tests never call real infra.
        limit: Max number of recent failing rows to consider (default 50).
        render_floor_prompt: Optional ``render_floor_prompt(prompt_key) -> str`` override
            for the no-active-version floor; defaults to the value configured via
            :func:`aegis.ops.config.configure_ops`.

    Returns:
        A :class:`DiagnoseResult`. ``draft_version_id`` is ``None`` when there were no
        failures or the optimizer response could not be used; otherwise it is the id of
        the newly-written DRAFT ``PromptVersion``.
    """
    rows = list(
        (
            await session.execute(
                select(EvalResult)
                .where(
                    EvalResult.passed.is_(False),
                    EvalResult.prompt_key == prompt_key,
                )
                .order_by(EvalResult.ts.desc(), EvalResult.id.desc())
                .limit(limit)
            )
        ).scalars().all()
    )

    breakdown: dict[str, int] = {}
    for row in rows:
        breakdown[row.metric] = breakdown.get(row.metric, 0) + 1

    summary = _build_summary(breakdown, len(rows))

    # Nothing to fix — return without touching the registry.
    if not rows:
        return DiagnoseResult(
            draft_version_id=None,
            failure_summary=summary,
            failures_considered=0,
            metric_breakdown={},
        )

    # Base prompt: the active version's prompt, else the injected floor.
    floor = render_floor_prompt or config.render_floor_prompt
    active = await registry.get_active(session, prompt_key)
    if active is not None:
        base_prompt = active.system_prompt
        parent_version = active.version
    else:
        base_prompt = floor(prompt_key)
        parent_version = None

    # Worst-offending critiques first (rows already ordered most-recent-first).
    examples = [_critique(row) for row in rows[:_MAX_EXAMPLES]]
    breakdown_lines = "\n".join(
        f"- {metric}: {breakdown[metric]} failures"
        for metric in sorted(breakdown, key=lambda m: breakdown[m], reverse=True)
    )
    example_lines = "\n".join(f"- {ex}" for ex in examples)
    user_prompt = (
        f"CURRENT SYSTEM PROMPT:\n{base_prompt}\n\n"
        f"FAILURE BREAKDOWN (metric: count):\n{breakdown_lines}\n\n"
        f"FAILURE EXAMPLES (judge critiques):\n{example_lines}"
    )

    # Ask the optimizer; a transport/parse failure ⇒ no draft, not a crash.
    try:
        result = await complete(
            ModelRole.REASONING,
            [
                {"role": "system", "content": _OPTIMIZER_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
        )
        improved = _parse_optimized_prompt(result.content)
    except Exception:  # noqa: BLE001 - a bad optimizer call must not crash diagnose
        logger.warning("diagnose: optimizer call failed for %s; no draft", prompt_key,
                       exc_info=True)
        improved = None

    if improved is None:
        return DiagnoseResult(
            draft_version_id=None,
            failure_summary=summary,
            failures_considered=len(rows),
            metric_breakdown=breakdown,
        )

    draft = await registry.create_draft(
        session,
        prompt_key=prompt_key,
        system_prompt=improved,
        config=dict(active.config) if active is not None else None,
        parent_version=parent_version,
        created_by="diagnose",
        notes=summary,
    )

    return DiagnoseResult(
        draft_version_id=draft.id,
        failure_summary=summary,
        failures_considered=len(rows),
        metric_breakdown=breakdown,
    )
