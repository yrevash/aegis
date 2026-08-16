"""Agent-risk map data for ``GET /risk-map`` — where each risk sits, and where we moved it.

A typed data module grounded verbatim in ``docs/security/owasp-agentic.md``. Each
entry is one **OWASP Top 10 for Agentic Applications (2026)** theme carrying **two**
points on the same 1..5 likelihood × impact grid:

* ``likelihood`` × ``impact`` — the **inherent** position, with no Aegis control.
* ``residual_likelihood`` × ``residual_impact`` — where it sits **after** the real
  control, whose file you can open via ``control_ref``.

The distance between those two points is the only claim this module makes on Aegis's
behalf, and it is made honestly. Controls almost always move **likelihood**, not
impact: the human gate does not make a wrongly-issued refund cheaper, it makes it
close to impossible for the agent to issue one on its own. Impact moves only where a
control genuinely shrinks the blast radius (reversible, typed tools).

The band (``low`` / ``medium`` / ``high``) is **derived** from the residual point by
``schemas.risk_band`` — it is not a second hand-authored field that could drift away
from the coordinate it is supposed to summarise.

The honesty posture the doc opens with is preserved here: no one has "solved" prompt
injection (reported attack-success ~50–84% even with best-effort defenses), so the
rails are credited with reducing how *often* an injection lands and **not** with
reducing what a landed injection is worth — AA-03 therefore stays the largest
residual on the map, in the medium band, and never renders as solved. The decisive
control elsewhere is the human gate — a consequential action cannot execute without a
human — which is why excessive-agency / tool-misuse residuals collapse to low
**despite** unchanged high impact.

Every number below is engineering judgement, not measurement. It is stated as such in
``_NOTE`` and rendered as such on the client page.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.api.schemas import RiskEntry, RiskMapResponse, RiskScale

_NOTE = (
    "This map reflects *this deployment's* own security posture, aligned to the OWASP "
    "Top 10 for Agentic Applications (2026). Both positions — inherent and residual — "
    "are honest engineering judgement grounded in docs/security/owasp-agentic.md, not "
    "measured incident rates. Defense-in-depth, not prevention: prompt injection is "
    "never marked resolved. Each control_ref names a real file. The map is repopulated "
    "per problem/deployment as controls change."
)

# Grounded verbatim in the "Risk → Aegis control → real file" table of
# docs/security/owasp-agentic.md. Inherent and residual are honest 1..5 bands; the
# comment above each entry states *why* the residual point sits where it does.
_RISKS: tuple[RiskEntry, ...] = (
    # The human gate is the decisive control: impact is untouched (a wrongly-issued
    # refund costs exactly the same) but the agent cannot reach the action alone.
    RiskEntry(
        id="AA-01",
        title="Excessive agency / autonomy",
        category="Autonomy",
        likelihood=3,
        impact=5,
        residual_likelihood=1,
        residual_impact=5,
        control_name="Human approval gate",
        mitigation=(
            "Any action rated high-risk stops and waits for a named person to approve it. "
            "The agent cannot complete a consequential action on its own."
        ),
        # Mechanics: actions at/above AgentConfig.gate_min_risk (default HIGH) route to the
        # LangGraph approval node, which interrupts the run until a human resumes it.
        control_ref="agent/graph.py (gate/approval nodes), agent/deps.py (gate_min_risk)",
    ),
    # Allowlist is checked before any side effect, so hijacking rarely reaches a
    # handler; impact drops one band because every result carries an InverseAction —
    # the damage is reversible, which is a genuine (not cosmetic) blast-radius cut.
    RiskEntry(
        id="AA-02",
        title="Tool misuse / hijacking",
        category="Tools",
        likelihood=3,
        impact=4,
        residual_likelihood=1,
        residual_impact=3,
        control_name="Tool allowlist + reversible actions",
        mitigation=(
            "Each agent role can only use the tools on its own approved list, checked before "
            "anything happens — and every action it does take can be undone."
        ),
        # Mechanics: run_tool checks ALLOWLIST[persona] and raises ToolNotAllowedError before
        # the handler runs; tools are typed, idempotent and carry an InverseAction. That
        # reversibility is why this is the one risk whose *impact* drops, not just likelihood.
        control_ref="adapter/tools.py (ALLOWLIST, run_tool, is_allowed, InverseAction)",
    ),
    # The honest one. Layered fail-closed screening lowers how often an injection
    # lands (4 → 3) but reported attack success stays ~50–84%, and the rails do
    # nothing to make a *successful* injection cheaper — impact is left at 4. This is
    # deliberately the largest residual on the map and must never read as solved.
    RiskEntry(
        id="AA-03",
        title="Prompt injection / jailbreak",
        category="Input integrity",
        likelihood=4,
        impact=4,
        residual_likelihood=3,
        residual_impact=4,
        control_name="Fail-closed injection screening",
        mitigation=(
            "Untrusted text is screened for hidden instructions before the planner reads it. "
            "If the screening itself fails, the input is treated as an attack rather than "
            "waved through — but no defence available today stops every injection."
        ),
        # Mechanics: deterministic signature backstop → cheap-model classifier; an unavailable
        # or unparseable classifier is treated as injection (fail closed). One entry point
        # shared by the programmatic rail and the NeMo Colang self_check_injection action.
        control_ref=(
            "guardrails/classifier.py (detect_injection), guardrails/rails.py (check_input)"
        ),
    ),
    # Anchored-regex + Luhn detectors are deterministic pure code running on both
    # paths, so a leak becomes rare; a leak that does happen is exactly as damaging
    # as before, so impact is untouched.
    RiskEntry(
        id="AA-04",
        title="Sensitive-information disclosure",
        category="Output integrity",
        likelihood=3,
        impact=5,
        residual_likelihood=1,
        residual_impact=5,
        control_name="PII redaction, both directions",
        mitigation=(
            "Personal data is masked before the model sees a question and again before an "
            "answer goes back out. Only the kind of data found is ever logged, never the value."
        ),
        # Mechanics: anchored-regex detectors (+ Luhn for card numbers) on both paths,
        # annotated LLM06 in the rails. Deterministic pure code — no model in the loop.
        control_ref="guardrails/pii.py, guardrails/rails.py (check_input/check_output)",
    ),
    # The output rail assumes a *complete* answer; the streaming caveat in the doc is
    # a real, stated gap, so this only drops one likelihood band and stays medium.
    RiskEntry(
        id="AA-05",
        title="Insecure output handling / trust-chain abuse",
        category="Output integrity",
        likelihood=3,
        impact=4,
        residual_likelihood=2,
        residual_impact=4,
        control_name="Output rail",
        mitigation=(
            "Every answer is checked for a valid shape and screened for leaked internal "
            "instructions before anything downstream is allowed to trust it."
        ),
        # Mechanics: schema validation → content filter → PII. Structural well-formedness
        # (LLM02) plus a content-filter backstop against system-prompt leakage. The rail
        # assumes a *complete* answer — the streaming caveat is a real, stated gap.
        control_ref="guardrails/rails.py (check_output), guardrails/schema.py",
    ),
    # Postgres RLS engages at the connection, below the application, with app-level
    # tenant filtering on top; a cross-tenant read would take two independent
    # failures. Impact of a breach is unchanged.
    RiskEntry(
        id="AA-06",
        title="Identity / privilege abuse across tenants",
        category="Governance",
        likelihood=2,
        impact=5,
        residual_likelihood=1,
        residual_impact=5,
        control_name="Tenant isolation (roles + database-enforced)",
        mitigation=(
            "Each customer's data is walled off by the database itself, underneath the "
            "application, with a second check in the application on top. Reading across "
            "that wall would take two independent failures."
        ),
        # Mechanics: per-request GovernanceContext threads tenant/user/role via contextvars;
        # set_tenant_scope sets the app.tenant_id GUC so Postgres RLS policies engage per
        # connection, with app-level tenant filtering as belt-and-suspenders.
        control_ref="core/governance.py, data/session.py (set_tenant_scope, RLS policies)",
    ),
    # Every autonomous or approved call writes an audit row on the same path that
    # executes it, so an untraced action is close to unreachable. Nothing about the
    # audit log makes an untraced action less damaging, so impact holds.
    RiskEntry(
        id="AA-07",
        title="Untraceable / unaccountable actions",
        category="Accountability",
        likelihood=2,
        impact=4,
        residual_likelihood=1,
        residual_impact=4,
        control_name="Immutable audit log",
        mitigation=(
            "Every action the agent takes writes a permanent record — who asked, which model "
            "ran, what was done, who approved it — on the same path that performs it."
        ),
        # Mechanics: every autonomous or approved tool call writes an AuditLog row (actor,
        # model, trace_id, payload, approver, tenant); every run is an OpenTelemetry trace
        # of typed spans. Written on the executing path, so it cannot be skipped.
        control_ref="data/audit.py (record_audit), observability/otel.py",
    ),
    # max_plan_iterations is a hard cap, so the loop terminates by construction and
    # the budget chokepoint raises before spend runs away — a structural guarantee,
    # hence likelihood 1. Impact unchanged.
    RiskEntry(
        id="AA-08",
        title="Cascading failures / unbounded consumption",
        category="Reliability",
        likelihood=3,
        impact=3,
        residual_likelihood=1,
        residual_impact=3,
        control_name="Hard loop cap + spend ceiling",
        mitigation=(
            "The agent's self-correction loop has a fixed maximum number of rounds, and "
            "per-customer spend limits stop a run before cost can escalate."
        ),
        # Mechanics: the plan→act→reflect loop is hard-capped by AgentConfig.
        # max_plan_iterations (termination guaranteed by construction, hence likelihood 1);
        # token/USD/RPM/TPM caps raise BudgetExceededError at the single model chokepoint.
        control_ref="agent/graph.py (reflect), data/governance.py (enforce_governance)",
    ),
    # Retrieval grounding + conformal abstention make ungrounded answers less
    # frequent, but no one has eliminated confabulation — one band, and it stays
    # medium rather than pretending to be solved.
    RiskEntry(
        id="AA-09",
        title="Hallucination / ungrounded answer",
        category="Reliability",
        likelihood=4,
        impact=3,
        residual_likelihood=3,
        residual_impact=3,
        control_name="Grounded answers + abstention",
        mitigation=(
            "Answers are built from retrieved source documents and cite them, and the system "
            "declines to answer rather than guessing when its confidence is too low."
        ),
        # Mechanics: retrieval-grounded generation with provenance; the ML spine's conformal
        # abstention defers when the prediction is degenerate rather than answering from
        # nothing. Rarer, not eliminated — confabulation is not a solved problem.
        control_ref="retrieval/pipeline.py, ml/model.py (conformal intervals)",
    ),
)


def risks() -> tuple[RiskEntry, ...]:
    """Return the typed risk entries (the module's real data spine)."""
    return _RISKS


def build_risk_map() -> RiskMapResponse:
    """Build the agent-risk map response for this deployment's posture."""
    return RiskMapResponse(
        generated_at=datetime.now(UTC).isoformat(),
        note=_NOTE,
        scale=RiskScale(likelihood=[1, 2, 3, 4, 5], impact=[1, 2, 3, 4, 5]),
        risks=[r.model_copy() for r in _RISKS],
    )
