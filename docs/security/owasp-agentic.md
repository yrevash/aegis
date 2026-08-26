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
| 3 | **Prompt injection / jailbreak** — instructions smuggled in user text | **Layered injection defense (fail-closed).** Deterministic signature backstop (regex, no API) → cheap-model classifier; an unparseable/unavailable classifier is treated as **injection** (fail closed). Single entry point shared by the programmatic rail and the NeMo Colang `self_check_injection` action, so they can't diverge. | `guardrails/classifier.py` (`detect_injection`, `deterministic_injection`, `classify_injection`), `guardrails/rails.py` (`check_input`) |
| 4 | **Sensitive-information disclosure** — PII/secrets leak in or out | **PII redaction on both paths, before the classifier and before the user.** Pure-code anchored-regex detectors (+ Luhn for cards) mask PII on the **inbound** path *before* it reaches the model **or** the classifier API (avoiding a self-inflicted disclosure), and on the **outbound** path before the answer is returned. Annotated `LLM06` in the rails. | `guardrails/pii.py`, `guardrails/rails.py` (`check_input`/`check_output`), `guardrails/config/rails/*.co` |
| 5 | **Insecure output handling / trust-chain abuse** — agent's output used downstream unchecked | **Output rail: schema validation → content filter → PII.** Structural well-formedness (`LLM02`) plus a content-filter backstop against system-prompt leakage before any answer is trusted downstream. | `guardrails/rails.py` (`check_output`), `guardrails/schema.py` |
| 6 | **Identity / privilege abuse across tenants** — one tenant reads another's data or spend | **Multi-tenant RBAC + Postgres RLS + budgets.** Per-request `GovernanceContext` (tenant/user/role) threaded via `contextvars`; `set_tenant_scope` sets the `app.tenant_id` GUC so Postgres **row-level security** policies (`users`, `usage_ledger`, `approvals`) engage per connection, with app-level tenant filtering as belt-and-suspenders. Budget/rate caps enforced at the LiteLLM chokepoint. | `core/governance.py`, `data/session.py` (`set_tenant_scope`, RLS policies), `data/governance.py` (`enforce_governance`, `effective_limits`) |
| 7 | **Untraceable / unaccountable actions** — no record of who/what did it | **Immutable audit log + end-to-end trace.** Every autonomous or approved tool call writes an `AuditLog` row (actor, model, trace_id, payload, approver, tenant); every run is an OpenTelemetry trace of typed spans. | `data/audit.py` (`record_audit`), `adapter/tools.py` (`_emit_audit`), `observability/otel.py`, `observability/semconv.py` |
| 8 | **Cascading failures / resource exhaustion** — runaway loops or spend | **Bounded self-repair + budget chokepoint.** The plan→act→reflect loop is hard-capped by `AgentConfig.max_plan_iterations` (guaranteed termination); token/USD/RPM/TPM caps raise `BudgetExceededError` at the single model chokepoint. | `agent/graph.py` (`reflect`), `agent/deps.py` (`max_plan_iterations`), `data/governance.py` (`enforce_governance`) |

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
| **Adversarial testing** | Garak red-team runner (see below) | `backend/scripts/garak_scan.py` | Runner present; scan is **run on the day** (garak not a runtime dep) |

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
  `GUARDRAILS_ENGINE=nemo`; the rail-only checks never invoke the `main` model, so screening
  text needs no key. Files: `guardrails/nemo.py`, `guardrails/rails.py`,
  `guardrails/config/rails/*.co`.
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
3. **Everything is accountable** — RBAC + Postgres RLS + budgets isolate tenants
   (`data/*`, `core/governance.py`), and an immutable audit log + OTel trace records every
   action (`data/audit.py`, `observability/*`).
4. **The claims are testable** — the guardrails have unit tests, and the Garak runner
   produces a real, comparable red-team artifact on the day.

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
