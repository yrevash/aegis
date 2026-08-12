"""Live trace-eval — grade a completed agent run and persist the scores.

This is the **Eval + Observe** stage of the LLM-Ops closed loop (see
``docs/learn/40-pipelines.md`` Gap 2 + Gap 4): after a run finishes, an
async judge grades both the **final answer** and the individual **trajectory
steps** (the OTel span kinds the graph already stamps — RETRIEVER / TOOL /
GUARDRAIL / CHAIN), writing one :class:`~aegis.ops.models.EvalResult` row per
graded facet, keyed by ``run_id`` and namespaced ``metric="step:<kind>"``. Those
rows are the substrate the **Diagnose** stage clusters over.

Design constraints (all deliberate):

* **Off the hot path.** This runs *post-run*, best-effort. It must never block or
  raise into the caller — :func:`evaluate_run` always returns a :class:`RunEval`,
  even on total failure (empty metrics → ``overall=0.0``, ``passed=False``).
* **Offline-testable via dependency injection.** The chat-completion callable
  ``complete`` is injected. When ``complete is None`` the grader degrades to
  **deterministic-only** lexical proxies and never touches a model.
* **Robust per-metric.** A judge call that fails for one metric is caught,
  logged and skipped — it never sinks the other metrics.
* **Commit is the caller's.** Rows are ``flush``-ed (assigned ids, visible within
  the session) but **not committed** here; the caller owns the transaction
  boundary so trace-eval composes into a larger post-run unit of work.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from aegis.core.models import ModelRole
from aegis.evals.judge import judge_answer
from aegis.ops.models import EvalResult

logger = logging.getLogger(__name__)

#: Default pass threshold for both per-metric ``passed`` and the run ``overall``.
DEFAULT_THRESHOLD = 0.6

#: Map an OTel/OpenInference span *kind* (as stamped on each step by the graph)
#: to the ``EvalResult.metric`` namespace this grader writes for it. Only the
#: kinds that "matter" for step-level diagnosis are graded; CHAIN/LLM/etc. steps
#: carry no dedicated step metric.
_KIND_METRIC: dict[str, str] = {
    "RETRIEVER": "step:retrieval",
    "TOOL": "step:tool",
    "GUARDRAIL": "step:guardrail",
}

#: Any run of non-alphanumeric characters — the tokeniser boundary for the
#: deterministic lexical proxies (mirrors ``aegis.evals.metrics`` normalisation so a
#: spotlight-datamarked context still matches).
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

#: Minimum token length kept by :func:`_tokens` — drops most stop-words/noise so
#: the overlap proxy tracks content words.
_MIN_TOKEN_LEN = 3


@dataclass
class RunEval:
    """The outcome of grading one run.

    Attributes:
        overall: Mean of every persisted metric score in ``[0, 1]`` (``0.0`` when
            nothing could be graded).
        passed: Whether ``overall`` cleared the configured threshold.
        metrics: ``metric name → score`` for every row written (last write wins
            when a step kind repeats; all rows are still persisted and counted
            toward ``overall``).
    """

    overall: float
    passed: bool
    metrics: dict[str, float] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic lexical proxies (the offline floor — no model, never crash)
# ─────────────────────────────────────────────────────────────────────────────


def _tokens(text: str) -> set[str]:
    """Return the set of content tokens in ``text`` (lowercased, length-filtered)."""
    return {
        tok
        for tok in _NON_ALNUM.split((text or "").lower())
        if len(tok) >= _MIN_TOKEN_LEN
    }


def _overlap(needle: str, haystack: str) -> float:
    """Fraction of ``needle``'s content tokens that appear in ``haystack`` ([0, 1]).

    A transparent, offline faithfulness/relevance proxy: how much of one text is
    lexically covered by another. Returns ``0.0`` when ``needle`` has no content
    tokens (nothing to support).
    """
    needle_tokens = _tokens(needle)
    if not needle_tokens:
        return 0.0
    hay_tokens = _tokens(haystack)
    return sum(tok in hay_tokens for tok in needle_tokens) / len(needle_tokens)


def _clamp(value: float) -> float:
    """Clamp ``value`` into ``[0.0, 1.0]``."""
    return max(0.0, min(1.0, float(value)))


def _step_contexts(step: dict[str, Any], run_contexts: list[str]) -> list[str]:
    """Return the contexts a retrieval step surfaced, falling back to run contexts."""
    detail = step.get("detail") or {}
    for key in ("contexts", "sources", "passages"):
        value = detail.get(key)
        if isinstance(value, list) and value:
            return [str(v) for v in value]
    return run_contexts


def _tool_name(step: dict[str, Any]) -> str:
    """Best-effort extract of the chosen tool's name from a TOOL step."""
    detail = step.get("detail") or {}
    for key in ("tool", "name", "action"):
        value = detail.get(key)
        if value:
            return str(value)
    return str(step.get("node") or "unknown")


