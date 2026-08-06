"""Agent-risk heat-map data for ``GET /risk-map``.

A typed data module grounded verbatim in ``docs/SECURITY_OWASP_AGENTIC.md``.
Each entry is one **OWASP Top 10 for Agentic Applications (2026)** theme, placed on a
1..5 likelihood × impact grid with an **honest** residual band after Aegis's real
control. Every ``control_ref`` points at a **real file/module** you can open — the
same files the security doc names — so the map is auditable, not decorative.

The honesty posture the doc opens with is preserved here: no one has "solved" prompt
injection (reported attack-success ~50–84% even with best-effort defenses), so
injection keeps a **medium** residual rather than a green all-clear. The decisive
control is the human gate — a consequential action cannot execute without a human —
which is why excessive-agency / tool-misuse residuals are low despite high impact.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.api.schemas import RiskEntry, RiskMapResponse, RiskScale

_NOTE = (
    "This map reflects *this deployment's* own security posture, aligned to the OWASP "
    "Top 10 for Agentic Applications (2026). Bands are honest engineering judgement "
    "grounded in docs/SECURITY_OWASP_AGENTIC.md — defense-in-depth, not prevention: "
    "prompt injection is never marked fully resolved. Each control_ref names a real "
    "file. The map is repopulated per problem/deployment as controls change."
)

# Grounded verbatim in the "Risk → Aegis control → real file" table of
# docs/SECURITY_OWASP_AGENTIC.md. Likelihood/impact are honest 1..5 bands.
_RISKS: tuple[RiskEntry, ...] = (
    RiskEntry(
        id="AA-01",
        title="Excessive agency / autonomy",
        category="Autonomy",
        likelihood=3,
        impact=5,
        mitigation=(
            "Risk-tiered tools + human gate: any action at/above AgentConfig."
            "gate_min_risk (default HIGH) routes to the LangGraph approval node, which "
            "interrupts and waits for a human. A consequential action cannot execute "
            "on its own."
        ),
        control_ref="agent/graph.py (gate/approval nodes), agent/deps.py (gate_min_risk)",
        residual="low",
    ),
    RiskEntry(
        id="AA-02",
        title="Tool misuse / hijacking",
        category="Tools",
        likelihood=3,
        impact=4,
        mitigation=(
            "Per-persona allowlist enforced before any side effect: run_tool checks "
            "ALLOWLIST[persona] and raises ToolNotAllowedError before the handler runs; "
            "tools are typed, idempotent and reversible (InverseAction)."
        ),
        control_ref="adapter/tools.py (ALLOWLIST, run_tool, is_allowed, InverseAction)",
        residual="low",
    ),
    RiskEntry(
        id="AA-03",
        title="Prompt injection / jailbreak",
        category="Input integrity",
        likelihood=4,
        impact=4,
        mitigation=(
            "Layered, fail-closed injection defense: deterministic signature backstop → "
            "cheap-model classifier; an unavailable/unparseable classifier is treated as "
            "injection (fail closed). One entry point shared by the programmatic rail and "
            "the NeMo Colang self_check_injection action."
        ),
        control_ref=(
            "guardrails/classifier.py (detect_injection), guardrails/rails.py (check_input)"
        ),
        residual="medium",
    ),
    RiskEntry(
        id="AA-04",
        title="Sensitive-information disclosure",
        category="Output integrity",
        likelihood=3,
        impact=5,
        mitigation=(
            "PII redaction on both paths: anchored-regex detectors (+ Luhn) mask PII "
            "inbound before the model or classifier sees it, and outbound before the "
            "answer is returned. Annotated LLM06 in the rails."
        ),
        control_ref="guardrails/pii.py, guardrails/rails.py (check_input/check_output)",
        residual="low",
    ),
    RiskEntry(
        id="AA-05",
        title="Insecure output handling / trust-chain abuse",
        category="Output integrity",
        likelihood=3,
        impact=4,
        mitigation=(
            "Output rail: schema validation → content filter → PII. Structural "
            "well-formedness (LLM02) plus a content-filter backstop against "
            "system-prompt leakage before any answer is trusted downstream."
        ),
        control_ref="guardrails/rails.py (check_output), guardrails/schema.py",
        residual="medium",
    ),
    RiskEntry(
        id="AA-06",
        title="Identity / privilege abuse across tenants",
        category="Governance",
        likelihood=2,
        impact=5,
        mitigation=(
            "Multi-tenant RBAC + Postgres RLS + budgets: per-request GovernanceContext "
            "threads tenant/user/role via contextvars; set_tenant_scope sets the "
            "app.tenant_id GUC so RLS policies engage per connection, with app-level "
            "tenant filtering as belt-and-suspenders."
        ),
        control_ref="core/governance.py, data/session.py (set_tenant_scope, RLS policies)",
        residual="low",
    ),
    RiskEntry(
        id="AA-07",
        title="Untraceable / unaccountable actions",
        category="Accountability",
        likelihood=2,
        impact=4,
        mitigation=(
            "Immutable audit log + end-to-end trace: every autonomous or approved tool "
            "call writes an AuditLog row (actor, model, trace_id, payload, approver, "
            "tenant); every run is an OpenTelemetry trace of typed spans."
        ),
        control_ref="data/audit.py (record_audit), observability/otel.py",
        residual="low",
    ),
    RiskEntry(
        id="AA-08",
        title="Cascading failures / unbounded consumption",
        category="Reliability",
        likelihood=3,
        impact=3,
        mitigation=(
            "Bounded self-repair + budget chokepoint: the plan→act→reflect loop is "
            "hard-capped by AgentConfig.max_plan_iterations (guaranteed termination); "
            "token/USD/RPM/TPM caps raise BudgetExceededError at the single model "
            "chokepoint."
        ),
        control_ref="agent/graph.py (reflect), data/governance.py (enforce_governance)",
        residual="low",
    ),
    RiskEntry(
        id="AA-09",
        title="Hallucination / ungrounded answer",
        category="Reliability",
        likelihood=4,
        impact=3,
        mitigation=(
            "Retrieval-grounded generation with provenance; the ML spine's conformal "
            "abstention defers when confidence is degenerate rather than answering "
            "confidently from nothing."
        ),
        control_ref="retrieval/pipeline.py, ml/model.py (conformal intervals)",
        residual="medium",
    ),
)


def risks() -> tuple[RiskEntry, ...]:
    """Return the typed risk entries (the module's real data spine)."""
    return _RISKS


def build_risk_map() -> RiskMapResponse:
    """Build the agent-risk heat-map response for this deployment's posture."""
    return RiskMapResponse(
        generated_at=datetime.now(UTC).isoformat(),
        note=_NOTE,
        scale=RiskScale(likelihood=[1, 2, 3, 4, 5], impact=[1, 2, 3, 4, 5]),
        risks=[r.model_copy() for r in _RISKS],
    )
