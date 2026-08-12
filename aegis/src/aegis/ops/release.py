"""Release — the smart, tiered gate that decides if a DRAFT prompt goes live.

The **Gate + Release** stage of the LLM-Ops closed loop (``docs/learn/40-pipelines.md``
Gap 2, "smart tiered"). :mod:`aegis.ops.diagnose` only ever proposes a *draft*; this module is
the single place a draft can go live, and it never does so blindly:

1. **Eval gate (always on).** The draft's system prompt must beat the current active
   version's (or the adapter floor's) score on the injected regression suite ``eval_fn`` by
   ``margin``. A draft that fails is **rejected** and archived — it is never promoted.
2. **Change-risk classifier (deterministic).** :func:`classify_change` sorts the diff into
   ``low`` / ``medium`` / ``high`` from transparent heuristics (diff size, safety/guardrail/
   tool/model-critical term changes, config field changes) — the same "class the change,
   then gate by class" pattern as the tool-risk tiers.
3. **Tiered decision.** In the enterprise-default ``tiered`` mode, a *low*-risk draft that
   beat the eval is promoted autonomously; *medium*/*high* risk is escalated to the durable
   approval inbox (``approval_enqueue``) and the draft is *staged* — a human promotes it via
   :func:`apply_release_decision`. ``auto`` promotes any eval-passing draft; ``manual`` always
   escalates.

Promotion is the only thing that goes live, and it is one-call reversible via
``registry.rollback``. All model/eval/approval calls are injected, so tests run offline.
"""

from __future__ import annotations

import difflib
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from aegis.ops import config, registry
from aegis.ops.config import RISK_ORDER, LoopParams
from aegis.ops.models import PromptStatus, PromptVersion

logger = logging.getLogger(__name__)


@dataclass
class ChangeRisk:
    """The risk classification of a proposed prompt/config change.

    Attributes:
        level: ``"low"``, ``"medium"`` or ``"high"``.
        reasons: Human-readable reasons backing the level (shown in the approval inbox).
    """

    level: str
    reasons: list[str] = field(default_factory=list)


@dataclass
class ReleaseResult:
    """The outcome of a release decision.

    Attributes:
        outcome: ``"promoted"``, ``"staged_for_approval"`` or ``"rejected"``.
        risk: The :class:`ChangeRisk` computed for the change.
        eval_score: The draft's regression-suite score.
        baseline_score: The active/floor baseline's regression-suite score.
        reason: A one-line explanation of the decision.
        approval_id: The id returned by ``approval_enqueue`` when staged, else ``None``.
    """

    outcome: str
    risk: ChangeRisk
    eval_score: float
    baseline_score: float
    reason: str
    approval_id: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Change-risk classifier (deterministic — no model calls)
# ─────────────────────────────────────────────────────────────────────────────


def _changed_line_fraction(old: str, new: str) -> float:
    """Return the fraction of lines that changed between ``old`` and ``new`` ([0, 1])."""
    old_lines = (old or "").splitlines()
    new_lines = (new or "").splitlines()
    denom = max(len(old_lines), len(new_lines), 1)
    sm = difflib.SequenceMatcher(a=old_lines, b=new_lines, autojunk=False)
    changed = denom - sum(
        block.size for block in sm.get_matching_blocks()
    )
    return changed / denom


def _term_count(text: str, term: str) -> int:
    """Count whole-word (or phrase) occurrences of ``term`` in ``text`` (case-insensitive)."""
    pattern = r"\b" + re.escape(term) + r"\b"
    return len(re.findall(pattern, (text or "").lower()))


def _changed_safety_terms(old: str, new: str, safety_terms: tuple[str, ...]) -> list[str]:
    """Return safety terms whose occurrence count changed between the two prompts."""
    changed = []
    for term in safety_terms:
        if _term_count(old, term) != _term_count(new, term):
            changed.append(term)
    return changed


def _critical_config_changes(
    old_config: dict[str, Any] | None,
    new_config: dict[str, Any] | None,
    markers: tuple[str, ...],
) -> list[str]:
    """Return critical (model/tool/permission/...) config keys that changed."""
    old_config = old_config or {}
    new_config = new_config or {}
    changed = []
    for key in set(old_config) | set(new_config):
        lowered = key.lower()
        if any(marker in lowered for marker in markers) and (
            old_config.get(key) != new_config.get(key)
        ):
            changed.append(key)
    return changed


def _config_changes_within_bounds(
    old_config: dict[str, Any] | None,
    new_config: dict[str, Any] | None,
    tunable_keys: tuple[str, ...],
    tunable_max_delta: dict[str, float],
) -> bool:
    """Whether every config change is a bounded tweak of a tunable key (else ``False``).

    Returns ``True`` when the configs are identical or differ *only* on the tunable keys
    (``temperature``/``top_k``/``top_p`` by default) by at most the per-key bound. Any other
    changed key, or an out-of-bounds delta, returns ``False``.
    """
    old_config = old_config or {}
    new_config = new_config or {}
    for key in set(old_config) | set(new_config):
        if old_config.get(key) == new_config.get(key):
            continue
        if key not in tunable_keys:
            return False
        try:
            delta = abs(float(new_config.get(key, 0)) - float(old_config.get(key, 0)))
        except (TypeError, ValueError):
            return False
        if delta > tunable_max_delta.get(key, 0.0):
            return False
    return True


