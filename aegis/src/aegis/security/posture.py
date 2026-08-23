"""The security-posture surface — every threat mapped to its *wired* control.

This is the honest, cross-cutting security accessor. Where `aegis.platform`'s
risk-map carries stable engineering *judgement* (likelihood/impact bands), this
module reports the **live control status** derived from the actual wiring at call
time — flipping as the process is (re)configured:

* Is the model-based injection classifier wired, or is only the deterministic
  backstop running? (:func:`aegis.guardrails.nemo.get_completer`)
* Is a real budget-governance hook injected at the gateway chokepoint, or is the
  gateway ungoverned / failing open? (:mod:`aegis.gateway.llm`)
* Is a strong JWT signing secret in force, or the documented dev default?
  (:func:`aegis.governance.effective_config`)
* Is Postgres RLS fail-closed, and which PII engine is active?

Every entry names a **real, importable** symbol (module + mechanism) so a test can
prove the control it claims actually exists — no fabricated ``enforced``. The
status vocabulary is deliberately small and honest:

* ``enforced``   — the control is wired and structurally active right now.
* ``partial``    — a real control runs, but a layer is off/advisory/host-side, so
  the posture is weaker than its fully-wired form (never a silent green).
* ``not_covered``— no control at this layer (stated plainly, never hidden).

The accessor is dependency-light: it imports only the modules it introspects
(pydantic + stdlib-level), never litellm/redis/postgres. Reading it never spends.
"""

from __future__ import annotations

import importlib
from enum import StrEnum

from pydantic import BaseModel, Field

from aegis.security.extraction import DEFAULT_THRESHOLDS

__all__ = [
    "PostureEntry",
    "PostureSignals",
    "PostureStatus",
    "read_signals",
    "resolve_symbol",
    "security_posture",
]


class PostureStatus(StrEnum):
    """Live status of a threat's control, derived from actual wiring.

    Deliberately three-valued so the surface can never fudge a green: a real but
    weakened control is ``partial`` (not ``enforced``), and an absent control is
    ``not_covered`` (never omitted or silently upgraded).
    """

    ENFORCED = "enforced"
    PARTIAL = "partial"
    NOT_COVERED = "not_covered"


class PostureEntry(BaseModel):
    """One threat → control mapping with a status derived from live wiring."""

    threat_id: str = Field(description="Taxonomy id, e.g. 'LLM01' or 'AGENTIC-IDENTITY'.")
    name: str = Field(description="Human-readable threat name.")
    control: str = Field(description="The Aegis control that answers this threat.")
    module: str = Field(description="The primary module the control lives in.")
    mechanism: str = Field(description="The concrete function/rail that runs.")
    status: PostureStatus = Field(description="Live control status at call time.")
    detail: str = Field(description="Honest note on why the status is what it is.")
    refs: list[str] = Field(
        default_factory=list,
        description=(
            "Importable 'module:attr' symbols backing this control, so a test can "
            "prove every claimed mechanism exists (no fabricated 'enforced')."
        ),
    )


class PostureSignals(BaseModel):
    """The live, introspected wiring signals the statuses are derived from.

    Kept as an explicit typed value (rather than free reads scattered through the
    builder) so a test can assert *which* config knob a status flip tracks.
    """

    model_layer_wired: bool = Field(
        description="A process-wide ChatCompleter is set for the model-based rails."
    )
    nemo_available: bool = Field(description="The optional nemoguardrails package imports.")
    mode: str = Field(description="AEGIS_MODE — full/lite/auto.")
    pii_engine: str = Field(description="Active PII engine (presidio/regex).")
    rls_fail_closed: bool = Field(description="RLS admits no rows when the tenant GUC is unset.")
    rls_enforced_on: str = Field(description="The dialect RLS policies engage on.")
    jwt_dev_secret: bool = Field(description="The documented dev JWT secret is still in force.")
    jwt_algorithm: str = Field(description="The JWT signing algorithm.")
    budget_hook_wired: bool = Field(
        description="A real governance hook is injected at the gateway (enforce-before-spend)."
    )
    budget_fail_open: bool = Field(
        description="A governance read failure lets the call through (fail-open)."
    )
    gate_min_risk: str = Field(description="Tool risk at/above which the human gate fires.")
    max_plan_iterations: int = Field(description="Hard cap on the self-repair loop.")
    hazard_categories: int = Field(description="MLCommons hazard categories screened.")
    rls_tables: int = Field(description="Tables carrying a per-tenant RLS policy.")


