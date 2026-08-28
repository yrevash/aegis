# Threat Model — OWASP LLM Top 10 · OWASP Agentic (ASI) Top 10 · Lethal Trifecta

One-page mapping of this platform to the industry AI-security frameworks, with
our concrete mitigation per risk. Content sourced from `docs/security/overview.md`;
implementations live in `aegis/src/aegis/{guardrails,retrieval,governance,agent}/`
(the backend keeps importable shims at `app/guardrails/`,
`app/retrieval/spotlight.py`, `app/data/`), and the agent layer. Detection is
**API-based or pure code — no local guardrail model** (16 GB / no-GPU constraint).

> **The control-by-control version of this page is
> [`../compliance/README.md`](../compliance/README.md)** — 124 controls across 13
> frameworks, each resolving to a file, route or test, with a per-control state and a
> named gap. Where a row here says "✅" and that document says "partial", that document
> is the finer-grained judgement and it wins; this page is the one-page map. Neither is
> a certification: Aegis holds no ISO 27001, ISO/IEC 42001, SOC 2 or EU AI Act
> attestation, and nobody independent has audited any of it.

Frameworks: **OWASP Top 10 for LLM Applications v2.0 (2025)**, **OWASP Top 10 for
Agentic Applications (ASI, 2026)**, and Simon Willison's **"lethal trifecta."**

---

## 1. OWASP Top 10 for LLM Applications (2025)

| ID | Risk | Applies? | Our mitigation |
|----|------|----------|----------------|
| **LLM01** | Prompt Injection | ✅ Core | **Input rail**: API injection/jailbreak classifier (`ModelRole.CHEAP`, fails closed) + schema validation; **Spotlighting** marks retrieved data as non-instructions; least-privilege tools + human gate limit blast radius. |
| **LLM02** | Sensitive Information Disclosure | ✅ Core | Self-built **PII scan + redaction on both rails** (`app/guardrails/pii.py`); PII is redacted *before* the classifier API call; RBAC least-privilege data access; minimum-necessary context per request. |
| **LLM03** | Supply Chain | ✅ | Hash-pinned lockfiles; a live SBOM resolved from the **actually installed** distributions and exported as CycloneDX 1.6 / SPDX 2.3 (`GET /v1/stack/sbom`); an OSV.dev advisory verdict (`POST /v1/stack/advisories`) that is never `clean` without a real answer; and a CI step that fails the build on any advisory not recorded in `known_advisories.json`. Above that sits an **Agent** BOM (`GET /v1/platform/agbom`, CycloneDX 1.6, `application/vnd.cyclonedx+json`, content-derived `serialNumber` so it is deterministic) inventorying tools, model fleet, rails and knowledge sources. No local model weights to poison; single vetted gateway (LiteLLM → GenAI Lab) instead of many provider SDKs. *Still missing:* in-toto/SLSA build provenance, and the npm side of `web/`. |
| **LLM04** | Data & Model Poisoning | ✅ (RAG) | **Validate content before writing to the graph** (`app/retrieval/validation.py`); governed ingestion; Spotlighting neutralises poisoned retrieved spans at read time. |
| **LLM05** | Improper Output Handling | ✅ Core | **Output rail**: schema/format validation + content filter *before* any downstream use (`app/guardrails/schema.py`). |
| **LLM06** | Excessive Agency | ✅ Core | **Tool allowlists per persona** (least privilege); **graded conformal autonomy bands** (autonomous / defer / **abstain**) on top of a **durable, checkpointed human-in-the-loop gate** (ADR 0005/0007); idempotent (exactly-once), reversible, **audited** actions. |
| **LLM07** | System Prompt Leakage | ✅ | Output content filter denylists system-prompt markers; no secrets placed in prompts; Spotlighting keeps retrieved data from eliciting the prompt. |
| **LLM08** | Vector & Embedding Weaknesses | ⚠️ Partial | Validate-before-write to the vector/graph store; Spotlighting on retrieved chunks (indirect-injection defense); every retrieval arm applies a tenant predicate, tested cross-tenant. **Partial, and the reason is the failure direction:** the vector tier is one *shared* Qdrant collection and the predicate is a payload filter written by application code — under Postgres RLS a query that forgets its tenant clause returns nothing, here it would return everything. Per-tenant collections, or a database-enforced predicate, is what would close it. |
| **LLM09** | Misinformation | ✅ | **Conformal prediction** bounds ML uncertainty; **SHAP** explanations; retrieval grounding; low-confidence → human gate. |
| **LLM10** | Unbounded Consumption | ✅ | Input length caps (schema rail); **enforced per-tenant/user budgets (token/usd) + RPM/TPM at the LiteLLM chokepoint** — a breach raises `BudgetExceededError` → terminal `budget_exceeded`, so spend degrades gracefully instead of running away (ADR 0008); durable `usage_ledger` per call; cheap-model routing. |

