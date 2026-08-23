"""Compliance-readiness data for ``GET /compliance`` — frameworks, states, evidence.

A typed data module grounded **verbatim** in ``docs/compliance/README.md``, in the
same relationship :mod:`app.platform.risk_map` has to ``docs/security/owasp-agentic.md``:
the document is the authority, this module is its projection onto the wire, and the
screen is a projection of *this*. Prose typed straight into a React component would
make the screen the authority, which is exactly backwards for a claim a buyer's
security reviewer is meant to be able to check.

**What this module is not.** It is not a certification, an attestation, or an audit
result, and :data:`DISCLAIMER` says so on every response. Aegis holds no ISO 27001
certificate, no ISO/IEC 42001 certificate, no SOC 2 report and no EU AI Act conformity
assessment. What the response carries is a control-by-control map from published
frameworks to **files, routes and tests in this repository**.

**Four states, not two.** :class:`ControlState` is deliberately four-valued so the
table can neither flatter nor evade:

* ``enforced``        — a control runs on every relevant request and a test proves it.
  Requires at least one ``file`` evidence item *and* one ``test`` evidence item; the
  test module asserts that, so an ``enforced`` cell cannot be typed into existence.
* ``partial``         — a real control runs but a layer is advisory, opt-in,
  configuration-dependent or narrower than the framework's control. ``gap`` must name
  *which layer*, not merely say "partially".
* ``not_implemented`` — no control at this layer. Stated plainly; a respectable cell.
* ``not_applicable``  — the control governs something this system does not do, and
  ``gap`` says why.

**Every claim resolves.** Each :class:`Evidence` item carries a machine-checkable
reference — a repository path, a served route, or a pytest node id — and
``backend/tests/api/test_compliance.py`` resolves every one of them against the real
filesystem, the real route table and the real test files on each run. A claim that
names a file which does not exist fails the suite rather than reaching a jury.

Reading this costs nothing: pure data, no I/O, no model call, no database.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.platform.residency import ResidencyReport, build_residency

__all__ = [
    "DISCLAIMER",
    "DOC_REF",
    "JURISDICTION_INDIA",
    "JURISDICTION_INTERNATIONAL",
    "ComplianceResponse",
    "ControlEntry",
    "ControlState",
    "Evidence",
    "EvidenceKind",
    "Framework",
    "FrameworkCoverage",
    "build_compliance",
]


DISCLAIMER = (
    "Compliance-readiness evidence, not certification. Aegis holds no ISO 27001, "
    "ISO/IEC 42001, SOC 2 or EU AI Act attestation, and nothing on this page has been "
    "audited by an independent party. Every cell links to a file, a route or a test in "
    "this repository so a reviewer can check the claim rather than take it."
)

DOC_REF = "docs/compliance/README.md"
"""The written position this module projects. Divergence is a defect, not a variant."""

JURISDICTION_INDIA = "India"
"""The home market. Its frameworks are served first, deliberately — see :data:`_FRAMEWORKS`."""

JURISDICTION_INTERNATIONAL = "International"
"""Everything else: the EU regulations, the US framework, and the ISO/OWASP/AICPA bodies."""


class ControlState(StrEnum):
    """How a framework control stands in this repository. Four-valued on purpose."""

    ENFORCED = "enforced"
    PARTIAL = "partial"
    NOT_IMPLEMENTED = "not_implemented"
    NOT_APPLICABLE = "not_applicable"


class EvidenceKind(StrEnum):
    """What kind of artefact an evidence reference names — and how it is resolved."""

    #: A path relative to the repository root. Resolved against the filesystem.
    FILE = "file"
    #: ``"GET /security/posture"`` — resolved against the served route table.
    ROUTE = "route"
    #: ``"path/to/test_x.py::test_name"`` — resolved against the test file's contents.
    TEST = "test"
    #: A document path. Resolved against the filesystem like ``FILE``, kept distinct so
    #: the surface can tell "we wrote it down" from "the code does it".
    DOC = "doc"


class Evidence(BaseModel):
    """One checkable reference behind a control claim."""

    kind: EvidenceKind = Field(description="file / route / test / doc.")
    ref: str = Field(description="The path, route or pytest node id.")
    label: str = Field(description="Short human label for the reference.")


class ControlEntry(BaseModel):
    """One framework control, its honest state, and what backs it."""

    id: str = Field(description="The framework's own control identifier.")
    title: str = Field(description="The control's name, as the framework words it.")
    state: ControlState = Field(
        description="enforced / partial / not_implemented / not_applicable."
    )
    summary: str = Field(description="What Aegis actually does here. One sentence.")
    gap: str = Field(
        default="",
        description=(
            "What is missing, in plain words. Required for partial, not_implemented and "
            "not_applicable; empty only for enforced."
        ),
    )
    evidence: list[Evidence] = Field(
        default_factory=list, description="Checkable references backing the claim."
    )


class FrameworkCoverage(BaseModel):
    """The four counts for one framework. Derived, never hand-authored."""

    enforced: int = 0
    partial: int = 0
    not_implemented: int = 0
    not_applicable: int = 0
    total: int = 0


class Framework(BaseModel):
    """One published framework and Aegis's control-by-control position against it."""

    id: str = Field(description="Stable slug, e.g. 'owasp-llm'.")
    name: str = Field(description="The framework's name.")
    version: str = Field(description="The edition these controls are taken from.")
    jurisdiction: str = Field(
        default=JURISDICTION_INTERNATIONAL,
        description=(
            "Which body of law or practice this framework belongs to — 'India' for the "
            "home market's own regulation, 'International' for everything else."
        ),
    )
    scope: str = Field(description="What part of Aegis this framework governs.")
    controls: list[ControlEntry] = Field(description="One entry per mapped control.")
    coverage: FrameworkCoverage = Field(
        default_factory=FrameworkCoverage, description="Derived state counts."
    )


class ComplianceResponse(BaseModel):
    """Body for ``GET /compliance`` — every framework, with its evidence."""

    disclaimer: str = Field(description="Readiness, not certification. Always present.")
    doc_ref: str = Field(description="The written authority this response projects.")
    generated_at: str = Field(description="ISO-8601 UTC timestamp of this read.")
    frameworks: list[Framework] = Field(description="The mapped frameworks.")
    coverage: FrameworkCoverage = Field(description="Totals across every framework.")
    residency: ResidencyReport = Field(
        description=(
            "Where this deployment's data actually goes, derived from live configuration. "
            "Two India rows depend on it — DPDP s.16 (cross-border transfer) and CERT-In "
            "Direction (iv) (logs within Indian jurisdiction) — and both are questions no "
            "prose answer settles."
        )
    )


# ─────────────────────────────────────────────────────────────────────────────
# Shorthand constructors — the table below is data, and reads like data
# ─────────────────────────────────────────────────────────────────────────────


def _f(ref: str, label: str) -> Evidence:
    """A repository file."""
    return Evidence(kind=EvidenceKind.FILE, ref=ref, label=label)


def _d(ref: str, label: str) -> Evidence:
    """A document."""
    return Evidence(kind=EvidenceKind.DOC, ref=ref, label=label)


def _r(ref: str, label: str) -> Evidence:
    """A served API route."""
    return Evidence(kind=EvidenceKind.ROUTE, ref=ref, label=label)


def _t(ref: str, label: str) -> Evidence:
    """A pytest node id."""
    return Evidence(kind=EvidenceKind.TEST, ref=ref, label=label)


# ─────────────────────────────────────────────────────────────────────────────
# 1. OWASP Top 10 for LLM Applications (2025)
#
# The live, wiring-derived twin of nine of these rows is GET /security/posture,
# whose statuses flip with configuration. These are the framework-level judgements,
# and where the two differ in scope (LLM04, LLM08) the gap text says so rather than
# quietly lowering one of them.
# ─────────────────────────────────────────────────────────────────────────────

