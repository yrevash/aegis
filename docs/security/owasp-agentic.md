# Aegis Security — mapped to OWASP Top 10 for Agentic Applications (2026)

Aegis is an *agentic* system: an LLM that **plans, retrieves, and calls tools that change
data**. That is a bigger attack surface than a chat endpoint, so this doc maps Aegis's
guardrail + governance layer to the **OWASP Top 10 for Agentic Applications (2026)** — the
agent-specific risk list from the OWASP GenAI Security Project, which is **distinct from**
the older *OWASP Top 10 for LLM Applications*. Every control below names a **real file**
you can open.

> **Posture, stated honestly up front — defense-in-depth, not prevention.** No one has
> "solved" prompt injection. Reported attack-success rates against frontier models remain in
> the **~50–84%** range even with best-effort defenses, so Aegis does **not** claim to block
> injection — it claims **layers**: redact before the model sees anything, screen with a
> deterministic backstop *and* a classifier, and — the decisive layer — **never let the
> model take a consequential action without a human**. The security stance of
> `docs/security/overview.md` §3 is "security is layers, not one tool," and the code is built that way.

> **Naming note.** This hedge used to say the ASI numbering "should be confirmed against
> the current OWASP publication before quoting an ASI0x identifier". That was true when it
> was written and stopped being true on **9 December 2025**, when the OWASP GenAI Security
> Project published the *Top 10 for Agentic Applications*, version 2026. The identifiers
> and titles used here and in `threat-model.md` are now taken **verbatim from that
> document's own PDF**, not from secondary sources — several of which disagree with it, and
> with each other.
>
> The themes below are organised by subject rather than by ASI number, so the two
> numbering schemes are deliberately not conflated. Where Aegis's own code annotates the
> older LLM Top 10 (e.g. `LLM02` insecure output handling, `LLM06` sensitive-info
> disclosure), those annotations are kept so the lineage stays traceable.

---

## Risk → Aegis control → real file