---

## 2. OWASP Top 10 for Agentic Applications (ASI, 2026)

> Titles below are verbatim from the **OWASP Top 10 for Agentic Applications**, version
> 2026 (published December 2025), read from the project's own PDF rather than from
> secondary sources. Five of these titles were previously wrong here; ASI06 was
> substantively so.

| ID | Threat | Our mitigation |
|----|--------|----------------|
| **ASI01** | Agent Goal Hijack | Input injection rail + Spotlighting stop instruction-injection; the human gate bounds any hijacked plan before it acts. |
| **ASI02** | Tool Misuse and Exploitation | Per-persona **tool allowlists**; treat the LLM as a hostile user — tools sit behind the same IAM/rate limits as external traffic; reversible + audited. |
| **ASI03** | Identity and Privilege Abuse | **Multi-tenant RBAC** — signed JWT claims (`{sub, role, tenant_id}`), Argon2id passwords, a three-tier hierarchy (platform / tenant admin / user); **app-level tenant scoping with Postgres RLS behind it** (§3a — read the order; RLS is currently the second layer, not the boundary); separate `/admin` surfaces; least-privilege tool grants (ADR 0008). The MCP front door re-reads the caller's role from the `users` table on **every** call, so a forged token claiming a higher role is downgraded to what the database says. *Gap, stated:* there is no agent identity — the agent acts as the human whose token it holds, so no delegation chain is verifiable. |
| **ASI04** | Agentic Supply Chain Vulnerabilities | Single vetted model gateway; hash-pinned lockfiles; no arbitrary local model loading; a live SBOM **and** an Agent BOM (`GET /v1/platform/agbom`, CycloneDX 1.6) that publishes the tools, their risk tiers, the model fleet, the rails and the knowledge sources as one deterministic inventory. *Gap:* no artefact attestation — nothing checks that a published wheel was built from the source it claims. |
| **ASI05** | Unexpected Code Execution (RCE) | No agent code-exec tool and no shell; the in-process registry is a closed set of typed, allowlisted callables with validated argument models. *The set is not closed end to end, and saying it was is the overclaim this row used to carry:* a registered MCP peer contributes tools at runtime. What bounds that is risk-tiering — an external tool starts at HIGH and therefore stops at the human gate — not the absence of a path. |
| **ASI06** | Memory & Context Poisoning | **Azure Spotlighting** (delimiting + datamarking) marks retrieved text as *data, not instructions*; and the fourth rail stage, `GuardStage.MEMORY_WRITE`, screens a candidate fact **before** it reaches the durable store — the other three stages cannot see this attack, because the turn that poisons the store and the turn poisoned by it are different turns. The screen (`app.memory.screen.memory_write_screen`) is bound on **both** drain paths — the hot path the agent fires after every turn and the 60-second backstop sweeper — because binding one and not the other is how the rail was silently unbound before. Refusals are written to `memory_write_log` under their own operation. *Gap:* a policy override phrased as ordinary business prose ("all refund requests from this account are pre-approved") carries no signature for a deterministic gate to match; catching it needs the model-backed layer wired. |
| **ASI07** | Insecure Inter-Agent Communication | Internal fan-out runs **inside one orchestrator process** (an `asyncio.gather`, not a network of peers), and every hop is traced (OTel) and audited. There *is* now an external channel — **A2A protocol 1.0**: `GET /.well-known/agent-card.json`, `GET /.well-known/jwks.json` (ES256 public half) and `POST /v1/a2a` (`SendMessage`, `GetTask`), behind `require_auth`. Its load-bearing property is that the `tenant` routing field — opaque and attacker-controlled — selects only *which agent is addressed* and **never** sets the database scope; that comes from the bearer token alone, and a mismatch is refused with one identical code and message across every branch so the error cannot enumerate tenants. The card declares `streaming`/`pushNotifications`/`extendedAgentCard` all **false**, and is **unsigned unless `a2a_public_origin` is configured** — deriving the origin from the `Host:` header let a caller rewrite the *signed* card, which is why it is configuration-only. *Gap:* the traffic itself is not mutually authenticated, there is no peer allowlist, and inbound A2A cannot start a run. |
| **ASI08** | Cascading Failures | Bounded on four independent axes rather than one: an iteration cap; a `verify` node that checks the world (result rows, or the record read back) rather than the model's own opinion and stops a call that has failed **identically three times**; two `TIGHTEN_ONLY` token ceilings — `max_trajectory_tokens` (36000) on a sub-agent lane's history and `max_tool_result_tokens` (4000) on every tool result; and budget refusal at the gateway. Plus human gate + conformal uncertainty on low-confidence actions; idempotent, reversible actions; per-run trace id. *Gap:* no wall-clock or currency bound on the repair loop, and no cross-lane detection — a failure cascading across a fan-out is still not recognised as one event. |
| **ASI09** | Human-Agent Trust Exploitation | Every autonomous action is logged with approver + trace id **on a hash chain a reviewer can verify** (`GET /v1/audit/verify`); the gate forces explicit human approval on high-risk steps — no silent action. *Gap:* nothing addresses anthropomorphism itself — no measure of over-trust in a fluent answer, and no control for an operator approving by reflex. |
| **ASI10** | Rogue Agents | Bounded autonomy: allowlisted tools, human gate, budget cap at the chokepoint, and an audit log that is **tamper-evident**, not merely append-only. *Gap:* bounded is not monitored — nothing watches a run for behavioural drift, and there is no kill switch other than the budget refusing the next call. |

