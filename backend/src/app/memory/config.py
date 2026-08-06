"""Tunable knobs for the memory subsystem (mirrors :class:`app.agent.AgentConfig`).

Every value here is a *mechanism* parameter — domain-neutral. What a "fact" means is
the adapter's job (:mod:`app.adapter.memory_spec`); how much of one we keep, how we
score it, and how we budget the context window is the core's job and lives here.

See ``docs/MEMORY_SPEC.md`` for the derivation of the defaults (Generative-Agents
recall blend, mem0 consolidation cadence, lost-in-the-middle assembly).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

MemoryBackend = Literal["postgres", "redis", "off"]
"""Intended degradation ladder: full Postgres stores → Redis rolling window → off.

**Wired today:** ``postgres`` (the full three-tier subsystem) is the live path, and
memory is effectively **off** whenever the agent has no ``MemoryDeps``/``session_id``
(the graph nodes no-op) — so the two ends of the ladder are real. The intermediate
``redis`` rolling-window tier is a documented target, **not yet wired**; graph gating is
on ``deps.memory``/``session_id`` today, not on this enum. Read the honest status here
rather than assuming all three tiers are active.
"""


def _default_tier_caps() -> dict[str, float]:
    """Per-tier fractions of the available context budget (must be sane, not sum to 1).

    They are independent ceilings, not a partition: the greedy assembler fills each
    tier up to its cap in priority order and stops at the global budget, so caps may
    overlap/overcommit — whichever tiers come first win the shared budget.
    """
    return {
        "profile": 0.10,
        "facts": 0.20,
        "skills": 0.10,
        "summary": 0.15,
        "rag": 0.30,
        "episodic": 0.15,
        "raw": 0.25,
    }


@dataclass
class MemoryConfig:
    """Recall, consolidation, and context-budget parameters.

    Attributes:
        raw_window_turns: Verbatim recent turns kept in hot recall for a session.
        k_fact / n_fact: Semantic-fact recall fan-out (retrieved) / kept.
        k_epi / n_epi: Episodic recall fan-out (retrieved) / kept.
        n_skill: Procedural skills selected per turn.
        consolidation_every_n: Consolidate episodic→semantic every N turns (background).
        tau_extract: Minimum extractor confidence to admit a candidate fact.
        dedup_cos: Cosine at/above which a same-predicate candidate is a NOOP (no LLM).
        w_rel / w_rec / w_imp / w_freq: Recall composite weights (relevance/recency/
            importance/frequency).
        half_life_days_fact / half_life_days_epi: Exponential recency half-lives.
        ctx_token_cap: Hard ceiling on the assembled working-memory window (tokens).
        answer_reserve: Tokens reserved for the model's answer (never used by context).
        summary_max_tokens: Cap on the running-summary block.
        forget_floor / forget_min_age_days: Prune-sweep archival thresholds.
        per_tier_caps: Per-tier fraction ceilings for the assembler.
        memory_backend: Active store tier (see :data:`MemoryBackend`).
    """

    raw_window_turns: int = 40
    k_fact: int = 20
    n_fact: int = 6
    k_epi: int = 20
    n_epi: int = 4
    n_skill: int = 2
    consolidation_every_n: int = 4
    tau_extract: float = 0.55
    dedup_cos: float = 0.97
    w_rel: float = 1.0
    w_rec: float = 0.5
    w_imp: float = 0.5
    w_freq: float = 0.1
    half_life_days_fact: float = 30.0
    half_life_days_epi: float = 3.0
    ctx_token_cap: int = 8000
    answer_reserve: int = 1200
    summary_max_tokens: int = 400
    forget_floor: float = 0.05
    forget_min_age_days: float = 90.0
    per_tier_caps: dict[str, float] = field(default_factory=_default_tier_caps)
    memory_backend: MemoryBackend = "postgres"

    @property
    def enabled(self) -> bool:
        """Whether long-term memory writes/recalls are active (not the ``off`` tier)."""
        return self.memory_backend != "off"