def classify_change(
    old_prompt: str,
    new_prompt: str,
    old_config: dict[str, Any] | None = None,
    new_config: dict[str, Any] | None = None,
    *,
    params: LoopParams | None = None,
) -> ChangeRisk:
    """Classify the risk of moving from ``old_prompt``/``old_config`` to the new pair.

    Deterministic heuristics (no model call), all tunable via :class:`LoopParams`:

    * **HIGH** — a large diff (``> high_diff_fraction`` of lines changed), a change to any
      safety/guardrail/tool/approval/policy term (``safety_terms``), or a change to a
      model/tool/permission config field (``critical_config_markers``).
    * **LOW** — a small wording/format-only nudge (``<= low_diff_fraction`` of lines
      changed) with either no config change or only a bounded tweak of a tunable key.
    * **MEDIUM** — anything in between.

    Args:
        old_prompt: The baseline system prompt.
        new_prompt: The candidate system prompt.
        old_config: The baseline config (optional).
        new_config: The candidate config (optional).
        params: The loop knobs to classify against; defaults to the configured effective
            params (:func:`aegis.ops.config.get_loop_params`), whose defaults reproduce the
            historical thresholds/term-lists exactly.

    Returns:
        A :class:`ChangeRisk` with the level and the human-readable reasons behind it.
    """
    params = params or config.get_loop_params()
    reasons: list[str] = []
    high = False

    fraction = _changed_line_fraction(old_prompt, new_prompt)
    if fraction > params.high_diff_fraction:
        high = True
        reasons.append(f"large diff: {fraction:.0%} of lines changed")

    safety = _changed_safety_terms(old_prompt, new_prompt, params.safety_terms)
    if safety:
        high = True
        reasons.append("safety/guardrail terms changed: " + ", ".join(safety))

    critical = _critical_config_changes(
        old_config, new_config, params.critical_config_markers
    )
    if critical:
        high = True
        reasons.append("model/tool/permission config changed: " + ", ".join(critical))

    if high:
        return ChangeRisk(level="high", reasons=reasons)

    small_text = fraction <= params.low_diff_fraction
    config_low_ok = _config_changes_within_bounds(
        old_config, new_config, params.tunable_config_keys, params.tunable_max_delta
    )
    if small_text and config_low_ok:
        detail = f"small wording change ({fraction:.0%} of lines)"
        if (old_config or {}) != (new_config or {}):
            detail += " + bounded config tweak"
        reasons.append(detail)
        return ChangeRisk(level="low", reasons=reasons)

    if not config_low_ok:
        reasons.append("config change beyond a bounded tunable tweak")
    if not small_text:
        reasons.append(f"moderate diff: {fraction:.0%} of lines changed")
    return ChangeRisk(level="medium", reasons=reasons)


# ─────────────────────────────────────────────────────────────────────────────
# Release decision
# ─────────────────────────────────────────────────────────────────────────────


def _baseline(
    active: PromptVersion | None,
    prompt_key: str,
    render_floor_prompt: Callable[[str], str],
) -> tuple[str, dict[str, Any] | None]:
    """Return the baseline ``(system_prompt, config)`` — the active version or the floor."""
    if active is not None:
        return active.system_prompt, dict(active.config or {})
    return render_floor_prompt(prompt_key), None


