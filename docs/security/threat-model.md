# Threat Model — OWASP LLM Top 10 · OWASP Agentic (ASI) Top 10 · Lethal Trifecta

One-page mapping of this platform to the industry AI-security frameworks, with
our concrete mitigation per risk. Content sourced from `docs/security/overview.md`;
implementations live in `app/guardrails/`, `app/retrieval/spotlight.py`,
`app/data/` (audit log), and the agent layer. Detection is **API-based or
pure code — no local guardrail model** (16 GB / no-GPU constraint).

Frameworks: **OWASP Top 10 for LLM Applications v2.0 (2025)**, **OWASP Top 10 for
Agentic Applications (ASI, 2026)**, and Simon Willison's **"lethal trifecta."**

---

## 1. OWASP Top 10 for LLM Applications (2025)

| ID | Risk | Applies? | Our mitigation |
|----|------|----------|----------------|
| **LLM01** | Prompt Injection | ✅ Core | **Input rail**: API injection/jailbreak classifier (`ModelRole.CHEAP`, fails closed) + schema validation; **Spotlighting** marks retrieved data as non-instructions; least-privilege tools + human gate limit blast radius. |
| **LLM02** | Sensitive Information Disclosure | ✅ Core | Self-built **PII scan + redaction on both rails** (`app/guardrails/pii.py`); PII is redacted *before* the classifier API call; RBAC least-privilege data access; minimum-necessary context per request. |
| **LLM03** | Supply Chain | ⚠️ Partial | Pinned deps in `pyproject.toml`; no local model weights to poison; single vetted gateway (LiteLLM → TCS GenAI Lab) instead of many provider SDKs. |
| **LLM04** | Data & Model Poisoning | ✅ (RAG) | **Validate content before writing to the graph** (`app/retrieval/validation.py`); governed ingestion; Spotlighting neutralises poisoned retrieved spans at read time. |
| **LLM05** | Improper Output Handling | ✅ Core | **Output rail**: schema/format validation + content filter *before* any downstream use (`app/guardrails/schema.py`). |
| **LLM06** | Excessive Agency | ✅ Core | **Tool allowlists per persona** (least privilege); **graded conformal autonomy bands** (autonomous / defer / **abstain**) on top of a **durable, checkpointed human-in-the-loop gate** (ADR 0005/0007); idempotent (exactly-once), reversible, **audited** actions. |
| **LLM07** | System Prompt Leakage | ✅ | Output content filter denylists system-prompt markers; no secrets placed in prompts; Spotlighting keeps retrieved data from eliciting the prompt. |
| **LLM08** | Vector & Embedding Weaknesses | ✅ (RAG) | Validate-before-write to the vector/graph store; RBAC-scoped retrieval; Spotlighting on retrieved chunks (indirect-injection defense). |
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
| **ASI03** | Identity and Privilege Abuse | **Multi-tenant RBAC** — signed JWT claims (`{sub, role, tenant_id}`), Argon2id passwords, a three-tier hierarchy (platform / tenant admin / user); **Postgres RLS + app-scoping** isolate every tenant's data; separate `/admin` surfaces; least-privilege tool grants (ADR 0008). |
| **ASI04** | Agentic Supply Chain Vulnerabilities | Single vetted model gateway; pinned deps; no arbitrary local model loading. |
| **ASI05** | Unexpected Code Execution (RCE) | No agent code-exec tool by default; tools are typed, allowlisted callables — not a shell. |
| **ASI06** | Memory & Context Poisoning | **Azure Spotlighting** (delimiting + datamarking) marks retrieved text as *data, not instructions*; and the fourth rail stage, `GuardStage.MEMORY_WRITE`, screens a candidate fact **before** it reaches the durable store — the other three stages cannot see this attack, because the turn that poisons the store and the turn poisoned by it are different turns. |
| **ASI07** | Insecure Inter-Agent Communication | Single-orchestrator design; every hop is traced (OTel) and audited; no unauthenticated agent-to-agent channel. |
| **ASI08** | Cascading Failures | Human gate + conformal uncertainty stop low-confidence actions from chaining; idempotent, reversible actions; per-run trace id. |
| **ASI09** | Human-Agent Trust Exploitation | Every autonomous action is logged with approver + trace id; the gate forces explicit human approval on high-risk steps — no silent action. |
| **ASI10** | Rogue Agents | Bounded autonomy: allowlisted tools, human gate, full audit log — an agent cannot exceed its declared scope unobserved. |

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
| **Multi-tenant isolation** (tenants, users, RLS) | Cross-tenant data/spend leakage; privilege escalation across tenants | **Postgres RLS** as the enforced boundary (`SET app.tenant_id` + per-table policy) **plus** app-level `WHERE tenant_id` scoping (defense-in-depth); JWT-pinned `tenant_id`; `_scope_tenant` rejects cross-tenant reads/writes (403) | ADR 0008; `data/session.py`, `api/routes.py`; tests: `test_cross_tenant_isolation.py`, `test_admin_governance.py` |
| **Budget / rate governance** (DoS via tokens) | Cost-exhaustion / token-flood DoS; unattributable spend | **Enforced inward** budgets (user clamped to tenant) + RPM/TPM checked at the **one** gateway chokepoint *before* spend; refusal is a clean terminal event, not a crash; durable per-call ledger | ADR 0008; `core/llm.py`, `data/governance.py`; tests: `test_governed_budget.py`, `test_governance_enforcement.py` |
| **Durable approval queue** (async inbox) | A poisoned/replayed decision double-executes an action; a lost pause silently drops a gated action | **Optimistic `PENDING→RESUMING` lock** (only the winner resumes) + an `approval_id` tool idempotency key ⇒ **exactly-once**; durable row is the source of truth; SLA sweeper auto-**rejects** HIGH-risk on timeout (fail-safe) | ADR 0005; `agent/orchestrator.py`, `data/approvals.py`; tests: `test_durable_approvals.py`, `test_durable_approval_roundtrip.py` |
| **ML abstention** (new terminal band) | An over-confident action on a degenerate prediction; a silent no-op the operator can't see | Graded conformal bands: **abstain** on degenerate/no-coverage/empty sets — do not act, emit `abstained`, return an "insufficient confidence" answer; HIGH-risk never autonomous (D5) | ADR 0007; `agent/graph.py`, `agent/deps.py`; tests: `test_autonomy_bands.py`, `test_ml_abstain.py` |

> **Quality gate.** An offline, deterministic eval (`app/eval/`, gated by
> `tests/eval/test_eval_gate.py`) scores the hybrid retrieval + answer path
> (context-precision/recall + groundedness proxies) and **fails CI** on a regression —
> so retrieval quality is a defended invariant, not an assumption. An optional
> reasoning-model LLM-as-judge is available behind `TAIF_EVAL_LLM_JUDGE`.

---

## 4. Cross-cutting controls (defense-in-depth)

- **Statelessness** — LLM calls are stateless; context injected cleanly per request.
- **Sandboxing & scoping** — every tool is least-privilege, boundaried like external traffic.
- **Observability** — exact prompt, output, tool rationale, model, and trace id logged to the **Postgres audit log** (a first-class table) on every autonomous action. *You can't secure what you can't see.*
- **Adversarial testing** — **Garak** run against the API endpoint pre-demo; block-rate captured as evidence.

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