def resolve_symbol(ref: str) -> object:
    """Import and return the object named by a ``"module:attr"`` reference.

    Args:
        ref: A ``"dotted.module:attr"`` string (as stored in
            :attr:`PostureEntry.refs`).

    Returns:
        The resolved attribute object.

    Raises:
        ValueError: If ``ref`` is not of the form ``module:attr``.
        ModuleNotFoundError / AttributeError: If it does not resolve.
    """
    if ":" not in ref:
        raise ValueError(f"Reference must be 'module:attr', got {ref!r}.")
    module_name, attr = ref.split(":", 1)
    return getattr(importlib.import_module(module_name), attr)


def read_signals() -> PostureSignals:
    """Introspect the live wiring into a :class:`PostureSignals`.

    Every field is read from the *effective* configuration/state of the real
    modules — never a static literal — so the returned signals (and the statuses
    derived from them) change when the process is reconfigured.
    """
    from aegis.agent import AgentConfig
    from aegis.core.config import CoreSettings
    from aegis.gateway import llm as gateway_llm
    from aegis.governance import effective_config
    from aegis.guardrails import nemo, pii
    from aegis.guardrails.content_safety import HAZARD_CATEGORIES

    gov = effective_config()
    cfg = AgentConfig()
    gw_cfg = gateway_llm._get_config()
    # A real host wires a governance hook via gateway.configure(governance=...);
    # the module ships an explicit no-op (ungoverned, fail-open by construction).
    budget_hook_wired = not isinstance(gateway_llm._governance, gateway_llm._NoOpGovernance)

    return PostureSignals(
        model_layer_wired=nemo.get_completer() is not None,
        nemo_available=nemo.nemo_available(),
        mode=str(CoreSettings().mode.value),
        pii_engine=pii.active_engine(),
        rls_fail_closed=gov.rls.fail_closed,
        rls_enforced_on=gov.rls.enforced_on,
        jwt_dev_secret=gov.jwt.secret_is_dev_default,
        jwt_algorithm=gov.jwt.algorithm,
        budget_hook_wired=budget_hook_wired,
        budget_fail_open=bool(gw_cfg.budget_fail_open),
        gate_min_risk=str(cfg.gate_min_risk.value),
        max_plan_iterations=int(cfg.max_plan_iterations),
        hazard_categories=len(HAZARD_CATEGORIES),
        rls_tables=len(gov.rls.tables),
    )