| # | Agentic risk (2026 theme) | Aegis control | Real file(s) |
|---|---|---|---|
| 1 | **Excessive agency / autonomy** — agent takes a consequential action on its own | **Risk-tiered tools + human gate.** Every tool carries a `RiskLevel`; a proposed action at/above `AgentConfig.gate_min_risk` (default **HIGH**) routes to the LangGraph `approval` node, which `interrupt`s and waits for a human. **ML never gates** — only tool risk does. | `adapter/tools.py` (`ToolSpec.risk`, `TOOL_REGISTRY`), `agent/graph.py` (`gate`, `approval` nodes), `agent/deps.py` (`AgentConfig.gate_min_risk`) |
| 2 | **Tool misuse / hijacking** — agent coerced into calling a tool it shouldn't | **Per-persona allowlist enforced *before* any side effect.** `run_tool` checks `ALLOWLIST[persona]` and raises `ToolNotAllowedError` before the handler runs; tools are **typed** (pydantic-validated args), **idempotent**, and **reversible** (each result carries an `InverseAction`). | `adapter/tools.py` (`ALLOWLIST`, `run_tool`, `is_allowed`, `InverseAction`) |
| 3 | **Prompt injection / jailbreak** — instructions smuggled in user text | **Layered injection defense (fail-closed), at four stages.** Deterministic signature backstop (regex, no API) → cheap-model classifier; an unparseable/unavailable classifier is treated as **injection** (fail closed). `GuardStage` has four members — `INPUT`, `OUTPUT`, `TOOL_RESULT` and `MEMORY_WRITE` — because the first two cannot see an override arriving in a tool return, and none of the first three can see a poisoned *fact*. Single entry point shared by the programmatic rail and the NeMo Colang `self_check_injection` action, so they can't diverge. | `guardrails/classifier.py` (`detect_injection`, `deterministic_injection`, `classify_injection`), `guardrails/rails.py` (`check_input`, `check_output`, `check_tool_result`), `aegis/core/types.py` (`GuardStage`) |
| 4 | **Sensitive-information disclosure** — PII/secrets leak in or out | **PII redaction on both paths, before the classifier and before the user.** Pure-code anchored-regex detectors (+ Luhn for cards) mask PII on the **inbound** path *before* it reaches the model **or** the classifier API (avoiding a self-inflicted disclosure), and on the **outbound** path before the answer is returned. Annotated `LLM06` in the rails. | `guardrails/pii.py`, `guardrails/rails.py` (`check_input`/`check_output`), `guardrails/config/rails/*.co` |
| 5 | **Insecure output handling / trust-chain abuse** — agent's output used downstream unchecked | **Output rail: schema validation → content filter → PII.** Structural well-formedness (`LLM02`) plus a content-filter backstop against system-prompt leakage before any answer is trusted downstream. | `guardrails/rails.py` (`check_output`), `guardrails/schema.py` |
| 6 | **Identity / privilege abuse across tenants** — one tenant reads another's data or spend | **Multi-tenant RBAC, app-level scoping, RLS underneath.** Per-request `GovernanceContext` (tenant/user/role) threaded via `contextvars`; `set_tenant_scope` sets the `app.tenant_id` GUC so the `tenant_isolation` policy on **25** tenant-scoped tables engages per connection. Read the order carefully: the **application's own `WHERE tenant_id = …` predicate is what carries the boundary today**, and RLS is the belt behind it — see the fail-open note below. Budget/rate caps enforced at the LiteLLM chokepoint. | `core/governance.py`, `data/session.py` (`set_tenant_scope`), `aegis/governance/rls.py` (`_TENANT_SCOPED_TABLES`, `tenant_policy_statements`), `data/governance.py` (`enforce_governance`, `effective_limits`) |
| 7 | **Untraceable / unaccountable actions** — no record of who/what did it | **Hash-chained audit log + end-to-end trace.** Every autonomous or approved tool call writes an `AuditLog` row (actor, model, trace_id, payload, approver, tenant), and each row carries `prev_hash`/`row_hash` over eight length-prefixed fields, so a rewritten or removed row breaks every row after it. `GET /v1/audit/verify` walks the chain per tenant and reports `intact`, `checked`, `broken_at`, `head` — and counts rows written **before** the chain existed in a separate `unchained` field rather than folding them into the verdict. Append-only is a database privilege on the serving role, not a convention; the owner connection can still rewrite the trail, which makes tampering *require* that connection rather than impossible. | `aegis/governance/chain.py` (`row_fingerprint`, `chain_hash`), `data/audit.py` (`record_audit`, `verify_audit_chain`), `adapter/tools.py` (`_emit_audit`), `observability/otel.py`, `observability/semconv.py` |
| 8 | **Cascading failures / resource exhaustion** — runaway loops or spend | **Four independent stops, not one.** An iteration cap (`AgentConfig.max_plan_iterations`, guaranteed termination); a `verify` node that checks the world rather than the model's opinion of itself and halts a call that has failed **identically three times** (`OSCILLATING`), so the loop cannot spend its budget arguing with itself or with a rail; two token ceilings — `max_trajectory_tokens` (36000) before each model call in a sub-agent lane and `max_tool_result_tokens` (4000) on every tool result on both the lane and the main graph, both `TIGHTEN_ONLY` in the settings catalogue; and token/USD/RPM/TPM caps raising `BudgetExceededError` at the single model chokepoint. | `agent/graph.py` (`verify`, `reflect`), `agent/subagent.py` (the trajectory ceiling), `agent/deps.py` (`max_plan_iterations`, `max_trajectory_tokens`, `max_tool_result_tokens`), `data/governance.py` (`enforce_governance`) |
| 9 | **Agent-to-agent trust** — a peer talks to Aegis, or Aegis's routing field talks to the database | **A2A 1.0, with the routing tenant structurally barred from the data plane.** `GET /.well-known/agent-card.json`, `GET /.well-known/jwks.json` (ES256 public half) and `POST /v1/a2a` (`SendMessage`, `GetTask`) are served behind `require_auth`. The `tenant` routing field is opaque and attacker-controlled: it selects which agent is addressed and **never** sets the database scope, which is derived from the bearer token alone; a mismatch is refused with one identical code and message across every rejection branch, so the error cannot be used to enumerate tenants. The card declares `streaming`, `pushNotifications` and `extendedAgentCard` all **false**, and is served **unsigned** unless `a2a_public_origin` is configured — because an earlier version let the `Host:` header rewrite the origin inside the *signed* card. | `app/a2a/routes.py`, `app/a2a/rpc.py` (`resolve_addressed_tenant`, `TenantMismatchError`), `app/a2a/card.py`, `app/a2a/signing.py` |
| 10 | **Agentic supply chain** — nobody can enumerate what the agent is made of | **An Agent Bill of Materials.** `GET /v1/platform/agbom` emits CycloneDX 1.6 as `application/vnd.cyclonedx+json`, inventorying the tool registry (with risk tier, personas and read-only flag), the model fleet, the four rail stages and the knowledge collections. It is **deterministic**: the `serialNumber` is derived from a SHA-256 of the sorted component list, so two builds of an unchanged deployment produce the same serial. Tools are emitted as `type: "application"` because `"tool"` is not in the CycloneDX 1.6 component-type enum — CycloneDX's own `tools` means the tools that *produced* the document — and emitting `"tool"` yields a file that fails schema validation. | `app/platform/agbom.py`, `backend/tests/platform/test_agbom.py` |

