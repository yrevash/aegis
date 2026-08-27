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
            "this degrades to partial and the live posture says so: ten battery probes "
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
            _f("aegis/src/aegis/redteam/battery.py", "16 of 48 attack probes tagged LLM01"),
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
        state=ControlState.ENFORCED,
        summary=(
            "Inventory, verdict and gate, each resolved from the running system rather "
            "than a maintained list: a live SBOM from the installed distributions, "
            "exported as CycloneDX 1.6 and SPDX 2.3 so a buyer's own scanner can read "
            "it; a live OSV.dev query giving each installed version a vulnerability "
            "verdict, which is the question patch freshness does not answer; and a CI "
            "step that fails the build on any advisory not recorded in "
            "backend/known_advisories.json, and on any package the feed could not be "
            "asked about. Artefact integrity is 6,367 sha256 pins across the two "
            "lockfiles — uv refuses a file whose digest does not match, which is the "
            "question a package signature would answer, answered per file. The tree "
            "currently carries two acknowledged advisories, both pinned by upstream "
            "libraries (presidio-anonymizer holds cryptography under 49; arize-phoenix "
            "pins strawberry-graphql exactly); the surface reports them as vulnerable "
            "rather than suppressing them, and the acknowledgement names what would "
            "release each. No in-toto/SLSA build provenance, which is the one "
            "assurance here that hash pinning does not substitute for."
        ),
        evidence=[
            _f("backend/uv.lock", "hash-pinned; 6,367 sha256 digests across both lockfiles"),
            _f("aegis/uv.lock", "the aegis half of the same pinning"),
            _f(
                "backend/src/app/platform/stack.py",
                "SBOM from importlib.metadata, never a hand-written pin list",
            ),
            _f(
                "backend/src/app/platform/sbom.py",
                "CycloneDX 1.6 + SPDX 2.3 from one inventory pass",
            ),
            _f(
                "backend/src/app/platform/advisories.py",
                "OSV.dev verdicts; never 'clean' without a real answer",
            ),
            _f(
                "backend/src/app/platform/patches.py",
                "live PyPI check; freshness, kept distinct from the verdict",
            ),
            _f(
                "backend/known_advisories.json",
                "the two upstream-pinned advisories, with what would release each",
            ),
            _f(".github/workflows/ci.yml", "the build gate that runs the audit"),
            _r("GET /stack", "the live bill of materials"),
            _r("GET /stack/sbom", "CycloneDX 1.6 / SPDX 2.3 export"),
            _r("POST /stack/advisories", "the vulnerability verdict, per package"),
            _r("POST /stack/patch-check", "installed vs latest, per package"),
            _d("docs/adr/0001-litellm-as-gateway.md", "one vetted gateway, not many provider SDKs"),
            _t(
                "backend/tests/api/test_supply_chain.py"
                "::test_an_audit_that_could_not_run_does_not_pass",
                "an unreachable feed is never a clean bill of health",
            ),
            _t(
                "backend/tests/api/test_supply_chain.py"
                "::test_the_cli_gate_exits_nonzero_on_a_finding",
                "the CI gate actually fails",
            ),
            _t(
                "backend/tests/api/test_supply_chain.py"
                "::test_an_acknowledged_advisory_unblocks_the_build_and_nothing_else",
                "an acknowledgement is about the build, never about the risk",
            ),
            _t(
                "backend/tests/api/test_supply_chain.py"
                "::test_cyclonedx_is_well_formed_and_every_component_carries_a_purl",
                "the export is joinable, not decorative",
            ),
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
            "Both halves are now attacked rather than asserted, and what leaks is "
            "named. The ML spine's training frame carries a content digest computed at "
            "fit time — SHA-256 over the feature and target columns, sensitive to any "
            "cell, to row order and to dtype, invariant to column order and the index — "
            "that rides the model through joblib onto the ModelCard and out of "
            "/v1/ml/model-card, so every fitted model names the exact data that "
            "produced it. That is provenance and tamper-EVIDENCE, not tamper- "
            "prevention: nothing screens a training frame, and detection needs a "
            "reference digest recorded while the data was trusted, which no part of the "
            "platform yet records or compares automatically. On the corpus half six "
            "poisoning probes run at a fourth battery stage aimed at the write gate "
            "itself (MITRE ATLAS AML.T0020) and five of the six are refused before the "
            "store. The sixth is the honest limit and it leaks: a poisoned *fact* in "
            "ordinary policy prose carries no instruction for a deterministic gate to "
            "match."
        ),
        evidence=[
            _f("aegis/src/aegis/retrieval/validation.py", "validate_content — the write-time gate"),
            _f("aegis/src/aegis/redteam/battery.py", "the poisoning probe set, Stage.INGEST"),
            _f(
                "aegis/src/aegis/ml/provenance.py",
                "frame_digest — the training-frame content digest",
            ),
            _t(
                "aegis/tests/ml/test_dataset_digest.py"
                "::test_refitting_on_changed_data_changes_the_digest",
                "a poisoned frame changes the fingerprint",
            ),
            _t(
                "aegis/tests/ml/test_dataset_digest.py"
                "::test_refitting_on_the_same_data_reproduces_the_digest",
                "and an unchanged one does not",
            ),
            _t(
                "aegis/tests/redteam/test_atlas_families.py"
                "::test_poisoned_documents_are_refused_before_the_store",
                "the gate is measured, not asserted",
            ),
            _t(
                "aegis/tests/redteam/test_atlas_families.py"
                "::test_a_poisoned_fact_carrying_no_instruction_gets_through_and_is_declared",
                "the leak is declared, not curated out",
            ),
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
            "The failure direction is now closed and the partition is not. A foreign "
            "row that reaches a result RAISES rather than being returned or quietly "
            "dropped: an independent post-check re-derives the permitted owners from "
            "the scope itself, deliberately not through the method the filter builder "
            "uses, so the defence and the thing it defends cannot go wrong together. A "
            "payload recording no owner is refused as unknown rather than read as "
            "shared. Proven by neutering the predicate and watching it refuse. What is "
            "still missing is the partition on the production path: the lite backend "
            "gives each tenant its own Qdrant collection, but LightRAG owns its "
            "retrieval internals and exposes no per-query metadata predicate, so its "
            "dense arm still reads one shared index holding every tenant's vectors and "
            "declines the foreign ones on the way out. Two independent application-code "
            "layers must now both fail for a leak; the database still enforces nothing. "
            "Per-tenant LightRAG instances are the real fix and are a reindex, not a "
            "config change."
        ),
        evidence=[
            _f(
                "aegis/src/aegis/retrieval/spotlight.py",
                "build_spotlighted_context — delimiting + datamarking",
            ),
            _f("aegis/src/aegis/retrieval/validation.py", "validate-before-write"),
            _f(
                "aegis/src/aegis/retrieval/lightrag_backend.py",
                "DEFAULT_CHUNK_COLLECTION — one shared index on the production path",
            ),
            _f(
                "aegis/src/aegis/retrieval/types.py",
                "verify_rows_in_scope — the independent, fail-closed post-check",
            ),
            _t(
                "aegis/tests/retrieval/test_tenant_isolation.py"
                "::test_a_forgotten_vector_predicate_raises_rather_than_returning_a_foreign_chunk",
                "neuter the predicate and it refuses instead of leaking",
            ),
            _t(
                "aegis/tests/retrieval/test_observability.py::test_spotlight_applied_by_default",
                "on by default, not opt-in",
            ),
            _t(
                "aegis/tests/retrieval/test_citations.py"
                "::test_a_span_survives_the_spotlight_datamarking",
                "datamarking does not corrupt the span",
            ),
            _t(
                "aegis/tests/retrieval/test_tenant_isolation.py"
                "::test_vector_arm_filters_by_tenant_in_the_qdrant_query",
                "the predicate is real — and it is a filter, which is the gap",
            ),
        ],
    ),
    ControlEntry(
        id="LLM09",
        title="Misinformation",
        state=ControlState.PARTIAL,
        summary=(
            "The grounding rail now returns two findings rather than one, because they "
            "deserve different answers. An answer the retrieved passages CONTRADICT — "
            "retrieval found the fact and the answer says the opposite — is blocked by "
            "default, grounding_block or not; there is no legitimate turn of that "
            "shape. An answer that is merely unsupported stays an advisory flag, "
            "because most of those are fine and a rail that blocks them is one an "
            "operator switches off. A per-metric eval regression gate fails the "
            "release on a measured quality drop."
        ),
        gap=(
            "The rail now has a deterministic backstop, so it is no longer worth "
            "nothing without a model: the assembled context numbers its passages "
            "[source N], the system prompt declares those labels the citation "
            "vocabulary, and a citation naming a passage this run did not retrieve is a "
            "provable falsehood that BLOCKS offline with no completer wired. What still "
            "needs the model layer is entailment itself — an unsupported or "
            "contradicted claim carrying no citation is caught only by the LLM self- "
            "check, so on a deployment with no completer those two findings are "
            "unavailable. The deterministic check also sees only the bracketed labels: "
            "an answer that fabricates a free-prose document id is not caught, because "
            "detecting one would need a regex for id-shaped tokens that would false- "
            "block the clause and part numbers this platform exists to retrieve. The "
            "zero-retrieval case remains a deliberate FLAG, never a block."
        ),
        evidence=[
            _f(
                "aegis/src/aegis/guardrails/grounding.py",
                "check_grounding — grounded and contradicted, judged separately",
            ),
            _f(
                "aegis/src/aegis/guardrails/pipeline.py",
                "_screen_grounding — contradiction blocks in either mode",
            ),
            _f("aegis/src/aegis/evals/regression.py", "declarative per-metric thresholds"),
            _t(
                "aegis/tests/guardrails/test_grounding.py"
                "::test_a_contradicted_answer_blocks_without_the_strict_posture",
                "the contradiction really blocks by default",
            ),
            _t(
                "aegis/tests/guardrails/test_grounding.py"
                "::test_a_checker_that_could_not_answer_never_manufactures_a_contradiction",
                "a downed checker never hard-blocks a working deployment",
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
        state=ControlState.ENFORCED,
        summary=(
            "The self-repair loop is always hard-capped, and token/USD/RPM/TPM caps "
            "are enforced before spend at the single gateway chokepoint. A budget "
            "control that fails open is not a control, so both ways it could fail to "
            "bind are now refused at boot outside dev, on the same asymmetry the JWT "
            "guard uses: BUDGET_FAIL_OPEN=true will not start, and neither will a "
            "deployment whose composition root has lost the governance hook at the "
            "gateway — the seam a cap binds at. The refusal names that variable rather "
            "than GATEWAY_BUDGET_FAIL_OPEN, which is the standalone gateway's own knob "
            "and is inert here because this platform injects a config. Dev keeps "
            "both, because dev is not the thing being protected and a guard that "
            "blocks the local loop is one somebody deletes. The live posture still "
            "reports a fail-open dev box as partial rather than green."
        ),
        evidence=[
            _f("aegis/src/aegis/governance/enforcement.py", "enforce_governance — before spend"),
            _f(
                "backend/src/app/config.py",
                "ensure_spend_caps_bind — refuses to boot uncapped outside dev",
            ),
            _f("backend/src/app/main.py", "called at the composition root, before the app exists"),
            _t(
                "aegis/tests/governance/test_enforcement.py::test_over_token_budget_raises",
                "the cap actually refuses",
            ),
            _t(
                "backend/tests/core/test_startup_guard.py"
                "::test_non_dev_refuses_to_boot_with_budgets_failing_open",
                "fail-open cannot reach production",
            ),
            _t(
                "backend/tests/core/test_startup_guard.py"
                "::test_non_dev_refuses_to_boot_with_no_governance_hook_at_the_gateway",
                "an unwired chokepoint cannot reach production either",
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
#
# Four families were added because four of these rows said, in writing, that nothing
# tested them. Each is a real attack with a real verdict from the rail it is aimed at,
# each runs inside ``owasp-full`` so its leaks are in the headline block rate, and each
# has probes that deliberately get through — a family whose every probe passes is a
# family that was written to pass.
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
        state=ControlState.ENFORCED,
        summary=(
            "A hostile peer is stood up and the platform is made to refuse it, end to "
            "end: a real in-process MCP server over the SDK's own transport, whose "
            "tool description, argument schema and return value each carry an attack, "
            "screened by the real TOOL_RESULT rail rather than by an injected stub. "
            "The poisoned tool is dropped at discovery so its text never reaches the "
            "planner's prompt; the poisoned result is withheld from the agent's "
            "context and does not appear in the audit row either. The four battery "
            "probes carry the same constants the peer serves, so the suite and the "
            "end-to-end test cannot describe different attacks. The fourth probe is "
            "the finding: a compromised peer that simply returns a plausible wrong "
            "answer — a well-formed invoice with the attacker's beneficiary — passes "
            "the rail, and what stops it is the tier, not the text. An external tool "
            "is HIGH until a platform admin lowers a named one, and the allowlist is "
            "checked before the connection opens."
        ),
        evidence=[
            _f("backend/src/app/mcp/server.py", "external tools gated HIGH by default"),
            _f(
                "backend/src/app/mcp/client.py",
                "the TOOL_RESULT rail at the network boundary, on results and schemas",
            ),
            _f("aegis/src/aegis/redteam/battery.py", "the plugin-compromise probe set"),
            _t(
                "backend/tests/mcp/test_hostile_peer.py"
                "::test_a_compromised_peers_return_value_is_withheld_from_the_agent",
                "a real peer, a real rail, a real refusal",
            ),
            _t(
                "backend/tests/mcp/test_hostile_peer.py"
                "::test_a_hostile_peers_poisoned_tool_never_reaches_the_planner",
                "the injection that never looks like a result",
            ),
            _t(
                "backend/tests/mcp/test_hostile_peer.py"
                "::test_a_plausible_wrong_answer_passes_the_rail_and_this_is_reported",
                "the attack the rail does not stop, reported rather than omitted",
            ),
            _t(
                "backend/tests/mcp/test_hostile_peer.py"
                "::test_the_battery_probes_carry_the_payloads_this_peer_actually_serves",
                "the suite and the end-to-end test cannot drift apart",
            ),
        ],
    ),
    ControlEntry(
        id="AML.T0020",
        title="Poison Training Data",
        state=ControlState.ENFORCED,
        summary=(
            "Six poisoning probes at a fourth battery stage aimed at the write-time "
            "gate — the only rail a poisoning attack ever meets, since it arrives as a "
            "document months before the question it is meant to answer. An override in "
            "a handbook page, a forged SYSTEM turn in a KB article, a stored macro "
            "instructing a later exfiltration, an oversized blob and a non-printable "
            "one are all refused before the store. The sixth is the honest limit and "
            "it leaks: a poisoned *fact* in ordinary policy prose carries no "
            "instruction to match. It is marked semantic-only, it is in the report, "
            "and the suite's floor is set at the reach the gate actually has."
        ),
        evidence=[
            _f("aegis/src/aegis/retrieval/validation.py", "validate_content — the gate"),
            _f("aegis/src/aegis/redteam/battery.py", "the poisoning probe set, Stage.INGEST"),
            _f("aegis/src/aegis/redteam/runner.py", "check_ingest — the fourth rail"),
            _r("POST /redteam/runs", "run it and read the verdicts"),
            _t(
                "aegis/tests/redteam/test_atlas_families.py"
                "::test_poisoned_documents_are_refused_before_the_store",
                "each probe blocked by the ingest rail, not by a neighbour",
            ),
            _t(
                "aegis/tests/redteam/test_atlas_families.py"
                "::test_the_poisoning_probes_go_to_the_ingest_rail_not_the_input_rail",
                "aimed at the right rail",
            ),
        ],
    ),
    ControlEntry(
        id="AML.T0024",
        title="Exfiltration via AI Inference API",
        state=ControlState.ENFORCED,
        summary=(
            "Both halves of the technique are refused. The channel is closed by an "
            "outbound rail that blocks an answer carrying data out through a URL nobody "
            "clicked — an auto-loading markdown or HTML image, or a link, pointing at "
            "an external host with an encoded payload in its query, path or fragment. "
            "The volume half is closed by a per-principal, tenant-scoped query-pattern "
            "monitor: a query is reduced to a template by masking ids and numbers, and "
            "a principal running one template over many distinct values, or touching an "
            "abnormal breadth of subjects, is refused with 429 before the stream opens, "
            "audited under the masked template so no swept id enters the trail, and "
            "raised to the tenant's admins as an alert. It blocks rather than flags, "
            "because a flag that lets query 31 through lets 500 through and this "
            "technique completes by volume. Four benign controls hold the false- "
            "positive rate at zero and each is saved by a different clause. What it "
            "does not catch is named and probed rather than assumed: the detector is "
            "rate-shaped, so a sweep paced under the threshold completes unobserved, "
            "and two probes are exactly that — declared leaks carrying beyond_rails "
            "rather than needs_llm, because no completer closes them either. The window "
            "is in-process and non-durable, so a restart clears it and a second API "
            "worker halves what each principal is seen doing; it is keyed per "
            "principal, so two credentials halve an attacker's observed rate; and there "
            "is no service-account exemption, so an authorised bulk job is "
            "behaviourally indistinguishable from enumeration and will be refused."
        ),
        gap="",
        evidence=[
            _f(
                "aegis/src/aegis/guardrails/schema.py",
                "exfiltration_channel — the outbound channel rail",
            ),
            _f(
                "aegis/src/aegis/security/extraction.py",
                "the per-principal query-pattern monitor",
            ),
            _f(
                "backend/src/app/api/routes.py",
                "refuse_if_extracting — wired as the first act of POST /query",
            ),
            _f("aegis/src/aegis/redteam/battery.py", "the inference-exfil probe set"),
            _t(
                "aegis/tests/redteam/test_atlas_families.py"
                "::test_an_answer_that_is_a_channel_is_blocked_on_the_way_out",
                "the channel is really closed",
            ),
            _t(
                "aegis/tests/redteam/test_atlas_families.py"
                "::test_extraction_by_query_volume_is_detected_per_principal_and_refused",
                "and the sweep is really refused",
            ),
            _t(
                "aegis/tests/redteam/test_atlas_families.py"
                "::test_ordinary_work_that_looks_like_enumeration_is_not_flagged",
                "while ordinary bulk work is not",
            ),
            _t(
                "backend/tests/api/test_extraction_gate.py"
                "::test_the_alert_reaches_the_tenants_admin_and_nobody_elses",
                "the finding reaches a human, tenant-scoped",
            ),
            _t(
                "aegis/tests/redteam/test_atlas_families.py"
                "::test_model_extraction_and_membership_inference_leak_and_are_declared",
                "the paced sweep that still leaks, asserted as leaking",
            ),
        ],
    ),
    ControlEntry(
        id="AML.T0043",
        title="Craft Adversarial Data",
        state=ControlState.ENFORCED,
        summary=(
            "The model an attacker actually crafts data against here is the injection "
            "detector on the request path, and it is attacked: five evasion probes "
            "take one override and perturb it until the signature layer stops matching "
            "— hex, percent-encoding, ROT13, plain reversal — each of which walked "
            "straight through before the rail learned to decode and screen it as the "
            "instruction it carries. The fifth is a paraphrase; it leaks, it is marked "
            "semantic-only, and it is the boundary between the deterministic layer and "
            "the model one. Base32, Morse and a separator-spelled instruction are "
            "named as remaining misses and asserted as such rather than left to be "
            "assumed covered. The forecasting ensemble is a different model and not a "
            "security control: its inputs are the host's own records, and its quality "
            "is gated by the eval regression thresholds rather than by a red-team probe."
        ),
        evidence=[
            _f(
                "aegis/src/aegis/guardrails/classifier.py",
                "_decoded_candidates — four encodings, decoded then screened",
            ),
            _f("aegis/src/aegis/guardrails/normalize.py", "the character-level fold it sits on"),
            _f("aegis/src/aegis/redteam/battery.py", "the adversarial-evasion probe set"),
            _t(
                "aegis/tests/guardrails/test_injection_evasion.py"
                "::test_an_encoded_instruction_is_screened_as_the_instruction",
                "one case per encoding; deleting a branch fails its case",
            ),
            _t(
                "aegis/tests/guardrails/test_injection_evasion.py"
                "::test_documented_coverage_limits_are_honest",
                "what is still missed, asserted rather than implied",
            ),
            _t(
                "aegis/tests/redteam/test_atlas_families.py"
                "::test_every_encoded_perturbation_is_screened_as_the_instruction",
                "caught by the detector under attack, not by a neighbouring rail",
            ),
        ],
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
        state=ControlState.ENFORCED,
        summary=(
            "This function is documentation and process, which is the form the framework "
            "asks for, so the artefacts are the control: an AI policy whose every clause "
            "names the mechanism enforcing it, a role register mapping the five roles the "
            "software actually guards to a named owner, an incident-response plan keyed to "
            "signals this system emits, and a review cadence a two-person team can keep — "
            "over nine ADRs, a written threat model and a per-tenant policy catalogue whose "
            "merge rules let a tenant tighten a control and never loosen one."
        ),
        gap="",
        evidence=[
            _d("docs/governance/ai-policy.md", "purpose, prohibited use, sourcing, oversight"),
            _d("docs/governance/accountable-roles.md", "an owner per role the code enforces"),
            _d("docs/governance/incident-response.md", "detect, triage, contain, notify, review"),
            _d("docs/governance/review-cadence.md", "who reviews what, and on which trigger"),
            _d("docs/security/threat-model.md", "the threat model the policy is written over"),
            _d("docs/adr/0008-multi-tenant-rls-governance.md", "a recorded decision"),
            _f("aegis/src/aegis/settings/spec.py", "the policy catalogue and its merge rules"),
            _t(
                "backend/tests/api/test_governance_docs.py"
                "::test_every_repository_path_a_governance_document_cites_exists",
                "a clause cannot outlive the mechanism it names",
            ),
            _t(
                "backend/tests/api/test_governance_docs.py"
                "::test_the_review_cadence_names_a_period_and_an_owner_for_every_artefact",
                "a cadence with no owner is theatre",
            ),
        ],
    ),
    ControlEntry(
        id="MAP",
        title="Map — context, risks, capabilities",
        state=ControlState.ENFORCED,
        summary=(
            "Each agentic risk carries an inherent and a residual position with the control "
            "that moved it and a control_ref naming a real file; the stack is the inventory; "
            "the model card states task, features, data source, calibration and training "
            "sizes; and a written context-of-use analysis names the people affected — a "
            "tenant's end customers, whose service requests and documents this system reads "
            "— with thirteen harms, the mechanism that reduces each, and four marked as "
            "having no mitigation at all."
        ),
        gap="",
        evidence=[
            _r("GET /risk-map", "inherent and residual, per risk"),
            _r("GET /stack", "the system inventory"),
            _r("GET /ml/model-card", "honest, measured model metadata"),
            _f("backend/src/app/platform/risk_map.py", "grounded verbatim in the security doc"),
            _d("docs/governance/context-and-impact.md", "context of use and impact on people"),
            _t(
                "backend/tests/api/test_governance_docs.py"
                "::test_the_impact_assessment_pairs_every_harm_with_a_mitigation_or_says_there_is_none",
                "a harm table with no gaps is one nobody checked",
            ),
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
            "deadlines and fail-safe auto-rejection, budget refusal as a clean terminal "
            "event, and a tiered release path with rollback."
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
            "Human gate, per-persona tool allowlists, a written intended-use and "
            "prohibited-use policy, and per-tenant settings that show which scope decided "
            "each value."
        ),
        gap=(
            "The policy states seven prohibited uses, and only some are mechanically "
            "prevented; the rest are labelled process-only in the document itself. There "
            "is no per-deployment addendum — one policy covers every tenant on this "
            "installation."
        ),
        evidence=[
            _d("docs/governance/ai-policy.md", "intended use, and seven prohibited uses"),
            _f("aegis/src/aegis/agent/graph.py", "the gate"),
            _t(
                "backend/tests/api/test_governance_docs.py"
                "::test_the_ai_policy_states_a_use_boundary_a_sourcing_position_and_oversight",
                "the policy must state a use boundary, not merely exist",
            ),
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
        gap=(
            "A.5.23 governs the acquisition, use and exit of cloud services, and this "
            "deployment consumes none: Postgres, Redis, Qdrant and Neo4j all run as "
            "local processes on the operator's own machine, installed from INSTALL.md "
            "with no container runtime, no orchestrator and no managed-service control "
            "plane to configure or hold credentials for. There is no provider agreement "
            "to review, no shared-responsibility boundary to draw and no exit plan to "
            "write, because there is no provider. The one external dependency is the "
            "model gateway, which is a supplier relationship and is assessed under "
            "A.5.19-A.5.22 rather than here. Deploy the same code onto managed "
            "infrastructure and this control applies immediately and is not met."
        ),
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
            "and the SLA sweeper auto-rejects on timeout so silence is a refusal rather "
            "than consent."
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
            _f("aegis/src/aegis/redteam/battery.py", "48 attacks and 11 benign controls"),
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
_OWASP_AGENTIC: tuple[ControlEntry, ...] = (
    ControlEntry(
        id="ASI01",
        title="Agent Goal Hijack",
        state=ControlState.PARTIAL,
        summary=(
            "Injection is screened at four rail stages now — input, output, tool result "
            "and the memory write — and the human approval gate bounds any plan that "
            "survives them before it acts."
        ),
        gap=(
            "Injection is never marked solved on this platform and is not marked solved "
            "here. Ten battery probes are semantic-only and leak with no model completer "
            "wired; the deterministic layer catches phrasing, not intent. The gate is the "
            "decisive control precisely because the rails are not sufficient."
        ),
        evidence=[
            _f("aegis/src/aegis/guardrails/pipeline.py", "the four rail entry points"),
            _f("aegis/src/aegis/agent/graph.py", "the human approval interrupt"),
            _t(
                "aegis/tests/redteam/test_stages_and_suites.py"
                "::test_each_probe_is_screened_by_the_rail_its_stage_names",
                "each probe reaches the rail its stage names",
            ),
        ],
    ),
    ControlEntry(
        id="ASI02",
        title="Tool Misuse and Exploitation",
        state=ControlState.ENFORCED,
        summary=(
            "Tools are a typed, per-persona allowlist with a risk tier each; anything at "
            "or above the gate threshold stops for a human, and every call is audited "
            "with its actor, arguments and approver."
        ),
        evidence=[
            _f("backend/src/app/adapter/tools.py", "ALLOWLIST + the risk-tiered registry"),
            _f("aegis/src/aegis/agent/graph.py", "gate, approval and _authorised_calls"),
            _t(
                "aegis/tests/agent/test_gate_authorises_what_runs.py"
                "::test_one_gate_authorises_exactly_the_actions_it_enumerated",
                "one approval authorises exactly the calls it named, and no others",
            ),
        ],
    ),
    ControlEntry(
        id="ASI03",
        title="Identity and Privilege Abuse",
        state=ControlState.PARTIAL,
        summary=(
            "Tenancy is enforced in Postgres RLS beneath the application filters, and "
            "the MCP surface re-resolves the caller's identity and authority on every "
            "call rather than once per connection — measured over one socket by swapping "
            "the bearer and watching the tool list and tenant scope change with it."
        ),
        gap=(
            "What is enforced is the tenant boundary and per-call re-resolution. What is "
            "absent is an agent identity at all: the agent acts as the human whose token "
            "it holds, so there is no delegation chain a third party could verify and no "
            "way to distinguish 'this agent, acting for this person' from 'this person'. "
            "The A2A card is the platform's first verifiable identity and it identifies "
            "the deployment, not a running agent."
        ),
        evidence=[
            _f("aegis/src/aegis/governance/rls.py", "row-level security policies"),
            _f("backend/src/app/mcp/server.py", "resolve_caller, per call"),
            _t(
                "backend/tests/api/test_admin_governance.py"
                "::test_tenant_admin_cannot_read_other_tenant",
                "a tenant-bound caller cannot reach another tenant's rows",
            ),
        ],
    ),
    ControlEntry(
        id="ASI04",
        title="Agentic Supply Chain Vulnerabilities",
        state=ControlState.PARTIAL,
        summary=(
            "Every model call goes through one vetted gateway, dependencies are pinned "
            "with a constraint block that a fresh resolve honours, and the platform "
            "serves its own SBOM in CycloneDX and SPDX."
        ),
        gap=(
            "There is no Agent Bill of Materials — the tools, their risk tiers, the model "
            "fleet, the rails and the knowledge sources are not published as one signed "
            "inventory, which is what the emerging agentic-supply-chain guidance asks "
            "for. Dependency pinning is verified; artefact attestation is not: nothing "
            "checks that a published wheel was built from the source it claims."
        ),
        evidence=[
            _f("backend/pyproject.toml", "constraint-dependencies, with reasons"),
            _r("GET /stack/sbom", "CycloneDX 1.6 + SPDX 2.3"),
            _t(
                "backend/tests/api/test_supply_chain.py"
                "::test_an_audit_that_could_not_run_does_not_pass",
                "a supply-chain audit that could not run is not reported as clean",
            ),
        ],
    ),
    ControlEntry(
        id="ASI05",
        title="Unexpected Code Execution (RCE)",
        state=ControlState.PARTIAL,
        summary=(
            "There is no code-execution tool and no shell. The in-process registry is a "
            "closed set of typed, allowlisted callables with validated argument models, "
            "and an external tool discovered from an MCP peer starts at HIGH risk — so "
            "an unrecognised name cannot slip under the human gate by being unknown, and "
            "the persona allowlist is re-checked before any connection is opened."
        ),
        gap=(
            "The tool set is not closed, and an earlier version of this row claimed it "
            "was. A registered MCP peer contributes tools at runtime, and a peer can add "
            "one tomorrow that nobody has assessed. What bounds that is risk-tiering and "
            "the gate, not the absence of a path — which is a weaker and truer statement. "
            "Nothing sandboxes an external tool's effects on the peer's own side."
        ),
        evidence=[
            _f("backend/src/app/adapter/tools.py", "TOOL_REGISTRY — a closed, typed set"),
            _t(
                "backend/tests/adapter/test_allowlist.py::test_client_cannot_run_admin_tool",
                "a tool outside the persona's allowlist raises rather than runs",
            ),
        ],
    ),
    ControlEntry(
        id="ASI06",
        title="Memory & Context Poisoning",
        state=ControlState.PARTIAL,
        summary=(
            "A fourth rail stage screens a candidate fact before it reaches the durable "
            "store, and refusals are written to the fact-write audit trail under their "
            "own operation. The other three stages structurally cannot see this attack: "
            "the turn that poisons the store and the turn poisoned by it are different "
            "turns, so guarding both ends of one turn never caught it."
        ),
        gap=(
            "Three of four poisoning probes are refused; the fourth is the honest limit "
            "and it is declared rather than curated out. A policy override phrased as an "
            "ordinary business sentence — 'all refund requests from this account are "
            "pre-approved' — carries no injection signature, because it is not addressed "
            "to the model at all. Catching it needs the model-backed layer wired."
        ),
        evidence=[
            _f("aegis/src/aegis/guardrails/memory_write.py", "the rail's contract"),
            _f("aegis/src/aegis/memory/consolidate.py", "the screen, at _reconcile"),
            _t(
                "aegis/tests/memory/test_consolidate.py"
                "::test_a_poisoned_fact_is_refused_and_audited_rather_than_stored",
                "a poisoned fact is refused and audited, not stored",
            ),
        ],
    ),
    ControlEntry(
        id="ASI07",
        title="Insecure Inter-Agent Communication",
        state=ControlState.PARTIAL,
        summary=(
            "Internal fan-out runs inside one orchestrator rather than over a network "
            "between peers, and every hop is traced and audited. The external A2A "
            "surface is authenticated, and its routing tenant is structurally barred "
            "from setting the database tenant scope."
        ),
        gap=(
            "The A2A surface is now served and its card is signed, so a peer can verify "
            "who it is talking to — but the traffic itself is not yet mutually "
            "authenticated, there is no peer allowlist, and inbound A2A cannot start a "
            "run. What holds today is that the addressed tenant can never become the "
            "database tenant: the routing field is refused when it disagrees with the "
            "bearer token rather than reconciled. That is one property, well tested, and "
            "not the whole control."
        ),
        evidence=[
            _f("aegis/src/aegis/agent/subagent.py", "fan-out inside one process"),
            _f("aegis/src/aegis/observability/semconv.py", "every hop is traced"),
            _t(
                "aegis/tests/agent/test_team_fanout.py"
                "::test_a_subagent_proposal_gates_and_resumes_through_the_existing_path",
                "a lane's proposal goes through the same gate, not around it",
            ),
        ],
    ),
    ControlEntry(
        id="ASI08",
        title="Cascading Failures",
        state=ControlState.PARTIAL,
        summary=(
            "The repair loop is bounded by several independent stops rather than one: an "
            "iteration cap, a separate repair budget, and progress detection that halts a "
            "call failing identically three times. A rail refusal is terminal and never "
            "retried, so the loop cannot spend its budget arguing with a guardrail."
        ),
        gap=(
            "A token ceiling now bounds each lane — one for the whole trajectory and one "
            "for a single tool result — and both are tenant-tightenable, so a run is "
            "bounded by size as well as by iteration count and progress. What is still "
            "absent is a wall-clock or currency bound on the repair loop itself, and "
            "cross-lane detection: a failure cascading across a fan-out is still not "
            "recognised as one event."
        ),
        evidence=[
            _f("aegis/src/aegis/agent/graph.py", "verify, and the termination bounds"),
            _t(
                "aegis/tests/agent/test_self_repair_loop.py"
                "::test_an_identical_call_failing_three_times_stops_the_loop",
                "a stuck call is stopped by progress, not by budget",
            ),
        ],
    ),
    ControlEntry(
        id="ASI09",
        title="Human-Agent Trust Exploitation",
        state=ControlState.PARTIAL,
        summary=(
            "Every consequential action stops for a named human and is recorded with its "
            "approver and trace id, and the console shows the evidence an answer stands "
            "on rather than asking to be believed."
        ),
        gap=(
            "Nothing addresses anthropomorphism itself. There is no measure of whether a "
            "reader over-trusts a fluent answer, no confidence calibration shown beside "
            "prose, and no control for an operator approving by reflex — which is the "
            "specific failure this category names."
        ),
        evidence=[
            _f("aegis/src/aegis/governance/audit.py", "approver and trace on every row"),
            _t(
                "aegis/tests/governance/test_audit.py"
                "::test_the_chain_verifies_and_a_tampered_row_is_caught",
                "the trail is verifiable, not merely append-only",
            ),
        ],
    ),
    ControlEntry(
        id="ASI10",
        title="Rogue Agents",
        state=ControlState.PARTIAL,
        summary=(
            "Autonomy is bounded on every axis a run can move along: an allowlisted tool "
            "set, a risk gate, a budget cap enforced at the gateway, and an audit trail "
            "that is now verifiable rather than merely privileged."
        ),
        gap=(
            "Bounded is not monitored. Nothing watches a run's behaviour for drift, and "
            "there is no kill switch that stops an in-flight agent other than the budget "
            "refusing its next call."
        ),
        evidence=[
            _f("aegis/src/aegis/gateway/llm.py", "budget enforced at the chokepoint"),
            _f("aegis/src/aegis/governance/chain.py", "the tamper-evident trail"),
            _t(
                "aegis/tests/governance/test_audit.py"
                "::test_a_deleted_row_breaks_every_row_after_it",
                "a removed row cannot hide",
            ),
        ],
    ),
)


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
        "owasp-agentic",
        "OWASP Top 10 for Agentic Applications",
        "2026",
        JURISDICTION_INTERNATIONAL,
        "The agent layer — goals, tools, identity, memory and the blast radius when one "
        "of them is turned against the system.",
        _OWASP_AGENTIC,
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