def security_posture(signals: PostureSignals | None = None) -> list[PostureEntry]:
    """Return the honest security-posture table, derived from live wiring.

    Maps the OWASP Top-10 for LLM Applications (2025) — LLM01/02/04/05/06/07/08/09/10
    — plus the key agentic themes (cross-tenant identity abuse, unaccountable
    actions, tool misuse) to the concrete Aegis control that answers each, with a
    ``status`` computed from :func:`read_signals`. A reconfiguration (wiring the
    injection completer, injecting a budget hook, replacing the dev JWT secret,
    flipping ``budget_fail_open``) flips the corresponding status.

    Args:
        signals: Pre-read wiring signals (mainly for tests); read live when ``None``.

    Returns:
        One :class:`PostureEntry` per major threat. Deterministic, side-effect free.
    """
    s = signals if signals is not None else read_signals()

    # LLM01 — Prompt injection. The deterministic signature backstop is pure code
    # and always runs (fail-closed classifier on top); the model classifier only
    # deepens it when a completer is wired. Deterministic-only is honestly weaker.
    injection_status = (
        PostureStatus.ENFORCED if s.model_layer_wired else PostureStatus.PARTIAL
    )
    injection_detail = (
        "Deterministic signature backstop + fail-closed model classifier both wired; "
        "an unparseable/unavailable classifier is treated as injection."
        if s.model_layer_wired
        else "Deterministic signature backstop runs (pure code), but no model classifier "
        "is wired (completer=None) — novel/obfuscated injections may slip; deterministic-only."
    )

    # LLM10 — Unbounded consumption. The self-repair loop is *always* hard-capped by
    # max_plan_iterations (guaranteed termination). Spend caps only bind when a real
    # governance hook is injected at the gateway AND it does not fail open.
    if s.budget_hook_wired and not s.budget_fail_open:
        consumption_status = PostureStatus.ENFORCED
        consumption_detail = (
            f"Loop hard-capped at max_plan_iterations={s.max_plan_iterations}; a governance "
            "hook enforces token/USD/RPM/TPM caps before spend (fail-closed)."
        )
    elif s.budget_hook_wired and s.budget_fail_open:
        consumption_status = PostureStatus.PARTIAL
        consumption_detail = (
            f"Loop hard-capped at max_plan_iterations={s.max_plan_iterations}, but "
            "budgets are configured fail-OPEN — a governance read failure lets the call "
            "through. The knob is the host's: BUDGET_FAIL_OPEN in this platform, "
            "GATEWAY_BUDGET_FAIL_OPEN for a host that injects no gateway config."
        )
    else:
        consumption_status = PostureStatus.PARTIAL
        consumption_detail = (
            f"Loop hard-capped at max_plan_iterations={s.max_plan_iterations} (runaway loops "
            "bounded), but no budget-governance hook is wired at the gateway — spend is ungoverned."
        )

    # AGENTIC identity/privilege abuse. RLS is fail-closed on Postgres + app-level
    # tenant filtering everywhere; but a dev JWT signing secret makes identity itself
    # forgeable, so that honestly downgrades the whole isolation story to partial.
    if s.jwt_dev_secret:
        identity_status = PostureStatus.PARTIAL
        identity_detail = (
            "RBAC + fail-closed RLS + Argon2id are wired, BUT the documented dev JWT secret "
            "is still in force — tokens are forgeable; inject a strong secret to reach enforced."
        )
    else:
        identity_status = PostureStatus.ENFORCED
        identity_detail = (
            f"Argon2id passwords + signed JWT ({s.jwt_algorithm}); per-tenant RLS "
            f"(fail_closed={s.rls_fail_closed}) on {s.rls_enforced_on} over "
            f"{s.rls_tables} tables + app-level tenant filtering."
        )

    return [
        PostureEntry(
            threat_id="LLM01",
            name="Prompt injection / jailbreak",
            control="Layered, fail-closed injection defense",
            module="aegis.guardrails.classifier",
            mechanism="deterministic_injection + classify_injection (via Guardrails.check_input)",
            status=injection_status,
            detail=injection_detail,
            refs=[
                "aegis.guardrails.classifier:deterministic_injection",
                "aegis.guardrails.classifier:classify_injection",
                "aegis.guardrails.pipeline:Guardrails",
            ],
        ),
        PostureEntry(
            threat_id="LLM02",
            name="Sensitive-information disclosure",
            control="PII redaction on both paths (before model & before user)",
            module="aegis.guardrails.pii",
            mechanism=f"redact (anchored regex + Luhn; engine={s.pii_engine})",
            status=PostureStatus.ENFORCED,
            detail=(
                "Pure-code PII detectors mask inbound before the model/classifier and outbound "
                f"before the answer; active engine={s.pii_engine}. Always on, no config gate."
            ),
            refs=[
                "aegis.guardrails.pii:redact",
                "aegis.guardrails.pii:scan",
            ],
        ),
        PostureEntry(
            threat_id="LLM04",
            name="Data & model poisoning",
            control="Write-time content validation before the store",
            module="aegis.retrieval.validation",
            mechanism="validate_content (injection-pattern + size + non-printable gate)",
            status=PostureStatus.ENFORCED,
            detail=(
                "Every ingested chunk is screened by a deterministic write-time gate rejecting "
                "embedded injection payloads / oversized / binary blobs before they hit the store."
            ),
            refs=["aegis.retrieval.validation:validate_content"],
        ),
        PostureEntry(
            threat_id="LLM05",
            name="Improper output handling",
            control="Output rail: schema validation → content filter → PII",
            module="aegis.guardrails.schema",
            mechanism="validate_output_format + content_filter (via Guardrails.check_output)",
            status=PostureStatus.ENFORCED,
            detail=(
                "Outbound answers are checked for structural well-formedness and filtered before "
                "they are trusted downstream. Pure code, always on."
            ),
            refs=[
                "aegis.guardrails.schema:validate_output_format",
                "aegis.guardrails.schema:content_filter",
                "aegis.guardrails.pipeline:Guardrails",
            ],
        ),
        PostureEntry(
            threat_id="LLM06",
            name="Excessive agency",
            control="Risk-tiered tools + human-in-the-loop gate",
            module="aegis.agent.graph",
            mechanism=f"gate/approval interrupt at gate_min_risk={s.gate_min_risk}",
            status=PostureStatus.ENFORCED,
            detail=(
                f"A proposed tool action at/above gate_min_risk={s.gate_min_risk} routes to the "
                "LangGraph approval node, which interrupts and waits for a human — a consequential "
                "action cannot execute on its own."
            ),
            refs=[
                "aegis.agent.graph:build_agent",
                "aegis.agent:AgentConfig",
                "aegis.core.types:RiskLevel",
            ],
        ),
        PostureEntry(
            threat_id="LLM07",
            name="System-prompt leakage",
            control="Output content-filter backstop",
            module="aegis.guardrails.schema",
            mechanism="content_filter (leakage signatures on the outbound path)",
            status=PostureStatus.ENFORCED,
            detail=(
                "The output rail's content filter is a deterministic backstop against prompt "
                "leakage before any answer is returned. Always on, pure code."
            ),
            refs=["aegis.guardrails.schema:content_filter"],
        ),
        PostureEntry(
            threat_id="LLM08",
            name="Vector & embedding weaknesses",
            control="Microsoft Spotlighting at retrieval + write-time validation",
            module="aegis.retrieval.spotlight",
            mechanism="build_spotlighted_context (delimiting + datamarking) + validate_content",
            status=PostureStatus.ENFORCED,
            detail=(
                "Retrieved spans are fenced and datamarked as untrusted DATA (Spotlighting, "
                "arXiv:2403.14720) and every write is validated first — defense at both RAG ends."
            ),
            refs=[
                "aegis.retrieval.spotlight:build_spotlighted_context",
                "aegis.retrieval.spotlight:spotlight",
                "aegis.retrieval.validation:validate_content",
            ],
        ),
        PostureEntry(
            threat_id="LLM09",
            name="Misinformation / ungrounded answer",
            control="Grounding self-check (contradiction blocks, unsupported flags)",
            module="aegis.guardrails.grounding",
            mechanism="check_grounding (BLOCK on contradicted; advisory FLAG on unsupported)",
            status=PostureStatus.PARTIAL,
            detail=(
                "An answer the retrieved passages CONTRADICT is blocked in either mode — there "
                "is no legitimate turn of that shape. An answer that is merely unsupported "
                "stays advisory, because blocking those is how a rail gets switched off. Still "
                "honestly partial: both findings are semantic entailment judgements with no "
                "deterministic backstop, so with no completer wired the rail is a no-op, and "
                "a run that retrieved nothing is reported but deliberately never blocked."
            ),
            refs=["aegis.guardrails.grounding:check_grounding"],
        ),
        PostureEntry(
            threat_id="LLM10",
            name="Unbounded consumption",
            control="Bounded self-repair loop + budget chokepoint (enforce-before-spend)",
            module="aegis.governance.enforcement",
            mechanism="enforce_governance (before spend) + AgentConfig.max_plan_iterations",
            status=consumption_status,
            detail=consumption_detail,
            refs=[
                "aegis.governance.enforcement:enforce_governance",
                "aegis.agent:AgentConfig",
                "aegis.gateway.types:BudgetExceededError",
            ],
        ),
        PostureEntry(
            threat_id="AGENTIC-IDENTITY",
            name="Cross-tenant identity / privilege abuse",
            control="RBAC + fail-closed Postgres RLS + Argon2id/JWT",
            module="aegis.governance",
            mechanism="bootstrap_rls (tenant_isolation policy) + create_access_token/hash_password",
            status=identity_status,
            detail=identity_detail,
            refs=[
                "aegis.governance.rls:bootstrap_rls",
                "aegis.governance.security:create_access_token",
                "aegis.governance.security:hash_password",
            ],
        ),
        PostureEntry(
            threat_id="AGENTIC-TRACEABILITY",
            name="Untraceable / unaccountable actions",
            control="Append-only audit log + end-to-end OTel trace",
            module="aegis.governance.audit",
            mechanism="record_audit (one row per autonomous/approved action)",
            status=PostureStatus.ENFORCED,
            detail=(
                "Every autonomous or approved tool call writes an audit row (actor, model, "
                "trace_id, payload, approver, tenant); runs are OpenTelemetry traces."
            ),
            refs=[
                "aegis.governance.audit:record_audit",
                "aegis.observability.otel:current_trace_id",
            ],
        ),
        PostureEntry(
            threat_id="AGENTIC-TOOL-MISUSE",
            name="Tool misuse / hijacking",
            control="Typed, risk-tiered, reversible tools + per-persona allowlist",
            module="aegis.agent",
            mechanism="RiskLevel-tiered tool contract (aegis) + host-side persona allowlist",
            status=PostureStatus.PARTIAL,
            detail=(
                "The aegis core supplies the typed, risk-tiered tool contract and the human gate; "
                "the per-persona ALLOWLIST enforced before any side effect lives host-side in the "
                "deployment's tool adapter, not introspectable from aegis core — partial here."
            ),
            refs=[
                "aegis.core.types:RiskLevel",
                "aegis.agent:AgentConfig",
            ],
        ),
        PostureEntry(
            threat_id="AGENTIC-EXTRACTION",
            name="Model extraction / membership inference by query volume",
            control="Per-principal, tenant-scoped query-pattern analysis",
            module="aegis.security.extraction",
            mechanism=(
                "ExtractionMonitor.screen — template enumeration and subject breadth "
                f"over a {int(DEFAULT_THRESHOLDS.window_seconds)}s sliding window"
            ),
            status=PostureStatus.PARTIAL,
            detail=(
                "A real detector, and pure code: every rail beside it screens a payload, "
                "and this one screens a principal over a window, which is the only shape "
                "in which extraction by query volume is visible at all. Honestly "
                "partial for two reasons that are properties of the design rather than "
                "of the configuration. The window is **in-process and non-durable** — a "
                "restart clears it, and a multi-process deployment observes each "
                "principal at a fraction of their true rate. And it is rate-shaped, so "
                "a sweep paced under the floor completes unseen; the red-team battery "
                "carries that as a declared leak (``exfil-06`` / ``exfil-07``) rather "
                "than leaving it to a footnote."
            ),
            refs=[
                "aegis.security.extraction:ExtractionMonitor",
                "aegis.security.extraction:query_signature",
                "aegis.guardrails.schema:exfiltration_channel",
            ],
        ),
    ]