---

## OWASP AI Agent Security Cheat Sheet — control checklist mapped to Aegis

The [OWASP AI Agent Security Cheat Sheet](https://cheatsheetseries.owasp.org/) enumerates
concrete defensive controls for agents. Aegis's coverage:

| Cheat-sheet control | Aegis implementation | Real file(s) | Honest status |
|---|---|---|---|
| **Least privilege** | Per-persona tool allowlist; a persona sees only the tools it may call | `adapter/tools.py` (`ALLOWLIST`, `tools_for`) | Enforced pre-side-effect |
| **Tool restriction** | Typed, risk-tiered registry; unknown tool → `UnknownToolError` | `adapter/tools.py` (`TOOL_REGISTRY`, `ToolSpec`) | Enforced |
| **Human approval** | HITL `interrupt` gate for actions ≥ `gate_min_risk` (default HIGH) | `agent/graph.py` (`gate`/`approval`), `agent/deps.py` | Enforced; durable approval rows in `data/approvals.py` |
| **Output validation** | Output rail: schema → content filter → PII redaction | `guardrails/rails.py` (`check_output`), `guardrails/schema.py` | Enforced (assumes complete answer — see streaming caveat) |
| **Memory isolation** | Answer/retrieval caches partition by **scope** (tenant + persona + role); a hit under one scope can never be returned for another (checked twice — index + entry) | `retrieval/answer_cache.py` (`AnswerCache`, `_index_key`) | Enforced as a correctness+security requirement |
| **Adversarial testing** | Garak red-team runner (see below), plus the in-repo battery that runs offline every CI pass | `backend/scripts/garak_scan.py`; `aegis/redteam/battery.py` | Garak runner present, scan **run on the day** (not a runtime dep); the in-repo `owasp-full` battery is 66 probes — 50 attacks and 16 benign controls — blocking 40 of 50 offline at a 0% false-positive rate |
| **Per-call authority, not per-session** | The MCP front door (`aegis-adapter-tools`) re-resolves the caller from the `users` table on **every** message rather than once at session open, and authorises on the row's role, not the token's claim | `backend/src/app/mcp/server.py` (`resolve_caller`, `live_principal`) | Enforced, and fails closed: a deactivated, missing or unreachable account raises rather than falling back to the claim — so a forged token claiming a higher role is downgraded to whatever the database says |

**Streaming caveat (stated in the code).** The output rail assumes the *complete* answer.
For token streaming you must buffer briefly or scan post-hoc before emitting — do **not**
stream raw tokens straight past the rail. See the note in `app/guardrails/rails.py` and
`app/guardrails/__init__`.

---

## Guardrail design lineage (cited, not overclaimed)

- **NeMo Guardrails (NVIDIA)** — *wired.* `nemoguardrails` (>=0.23, Colang 1.0) is a real
  optional dependency. The security policy is expressed declaratively as **Colang** flows
  (`guardrails/config/rails/input.co`, `output.co`) whose custom actions delegate straight
  back to the same programmatic functions the agent graph calls — *one policy, two front
  doors* (the fast programmatic API + the human-readable Colang artifact). Selected via
  `GUARDRAILS_ENGINE`, which takes `programmatic` (**the default, and what this checkout's
  `backend/.env` leaves in force**), `nemo`, or `both` — pipeline first, then Colang over
  what it returned, strictest verdict winning. The rail-only checks never invoke the
  `main` model, so screening text needs no key. Files: `guardrails/nemo.py`,
  `guardrails/rails.py`, `guardrails/config/rails/*.co`.
- **LlamaFirewall (Meta)** — *design lineage, not integrated.* Aegis's layered
  input-rail-before-the-model + injection-classifier + fail-closed shape follows the same
  guardrail-framework philosophy LlamaFirewall articulates. **`llamafirewall` is not a
  dependency and is not called by Aegis.** Cited as design lineage only.
  Reference: *LlamaFirewall: An open source guardrail system for building secure AI agents*,
  arXiv:2505.03574.

---

## Red-team: Garak scan (real runner, run on the day)

`backend/scripts/garak_scan.py` is a **runner** for
[NVIDIA garak](https://github.com/NVIDIA/garak) (LLM vulnerability scanner) — **not** a
stored result. It honestly degrades: if garak isn't installed, or the gateway key / network
/ backend isn't there, it prints install+run instructions and **exits non-zero without
writing any report**. Two targets tell the "before vs after guardrails" story:

- `--target gateway` — points garak's `rest` generator at the **base model** via the same
  OpenAI-compatible gateway the platform uses (the honest raw-vulnerability baseline).
- `--target endpoint` — points garak at the **guardrail-protected `/query` SSE** endpoint
  (logs in for a JWT first), so a blocked probe surfaces as a refusal/guardrail event.

Curated probes: `promptinject`, `dan`, `encoding`, `leakreplay`. `garak` is a **day-of dev
tool (`pip install garak`), not a core runtime dependency.** Comparing the two targets'
block rates is exactly what quantifies the ~50–84% base-model injection reality this doc
opens with — and how much the layered rails mitigate it. See `docs/security/overview.md` §3.

---

## Honest bottom line

Aegis's security value is **not** "we block prompt injection" — nobody credibly can. It is:

1. **Layers, not one tool** — redact → deterministic backstop → classifier (fail-closed) →
   schema/content output rail, each able only to *tighten* the verdict.
2. **The human gate is the real safety net** — a consequential (HIGH-risk) action *cannot*
   execute without a human, regardless of what talked its way past the rails
   (`agent/graph.py`, `adapter/tools.py`).
3. **Everything is accountable** — RBAC + app-level tenant scoping (with RLS behind it) +
   budgets isolate tenants (`data/*`, `core/governance.py`), and a **hash-chained,
   independently verifiable** audit log + OTel trace records every action
   (`aegis/governance/chain.py`, `data/audit.py`, `GET /v1/audit/verify`,
   `observability/*`). "Immutable" is the word this section used to use and it was one
   notch too strong: append-only is a privilege held against the *serving* role, and the
   owner connection can still rewrite a row — what the chain adds is that doing so is
   **detectable**.
4. **The claims are testable** — the guardrails have unit tests, the in-repo red-team
   battery reports a false-positive rate beside its block rate on every run, and the
   Garak runner produces a real, comparable external artifact on the day.

> **One posture claim this document will not make: RLS is not fail-closed here.**
> `rls_fail_closed` defaults to `False`, so the installed `tenant_isolation` predicate
> admits every row when the session's `app.tenant_id` GUC is unset, empty or non-numeric.
> The boundary is carried by the application's `WHERE tenant_id = …` predicate on every
> scoped query, and no read path skips it — which makes RLS today an inert second layer
> rather than an open door, and makes "the database enforces it regardless of what the
> handler wrote" a statement about the fail-closed configuration and not the running one.
> Flipping `RLS_FAIL_CLOSED=true` installs the closed predicate
> (`aegis/src/aegis/governance/rls.py`, `_TENANT_ISOLATION_PREDICATE_CLOSED`).

---

## References

- **OWASP Top 10 for Agentic Applications (2026)** / OWASP GenAI Security Project —
  <https://genai.owasp.org/> (agentic list; distinct from the OWASP Top 10 for LLM
  Applications).
- **OWASP AI Agent Security Cheat Sheet** — <https://cheatsheetseries.owasp.org/>.
- **NeMo Guardrails (NVIDIA)** — <https://github.com/NVIDIA/NeMo-Guardrails> (real optional
  dependency; Colang policy).
- **LlamaFirewall (Meta)** — arXiv:2505.03574 (design lineage; **not** integrated).
- **garak (NVIDIA)** — <https://github.com/NVIDIA/garak> (day-of red-team tool; runner at
  `backend/scripts/garak_scan.py`).
- **Prompt-injection efficacy (~50–84%)** — the honest framing that best-effort defenses do
  not "solve" injection; motivates the human-gate-as-safety-net posture (`docs/security/overview.md`
  §3).