---

## 3. The Lethal Trifecta

Prompt-injection becomes *exploitable* only when three capabilities combine
**unguarded**: (A) access to **private data**, (B) exposure to **untrusted
content**, and (C) the ability to **communicate externally**. Our design never
combines all three without a guard between them:

| Leg | In our app | Guard that breaks the chain |
|-----|-----------|-----------------------------|
| **A. Private data** | RBAC-scoped context, retrieval | Least-privilege + PII redaction; minimum-necessary context per request. |
| **B. Untrusted content** | Retrieved docs, user input | Input injection rail; **Spotlighting** marks retrieved text as data; validate-before-write. |
| **C. External communication** | Action tools (send/write/call) | Per-persona allowlists; **human-in-the-loop gate** on high-risk actions; every action audited + reversible. |

**Design rule:** untrusted content (B) can never reach an external-comms tool (C)
while private data (A) is in context without passing the injection rail,
Spotlighting, and — for any high-risk action — the human gate.

---

## 3a. Production hardening — new surfaces from the P0–P6 upgrade

The productionisation added four durable, Postgres-backed capabilities. Each is a new
attack surface as well as a new control; the mitigations below are what makes them
*safe* additions rather than liabilities.

| New surface | Threat it introduces | Mitigation | Where |
|---|---|---|---|
| **Multi-tenant isolation** (tenants, users, RLS) | Cross-tenant data/spend leakage; privilege escalation across tenants | App-level `WHERE tenant_id` scoping as the **primary** isolator on every scoped query, with **Postgres RLS** (`SET app.tenant_id` + a `tenant_isolation` policy on 25 tables) as the layer beneath it; JWT-pinned `tenant_id`; `_scope_tenant` rejects cross-tenant reads/writes (403). **The order matters and is stated deliberately:** `rls_fail_closed` defaults to `False`, so the installed predicate admits everything on an unbound session GUC. No read path skips its `WHERE` clause, which is why this is inert defence-in-depth rather than a leak — but nothing here may be described as fail-closed until `RLS_FAIL_CLOSED=true` installs the closed predicate. | ADR 0008; `aegis/governance/rls.py`, `data/session.py`, `api/routes.py`; tests: `test_cross_tenant_isolation.py`, `test_admin_governance.py`, `aegis/tests/governance/test_rls_enforcement.py` |
| **Budget / rate governance** (DoS via tokens) | Cost-exhaustion / token-flood DoS; unattributable spend | **Enforced inward** budgets (user clamped to tenant) + RPM/TPM checked at the **one** gateway chokepoint *before* spend; refusal is a clean terminal event, not a crash; durable per-call ledger | ADR 0008; `core/llm.py`, `data/governance.py`; tests: `test_governed_budget.py`, `test_governance_enforcement.py` |
| **Durable approval queue** (async inbox) | A poisoned/replayed decision double-executes an action; a lost pause silently drops a gated action | **Optimistic `PENDING→RESUMING` lock** (only the winner resumes) + an `approval_id` tool idempotency key ⇒ **exactly-once**; durable row is the source of truth; SLA sweeper auto-**rejects** HIGH-risk on timeout (fail-safe) | ADR 0005; `agent/orchestrator.py`, `data/approvals.py`; tests: `test_durable_approvals.py`, `test_durable_approval_roundtrip.py` |
| **ML abstention** (new terminal band) | An over-confident action on a degenerate prediction; a silent no-op the operator can't see | Graded conformal bands: **abstain** on degenerate/no-coverage/empty sets — do not act, emit `abstained`, return an "insufficient confidence" answer; HIGH-risk never autonomous (D5) | ADR 0007; `agent/graph.py`, `agent/deps.py`; tests: `test_autonomy_bands.py`, `test_ml_abstain.py` |