# ─────────────────────────────────────────────────────────────────────────────
# Cheap LLM judge helper (used only when ``complete`` is injected)
# ─────────────────────────────────────────────────────────────────────────────


async def _cheap_score(complete: Any, system: str, user: str) -> float:  # noqa: ANN401
    """Ask the CHEAP role for a single ``{"score": <float>}`` and parse it ([0, 1]).

    A lightweight per-step judge (much cheaper than the reasoning-model answer
    judge). Any transport/parse failure raises to the caller, which catches it so
    one bad metric never sinks the rest.
    """
    result = await complete(
        ModelRole.CHEAP,
        [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    data = json.loads(result.content)
    return _clamp(data["score"])


_RETRIEVAL_SYSTEM = (
    "You grade a retrieval step. Given a QUERY and the retrieved CONTEXTS, rate how "
    "relevant the contexts are to answering the query on a 0.0-1.0 scale. Respond with "
    'ONLY a JSON object {"score": <float>} and nothing else.'
)
_TOOL_SYSTEM = (
    "You grade a tool-selection step in an agent trajectory. Given the QUERY/PLAN and "
    "the CHOSEN TOOL, rate how appropriate that tool is for the task on a 0.0-1.0 "
    'scale. Respond with ONLY a JSON object {"score": <float>} and nothing else.'
)
_GUARDRAIL_SYSTEM = (
    "You grade a guardrail step. Given the INPUT and the guardrail VERDICT, rate "
    "whether the verdict was appropriate for that input on a 0.0-1.0 scale. Respond "
    'with ONLY a JSON object {"score": <float>} and nothing else.'
)


# ─────────────────────────────────────────────────────────────────────────────
# Per-facet graders — each returns (score, detail) or None to skip; each is run
# under a catch so a single failure is logged and skipped, never fatal.
# ─────────────────────────────────────────────────────────────────────────────


async def _grade_answer(
    *, query: str, answer: str, contexts: list[str], complete: Any  # noqa: ANN401
) -> tuple[float, dict[str, Any]]:
    """Grade the final answer: reasoning-model judge when online, else lexical proxy."""
    context_blob = "\n\n".join(contexts)
    if complete is not None:
        verdict = await judge_answer(query, context_blob, answer, complete=complete)
        blend = (verdict.groundedness + verdict.relevance) / 2.0
        return _clamp(blend), {
            "method": "judge",
            "groundedness": verdict.groundedness,
            "relevance": verdict.relevance,
        }
    grounded = _overlap(answer, context_blob)
    return grounded, {"method": "deterministic", "groundedness": grounded}


async def _grade_retrieval(
    step: dict[str, Any], *, query: str, run_contexts: list[str], complete: Any  # noqa: ANN401
) -> tuple[float, dict[str, Any]]:
    """Grade a retrieval step: cheap relevance judge online, else lexical overlap."""
    contexts = _step_contexts(step, run_contexts)
    context_blob = "\n\n".join(contexts)
    if complete is not None:
        score = await _cheap_score(
            complete,
            _RETRIEVAL_SYSTEM,
            f"QUERY:\n{query}\n\nCONTEXTS:\n{context_blob}",
        )
        return score, {"method": "judge", "node": step.get("node")}
    relevance = _overlap(query, context_blob)
    return relevance, {"method": "deterministic", "node": step.get("node")}


async def _grade_tool(
    step: dict[str, Any], *, query: str, complete: Any  # noqa: ANN401
) -> tuple[float, dict[str, Any]]:
    """Grade a tool-selection step: cheap appropriateness judge, else a success rule."""
    tool = _tool_name(step)
    detail = step.get("detail") or {}
    plan = str(detail.get("plan") or query)
    if complete is not None:
        score = await _cheap_score(
            complete,
            _TOOL_SYSTEM,
            f"QUERY/PLAN:\n{plan}\n\nCHOSEN TOOL:\n{tool}",
        )
        return score, {"method": "judge", "tool": tool, "node": step.get("node")}
    # Offline rule: a tool that executed without error is treated as appropriate.
    ok = bool(detail.get("ok", True)) and not detail.get("error")
    return (1.0 if ok else 0.0), {
        "method": "deterministic",
        "tool": tool,
        "node": step.get("node"),
    }


async def _grade_guardrail(
    step: dict[str, Any], *, complete: Any  # noqa: ANN401
) -> tuple[float, dict[str, Any]]:
    """Grade a guardrail step: cheap appropriateness judge, else a verdict-present rule."""
    detail = step.get("detail") or {}
    verdict = detail.get("verdict")
    input_text = str(detail.get("input") or detail.get("text") or "")
    if complete is not None:
        score = await _cheap_score(
            complete,
            _GUARDRAIL_SYSTEM,
            f"INPUT:\n{input_text}\n\nVERDICT:\n{verdict}",
        )
        return score, {"method": "judge", "verdict": verdict, "node": step.get("node")}
    # Offline rule: a guardrail that produced a definite verdict is treated as
    # having acted appropriately; an absent verdict is neutral (0.5).
    score = 1.0 if verdict is not None else 0.5
    return score, {
        "method": "deterministic",
        "verdict": verdict,
        "node": step.get("node"),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────


async def evaluate_run(
    session: Any,  # noqa: ANN401 - AsyncSession, kept loose for offline testability
    *,
    run_id: str,
    query: str,
    answer: str,
    contexts: list[str],
    steps: list[dict[str, Any]],
    complete: Any = None,  # noqa: ANN401 - async complete(role, messages, ...) -> .content
    prompt_key: str | None = None,
    tenant_id: int | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> RunEval:
    """Grade a completed run and persist one ``EvalResult`` per graded facet.

    Grades the final ``answer`` and each trajectory step that matters
    (RETRIEVER → ``step:retrieval``, TOOL → ``step:tool``, GUARDRAIL →
    ``step:guardrail``), writing a row keyed by ``run_id`` for each. When
    ``complete`` is injected the graders use the (reasoning-model) answer judge
    and cheap per-step judges; when ``complete is None`` they degrade to
    deterministic lexical proxies and never call a model.

    The function is **best-effort and total**: a failure grading one facet is
    caught, logged and skipped, and any unexpected error is swallowed so the
    caller (which runs this off the hot path) always gets a :class:`RunEval`.
    Rows are flushed but **not committed** — the caller owns the transaction.

    Args:
        session: An async SQLAlchemy session to write ``EvalResult`` rows into.
        run_id: The run these scores belong to (the ``EvalResult.run_id`` key).
        query: The user query the run answered.
        answer: The run's final answer text.
        contexts: The retrieved contexts backing the answer (run-level fallback
            for step-level retrieval grading).
        steps: The trajectory steps, each ``{"node": str, "kind": str,
            "detail": dict}`` mirroring the graph's OTel span kinds.
        complete: Async ``complete(role, messages, *, response_format=None,
            temperature=...) -> result`` with ``.content: str``; ``None`` selects
            deterministic-only grading.
        prompt_key: The prompt/persona the graded run used — written to the
            ``EvalResult.prompt_key`` column so Diagnose clusters a prompt's own
            failures and ``/ops/evals?prompt_key=`` filters for real.
        tenant_id: Optional tenant id, written to the ``EvalResult.tenant_id`` column.
        threshold: Pass bar for both per-metric ``passed`` and the run ``overall``
            (default :data:`DEFAULT_THRESHOLD`).

    Returns:
        A :class:`RunEval` (``overall`` = mean of written scores; ``passed`` =
        ``overall >= threshold``; ``metrics`` = the written ``metric → score`` map).
    """
    written: list[tuple[str, float]] = []

    async def _persist(metric: str, score: float, detail: dict[str, Any]) -> None:
        session.add(
            EvalResult(
                run_id=run_id,
                prompt_key=prompt_key,
                tenant_id=tenant_id,
                metric=metric,
                score=score,
                passed=score >= threshold,
                detail=detail,
            )
        )
        written.append((metric, score))

    async def _grade(metric: str, coro: Any) -> None:  # noqa: ANN401
        """Run one facet grader, persist its row, and never let it sink the rest."""
        try:
            score, detail = await coro
        except Exception:  # noqa: BLE001 - one bad metric must not sink the eval
            logger.warning(
                "trace_eval: metric %s failed for run %s; skipping",
                metric,
                run_id,
                exc_info=True,
            )
            return
        await _persist(metric, _clamp(score), detail)

    try:
        # ANSWER — always graded.
        await _grade(
            "answer",
            _grade_answer(
                query=query, answer=answer, contexts=contexts, complete=complete
            ),
        )

        # PER-STEP — grade the kinds that matter; skip unknown/CHAIN kinds.
        for step in steps or []:
            kind = str(step.get("kind") or "").upper()
            metric = _KIND_METRIC.get(kind)
            if metric is None:
                continue
            if kind == "RETRIEVER":
                await _grade(
                    metric,
                    _grade_retrieval(
                        step, query=query, run_contexts=contexts, complete=complete
                    ),
                )
            elif kind == "TOOL":
                await _grade(metric, _grade_tool(step, query=query, complete=complete))
            elif kind == "GUARDRAIL":
                await _grade(metric, _grade_guardrail(step, complete=complete))

        # Persist assigned ids / make rows visible in-session; commit is the caller's.
        with contextlib.suppress(Exception):
            await session.flush()
    except Exception:  # noqa: BLE001 - trace_eval is best-effort; never raise into caller
        logger.warning("trace_eval: run %s eval aborted", run_id, exc_info=True)

    overall = sum(s for _, s in written) / len(written) if written else 0.0
    return RunEval(
        overall=overall,
        passed=overall >= threshold,
        metrics=dict(written),
    )