_OWASP_LLM: tuple[ControlEntry, ...] = (
    ControlEntry(
        id="LLM01",
        title="Prompt Injection",
        state=ControlState.ENFORCED,
        summary=(
            "A deterministic signature backstop plus a fail-closed model classifier run "
            "at all three rails — input, output and tool result — so indirect injection "
            "is screened where it would actually arrive. With no model completer wired "
            "this degrades to partial and the live posture says so: five battery probes "
            "are semantic-only and leak offline by design, reported rather than curated "
            "out. Injection is never marked solved anywhere on this platform."
        ),
        evidence=[
            _f(
                "aegis/src/aegis/guardrails/classifier.py",
                "deterministic_injection + classify_injection",
            ),
            _f(
                "aegis/src/aegis/guardrails/pipeline.py",
                "check_input / check_output / check_tool_result",
            ),
            _f("aegis/src/aegis/redteam/battery.py", "11 of 28 attack probes tagged LLM01"),
            _r("GET /security/posture", "live status, derived from wiring"),
            _t(
                "aegis/tests/redteam/test_stages_and_suites.py"
                "::test_the_indirect_injections_are_caught_at_the_tool_result_rail",
                "indirect injection caught at the tool-result rail",
            ),
            _t(
                "aegis/tests/security/test_posture.py"
                "::test_injection_enforced_when_model_layer_wired",
                "status tracks real wiring",
            ),
        ],
    ),
    ControlEntry(
        id="LLM02",
        title="Sensitive Information Disclosure",
        state=ControlState.ENFORCED,
        summary=(
            "PII is detected and masked inbound before the model (and before the "
            "classifier call), outbound before the answer, and on every tool result."
        ),
        evidence=[
            _f(
                "aegis/src/aegis/guardrails/pii.py",
                "redact / scan — Presidio or anchored regex + Luhn",
            ),
            _f("aegis/src/aegis/redteam/battery.py", "suite 'disclosure' — 5 probes"),
            _t(
                "aegis/tests/guardrails/test_pipeline.py::test_redacts_pii_on_clean_input",
                "redaction on the live path",
            ),
            _t(
                "aegis/tests/redteam/test_redteam.py::test_pii_attack_neutralized_via_redact",
                "adversarial probe neutralised",
            ),
        ],
    ),
    ControlEntry(
        id="LLM03",
        title="Supply Chain",
        state=ControlState.PARTIAL,
        summary=(
            "Fully hash-pinned lockfiles, a live SBOM resolved from the actually "
            "installed distributions at request time, and registry-verified freshness "
            "that refuses to report 'current' without a real answer from PyPI."
        ),
        gap=(
            "No CVE/OSV advisory feed — this is patch freshness, not a vulnerability "
            "verdict. No CycloneDX/SPDX export, and no signature or provenance "
            "attestation. CI exists (.github/workflows/ci.yml) and gates lint and the "
            "three test suites, but it scans no dependency and produces no provenance."
        ),
        evidence=[
            _f("backend/uv.lock", "319 distributions, 4219 sha256 digests"),
            _f("aegis/uv.lock", "129 distributions, hash-pinned"),
            _f(
                "backend/src/app/platform/stack.py",
                "SBOM from importlib.metadata, never a hand-written pin list",
            ),
            _f(
                "backend/src/app/platform/patches.py",
                "live PyPI check; no clean bill of health without a real answer",
            ),
            _r("GET /stack", "the live bill of materials"),
            _r("POST /stack/patch-check", "installed vs latest, per package"),
            _d("docs/adr/0001-litellm-as-gateway.md", "one vetted gateway, not many provider SDKs"),
            _t(
                "backend/tests/api/test_platform_surfaces.py::test_stack_shape_and_real_versions",
                "versions are real, not literals",
            ),
            _t(
                "backend/tests/api/test_platform_surfaces.py"
                "::test_patch_check_offline_reports_unknown",
                "offline degrades honestly",
            ),
        ],
    ),
    ControlEntry(
        id="LLM04",
        title="Data and Model Poisoning",
        state=ControlState.PARTIAL,
        summary=(
            "Every ingested chunk passes a deterministic write-time gate that rejects "
            "embedded injection payloads, oversized and non-printable blobs before the "
            "store."
        ),
        gap=(
            "Scope note: /security/posture reports LLM04 enforced for the ingestion gate, "
            "which is correct for the control it names. The framework's control is wider — "
            "the ML spine trains from a host-supplied frame with no provenance attestation "
            "and no dataset integrity hash."
        ),
        evidence=[
            _f("aegis/src/aegis/retrieval/validation.py", "validate_content — the write-time gate"),
            _f("aegis/src/aegis/ml/dataset.py", "the unattested training frame — the gap itself"),
            _t(
                "backend/tests/api/test_memory_control.py"
                "::test_a_correction_carrying_an_injection_is_refused",
                "poisoned write refused",
            ),
        ],
    ),
    ControlEntry(
        id="LLM05",
        title="Improper Output Handling",
        state=ControlState.ENFORCED,
        summary=(
            "The outbound rail validates structural well-formedness and then content-"
            "filters before anything downstream is allowed to trust the answer."
        ),
        evidence=[
            _f("aegis/src/aegis/guardrails/schema.py", "validate_output_format + content_filter"),
            _t(
                "aegis/tests/guardrails/test_schema.py::test_content_filter_flags_leak_marker",
                "the filter fires",
            ),
            _t(
                "aegis/tests/security/test_posture.py::test_pure_code_controls_are_enforced",
                "always on, no config gate",
            ),
        ],
    ),
    ControlEntry(
        id="LLM06",
        title="Excessive Agency",
        state=ControlState.ENFORCED,
        summary=(
            "Typed, risk-tiered tools behind a durable human gate: an action at or above "
            "gate_min_risk interrupts the run and waits for a named person, and the SLA "
            "sweeper auto-rejects rather than auto-approves on timeout. The per-persona "
            "tool allowlist that runs beneath the gate is enforced host-side in the "
            "deployment's adapter and is not introspectable from aegis core, which is "
            "why the live posture reports AGENTIC-TOOL-MISUSE as partial beside this."
        ),
        evidence=[
            _f("aegis/src/aegis/agent/graph.py", "the gate / approval interrupt"),
            _f(
                "backend/src/app/data/approvals.py",
                "durable queue, exactly-once resume, SLA sweeper",
            ),
            _f("backend/src/app/mcp/server.py", "external MCP tools default to HIGH risk"),
            _d("docs/adr/0005-postgressaver-durable-execution.md", "why the pause is durable"),
            _t(
                "backend/tests/data/test_approvals.py"
                "::test_sla_sweeper_expires_and_auto_rejects_high",
                "fail-safe on timeout",
            ),
            _t(
                "backend/tests/data/test_approvals.py"
                "::test_the_sweepers_automated_decision_leaves_an_audit_trail",
                "the auto-decision is on the record",
            ),
        ],
    ),
    ControlEntry(
        id="LLM07",
        title="System Prompt Leakage",
        state=ControlState.ENFORCED,
        summary=(
            "The output rail's content filter carries leakage signatures as a "
            "deterministic backstop before any answer is returned."
        ),
        evidence=[
            _f("aegis/src/aegis/guardrails/schema.py", "content_filter leak signatures"),
            _f("aegis/src/aegis/redteam/battery.py", "3 meta-prompt-extraction probes"),
            _t(
                "aegis/tests/guardrails/test_schema.py::test_content_filter_flags_leak_marker",
                "signature fires on a leak marker",
            ),
        ],
    ),
    ControlEntry(
        id="LLM08",
        title="Vector and Embedding Weaknesses",
        state=ControlState.PARTIAL,
        summary=(
            "Both ends of RAG are defended: retrieved spans are fenced and datamarked as "
            "untrusted DATA (Microsoft Spotlighting), and every write is validated first."
        ),
        gap=(
            "Scope note: those two mechanisms are enforced. The vector tier itself is a "
            "shared Qdrant node outside Postgres RLS, so tenant separation there is "
            "application-scoped rather than database-enforced."
        ),
        evidence=[
            _f(
                "aegis/src/aegis/retrieval/spotlight.py",
                "build_spotlighted_context — delimiting + datamarking",
            ),
            _f("aegis/src/aegis/retrieval/validation.py", "validate-before-write"),
            _t(
                "aegis/tests/retrieval/test_observability.py::test_spotlight_applied_by_default",
                "on by default, not opt-in",
            ),
            _t(
                "aegis/tests/retrieval/test_citations.py"
                "::test_a_span_survives_the_spotlight_datamarking",
                "datamarking does not corrupt the span",
            ),
        ],
    ),
    ControlEntry(
        id="LLM09",
        title="Misinformation",
        state=ControlState.PARTIAL,
        summary=(
            "A grounding self-check exists alongside conformal abstention bands and a "
            "per-metric eval regression gate that fails on a quality drop."
        ),
        gap=(
            "The grounding rail is opt-in and advisory by default — it flags, it does not "
            "block, unless a deployment sets grounding_block."
        ),
        evidence=[
            _f("aegis/src/aegis/guardrails/grounding.py", "check_grounding — advisory by default"),
            _f("aegis/src/aegis/evals/regression.py", "declarative per-metric thresholds"),
            _d(
                "docs/adr/0007-conformal-autonomy-bands.md",
                "abstain rather than act over-confidently",
            ),
            _t(
                "aegis/tests/security/test_posture.py::test_misinformation_is_honestly_partial",
                "the honest downgrade is asserted",
            ),
        ],
    ),
    ControlEntry(
        id="LLM10",
        title="Unbounded Consumption",
        state=ControlState.PARTIAL,
        summary=(
            "The self-repair loop is always hard-capped, and token/USD/RPM/TPM caps are "
            "enforced before spend at the single gateway chokepoint."
        ),
        gap=(
            "Spend caps bind only when the host injects a governance hook at the gateway; "
            "GATEWAY_BUDGET_FAIL_OPEN=true lets a governance read failure through. The "
            "live posture reports both rather than hiding them."
        ),
        evidence=[
            _f("aegis/src/aegis/governance/enforcement.py", "enforce_governance — before spend"),
            _t(
                "aegis/tests/governance/test_enforcement.py::test_over_token_budget_raises",
                "the cap actually refuses",
            ),
            _t(
                "aegis/tests/security/test_posture.py"
                "::test_consumption_partial_when_budget_fail_open",
                "fail-open is never green",
            ),
        ],
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# 2. OWASP Top 10:2025 — the web/API surface
#
# SSRF is no longer its own category in the 2025 list; the unvalidated MCP peer URL
# is recorded under A01, which is where it belongs and where it is honest.
# ─────────────────────────────────────────────────────────────────────────────

_OWASP_WEB: tuple[ControlEntry, ...] = (
    ControlEntry(
        id="A01:2025",
        title="Broken Access Control",
        state=ControlState.PARTIAL,
        summary=(
            "Role guards on every route plus Postgres RLS with FORCE ROW LEVEL SECURITY "
            "and a NOSUPERUSER NOBYPASSRLS serving role whose exemption status is audited "
            "at boot; the portal catalogue and the served route table are related by a test."
        ),
        gap=(
            "SSRF, named: a platform admin registers an MCP peer by URL with no scheme, "
            "host or private-range validation — the field takes any string and the client "
            "dials it. Authenticated-admin only, but it is a real unguarded outbound request."
        ),
        evidence=[
            _f(
                "aegis/src/aegis/governance/rls.py",
                "FORCE RLS, boot-time bypass audit, RlsBypassError",
            ),
            _f(
                "backend/src/app/api/routes_mcp.py", "ServerCreate.url: str — the unvalidated field"
            ),
            _f("backend/src/app/mcp/client.py", "the peer dial"),
            _t(
                "backend/tests/api/test_cross_tenant_holes.py::test_audit_is_tenant_scoped",
                "no cross-tenant read",
            ),
            _t(
                "aegis/tests/governance/test_rls_enforcement.py"
                "::test_a_bypassing_role_stops_the_process_when_the_check_is_fatal",
                "a bypassing role halts the boot",
            ),
            _t(
                "backend/tests/api/test_route_coverage.py"
                "::test_every_non_public_endpoint_is_reachable_from_a_portal",
                "portal and route table cannot drift",
            ),
        ],
    ),
    ControlEntry(
        id="A02:2025",
        title="Security Misconfiguration",
        state=ControlState.PARTIAL,
        summary=(
            "Misconfiguration is surfaced rather than assumed: the posture reads the dev "
            "JWT secret, budget_fail_open and rls_fail_closed from live wiring and "
            "downgrades itself accordingly; the DB console is off unless two env vars are set."
        ),
        gap=(
            "The documented dev JWT secret is still in force in the default APP_ENV=dev "
            "run — the posture says so, which is the honest state, not a passing one. It "
            "can no longer reach production: a non-dev boot refuses the built-in default, "
            "a secret under 32 chars, a known placeholder ('change-me', 'supersecret', "
            "matched as substrings) and one drawing on fewer than 12 distinct characters, "
            "which is what 'x' * 48 did while passing the old length-only check. No "
            "hardening baseline and no TLS configuration owned in this repository."
        ),
        evidence=[
            _f("aegis/src/aegis/security/posture.py", "read_signals — configuration read live"),
            _f("backend/src/app/api/routes_db.py", "console off by default"),
            _t(
                "aegis/tests/security/test_posture.py::test_identity_partial_under_dev_jwt_secret",
                "the dev secret downgrades identity",
            ),
            _t(
                "backend/tests/api/test_request_models_forbid_extras.py"
                "::test_every_request_model_forbids_a_field_it_does_not_carry",
                "no silently accepted fields",
            ),
        ],
    ),
    ControlEntry(
        id="A03:2025",
        title="Software Supply Chain Failures",
        state=ControlState.PARTIAL,
        summary=(
            "Hash-pinned lockfiles, a live SBOM and a registry-verified freshness check — "
            "the same machinery as LLM03, labelled here as the web-surface control."
        ),
        gap=(
            "No advisory feed and no attestation. CI exists "
            "(.github/workflows/ci.yml) but gates tests and lint, not the supply chain."
        ),
        evidence=[
            _f("backend/uv.lock", "hash-pinned dependency graph"),
            _f("backend/src/app/platform/stack.py", "live SBOM"),
            _r("POST /stack/patch-check", "installed vs latest"),
            _t(
                "backend/tests/api/test_platform_surfaces.py"
                "::test_patch_check_online_current_and_outdated",
                "verdicts come from the registry",
            ),
        ],
    ),
    ControlEntry(
        id="A04:2025",
        title="Cryptographic Failures",
        state=ControlState.PARTIAL,
        summary=(
            "Argon2id password hashing and signed JWTs; MCP peer credentials are held in "
            "the serving process only and never written to the database or returned."
        ),
        gap=(
            "Symmetric HS256; the dev default ships but a non-dev boot now refuses it. "
            "Nothing is encrypted at rest, and column-level encryption was assessed and "
            "deliberately refused rather than overlooked: one document comes to rest in "
            "eight places, column encryption reaches three, and the two holding the most "
            "content (chunks.text, memory_fact) cannot be encrypted without removing "
            "retrieval and semantic recall. It also defends only a stolen dump, never a "
            "compromised application, which holds the key. The owed control is transparent "
            "volume encryption plus encrypted backups — a deployment control, reported "
            "from live wiring and never claimed as verified. No key rotation."
        ),
        evidence=[
            _f(
                "aegis/src/aegis/governance/security.py",
                "hash_password (Argon2id) + create_access_token",
            ),
            _f("backend/src/app/api/routes_mcp.py", "credentials never persisted or returned"),
            _t(
                "aegis/tests/governance/test_security.py::test_password_hash_and_verify",
                "Argon2id hash and verify",
            ),
        ],
    ),
    ControlEntry(
        id="A05:2025",
        title="Injection",
        state=ControlState.ENFORCED,
        summary=(
            "Every query is parameterised; the DB console is a closed set of reads over a "
            "role holding SELECT and nothing else, with the tenant clause written by the "
            "server; identifiers reaching DDL are validated and refused rather than escaped; "
            "CSV/DDE formula injection is neutralised on export."
        ),
        evidence=[
            _f("scripts/sql/aegis-readonly-role.sql", "SELECT only; INSERT/UPDATE/DELETE revoked"),
            _f("backend/src/app/api/routes_db.py", "closed set of parameterised reads"),
            _f("aegis/src/aegis/reports/writer.py", "CSV/DDE formula neutralisation"),
            _t(
                "aegis/tests/reports/test_writer.py"
                "::test_a_string_that_looks_like_a_formula_is_neutralised",
                "export injection blocked",
            ),
            _t(
                "backend/tests/api/test_db_console.py"
                "::test_multiple_commands_cannot_be_sent_in_one_statement",
                "no statement stacking",
            ),
        ],
    ),
    ControlEntry(
        id="A06:2025",
        title="Insecure Design",
        state=ControlState.PARTIAL,
        summary=(
            "A written threat model, an inherent-versus-residual risk map with a control_ref "
            "per row, nine ADRs, fail-closed defaults, and an adversarial battery run "
            "against the design."
        ),
        gap=(
            "No formal design-review or abuse-case process; the risk-map figures are stated "
            "engineering judgement and the module says so in its own docstring."
        ),
        evidence=[
            _d("docs/security/threat-model.md", "the one-page threat model"),
            _f("backend/src/app/platform/risk_map.py", "inherent and residual positions per risk"),
            _r("GET /risk-map", "the assurance surface"),
            _t(
                "backend/tests/api/test_platform_surfaces.py"
                "::test_risk_map_every_risk_moves_and_totals_reduce",
                "no risk claims to be solved",
            ),
        ],
    ),
    ControlEntry(
        id="A07:2025",
        title="Authentication Failures",
        state=ControlState.PARTIAL,
        summary=(
            "Argon2id, signed JWT carrying {sub, role, tenant_id}, fine-grained portal roles, "
            "and a regression test written specifically against auth bypass."
        ),
        gap=(
            "No MFA. No account lockout or login rate limiting. No password policy. No token "
            "revocation list — a leaked token is valid until it expires."
        ),
        evidence=[
            _f("aegis/src/aegis/governance/security.py", "hashing and token issue"),
            _t(
                "backend/tests/api/test_auth_backdoor.py"
                "::test_a_provisioned_store_answers_an_unknown_user_with_401",
                "no backdoor principal",
            ),
            _t(
                "backend/tests/api/test_login_fine_role.py"
                "::test_the_login_user_id_is_the_tokens_subject",
                "the token carries the real principal",
            ),
        ],
    ),
    ControlEntry(
        id="A08:2025",
        title="Software or Data Integrity Failures",
        state=ControlState.PARTIAL,
        summary=(
            "Hash-pinned lockfiles, an append-only audit trail, and exactly-once approval "
            "resume via an optimistic lock plus a tool idempotency key."
        ),
        gap=(
            "No signed build artefacts and no SLSA provenance — CI now exists and "
            "produces neither. The audit trail is append-only by database privilege on "
            "the serving role, not against the owner connection."
        ),
        evidence=[
            _f("backend/uv.lock", "sha256 per distribution"),
            _f("backend/src/app/data/approvals.py", "PENDING->RESUMING optimistic lock"),
            _t(
                "backend/tests/api/test_approvals_endpoints.py"
                "::test_decision_endpoint_shape_and_idempotency",
                "exactly-once decision",
            ),
        ],
    ),
    ControlEntry(
        id="A09:2025",
        title="Security Logging and Alerting Failures",
        state=ControlState.PARTIAL,
        summary=(
            "Every autonomous or approved action writes an audit row carrying actor, model, "
            "trace_id, payload, approver and tenant; reads filter in SQL, never in the page; "
            "runs are OpenTelemetry traces; SLA and auto-decision events reach a durable inbox."
        ),
        gap=(
            "Append-only is now a database guarantee against the serving role: it holds "
            "SELECT, INSERT on audit_log, usage_ledger and run_events (and each run_events "
            "monthly partition, which is separately addressable by name), so DELETE FROM "
            "audit_log on the connection every request arrives on is refused. It is NOT a "
            "guarantee against the owner connection, which still holds full DML — tampering "
            "now requires POSTGRES_ADMIN_DSN rather than being impossible. memory_write_log "
            "is deliberately excluded and keeps DELETE, because the erasure route must reach "
            "it from a request handler. No SIEM export, and no documented audit_log retention "
            "or partitioning."
        ),
        evidence=[
            _f("aegis/src/aegis/governance/audit.py", "record_audit — one row per action"),
            _f(
                "aegis/src/aegis/governance/rls.py",
                "_APPEND_ONLY_TABLES — the revoke that makes it structural",
            ),
            _r("GET /audit", "the trail, tenant-scoped in SQL"),
            _t(
                "aegis/tests/governance/test_audit_filters.py"
                "::test_the_filter_runs_before_the_limit",
                "filters are WHERE clauses, not page slices",
            ),
            _d(
                "docs/dev_new_docs_v2/backlog-post-hackathon.md",
                "retention and partitioning recorded as owed",
            ),
        ],
    ),
    ControlEntry(
        id="A10:2025",
        title="Mishandling of Exceptional Conditions",
        state=ControlState.PARTIAL,
        summary=(
            "Fail-closed where it matters: an unparseable classifier is treated as "
            "injection, RLS admits no rows without a tenant, the SLA sweeper auto-rejects, "
            "the patch-check refuses a clean bill of health it cannot verify, and the MCP "
            "client enforces a deadline a cancel-resistant SDK teardown cannot outlive."
        ),
        gap="Not systematically audited across every handler; no chaos or error-budget testing.",
        evidence=[
            _f(
                "backend/src/app/mcp/client.py", "_bounded — a deadline the teardown cannot outlive"
            ),
            _f("backend/src/app/platform/patches.py", "no clean bill of health without an answer"),
            _t(
                "aegis/tests/guardrails/test_content_safety.py"
                "::test_classify_content_fails_closed_on_unparseable",
                "unparseable means unsafe",
            ),
            _t(
                "aegis/tests/guardrails/test_pipeline.py"
                "::test_an_outage_is_not_cached_as_a_verdict",
                "an outage is not a pass",
            ),
        ],
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# 3. MITRE ATLAS — only what the battery actually exercises
# ─────────────────────────────────────────────────────────────────────────────

_ATLAS: tuple[ControlEntry, ...] = (
    ControlEntry(
        id="AML.T0051.000",
        title="LLM Prompt Injection: Direct",
        state=ControlState.ENFORCED,
        summary=(
            "Four direct instruction-override probes at the input rail, in suite "
            "'prompt-injection'."
        ),
        evidence=[
            _f("aegis/src/aegis/redteam/battery.py", "the _INJECTION probe set"),
            _r("POST /redteam/runs", "run it and read the verdicts"),
            _t(
                "aegis/tests/redteam/test_redteam.py"
                "::test_offline_deterministic_attacks_are_blocked",
                "they are actually blocked",
            ),
        ],
    ),
    ControlEntry(
        id="AML.T0051.001",
        title="LLM Prompt Injection: Indirect",
        state=ControlState.ENFORCED,
        summary=(
            "Three probes fed to check_tool_result — the rail a poisoned tool return "
            "actually arrives on, rather than pasted into the input rail where it is no "
            "longer the same attack."
        ),
        evidence=[
            _f("aegis/src/aegis/redteam/battery.py", "the _INDIRECT probe set, Stage.TOOL_RESULT"),
            _t(
                "aegis/tests/redteam/test_stages_and_suites.py"
                "::test_the_indirect_injections_are_caught_at_the_tool_result_rail",
                "caught at the right rail",
            ),
        ],
    ),
    ControlEntry(
        id="AML.T0054",
        title="LLM Jailbreak",
        state=ControlState.ENFORCED,
        summary=(
            "Four jailbreak probes, one deliberately semantic-only and marked needs_llm so "
            "the report explains an offline leak instead of hiding it."
        ),
        evidence=[
            _f("aegis/src/aegis/redteam/battery.py", "the _JAILBREAK probe set"),
            _t(
                "aegis/tests/redteam/test_redteam.py"
                "::test_offline_leaked_set_is_within_the_semantic_only_attacks",
                "leaks are the declared ones",
            ),
        ],
    ),
    ControlEntry(
        id="AML.T0056",
        title="LLM Meta Prompt Extraction",
        state=ControlState.ENFORCED,
        summary=(
            "Three system-prompt-leak probes in suite 'disclosure', against the output content "
            "filter."
        ),
        evidence=[
            _f("aegis/src/aegis/redteam/battery.py", "the _LEAK probe set"),
            _t(
                "aegis/tests/guardrails/test_schema.py::test_content_filter_flags_leak_marker",
                "the backstop fires",
            ),
        ],
    ),
    ControlEntry(
        id="AML.T0057",
        title="LLM Data Leakage",
        state=ControlState.ENFORCED,
        summary=(
            "Three PII-extraction probes at the input rail and two disclosure probes at the "
            "output rail — the attack that was never in the prompt at all."
        ),
        evidence=[
            _f("aegis/src/aegis/redteam/battery.py", "the _PII and _OUTPUT probe sets"),
            _t(
                "aegis/tests/redteam/test_redteam.py::test_pii_attack_neutralized_via_redact",
                "neutralised by redaction",
            ),
        ],
    ),
    ControlEntry(
        id="AML.T0053",
        title="LLM Plugin Compromise",
        state=ControlState.PARTIAL,
        summary=(
            "Three excessive-agency probes plus the real control: an external MCP tool is "
            "HIGH risk and stops at the human gate until a platform admin lowers a named one."
        ),
        gap="The probes test the rail's refusal, not a compromised peer end to end.",
        evidence=[
            _f("backend/src/app/mcp/server.py", "external tools gated HIGH by default"),
            _f("aegis/src/aegis/redteam/battery.py", "the _AGENCY probe set"),
        ],
    ),
    ControlEntry(
        id="AML.T0020",
        title="Poison Training Data",
        state=ControlState.NOT_IMPLEMENTED,
        summary="No training-data attack is exercised.",
        gap="The battery has no poisoning probe; see LLM04 for the same gap from the other side.",
        evidence=[_f("aegis/src/aegis/ml/dataset.py", "the unattested training frame")],
    ),
    ControlEntry(
        id="AML.T0024",
        title="Exfiltration via AI Inference API",
        state=ControlState.NOT_IMPLEMENTED,
        summary="No model-extraction or membership-inference probes.",
        gap="Nothing in the battery attacks the inference API for the model itself.",
    ),
    ControlEntry(
        id="AML.T0043",
        title="Craft Adversarial Data",
        state=ControlState.NOT_IMPLEMENTED,
        summary="No evasion or perturbation probes against the ML spine.",
        gap="Adversarial robustness of the conformal ensemble is unmeasured.",
    ),
    ControlEntry(
        id="AML.T0018",
        title="Backdoor ML Model",
        state=ControlState.NOT_APPLICABLE,
        summary="Aegis loads no third-party model weights.",
        gap=(
            "The only fitted model is trained in-process from the host's own frame, so there "
            "is no downloaded artefact to have been backdoored."
        ),
        evidence=[_f("aegis/src/aegis/ml/model.py", "the ensemble is fitted here, not loaded")],
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# 4. NIST AI RMF 1.0
# ─────────────────────────────────────────────────────────────────────────────

_NIST: tuple[ControlEntry, ...] = (
    ControlEntry(
        id="GOVERN",
        title="Govern — policies, accountability, culture",
        state=ControlState.PARTIAL,
        summary=(
            "Nine ADRs recording each decision and its rejected alternatives, a written "
            "threat model, and a per-tenant policy catalogue stating who may write each "
            "setting and how scopes merge."
        ),
        gap=(
            "No written AI policy, no accountable-role register, no incident-response plan, "
            "no review cadence. This function is documentation, not process."
        ),
        evidence=[
            _d("docs/adr/0008-multi-tenant-rls-governance.md", "a recorded decision"),
            _d("docs/security/threat-model.md", "the threat model"),
            _f("aegis/src/aegis/settings/spec.py", "the policy catalogue and its merge rules"),
        ],
    ),
    ControlEntry(
        id="MAP",
        title="Map — context, risks, capabilities",
        state=ControlState.PARTIAL,
        summary=(
            "Each agentic risk carries an inherent and a residual position with the control "
            "that moved it and a control_ref naming a real file; the stack is the inventory; "
            "the model card states task, features, data source, calibration and training sizes."
        ),
        gap="No context-of-use analysis and no assessment of impact on affected individuals.",
        evidence=[
            _r("GET /risk-map", "inherent and residual, per risk"),
            _r("GET /stack", "the system inventory"),
            _r("GET /ml/model-card", "honest, measured model metadata"),
            _f("backend/src/app/platform/risk_map.py", "grounded verbatim in the security doc"),
        ],
    ),
    ControlEntry(
        id="MEASURE",
        title="Measure — analyse, benchmark, monitor",
        state=ControlState.ENFORCED,
        summary=(
            "Everything here is measured rather than asserted: block rate reported beside a "
            "false-positive rate against per-suite floors, per-metric eval thresholds, "
            "conformal coverage reported as empirical coverage on a held-out split, latency "
            "from real samples with an honest empty state, and a security posture derived "
            "from live wiring on every request."
        ),
        gap="",
        evidence=[
            _f("aegis/src/aegis/redteam/battery.py", "benign controls measure over-blocking"),
            _f("aegis/src/aegis/evals/regression.py", "declarative per-metric thresholds"),
            _r("POST /redteam/runs", "run the battery, keep the history"),
            _r("GET /security/posture", "status derived, never declared"),
            _t(
                "aegis/tests/redteam/test_redteam.py"
                "::test_control_category_blockrate_is_false_positive_rate",
                "the FP rate is real",
            ),
            _t(
                "aegis/tests/security/test_posture.py"
                "::test_no_threat_claimed_enforced_when_its_control_is_off",
                "a disabled control cannot report green",
            ),
        ],
    ),
    ControlEntry(
        id="MANAGE",
        title="Manage — prioritise, respond, recover",
        state=ControlState.ENFORCED,
        summary=(
            "A consequential action cannot execute alone: a durable human gate with SLA "
            "deadlines and fail-safe auto-rejection, a conformal abstain band that declines "
            "rather than acting on a degenerate prediction, budget refusal as a clean "
            "terminal event, and a tiered release path with rollback."
        ),
        gap="",
        evidence=[
            _f("backend/src/app/data/approvals.py", "the durable queue and its sweeper"),
            _f("aegis/src/aegis/ops/release.py", "tiered release with rollback"),
            _r("GET /approvals", "the human gate's inbox"),
            _t(
                "backend/tests/data/test_approvals.py"
                "::test_sla_sweeper_expires_and_auto_rejects_high",
                "fail-safe, not fail-open",
            ),
            _t(
                "aegis/tests/governance/test_enforcement.py::test_over_token_budget_raises",
                "the cap refuses before spend",
            ),
        ],
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# 5. ISO/IEC 42001:2023 — Annex A (AI management system)
# ─────────────────────────────────────────────────────────────────────────────

_ISO_42001: tuple[ControlEntry, ...] = (
    ControlEntry(
        id="A.2",
        title="Policies related to AI",
        state=ControlState.PARTIAL,
        summary="The security documents and nine ADRs carry the position in writing.",
        gap="There is no signed AI policy document and no review cycle.",
        evidence=[
            _d("docs/security/overview.md", "the written security position"),
            _d("docs/adr/0002-nemo-guardrails.md", "a recorded decision"),
        ],
    ),
    ControlEntry(
        id="A.3",
        title="Internal organization",
        state=ControlState.NOT_IMPLEMENTED,
        summary="Aegis has a system role model — which is access control, not AI governance.",
        gap=(
            "No organisational structure for AI concerns and no reporting line. The RBAC "
            "role catalogue and named seats govern who may use a surface, not who is "
            "accountable for the AI."
        ),
        evidence=[
            _f("aegis/src/aegis/settings/spec.py", "named seats — access, not accountability")
        ],
    ),
    ControlEntry(
        id="A.4",
        title="Resources for AI systems",
        state=ControlState.ENFORCED,
        summary=(
            "Every resource the system depends on is resolved from the running system, not "
            "a maintained list: the live SBOM tied to the module each component powers, the "
            "pipeline declarations, the model card and the settings catalogue."
        ),
        gap="",
        evidence=[
            _r("GET /stack", "components, versions, and the module each powers"),
            _r("GET /ml/model-card", "data source, training and calibration sizes"),
            _f("backend/src/app/platform/stack.py", "resolved from installed distributions"),
            _t(
                "backend/tests/api/test_platform_surfaces.py::test_stack_shape_and_real_versions",
                "no invented versions",
            ),
        ],
    ),
    ControlEntry(
        id="A.5",
        title="Assessing impacts of AI systems",
        state=ControlState.PARTIAL,
        summary=(
            "A real assessment of how the system can go wrong, with inherent and residual "
            "positions."
        ),
        gap=(
            "It assesses the system, not the impact on individuals or society, which is "
            "what this control asks for."
        ),
        evidence=[
            _r("GET /risk-map", "the system risk assessment"),
            _d("docs/security/owasp-agentic.md", "its written source"),
        ],
    ),
    ControlEntry(
        id="A.6",
        title="AI system life cycle",
        state=ControlState.PARTIAL,
        summary=(
            "Verification is genuine: an eval regression gate, an adversarial battery, a "
            "large test suite, a tiered release with rollback, and an ADR at each decision."
        ),
        gap="No documented per-release acceptance criteria beyond the metric thresholds.",
        evidence=[
            _f("aegis/src/aegis/evals/regression.py", "the CI-pattern regression gate"),
            _f("aegis/src/aegis/ops/release.py", "tiered release and rollback"),
            _f(
                "aegis/src/aegis/conformance/test_conformance.py",
                "every check descends from a real defect",
            ),
        ],
    ),
    ControlEntry(
        id="A.7",
        title="Data governance",
        state=ControlState.PARTIAL,
        summary=(
            "Write-time content validation, PII redaction on all three stages, per-tenant "
            "RLS, configurable retention horizons with the reason for the merge rule stated, "
            "and real hard erasure."
        ),
        gap="No provenance record for the ML training frame and no data classification scheme.",
        evidence=[
            _f(
                "aegis/src/aegis/memory/retention.py", "the one unconditional scheduled hard delete"
            ),
            _f("aegis/src/aegis/settings/spec.py", "memory.retention_days and its rationale"),
            _t(
                "aegis/tests/memory/test_crud.py::test_forget_fact_hard_removes_row_but_logs",
                "erasure is real",
            ),
        ],
    ),
    ControlEntry(
        id="A.8",
        title="Information for interested parties",
        state=ControlState.PARTIAL,
        summary=(
            "The model card, the generated OpenAPI contract, the receipt-under-every-figure "
            "discipline, and 29 teaching documents."
        ),
        gap="No external incident-reporting channel.",
        evidence=[
            _r("GET /ml/model-card", "what the model is and is not"),
            _d("docs/teaching/guardrails.md", "one of 29 teaching documents"),
            _f("backend/openapi.json", "the generated contract the frontend types derive from"),
        ],
    ),
    ControlEntry(
        id="A.9",
        title="Use of AI systems",
        state=ControlState.PARTIAL,
        summary=(
            "Human gate, conformal autonomy bands, per-persona tool allowlists, and "
            "per-tenant settings that show which scope decided each value."
        ),
        gap="No stated intended-use or prohibited-use documentation per deployment.",
        evidence=[
            _d("docs/adr/0007-conformal-autonomy-bands.md", "autonomous / defer / abstain"),
            _f("aegis/src/aegis/agent/graph.py", "the gate"),
        ],
    ),
    ControlEntry(
        id="A.10",
        title="Third-party and customer relationships",
        state=ControlState.PARTIAL,
        summary=(
            "One vetted model gateway, and an MCP peer registry where every external tool is "
            "HIGH risk until a platform admin lowers a named one, a disabled peer's tools "
            "leave the payload entirely, and whatever a peer returns passes the tool-result "
            "rail before it reaches a prompt."
        ),
        gap="No supplier assessment record.",
        evidence=[
            _d("docs/adr/0001-litellm-as-gateway.md", "the single chokepoint"),
            _f("backend/src/app/mcp/server.py", "peer tools gated HIGH"),
            _r("GET /mcp/console", "the declared peers and their tools"),
        ],
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# 6. ISO/IEC 27001:2022 — Annex A, the controls that actually map
#
# Deliberately not all 93. A control with no mechanism in this repository is not
# listed, because listing it would be padding a table rather than answering it.
# ─────────────────────────────────────────────────────────────────────────────

_ISO_27001: tuple[ControlEntry, ...] = (
    ControlEntry(
        id="A.5.15",
        title="Access control",
        state=ControlState.ENFORCED,
        summary="Role guards per route plus per-tenant row-level security beneath them.",
        gap="",
        evidence=[
            _f("aegis/src/aegis/governance/rls.py", "the tenant_isolation policy"),
            _t(
                "backend/tests/api/test_roles_rbac.py::test_require_devops_admits_only_devops",
                "the guards hold",
            ),
        ],
    ),
    ControlEntry(
        id="A.5.18",
        title="Access rights",
        state=ControlState.ENFORCED,
        summary=(
            "Named seats are revoke-only grants over the coarse role, and a defensive guard "
            "refuses to demote the last platform admin."
        ),
        gap="",
        evidence=[
            _f(
                "aegis/src/aegis/governance/enforcement.py", "the last-platform-admin lockout guard"
            ),
            _t(
                "backend/tests/api/test_roles_rbac.py::test_cannot_demote_last_platform_admin",
                "the guard fires",
            ),
        ],
    ),
    ControlEntry(
        id="A.5.34",
        title="Privacy and protection of PII",
        state=ControlState.ENFORCED,
        summary=(
            "Detection and masking on input, output and tool results, plus real "
            "erasure and bounded retention."
        ),
        gap="",
        evidence=[
            _f("aegis/src/aegis/guardrails/pii.py", "redact / scan"),
            _f("aegis/src/aegis/memory/retention.py", "the retention horizon"),
            _t(
                "backend/tests/memory/test_e2e.py::test_forget_hard_erases_and_audits",
                "erasure end to end",
            ),
        ],
    ),
    ControlEntry(
        id="A.8.2",
        title="Privileged access rights",
        state=ControlState.ENFORCED,
        summary=(
            "The serving role is provisioned NOSUPERUSER NOBYPASSRLS and owns no objects; "
            "the console role holds SELECT and nothing else."
        ),
        gap="",
        evidence=[
            _f("scripts/sql/aegis-app-role.sql", "the least-privilege serving role"),
            _f("scripts/sql/aegis-readonly-role.sql", "SELECT and nothing else"),
            _t(
                "aegis/tests/governance/test_rls_enforcement.py"
                "::test_grants_are_exactly_the_dml_the_serving_role_needs",
                "no privilege beyond the need",
            ),
        ],
    ),
    ControlEntry(
        id="A.8.3",
        title="Information access restriction",
        state=ControlState.ENFORCED,
        summary=(
            "FORCE ROW LEVEL SECURITY removes even the table owner's exemption, the tenant is "
            "bound per request, and the serving role's bypass status is audited at boot."
        ),
        gap="",
        evidence=[
            _f("aegis/src/aegis/governance/rls.py", "FORCE RLS + audit_rls_enforcement"),
            _t(
                "aegis/tests/governance/test_rls_enforcement.py"
                "::test_a_plain_login_role_is_reported_as_enforced",
                "the audit is real",
            ),
        ],
    ),
    ControlEntry(
        id="A.8.5",
        title="Secure authentication",
        state=ControlState.PARTIAL,
        summary="Argon2id password hashing and signed JWTs.",
        gap="No MFA, no lockout, no revocation.",
        evidence=[
            _f("aegis/src/aegis/governance/security.py", "hash_password / create_access_token")
        ],
    ),
    ControlEntry(
        id="A.8.8",
        title="Management of technical vulnerabilities",
        state=ControlState.PARTIAL,
        summary="Installed versions compared against the live registry, per package, best-effort.",
        gap=(
            "No advisory feed, so this is patch freshness rather than vulnerability "
            "management: an up-to-date package with a known CVE reports 'current'."
        ),
        evidence=[
            _r("POST /stack/patch-check", "installed vs latest"),
            _f("backend/src/app/platform/patches.py", "the honesty rule"),
        ],
    ),
    ControlEntry(
        id="A.8.12",
        title="Data leakage prevention",
        state=ControlState.ENFORCED,
        summary=(
            "PII masked before the model and before the user, an output content filter, and "
            "CSV/DDE neutralisation on every export."
        ),
        gap="",
        evidence=[
            _f("aegis/src/aegis/guardrails/pii.py", "both directions"),
            _f("aegis/src/aegis/reports/writer.py", "export neutralisation"),
            _t(
                "aegis/tests/reports/test_writer.py"
                "::test_a_string_that_looks_like_a_formula_is_neutralised",
                "the export is safe",
            ),
        ],
    ),
    ControlEntry(
        id="A.8.15",
        title="Logging",
        state=ControlState.ENFORCED,
        summary=(
            "An audit row per autonomous or approved action, plus end-to-end OpenTelemetry traces."
        ),
        gap="",
        evidence=[
            _f("aegis/src/aegis/governance/audit.py", "record_audit"),
            _r("GET /audit", "the trail"),
            _t(
                "aegis/tests/governance/test_audit.py::test_record_and_list_recent_audit",
                "the row is written and readable",
            ),
        ],
    ),
    ControlEntry(
        id="A.8.16",
        title="Monitoring activities",
        state=ControlState.PARTIAL,
        summary=(
            "A latency window, an ops diagnose surface, a durable notification inbox, and SLA "
            "sweeps."
        ),
        gap="No security alerting rules and no SIEM integration.",
        evidence=[
            _r("GET /latency", "percentiles from real samples"),
            _r("GET /notifications", "the durable inbox"),
        ],
    ),
    ControlEntry(
        id="A.8.24",
        title="Use of cryptography",
        state=ControlState.PARTIAL,
        summary="Password hashing and token signing.",
        gap="Nothing is encrypted at rest and there is no key management or rotation.",
        evidence=[_f("aegis/src/aegis/governance/security.py", "the whole of it")],
    ),
    ControlEntry(
        id="A.8.26",
        title="Application security requirements",
        state=ControlState.PARTIAL,
        summary=(
            "Typed contracts end to end, request models that forbid unknown fields, a "
            "generated OpenAPI the frontend types are derived from, and a large test suite."
        ),
        gap="No documented security requirements per feature.",
        evidence=[
            _f("backend/openapi.json", "the generated contract"),
            _t(
                "backend/tests/api/test_openapi_snapshot.py"
                "::test_the_committed_snapshot_is_the_served_schema",
                "the contract cannot drift",
            ),
        ],
    ),
    ControlEntry(
        id="A.8.28",
        title="Secure coding",
        state=ControlState.PARTIAL,
        summary=(
            "ADRs, a threat model, fail-closed defaults, and an executable conformance suite "
            "where every check descends from a defect this repository actually shipped."
        ),
        gap="No SAST and no dependency scanning. CI runs ruff and all three suites.",
        evidence=[
            _f(
                "aegis/src/aegis/conformance/test_conformance.py",
                "checks with scars, not checklists",
            ),
            _d("docs/security/threat-model.md", "the written model"),
        ],
    ),
    ControlEntry(
        id="A.8.31",
        title="Separation of development, test and production",
        state=ControlState.NOT_IMPLEMENTED,
        summary="No environment separation is expressed in this repository.",
        gap="One configuration, one host; there is nothing here that separates the three.",
    ),
    ControlEntry(
        id="A.8.32",
        title="Change management",
        state=ControlState.PARTIAL,
        summary=(
            "ADRs, a tiered LLM-Ops release path with rollback, a four-state prompt lifecycle, "
            "and a CI merge gate running ruff, all three test suites, the type check and the "
            "OpenAPI snapshot check on every push and pull request."
        ),
        gap=(
            "No change-approval record: nothing requires a reviewer before a merge, and no "
            "branch protection is configured here, so the CI gate reports rather than blocks."
        ),
        evidence=[
            _f("aegis/src/aegis/ops/release.py", "the release path"),
            _f(".github/workflows/ci.yml", "ruff, three suites, tsc, the OpenAPI snapshot"),
            _d("docs/teaching/ops.md", "the four-state lifecycle"),
        ],
    ),
    ControlEntry(
        id="A.5.7",
        title="Threat intelligence",
        state=ControlState.NOT_IMPLEMENTED,
        summary="There is no threat-intelligence feed of any kind in this system.",
        gap=(
            "The OWASP lists and the MLCommons hazard taxonomy are static references built "
            "into the battery, not intelligence about current threats."
        ),
    ),
    ControlEntry(
        id="A.5.23",
        title="Information security for use of cloud services",
        state=ControlState.NOT_APPLICABLE,
        summary="Aegis is deployed natively on a single host.",
        gap="No Docker, no cloud control plane and no managed service to configure.",
        evidence=[_d("INSTALL.md", "the native, no-Docker install")],
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# 7. EU AI Act (Regulation (EU) 2024/1689)
#
# Aegis is a domain-agnostic platform, not a deployed AI system: its risk class is
# decided by what a deployment does with it. These are the articles a limited- or
# high-risk deployment must satisfy, and what Aegis brings to that deployment.
# ─────────────────────────────────────────────────────────────────────────────

_EU_AI_ACT: tuple[ControlEntry, ...] = (
    ControlEntry(
        id="Art. 9",
        title="Risk management system",
        state=ControlState.PARTIAL,
        summary=(
            "A real risk assessment exists and is exercised adversarially against the running "
            "rails."
        ),
        gap="It is not a documented, iterative lifecycle RMS with review triggers.",
        evidence=[
            _r("GET /risk-map", "the assessment"),
            _r("POST /redteam/runs", "the adversarial exercise"),
        ],
    ),
    ControlEntry(
        id="Art. 10",
        title="Data and data governance",
        state=ControlState.PARTIAL,
        summary=(
            "Write-time validation, retention horizons, erasure, PII redaction and tenant "
            "isolation."
        ),
        gap="No training-data governance record and no bias examination.",
        evidence=[
            _f("aegis/src/aegis/retrieval/validation.py", "the write-time gate"),
            _f("aegis/src/aegis/memory/retention.py", "bounded retention"),
        ],
    ),
    ControlEntry(
        id="Art. 11",
        title="Technical documentation (Annex IV)",
        state=ControlState.PARTIAL,
        summary=(
            "A model card, an architecture document, nine ADRs, verified pipeline "
            "declarations, a generated API contract and 29 teaching documents."
        ),
        gap="Substantial, but not assembled in Annex IV form.",
        evidence=[
            _r("GET /ml/model-card", "the model card"),
            _d("docs/architecture/memory-spec.md", "a component specification"),
            _r("GET /pipelines", "flows verified against the code before they are served"),
        ],
    ),
    ControlEntry(
        id="Art. 12",
        title="Record-keeping (automatic logging)",
        state=ControlState.ENFORCED,
        summary=(
            "Every autonomous or approved action writes a row carrying actor, model, "
            "trace_id, payload, approver and tenant; runs are traces; every call is on the "
            "usage ledger."
        ),
        gap="",
        evidence=[
            _f("aegis/src/aegis/governance/audit.py", "record_audit"),
            _f("aegis/src/aegis/observability/otel.py", "trace_id on every run"),
            _r("GET /audit", "the readable trail"),
            _t(
                "aegis/tests/governance/test_audit.py::test_record_audit_pulls_tenant_from_context",
                "the row carries its tenant",
            ),
        ],
    ),
    ControlEntry(
        id="Art. 13",
        title="Transparency and provision of information to deployers",
        state=ControlState.ENFORCED,
        summary=(
            "The product's core discipline: a receipt under every figure, a stated absence "
            "where a figure cannot be sourced, a control_ref on every risk row, importable "
            "symbols behind every posture status, and this page's own refusal to claim "
            "certification."
        ),
        gap="",
        evidence=[
            _f("web/src/components/primitives/Receipt.tsx", "the origin under every figure"),
            _f("aegis/src/aegis/security/posture.py", "refs[] — importable symbols per status"),
            _t(
                "aegis/tests/security/test_posture.py::test_all_refs_are_importable",
                "every reference resolves",
            ),
        ],
    ),
    ControlEntry(
        id="Art. 14",
        title="Human oversight",
        state=ControlState.ENFORCED,
        summary=(
            "A consequential action cannot execute alone: the graph interrupts and waits for "
            "a named person, the inbox shows what approving would run and why the gate fired, "
            "the SLA sweeper auto-rejects on timeout, and the abstain band declines rather "
            "than acting on a degenerate prediction."
        ),
        gap="",
        evidence=[
            _f("aegis/src/aegis/agent/graph.py", "the interrupt"),
            _f("backend/src/app/data/approvals.py", "the durable queue and sweeper"),
            _r("GET /approvals", "the human's inbox"),
            _t(
                "backend/tests/data/test_approvals.py"
                "::test_sla_sweeper_expires_and_auto_rejects_high",
                "silence is a refusal, not consent",
            ),
        ],
    ),
    ControlEntry(
        id="Art. 15",
        title="Accuracy, robustness and cybersecurity",
        state=ControlState.PARTIAL,
        summary=(
            "Accuracy is measured, not claimed — empirical conformal coverage on a held-out "
            "split, per-metric eval thresholds — and robustness is measured by the battery "
            "with a false-positive control beside the block rate."
        ),
        gap=(
            "Adversarial robustness of the ML spine is unmeasured, and there is no "
            "production-time accuracy monitoring."
        ),
        evidence=[
            _r("GET /ml/model-card", "empirical coverage, null when nothing was held out"),
            _r("POST /redteam/runs", "the measured block and false-positive rates"),
            _f("aegis/src/aegis/evals/regression.py", "the per-metric gate"),
        ],
    ),
    ControlEntry(
        id="Art. 17",
        title="Quality management system",
        state=ControlState.NOT_IMPLEMENTED,
        summary="There is no quality management system of any kind.",
        gap="No documented quality management system of any kind.",
    ),
    ControlEntry(
        id="Art. 50",
        title="Transparency obligations for certain AI systems",
        state=ControlState.NOT_IMPLEMENTED,
        summary="Generated content is not marked as AI-generated.",
        gap="No synthetic-content disclosure and no watermarking on any output path.",
    ),
    ControlEntry(
        id="Art. 72",
        title="Post-market monitoring",
        state=ControlState.PARTIAL,
        summary=(
            "Latency, analytics, notifications and the audit trail give a deployment the telemetry."
        ),
        gap="There is no post-market monitoring plan.",
        evidence=[
            _r("GET /latency", "the operational window"),
            _r("GET /audit", "the action record"),
        ],
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# 8. SOC 2 — AICPA Trust Services Criteria
# ─────────────────────────────────────────────────────────────────────────────

_SOC2: tuple[ControlEntry, ...] = (
    ControlEntry(
        id="CC4.1",
        title="Monitoring of controls",
        state=ControlState.PARTIAL,
        summary=(
            "Unusually strong: control status is re-derived from live wiring on every "
            "request, and a test asserts that no threat can be claimed enforced while its "
            "control is off — so a disabled control cannot keep reporting green."
        ),
        gap="There is no independent review of control effectiveness.",
        evidence=[
            _r("GET /security/posture", "derived, never declared"),
            _t(
                "aegis/tests/security/test_posture.py"
                "::test_no_threat_claimed_enforced_when_its_control_is_off",
                "the guard itself",
            ),
        ],
    ),
    ControlEntry(
        id="CC6.1",
        title="Logical access — security software and infrastructure",
        state=ControlState.ENFORCED,
        summary=(
            "RBAC route guards, per-tenant RLS, and least-privilege database roles beneath both."
        ),
        gap="",
        evidence=[
            _f("scripts/sql/aegis-app-role.sql", "the serving role"),
            _f("aegis/src/aegis/governance/rls.py", "the isolation policy"),
            _t(
                "backend/tests/data/test_rls_serving_role.py"
                "::test_a_bypassing_serving_role_refuses_to_boot_outside_dev",
                "a bypassing role cannot serve",
            ),
        ],
    ),
    ControlEntry(
        id="CC6.2",
        title="Registration and authorisation of users",
        state=ControlState.PARTIAL,
        summary=(
            "Every grant change writes an audit row, and the last-platform-admin lockout is "
            "guarded."
        ),
        gap="No periodic access review and no joiner/mover/leaver process.",
        evidence=[
            _r("GET /governance/dashboard", "tenants, users and grants"),
            _f("aegis/src/aegis/governance/enforcement.py", "the lockout guard"),
        ],
    ),
    ControlEntry(
        id="CC6.6",
        title="Boundary protection",
        state=ControlState.PARTIAL,
        summary=(
            "A single model gateway chokepoint and an explicitly declared, risk-tiered MCP peer "
            "registry."
        ),
        gap="The unvalidated peer URL recorded under OWASP A01:2025 is the hole in this boundary.",
        evidence=[
            _f("backend/src/app/mcp/client.py", "the peer dial"),
            _r("GET /mcp/console", "the declared peers and their tools"),
        ],
    ),
    ControlEntry(
        id="CC6.7",
        title="Restricting the transmission and removal of information",
        state=ControlState.PARTIAL,
        summary=(
            "PII redaction on every path, real hard erasure, export sanitisation, and peer "
            "credentials that are never persisted or returned by any route."
        ),
        gap="No DLP on exports beyond formula neutralisation.",
        evidence=[
            _f("aegis/src/aegis/reports/writer.py", "export neutralisation"),
            _t(
                "backend/tests/memory/test_e2e.py::test_delete_single_fact_erases_and_audits",
                "removal is real and recorded",
            ),
        ],
    ),
    ControlEntry(
        id="CC7.1",
        title="Vulnerability identification",
        state=ControlState.PARTIAL,
        summary="Registry-verified dependency freshness across the tracked stack.",
        gap="No vulnerability scanning and no advisory feed.",
        evidence=[_r("POST /stack/patch-check", "installed vs latest")],
    ),
    ControlEntry(
        id="CC7.2",
        title="Monitoring for anomalies",
        state=ControlState.PARTIAL,
        summary=(
            "A durable notification inbox, SLA sweeps, and budget refusals as clean terminal "
            "events."
        ),
        gap="No security-specific alerting rules.",
        evidence=[
            _r("GET /notifications", "the inbox"),
            _f("backend/src/app/data/approvals.py", "SLA events"),
        ],
    ),
    ControlEntry(
        id="CC7.4",
        title="Incident response",
        state=ControlState.NOT_IMPLEMENTED,
        summary="There is no incident-response procedure.",
        gap="No documented detection, triage, containment or communication process.",
    ),
    ControlEntry(
        id="CC8.1",
        title="Change management",
        state=ControlState.PARTIAL,
        summary=(
            "ADRs, a tiered release path with rollback, and three test suites "
            "(aegis, backend, web) run before a change ships."
        ),
        gap=(
            "No change-approval record: CI now runs those suites on every push and pull "
            "request, but nothing requires a reviewer and no branch protection is configured "
            "here, so the gate reports rather than blocks."
        ),
        evidence=[
            _f("aegis/src/aegis/ops/release.py", "tiered release"),
            _d("docs/adr/0009-embedded-vector-store.md", "a recorded change"),
        ],
    ),
    ControlEntry(
        id="A1.2",
        title="Availability — backup and recovery",
        state=ControlState.NOT_IMPLEMENTED,
        summary="No backup or restore procedure exists in this repository.",
        gap="Durable execution survives a worker kill, but that is resumption, not recovery.",
    ),
    ControlEntry(
        id="C1.1",
        title="Confidentiality — identification and protection",
        state=ControlState.PARTIAL,
        summary="Tenant isolation, redaction and erasure protect confidential data in use.",
        gap="No data classification scheme and no encryption at rest.",
        evidence=[_f("aegis/src/aegis/governance/rls.py", "the tenant boundary")],
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# 9. GDPR (EU 2016/679) and India's DPDP Act 2023
# ─────────────────────────────────────────────────────────────────────────────

_PRIVACY: tuple[ControlEntry, ...] = (
    ControlEntry(
        id="GDPR Art. 5(1)(c)",
        title="Data minimisation",
        state=ControlState.PARTIAL,
        summary=(
            "PII is redacted before the model and before the classifier call, only "
            "minimum-necessary context is injected per request, and retention horizons bound "
            "what is kept — with 0 documented as what a legal hold looks like."
        ),
        gap="No formal minimisation review per stored field.",
        evidence=[
            _f("aegis/src/aegis/guardrails/pii.py", "redaction before the model"),
            _f("aegis/src/aegis/settings/spec.py", "memory.retention_days, default 90"),
        ],
    ),
    ControlEntry(
        id="GDPR Art. 5(1)(b)",
        title="Purpose limitation",
        state=ControlState.NOT_IMPLEMENTED,
        summary="Nothing binds stored data to a declared purpose.",
        gap="No purpose register and no purpose field on any stored record.",
    ),
    ControlEntry(
        id="GDPR Art. 15",
        title="Right of access",
        state=ControlState.PARTIAL,
        summary=(
            "A subject's stored facts, profile and sessions are readable, and four CSV exports "
            "exist."
        ),
        gap="There is no single subject-access-request export endpoint.",
        evidence=[
            _r("GET /memory/facts", "what the system believes about a person"),
            _r("GET /reports/audit.csv", "an export path"),
        ],
    ),
    ControlEntry(
        id="GDPR Art. 16",
        title="Right to rectification",
        state=ControlState.ENFORCED,
        summary=(
            "A stored belief can be corrected, and the belief timeline retains the "
            "supersession rather than silently rewriting history."
        ),
        gap="",
        evidence=[
            _f("aegis/src/aegis/memory/crud.py", "correction with a bitemporal trail"),
            _r("PATCH /memory/facts/{fact_id}", "the correction endpoint"),
            _t(
                "backend/tests/api/test_memory_control.py"
                "::test_a_correction_supersedes_the_old_fact_and_names_the_person",
                "who corrected it is recorded",
            ),
        ],
    ),
    ControlEntry(
        id="GDPR Art. 17",
        title="Right to erasure",
        state=ControlState.ENFORCED,
        summary=(
            "A real hard delete, subject- and tenant-scoped and audited, plus one scheduled "
            "unconditional hard delete of raw conversation turns past the retention horizon."
        ),
        gap="",
        evidence=[
            _f("aegis/src/aegis/memory/crud.py", "forget_fact(hard=True)"),
            _f("aegis/src/aegis/memory/retention.py", "the scheduled sweep"),
            _r("POST /memory/forget", "the erasure endpoint"),
            _t(
                "backend/tests/memory/test_e2e.py::test_forget_hard_erases_and_audits",
                "the row is gone and the act is recorded",
            ),
        ],
    ),
    ControlEntry(
        id="GDPR Art. 30",
        title="Records of processing activities",
        state=ControlState.NOT_IMPLEMENTED,
        summary="The audit log records actions, which is not a ROPA.",
        gap="No record of processing purposes, categories, recipients or transfers.",
    ),
    ControlEntry(
        id="GDPR Art. 32",
        title="Security of processing",
        state=ControlState.PARTIAL,
        summary=(
            "Tenant isolation, least-privilege database roles, redaction, audit and guardrails."
        ),
        gap=(
            "No encryption at rest, no MFA, and no tested restore — the gaps listed under ISO "
            "27001."
        ),
        evidence=[_f("aegis/src/aegis/governance/rls.py", "the enforced boundary")],
    ),
    ControlEntry(
        id="GDPR Art. 33",
        title="Breach notification",
        state=ControlState.NOT_IMPLEMENTED,
        summary="No breach-notification procedure.",
        gap="No detection-to-notification path and no 72-hour process.",
    ),
    ControlEntry(
        id="GDPR Art. 35",
        title="Data protection impact assessment",
        state=ControlState.NOT_IMPLEMENTED,
        summary="The risk map is a system-risk assessment, not a DPIA.",
        gap="No assessment of processing risk to data subjects.",
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# 10. India — Digital Personal Data Protection Act 2023 + DPDP Rules 2025
#
# The home market's own law, and the only framework on this page whose obligations
# arrive on a statutory clock: the Rules were notified 14 November 2025 and phase in to
# full effect on 13 May 2027, at which point consent notices, data-principal rights,
# breach reporting and Significant Data Fiduciary duties all bind at once.
#
# Two rows here are genuinely strong, because Aegis built erasure and correction for
# engineering reasons long before anyone mapped them to a section number. The rest of
# the Act is paperwork Aegis does not have, and saying so is the point: a padded DPDP
# table is the one defect an Indian reviewer would catch fastest.
# ─────────────────────────────────────────────────────────────────────────────

_DPDP: tuple[ControlEntry, ...] = (
    ControlEntry(
        id="s.5 / s.6",
        title="Notice and consent",
        state=ControlState.NOT_IMPLEMENTED,
        summary="There is no consent artefact anywhere in the system.",
        gap=(
            "No itemised notice, no consent record with a purpose attached, no withdrawal "
            "path, and no Consent Manager integration. Rule 3's notice content and Rule 4's "
            "Consent Manager register are both unaddressed. Consent is the deployment's to "
            "obtain; Aegis stores nothing that would prove it was."
        ),
    ),
    ControlEntry(
        id="s.8(1)-(3)",
        title="Data Fiduciary accountability and accuracy",
        state=ControlState.PARTIAL,
        summary=(
            "A stored belief carries who asserted it, who corrected it and when it was "
            "superseded, and every ingested chunk passes a write-time validity gate before "
            "it can become evidence."
        ),
        gap=(
            "No processor agreement, no register of what is processed for which purpose, and "
            "no reconciliation between what a Data Principal supplied and what the system "
            "later inferred. Accountability is per-record, not per-processing-activity."
        ),
        evidence=[
            _f("aegis/src/aegis/memory/crud.py", "bitemporal supersession, never overwrite"),
            _f("aegis/src/aegis/retrieval/validation.py", "validate_content — the write-time gate"),
            _r("GET /memory/writes", "why the agent believes what it believes"),
        ],
    ),
    ControlEntry(
        id="s.8(4)-(5)",
        title="Technical, organisational and security safeguards (Rule 6)",
        state=ControlState.PARTIAL,
        summary=(
            "Per-tenant row-level security with FORCE RLS and a NOSUPERUSER NOBYPASSRLS "
            "serving role, Argon2id password hashing, PII detection and masking before the "
            "model and before the answer, and a guardrail pipeline on all three rails."
        ),
        gap=(
            "Rule 6 names encryption, obfuscation, masking and virtual tokens as the "
            "safeguards it expects. Masking is real; encryption at rest is absent across "
            "every store, there is no key management or rotation, and authentication has no "
            "MFA. Access control is strong; cryptographic protection of stored data is not."
        ),
        evidence=[
            _f("aegis/src/aegis/governance/rls.py", "FORCE RLS + the boot-time bypass audit"),
            _f(
                "aegis/src/aegis/guardrails/pii.py",
                "redact / scan on input, output and tool results",
            ),
            _f("aegis/src/aegis/governance/security.py", "Argon2id — and the whole of the crypto"),
            _t(
                "aegis/tests/governance/test_rls_enforcement.py"
                "::test_a_bypassing_role_stops_the_process_when_the_check_is_fatal",
                "a role that could read across tenants halts the boot",
            ),
        ],
    ),
    ControlEntry(
        id="s.8(6)",
        title="Personal data breach intimation (Rule 7)",
        state=ControlState.NOT_IMPLEMENTED,
        summary="There is no breach-notification path of any kind.",
        gap=(
            "Rule 7 requires intimation to every affected Data Principal without delay and a "
            "detailed report to the Data Protection Board within 72 hours. Aegis has a durable "
            "notification inbox for approvals and SLA events, and nothing that classifies an "
            "event as a personal-data breach or routes it anywhere outside the tenant."
        ),
    ),
    ControlEntry(
        id="s.8(7)",
        title="Storage limitation — erasure once the purpose is served (Rule 8)",
        state=ControlState.PARTIAL,
        summary=(
            "One module performs an unconditional, scheduled hard delete: raw conversation "
            "turns past the episodic horizon and facts that have been closed past their own, "
            "with the horizons configurable per tenant and 0 documented as what a legal hold "
            "looks like. The sweep is inspectable and runnable from the API."
        ),
        gap=(
            "The horizon is a timer, not a purpose test — Rule 8 asks for erasure when the "
            "specified purpose is no longer being served, and nothing in the system records a "
            "purpose to test against. Rule 8's advance intimation to the Data Principal before "
            "erasure does not exist. And the sweep is deliberately narrow: audit_log, the "
            "usage ledger and memory_write_log are never swept, so personal data referenced in "
            "those rows outlives the horizon by design."
        ),
        evidence=[
            _f(
                "aegis/src/aegis/memory/retention.py",
                "the one unconditional scheduled hard delete",
            ),
            _f("aegis/src/aegis/settings/spec.py", "memory.retention_days (90) and its merge rule"),
            _r("GET /memory/retention", "what the horizon would remove, before it removes it"),
            _r("POST /memory/retention/sweep", "the sweep itself"),
        ],
    ),
    ControlEntry(
        id="s.9",
        title="Children's personal data and verifiable parental consent",
        state=ControlState.NOT_IMPLEMENTED,
        summary="Nothing in the system knows or asks how old a Data Principal is.",
        gap=(
            "No age signal, no verifiable parental consent gate, and no switch that would "
            "disable behavioural tracking or targeted advertising for a child — which Aegis "
            "does not do, but cannot demonstrate it does not do per-subject. Rule 10's "
            "verification mechanism is entirely absent."
        ),
    ),
    ControlEntry(
        id="s.10",
        title="Significant Data Fiduciary — DPIA, audit, DPO (Rule 13)",
        state=ControlState.NOT_IMPLEMENTED,
        summary="None of the three additional obligations exists.",
        gap=(
            "No annual Data Protection Impact Assessment, no independent data auditor and no "
            "named Data Protection Officer based in India. The risk map is a system-risk "
            "assessment and is explicitly not a DPIA. Rule 13's algorithmic-due-diligence "
            "verification — that algorithmic software does not risk Data Principals' rights — "
            "is the closest thing Aegis has evidence for, and even that is unassessed."
        ),
        evidence=[_r("GET /risk-map", "a system-risk assessment, and not a DPIA")],
    ),
    ControlEntry(
        id="s.11",
        title="Right to access information about personal data",
        state=ControlState.PARTIAL,
        summary=(
            "What the system holds about a subject is readable end to end: the facts it "
            "believes, the profile it derived, the sessions and turns behind them, the write "
            "log explaining each belief, and four CSV exports."
        ),
        gap=(
            "s.11 asks for a summary of the personal data being processed and the processing "
            "activities undertaken plus the identities of other Fiduciaries it was shared "
            "with. Aegis serves the data; it does not serve the summary, and it holds no "
            "record of recipients. There is no single subject-access-request export."
        ),
        evidence=[
            _r("GET /memory/facts", "what the system believes about a person"),
            _r("GET /memory/profile", "the derived profile"),
            _r("GET /memory/writes", "why each belief is held"),
            _r("GET /reports/audit.csv", "an export path"),
        ],
    ),
    ControlEntry(
        id="s.12(1)-(2)",
        title="Right to correction and completion",
        state=ControlState.ENFORCED,
        summary=(
            "A stored belief can be corrected through a real endpoint, and the belief timeline "
            "retains the supersession with the name of the person who made it rather than "
            "silently rewriting history — so a correction is itself auditable, which is what "
            "makes it defensible to the Data Principal who asked for it."
        ),
        evidence=[
            _f("aegis/src/aegis/memory/crud.py", "correction with a bitemporal trail"),
            _r("PATCH /memory/facts/{fact_id}", "the correction endpoint"),
            _t(
                "backend/tests/api/test_memory_control.py"
                "::test_a_correction_supersedes_the_old_fact_and_names_the_person",
                "who corrected it is on the record",
            ),
        ],
    ),
    ControlEntry(
        id="s.12(3)",
        title="Right to erasure",
        state=ControlState.ENFORCED,
        summary=(
            "Erasure is a real hard delete, not a soft flag: the row is gone, the act is "
            "audited, and it is scoped to one subject within one tenant. The rest of the "
            "memory package deliberately archives rather than deletes — this is the one seam "
            "that removes, and it is the seam a Data Principal's request lands on."
        ),
        evidence=[
            _f("aegis/src/aegis/memory/crud.py", "forget_fact(hard=True)"),
            _r("POST /memory/forget", "the erasure endpoint"),
            _r("DELETE /memory/facts/{fact_id}", "single-fact erasure"),
            _t(
                "backend/tests/memory/test_e2e.py::test_forget_hard_erases_and_audits",
                "the row is gone and the act is recorded",
            ),
            _t(
                "aegis/tests/memory/test_crud.py::test_forget_fact_hard_removes_row_but_logs",
                "hard means hard",
            ),
        ],
    ),
    ControlEntry(
        id="s.13",
        title="Right of grievance redressal",
        state=ControlState.NOT_IMPLEMENTED,
        summary="There is no grievance channel for a Data Principal.",
        gap=(
            "Rule 9 requires a published grievance mechanism and a response period the "
            "Fiduciary states. Aegis publishes no contact point, has no complaint record, and "
            "runs no clock. The notification inbox is an operator surface, not a data-principal "
            "one, and calling it grievance redressal would be the padding this page refuses."
        ),
    ),
    ControlEntry(
        id="s.16",
        title="Transfer of personal data outside India",
        state=ControlState.PARTIAL,
        summary=(
            "This is the strongest structural answer Aegis has for India, and it is measured "
            "rather than asserted: every destination the deployment can reach is derived from "
            "live configuration on each read. Postgres, Qdrant, Neo4j and Redis all resolve to "
            "this host, so tenant documents, embeddings, the knowledge graph, memory and the "
            "audit trail are at rest inside the deployment. One channel carries content out — "
            "the model gateway — and the inventory names it rather than rounding it away."
        ),
        gap=(
            "The gateway is deployment-configured, and nothing in code refuses an offshore "
            "one: prompts after redaction, completions, and the chunk text sent for embedding "
            "all travel to whatever GATEWAY_BASE_URL names. In this repository's own run file "
            "that is an Azure endpoint in a US region, not an Indian one. Web search sends the "
            "composed query to Tavily when a key is set. There is no transfer-impact assessment "
            "and no contractual safeguard — and the surface reports where a host is addressed, "
            "not where a cloud region physically sits."
        ),
        evidence=[
            _f("backend/src/app/platform/residency.py", "the destination inventory, derived"),
            _f("aegis/src/aegis/gateway/llm.py", "the single chokepoint every model call uses"),
            _f("aegis/src/aegis/websearch/tavily.py", "the query-only web-search egress"),
            _r("GET /compliance", "the inventory travels with this map"),
            _t(
                "backend/tests/api/test_residency.py::test_an_offshore_store_is_reported_external",
                "moving a store offshore flips the verdict",
            ),
            _t(
                "backend/tests/api/test_residency.py"
                "::test_the_local_deployment_keeps_every_store_on_the_host",
                "the local claim, asserted rather than written",
            ),
        ],
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# 11. CERT-In Directions, 28 April 2022 (No. 20(3)/2022-CERT-In, s.70B IT Act)
#
# Six directions, binding on every body corporate serving users in India. Only one of
# them has a mechanism in this repository, and the honest half of that one is worth more
# than a table of green cells: "nothing deletes the audit trail" satisfies a 180-day
# window by accident, not by control, and this section says which.
# ─────────────────────────────────────────────────────────────────────────────

_CERT_IN: tuple[ControlEntry, ...] = (
    ControlEntry(
        id="Dir. (i)",
        title="Clock synchronisation to NIC or NPL NTP",
        state=ControlState.NOT_IMPLEMENTED,
        summary="Nothing in this repository configures or asserts a time source.",
        gap=(
            "Every timestamp is taken in UTC from the host clock or from the database's own "
            "now(), which is internally consistent and completely silent about provenance. The "
            "Direction requires synchronisation to the NIC or NPL servers, or to a source "
            "traceable to them, and there is no configuration, check or startup assertion for "
            "it. A deployment can satisfy this at the OS level; Aegis cannot show that it did."
        ),
    ),
    ControlEntry(
        id="Dir. (ii)",
        title="Report cyber incidents to CERT-In within 6 hours",
        state=ControlState.NOT_IMPLEMENTED,
        summary="No incident is classified, and nothing is reported to anyone outside the tenant.",
        gap=(
            "The six-hour clock starts on noticing an incident. Aegis has the raw material — an "
            "audit trail, SLA events, budget refusals, guardrail blocks and a durable inbox — "
            "and none of the three things the Direction actually needs: a definition of which "
            "of those constitutes a reportable incident, a person accountable for the clock, "
            "and a path to CERT-In's reporting form."
        ),
        evidence=[_r("GET /notifications", "raw material, not an incident process")],
    ),
    ControlEntry(
        id="Dir. (iii)",
        title="Designated point of contact and response to CERT-In orders",
        state=ControlState.NOT_IMPLEMENTED,
        summary="No point of contact is named anywhere in the system or its documentation.",
        gap=(
            "The Direction requires a named point of contact registered with CERT-In and a duty "
            "to furnish information when directed. This is an organisational obligation the "
            "deployment must meet; nothing in this repository carries it, and ISO/IEC 42001 A.3 "
            "records the same absence from the other side."
        ),
    ),
    ControlEntry(
        id="Dir. (iv)",
        title="Maintain ICT system logs for 180 days, within Indian jurisdiction",
        state=ControlState.PARTIAL,
        summary=(
            "Both halves have a real answer. Volume: every autonomous or approved action "
            "writes an audit row carrying actor, model, trace_id, payload, approver and tenant; "
            "runs are OpenTelemetry traces; every model call is on the usage ledger. Place: "
            "the derived residency inventory shows the log stores resolving to this host, and "
            "the memory retention sweep is deliberately written never to touch audit_log, the "
            "usage ledger or the write log."
        ),
        gap=(
            "The 180-day window is met by the absence of a deleter, not by a control: there "
            "is no retention policy on audit_log, no partitioning, no archival job and no test "
            "that a row 180 days old is still there. Append-only IS now a database guarantee "
            "against the serving role — it holds SELECT, INSERT on audit_log, usage_ledger and "
            "run_events and their partitions, so DELETE FROM audit_log is refused on the "
            "connection every request arrives on — but not against the owner connection, which "
            "still holds full DML. The jurisdiction half is a live read of "
            "configuration, not a guarantee — re-point POSTGRES_DSN offshore and the logs "
            "follow it, which is exactly what the inventory would then say."
        ),
        evidence=[
            _f("aegis/src/aegis/governance/audit.py", "record_audit — one row per action"),
            _f(
                "aegis/src/aegis/memory/retention.py",
                "the sweep that deliberately never touches the audit trail",
            ),
            _f("backend/src/app/platform/residency.py", "where the log stores actually resolve"),
            _f(
                "aegis/src/aegis/governance/rls.py",
                "_APPEND_ONLY_TABLES — SELECT, INSERT on the ledgers",
            ),
            _r("GET /audit", "the trail, tenant-scoped in SQL"),
            _t(
                "aegis/tests/governance/test_audit.py::test_record_audit_pulls_tenant_from_context",
                "the row carries its tenant",
            ),
            _t(
                "backend/tests/api/test_residency.py"
                "::test_the_local_deployment_keeps_every_store_on_the_host",
                "the stores are on the host, asserted",
            ),
        ],
    ),
    ControlEntry(
        id="Dir. (v)-(vi)",
        title="Subscriber KYC and transaction records (service providers, virtual assets)",
        state=ControlState.NOT_APPLICABLE,
        summary=(
            "These bind data centres, VPS, cloud and VPN providers, and virtual-asset service "
            "providers — none of which Aegis is."
        ),
        gap=(
            "Aegis rents nobody infrastructure and custodies no virtual asset, so there is no "
            "subscriber to KYC and no transaction record to keep for five years. Recorded "
            "rather than omitted, because a reader checking the Directions will count six and "
            "should find all six answered."
        ),
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# 12. India — MeitY, RBI, SEBI and BIS
#
# Grouped on purpose. Four thin tables would each be mostly "not applicable"; one table
# answers the question a jury actually asks, which is "what does this mean for an Indian
# deployment, and for a bank". The BFSI rows are deliberately not_applicable with the
# inheritance spelled out: Aegis has earned no BFSI compliance and must not imply it.
# ─────────────────────────────────────────────────────────────────────────────

_INDIA_SECTORAL: tuple[ControlEntry, ...] = (
    ControlEntry(
        id="IAGG · Accountability",
        title="MeitY India AI Governance Guidelines — accountability across the AI value chain",
        state=ControlState.PARTIAL,
        summary=(
            "The Guidelines ask that developers and deployers stay identifiable and "
            "accountable. Every autonomous or approved action names its actor, its model and "
            "its trace; every figure on the product carries a receipt naming where it came "
            "from; and control status is re-derived from live wiring rather than declared, so "
            "a switched-off control cannot keep reporting green."
        ),
        gap=(
            "Accountability is technical, not organisational: no accountable-role register for "
            "the deployment, no self-assessment against the Guidelines, and no grievance path. "
            "The Guidelines are voluntary and graded — nobody has graded this."
        ),
        evidence=[
            _f("aegis/src/aegis/governance/audit.py", "actor, model and trace on every action"),
            _f("web/src/components/primitives/Receipt.tsx", "the origin under every figure"),
            _r("GET /security/posture", "derived from wiring, never declared"),
            _t(
                "aegis/tests/security/test_posture.py"
                "::test_no_threat_claimed_enforced_when_its_control_is_off",
                "a disabled control cannot report green",
            ),
        ],
    ),
    ControlEntry(
        id="IAGG · Transparency",
        title="MeitY India AI Governance Guidelines — transparency and explainability",
        state=ControlState.PARTIAL,
        summary=(
            "Transparency about the system's own controls is unusually strong: importable "
            "symbols behind every posture status, a control_ref on every risk row, a stated "
            "absence where a figure cannot be sourced, and a model card that reports empirical "
            "coverage on a held-out split rather than a claimed accuracy."
        ),
        gap=(
            "Transparency to the operator is real; transparency to the end user is not. "
            "Generated content is not marked as AI-generated on any output path — the same gap "
            "the EU AI Act Art. 50 row records — and there is no user-facing disclosure that an "
            "AI system is being interacted with."
        ),
        evidence=[
            _f("aegis/src/aegis/security/posture.py", "refs[] — importable symbols per status"),
            _r("GET /ml/model-card", "measured, not claimed"),
            _t(
                "aegis/tests/security/test_posture.py::test_all_refs_are_importable",
                "every reference resolves",
            ),
        ],
    ),
    ControlEntry(
        id="IAGG · Oversight",
        title="MeitY India AI Governance Guidelines — human oversight and redressal",
        state=ControlState.PARTIAL,
        summary=(
            "The oversight half is the strongest control on this platform: a consequential "
            "action interrupts the run and waits for a named person, the inbox shows what "
            "approving would actually run, and the SLA sweeper auto-rejects on timeout, so "
            "silence is a refusal rather than consent."
        ),
        gap=(
            "The Guidelines pair oversight with grievance redressal, and the redressal half is "
            "absent — no channel, no complaint record, no response clock. Same gap as DPDP s.13, "
            "recorded here because this framework asks for both together."
        ),
        evidence=[
            _f("aegis/src/aegis/agent/graph.py", "the interrupt at gate_min_risk"),
            _f("backend/src/app/data/approvals.py", "the durable queue and its sweeper"),
            _r("GET /approvals", "the human's inbox"),
            _t(
                "backend/tests/data/test_approvals.py"
                "::test_sla_sweeper_expires_and_auto_rejects_high",
                "silence is a refusal, not consent",
            ),
        ],
    ),
    ControlEntry(
        id="IAGG · Safety",
        title="MeitY India AI Governance Guidelines — risk mitigation by design",
        state=ControlState.PARTIAL,
        summary=(
            "Guardrails on all three rails, an adversarial battery that reports a "
            "false-positive rate beside its block rate, and an inherent-versus-residual risk "
            "map where no risk is ever marked solved."
        ),
        gap=(
            "The hazard taxonomy behind the battery is MLCommons and OWASP — there is no "
            "India-specific harm set, no Indian-language adversarial probe, and no evaluation "
            "against the deepfake and synthetic-content harms the Guidelines emphasise."
        ),
        evidence=[
            _f("aegis/src/aegis/redteam/battery.py", "28 attacks and 8 benign controls"),
            _f("backend/src/app/platform/risk_map.py", "inherent and residual, per risk"),
            _t(
                "aegis/tests/redteam/test_redteam.py"
                "::test_control_category_blockrate_is_false_positive_rate",
                "the false-positive rate is real",
            ),
        ],
    ),
    ControlEntry(
        id="IS 17428-1",
        title="BIS IS 17428 Part 1 — data privacy assurance (engineering and management)",
        state=ControlState.PARTIAL,
        summary=(
            "The engineering half of Part 1 has real mechanisms: privacy by design in the "
            "redaction rails, tenant isolation enforced in the database, bounded retention "
            "with a scheduled hard delete, correction and erasure endpoints, and least-"
            "privilege database roles."
        ),
        gap=(
            "The management half is entirely absent — no privacy policy, no privacy function or "
            "officer, no records of processing, no internal privacy audit and no continual "
            "improvement cycle. Part 1's management requirements are the mandatory ones, so a "
            "certification against this standard would fail today on process, not on code."
        ),
        evidence=[
            _f("aegis/src/aegis/guardrails/pii.py", "privacy by design on all three rails"),
            _f("aegis/src/aegis/memory/retention.py", "bounded retention"),
            _f("scripts/sql/aegis-app-role.sql", "least-privilege serving role"),
            _t(
                "backend/tests/memory/test_e2e.py::test_delete_single_fact_erases_and_audits",
                "removal is real and recorded",
            ),
        ],
    ),
    ControlEntry(
        id="RBI ITGRCA",
        title="RBI Master Direction on IT Governance, Risk, Controls and Assurance (2023)",
        state=ControlState.NOT_APPLICABLE,
        summary=(
            "This binds RBI-regulated entities — banks, NBFCs and co-operative banks. Aegis is "
            "not one, and claims no BFSI compliance it has not earned."
        ),
        gap=(
            "Applicable only if a regulated entity deploys Aegis, and then it is that entity's "
            "obligation, not the platform's. What such a deployment would inherit: "
            "database-enforced tenant isolation, least-privilege roles, an action-level audit "
            "trail, a human approval gate with fail-safe timeout, and a derived inventory of "
            "every destination the system reaches. What it would still owe: a board-approved "
            "IT strategy and IT Strategy Committee, an independent Information Systems audit, "
            "documented BCP/DR with tested recovery, change and incident management processes, "
            "and the RBI's own storage expectation for payment-system data. Aegis supplies none "
            "of those five."
        ),
        evidence=[
            _f(
                "aegis/src/aegis/governance/rls.py",
                "the isolation a regulated deployment inherits",
            ),
            _f("backend/src/app/platform/residency.py", "where the data would sit, derived"),
        ],
    ),
    ControlEntry(
        id="SEBI CSCRF",
        title="SEBI Cyber Security and Cyber Resilience Framework (2024)",
        state=ControlState.NOT_APPLICABLE,
        summary=(
            "This binds SEBI-regulated entities and is graded by entity category. Aegis is not "
            "a regulated entity and holds no CSCRF compliance."
        ),
        gap=(
            "Applicable only inside a SEBI-regulated deployment. CSCRF's core demands are "
            "organisational and continuous — a Security Operations Centre with defined "
            "monitoring, periodic VAPT with closure timelines, a cyber-audit report to SEBI, and "
            "a tested cyber-crisis management plan. Aegis has no SOC, no VAPT record and no "
            "incident-response procedure — every one of which such a deployment "
            "would still owe. It contributes evidence; it satisfies none of the framework itself."
        ),
    ),
)


# ─────────────────────────────────────────────────────────────────────────────
# Assembly
# ─────────────────────────────────────────────────────────────────────────────

#: (id, name, version, jurisdiction, scope, controls).
#:
#: **India first, and not as a courtesy.** Aegis's home market is India, and for an
#: Indian deployment the DPDP Act and the CERT-In Directions are *law* while ISO 42001
#: and the OWASP lists are practice. Ordering by jurisdiction rather than by how well
#: Aegis scores puts the binding obligations at the top of the picker and the voluntary
#: frameworks below them, which is the order a reviewer's own risk register uses. The
#: screen groups on this field rather than re-deriving the grouping from names.
_FRAMEWORKS: tuple[tuple[str, str, str, str, str, tuple[ControlEntry, ...]], ...] = (
    (
        "dpdp",
        "India DPDP Act 2023 and DPDP Rules 2025",
        "Act 2023 · Rules notified 14 Nov 2025",
        JURISDICTION_INDIA,
        "India's data-protection law, and the home market's binding obligation. Erasure and "
        "correction are real endpoints with tests; consent, breach reporting, DPIA and "
        "grievance redressal do not exist. Full effect 13 May 2027.",
        _DPDP,
    ),
    (
        "cert-in",
        "CERT-In Directions",
        "No. 20(3)/2022-CERT-In, 28 Apr 2022",
        JURISDICTION_INDIA,
        "The six directions under s.70B of the IT Act. Only the logging one has a mechanism "
        "here, and the honest half of it is that nothing enforces the 180-day window.",
        _CERT_IN,
    ),
    (
        "india-sectoral",
        "India — MeitY, RBI, SEBI and BIS",
        "IAGG Nov 2025 · ITGRCA 2023 · CSCRF 2024 · IS 17428:2020",
        JURISDICTION_INDIA,
        "The advisory and sectoral layer. The BFSI rows are not applicable on purpose: Aegis "
        "has earned no banking or securities compliance, and says what a regulated deployment "
        "would inherit and what it would still owe.",
        _INDIA_SECTORAL,
    ),
    (
        "owasp-llm",
        "OWASP Top 10 for LLM Applications",
        "v2.0 (2025)",
        JURISDICTION_INTERNATIONAL,
        "The application layer — what an attacker sends the model and what the model sends back.",
        _OWASP_LLM,
    ),
    (
        "owasp-web",
        "OWASP Top 10",
        "2025",
        JURISDICTION_INTERNATIONAL,
        "The API surface — authorisation, injection, logging and the outbound requests Aegis "
        "makes.",
        _OWASP_WEB,
    ),
    (
        "mitre-atlas",
        "MITRE ATLAS",
        "adversarial ML knowledge base",
        JURISDICTION_INTERNATIONAL,
        "Only the techniques the 28-attack battery actually exercises. Nothing is claimed from a "
        "mapping alone.",
        _ATLAS,
    ),
    (
        "nist-ai-rmf",
        "NIST AI RMF — AI Risk Management Framework",
        "1.0",
        JURISDICTION_INTERNATIONAL,
        "The four functions. Measure and Manage are where Aegis's evidence is strongest.",
        _NIST,
    ),
    (
        "iso-42001",
        "ISO/IEC 42001 — AI management system",
        "2023, Annex A",
        JURISDICTION_INTERNATIONAL,
        "The management-system controls. Aegis is a system, not an organisation, which is where "
        "this framework strains.",
        _ISO_42001,
    ),
    (
        "iso-27001",
        "ISO/IEC 27001 — Annex A",
        "2022",
        JURISDICTION_INTERNATIONAL,
        "Only the controls with a real mechanism in this repository. Seventeen of ninety-three, "
        "not padded.",
        _ISO_27001,
    ),
    (
        "eu-ai-act",
        "EU AI Act",
        "Regulation (EU) 2024/1689",
        JURISDICTION_INTERNATIONAL,
        "Aegis is a platform, not a deployed system — its risk class depends on the deployment. "
        "These are the articles a limited- or high-risk deployment must satisfy.",
        _EU_AI_ACT,
    ),
    (
        "soc2",
        "SOC 2 — Trust Services Criteria",
        "AICPA, 2017 (rev. 2022)",
        JURISDICTION_INTERNATIONAL,
        "What the audit trail, RBAC and change control can demonstrate. No auditor has looked at "
        "any of it.",
        _SOC2,
    ),
    (
        "privacy",
        "GDPR",
        "Regulation (EU) 2016/679",
        JURISDICTION_INTERNATIONAL,
        "The EU's data-subject rights and processing obligations. India's equivalents are their "
        "own framework above; the rows are not repeated here, so no evidence is counted twice.",
        _PRIVACY,
    ),
)


def _coverage(controls: list[ControlEntry]) -> FrameworkCoverage:
    """Count the four states over ``controls``. Derived, so it cannot drift."""
    counts = FrameworkCoverage(total=len(controls))
    for control in controls:
        match control.state:
            case ControlState.ENFORCED:
                counts.enforced += 1
            case ControlState.PARTIAL:
                counts.partial += 1
            case ControlState.NOT_IMPLEMENTED:
                counts.not_implemented += 1
            case ControlState.NOT_APPLICABLE:
                counts.not_applicable += 1
    return counts


def build_compliance() -> ComplianceResponse:
    """Return the full compliance-readiness map.

    Pure data assembly: no I/O, no database, no model call. Counts are derived from
    the entries rather than hand-authored, so a state change cannot leave a stale
    total behind it.

    Returns:
        A :class:`ComplianceResponse` carrying every framework, its controls, its
        evidence and its derived coverage — with :data:`DISCLAIMER` attached, because
        a readiness claim that travels without it is the one defect this whole
        surface exists to avoid.
    """
    frameworks: list[Framework] = []
    totals = FrameworkCoverage()
    for fid, name, version, jurisdiction, scope, controls in _FRAMEWORKS:
        entries = list(controls)
        coverage = _coverage(entries)
        totals.enforced += coverage.enforced
        totals.partial += coverage.partial
        totals.not_implemented += coverage.not_implemented
        totals.not_applicable += coverage.not_applicable
        totals.total += coverage.total
        frameworks.append(
            Framework(
                id=fid,
                name=name,
                version=version,
                jurisdiction=jurisdiction,
                scope=scope,
                controls=entries,
                coverage=coverage,
            )
        )
    return ComplianceResponse(
        disclaimer=DISCLAIMER,
        doc_ref=DOC_REF,
        generated_at=datetime.now(UTC).isoformat(),
        frameworks=frameworks,
        coverage=totals,
        residency=build_residency(),
    )