async def release(
    session: Any,  # noqa: ANN401 - AsyncSession, kept loose for offline testability
    *,
    draft_version_id: int,
    eval_fn: Any,  # noqa: ANN401 - async eval_fn(system_prompt: str) -> float
    approval_enqueue: Any,  # noqa: ANN401 - async approval_enqueue(...) -> str
    autonomy: str = "tiered",
    margin: float | None = None,
    render_floor_prompt: Callable[[str], str] | None = None,
    params: LoopParams | None = None,
) -> ReleaseResult:
    """Run the eval gate + tiered decision on a draft and act on it.

    Scores the draft and the current baseline (active version, else the adapter floor) via
    ``eval_fn``. If the draft does not beat ``baseline + margin`` it is **rejected** and
    archived. Otherwise the change is classified and the outcome depends on ``autonomy``:

    * ``"tiered"`` (default) — risk at/below ``params.auto_promote_ceiling`` ⇒ promote
      autonomously; a riskier change ⇒ enqueue for approval and stage the draft.
    * ``"auto"`` — promote any eval-passing draft regardless of risk.
    * ``"manual"`` — always enqueue for approval, whatever the risk.

    Args:
        session: An async SQLAlchemy session.
        draft_version_id: The DRAFT ``PromptVersion`` to evaluate.
        eval_fn: Async ``eval_fn(system_prompt: str) -> float`` — the regression-suite score.
        approval_enqueue: Async ``approval_enqueue(*, prompt_key, draft_version_id, risk,
            reason) -> str`` — enqueues a durable approval and returns its id.
        autonomy: ``"tiered"`` | ``"auto"`` | ``"manual"`` (default ``"tiered"``).
        margin: How much the draft must beat the baseline by. ``None`` (default) uses
            ``params.eval_margin`` so an operator can tune the pass bar centrally.
        render_floor_prompt: Optional ``render_floor_prompt(prompt_key) -> str`` override
            for the no-active-version baseline floor; defaults to the value configured via
            :func:`aegis.ops.config.configure_ops`.
        params: The loop knobs for the eval margin, the change-risk classifier, and the
            auto-promote ceiling; defaults to the configured effective params
            (:func:`aegis.ops.config.get_loop_params`).

    Returns:
        A :class:`ReleaseResult` describing the outcome, scores, risk and any approval id.

    Raises:
        ValueError: If ``draft_version_id`` does not exist.
    """
    params = params or config.get_loop_params()
    effective_margin = params.eval_margin if margin is None else margin
    draft = await session.get(PromptVersion, draft_version_id)
    if draft is None:
        raise ValueError(f"No PromptVersion with id={draft_version_id}")
    # Only a genuine DRAFT may be released — never re-release an already-active,
    # staged, or archived version (which would re-promote/re-archive it).
    if draft.status is not PromptStatus.DRAFT:
        raise ValueError(
            f"PromptVersion {draft_version_id} is {draft.status.value}, not a draft; "
            "only drafts can be released."
        )

    floor = render_floor_prompt or config.render_floor_prompt
    active = await registry.get_active(session, draft.prompt_key)
    baseline_prompt, baseline_config = _baseline(active, draft.prompt_key, floor)

    draft_score = float(await eval_fn(draft.system_prompt))
    baseline_score = float(await eval_fn(baseline_prompt))

    risk = classify_change(
        baseline_prompt,
        draft.system_prompt,
        baseline_config,
        dict(draft.config or {}),
        params=params,
    )

    # (a) EVAL GATE — a draft that does not beat the baseline by the margin is rejected.
    if draft_score < baseline_score + effective_margin:
        draft.status = PromptStatus.ARCHIVED
        await session.flush()
        return ReleaseResult(
            outcome="rejected",
            risk=risk,
            eval_score=draft_score,
            baseline_score=baseline_score,
            reason=(
                f"eval gate failed: draft {draft_score:.3f} < "
                f"baseline {baseline_score:.3f} + margin {effective_margin:.3f}"
            ),
            approval_id=None,
        )

    beat = f"draft {draft_score:.3f} beat baseline {baseline_score:.3f}"

    async def _stage(reason: str) -> ReleaseResult:
        approval_id = await approval_enqueue(
            prompt_key=draft.prompt_key,
            draft_version_id=draft.id,
            risk=risk.level,
            reason=reason,
        )
        draft.status = PromptStatus.STAGED
        await session.flush()
        return ReleaseResult(
            outcome="staged_for_approval",
            risk=risk,
            eval_score=draft_score,
            baseline_score=baseline_score,
            reason=reason,
            approval_id=approval_id,
        )

    async def _promote(reason: str) -> ReleaseResult:
        await registry.promote(session, draft.id)
        return ReleaseResult(
            outcome="promoted",
            risk=risk,
            eval_score=draft_score,
            baseline_score=baseline_score,
            reason=reason,
            approval_id=None,
        )

    # (b/c) TIERED decision.
    if autonomy == "auto":
        return await _promote(f"autonomy=auto: {beat}")
    if autonomy == "manual":
        return await _stage(f"autonomy=manual: {beat}; awaiting human approval")
    # "tiered" (default, enterprise-safe): auto-promote iff risk <= the configured ceiling.
    ceiling = RISK_ORDER.get(params.auto_promote_ceiling, RISK_ORDER["low"])
    if RISK_ORDER.get(risk.level, RISK_ORDER["high"]) <= ceiling:
        return await _promote(f"{risk.level}-risk change auto-promoted: {beat}")
    return await _stage(
        f"{risk.level}-risk change staged for approval: {beat}"
    )


async def apply_release_decision(
    session: Any,  # noqa: ANN401 - AsyncSession, kept loose for offline testability
    *,
    draft_version_id: int,
    approved: bool,
) -> PromptVersion | None:
    """Resolve a staged release once a human decides: promote on approve, archive on reject.

    Args:
        session: An async SQLAlchemy session.
        draft_version_id: The staged draft the human acted on.
        approved: ``True`` to promote it live, ``False`` to archive (reject) it.

    Returns:
        The affected :class:`PromptVersion` (now active or archived), or ``None`` if the
        draft id does not exist.
    """
    draft = await session.get(PromptVersion, draft_version_id)
    if draft is None:
        return None
    if approved:
        return await registry.promote(session, draft_version_id)
    draft.status = PromptStatus.ARCHIVED
    await session.flush()
    return draft