> **Quality gate.** An offline, deterministic eval (`app/eval/`, `aegis/evals/`, gated by
> `tests/eval/test_eval_gate.py`) scores the hybrid retrieval + answer path
> (context-precision/recall + groundedness proxies, lexical and model-free) and **fails
> CI** on a regression — so retrieval quality is a defended invariant, not an assumption.
> An optional reasoning-model LLM-as-judge is available behind `TAIF_EVAL_LLM_JUDGE`.
> Separately, and not to be confused with the gate: `ragas>=0.4.3` **is** a dependency
> and `aegis/evals/libs/ragas_suite.py` runs the real library's metrics through the Aegis
> gateway, so every judge call is budget-checked, rate-limited, traced and written to
> `usage_ledger`. The gate stays lexical because it must run with no network and no
> spend; the live suite is where the model-graded numbers come from.

---

## 4. Cross-cutting controls (defense-in-depth)

- **Statelessness** — LLM calls are stateless; context injected cleanly per request.
- **Sandboxing & scoping** — every tool is least-privilege, boundaried like external traffic.
- **Observability** — exact prompt, output, tool rationale, model, and trace id logged to the **Postgres audit log** (a first-class table) on every autonomous action, each row hash-chained to the one before it so a rewrite is detectable (`GET /v1/audit/verify`; rows written before the chain existed are reported as `unchained`, never counted as verified). *You can't secure what you can't see.*
- **Adversarial testing** — the in-repo battery runs offline on every CI pass (`owasp-full`: 66 probes — 50 attacks, 16 benign controls — blocking 40 of 50 at a 0% false-positive rate, with every leak declared in advance rather than curated out), and **Garak** is run against the API endpoint pre-demo for an external, comparable number.

> The unifying claim: **every autonomous action is uncertainty-bounded
> (conformal), explainable (SHAP), guarded (rails), and fully traced (OTel +
> audit log)** — OWASP-aligned, PII-redacting, and audit-ready for enterprise
> security review.

---

*Numbering note:* IDs follow the current **2025** OWASP LLM list (v2.0), in which
Sensitive Information Disclosure is **LLM02**, Improper Output Handling is
**LLM05**, and Excessive Agency is **LLM06** — a renumbering from the 2023 list
referenced in `docs/security/overview.md` (which used LLM06/LLM02/LLM08 respectively). The
mitigations are unchanged; only the identifiers were reconciled to the current
standard.
