# Security design, and how to show it

> **Where this sits, 2026-08-28.** This is the security *design brief* — what is built,
> how it maps to standards, and how to demonstrate it. Two neighbours answer different
> questions and are more current where they overlap:
> [`../compliance/README.md`](../compliance/README.md) is the control-by-control
> evidence map (124 controls across 13 frameworks, every claim resolving to a file,
> route or test — and readiness evidence, never certification: Aegis holds no ISO 27001,
> ISO/IEC 42001, SOC 2 or EU AI Act attestation), and
> [`../teaching/security.md`](../teaching/security.md) explains the live posture surface
> that reports which controls are wired *in the running process*. Sections 7 and 8 below
> are implementation checklists from the original build and describe work that has since
> shipped; read them as history, not as a to-do list.

> Security is a rubric axis ("code quality, maintainability **and security**") AND our biggest differentiator, because in 2026 AI security is a specific, nameable discipline most teams skip. This file defines *what we build*, *how it maps to standards*, and *how to demo and market it*. Nothing heavy runs locally (16 GB, no Docker) — detection is API-based or self-built code.

---

## 1. Frameworks we align to (name these explicitly to the jury)

- **OWASP Top 10 for LLM Applications** — application-layer risk.
- **OWASP Top 10 for Agentic Applications (ASI)** — agent-layer risk (goal hijacking, excessive agency, etc.).
- **The "lethal trifecta"** — private data + untrusted content + external communication together are the deterministic root of prompt-injection exploitability. Design to never combine all three unguarded.

Deliverable: a **one-page threat model** mapping our app to these lists, with our mitigation per risk. This single artifact scores on security *and* documentation.

---

## 2. The top risks and our mitigations

| Risk (OWASP) | What it is | Our mitigation |
|---|---|---|
| Prompt injection (LLM01) | Crafted input read as instructions | Input classifier (API) + input rail + least-privilege tools + human gate |
| RAG/vector poisoning | Malicious content injected into the store, retrieved later | Validate content before writing to graph; **Azure Spotlighting** marks retrieved text as data, not instructions |
| Sensitive info disclosure (LLM02) | PII/secret leakage | PII detection + **redaction on the outbound path**; least-privilege data access via RBAC |
| Insecure output handling (LLM05) | Unsafe/malformed model output used downstream | Output **schema validation** + filtering before use |
| Excessive agency (LLM06 / agentic) | Agent does more than intended | Tool **allowlists per persona**; high-risk/high-uncertainty → **human gate**; idempotent, reversible, audited actions |
| Memory poisoning (memory is built) | Adversarial "fact" persists across sessions | Governed memory: the fourth rail stage, `MEMORY_WRITE`, screens a candidate fact before it reaches the durable store, and a refusal is written to `memory_write_log` under its own operation |

> *ID note:* IDs follow the current **OWASP Top 10 for LLM Applications v2.0
> (2025)** — Sensitive Information Disclosure is **LLM02**, Improper/Insecure
> Output Handling is **LLM05**, Excessive Agency is **LLM06** — matching
> `docs/security/threat-model.md`. (The 2023 list numbered these LLM06 / LLM02 / LLM08.)

---

## 3. Layered guardrail architecture (defense-in-depth, RAM-friendly)

Security is layers, not one tool. All detection is API-based or pure code — **no local guardrail models** (Llama Guard etc. are dropped due to RAM).

**Input rail** (before anything reaches the model):
- **PII scan + redaction** (self-built: regex/heuristics on the inbound path).
- **Injection/jailbreak detection** via an **API classifier** — a cheap `gpt-4o-mini` call that returns "is this prompt injection? yes/no" (optionally an LLM-as-guardrail that can reason about novel attacks).
- **Schema/format validation** on the request.

**Agent layer:**
- **Least-privilege tools**, allowlisted per persona.
- Treat the LLM as a hostile user — agent functions behind the same rate limits / IAM boundaries as external traffic.
- **Human-in-the-loop gate** on high-risk actions or high-conformal-uncertainty predictions.
- **Azure Spotlighting** on retrieved content (indirect-injection defense).

**Output rail** (before the user/downstream sees it):
- **Output schema validation.**
- **PII redaction / content filter.**
- Streaming caveat: if output streams token-by-token, the output guard can't scan first — either buffer briefly, or scan post-hoc and redact. Coordinate with `frontend.md`.

**Four stages, not two.** The list above is written as "input rail" and "output rail" because that is where the design started. `GuardStage` (`aegis/src/aegis/core/types.py`) now has **four** members, and the two that were added are the two the original pair structurally could not see:

- `INPUT` and `OUTPUT` — the two ends of a turn.
- `TOOL_RESULT` — a tool pulls third-party content (a search result, a scraped page, a record from a system nobody here controls) straight into the agent's context, where the model reads it as instructions-adjacent text. Screening the user and screening the answer leaves that whole surface open, which is OWASP LLM01 exactly.
- `MEMORY_WRITE` — a poisoned *fact* is screened by none of the other three, because the turn that poisons the store and the turn poisoned by it are **different turns**. The screen sits at `app.memory.screen.memory_write_screen` and is bound on **both** drain paths — the hot path the agent fires after every turn (`backend/src/app/agent/deps.py`, `MemoryDeps._run_consolidation`) and the 60-second backstop sweeper (`backend/src/app/main.py`, `_run_memory_sweeper`) — because binding only one of them is how the rail came to be unbound twice. Test: `backend/tests/memory/test_write_screen_bound.py`.

**Orchestration — two front doors over one policy.** The fast, offline-testable programmatic pipeline lives in `aegis/src/aegis/guardrails/pipeline.py` (the backend keeps a shim at `backend/src/app/guardrails/rails.py`); the declarative **NeMo Guardrails Colang policy** lives in `aegis/src/aegis/guardrails/config/rails/*.co` and is genuinely loaded and executed via `LLMRails`. `GUARDRAILS_ENGINE` takes `programmatic`, `nemo` or `both`; `both` runs the offline pipeline first and then the Colang engine over what the pipeline returned, folding verdicts strictest-wins and accumulating redactions, and an already-blocked payload is never forwarded to the second engine. Its custom actions call the same rail functions, so the two cannot drift. Verified by `tests/guardrails/test_nemo_rails.py`, which runs the real engine and asserts the input/output rails block a jailbreak and redact PII. The Colang file doubles as a human-readable security artifact.

> **Which engine is actually running — stated rather than assumed.** `guardrails_engine` defaults to **`programmatic`** (`backend/src/app/config.py`), and the checked-in run file `backend/.env` does not override it, so what runs on this box today is the programmatic pipeline alone. `both` is a per-deployment opt-in (`backend/.env.run1` is the run file that sets it). An earlier revision of this section asserted `both` as the standing posture; it was true of one run file and is not a property of the default.

**Pre-deployment red-teaming:** a **real Garak runner** ships at
`backend/scripts/garak_scan.py` (Garak = NVIDIA's open-source LLM vulnerability
scanner). It is a *runner*, **not** a stored result — it is **executed on the
day** because it needs the gateway API key + network (or a running backend),
which aren't available while building. No local model needed. Two targets:

- `--target gateway` (default): garak's `rest` generator against the **same
  upstream OpenAI-compatible gateway + model** our LiteLLM chokepoint uses —
  the *base model's* raw susceptibility, the number our guardrails then mitigate.
- `--target endpoint`: garak against our **guardrail-protected `POST /query`**
  SSE surface (the runner logs in for a JWT first) — the *guarded* surface.

Run both and compare block rates. Curated probe set: `promptinject` (goal
hijacking), `dan` (jailbreak), `encoding` (obfuscated payloads), `leakreplay`
(data/system-prompt leakage). On the day:

```bash
pip install garak                         # dev/day-of tool — NOT a core runtime dep
export GENAILAB_API_KEY=...               # the gateway key (backend/.env)
python scripts/garak_scan.py --target gateway     # base-model baseline
python scripts/garak_scan.py --target endpoint    # guarded surface (backend up)
```

Reports write to `backend/scripts/garak_reports/` (gitignored); commit the real
`.report.jsonl` / `.report.html` there once the scan completes — *that* is the
block-rate evidence. The runner **degrades honestly**: if garak isn't installed
or the key/network/backend is absent it prints install+run steps and exits
non-zero **without fabricating any result**.

---

## 4. The three architecture principles (design by these)

1. **Statelessness** — keep LLM calls as stateless as possible; inject context cleanly per request. (Also enables horizontal scaling.)
2. **Sandboxing & scoping** — every agent function is least-privilege and boundaried like external traffic.
3. **Observability** — log the exact prompt, output, tool-selection rationale, model used, and trace id. You can't secure what you can't see. This is the **audit log** (a first-class Postgres table).

---

## 5. RBAC & data privacy

- **Separate admin (`/admin`) and user (`/app`) surfaces** with role-scoped data access — a security control, not just UX.
- **Postgres row-level security** is **enabled at startup** (`create_all` followed by a `bootstrap_rls()` routine — *not* a migration). The registry has grown from the three tables this line originally named to **25** (`_TENANT_SCOPED_TABLES` in `aegis/src/aegis/governance/rls.py`), and `audit_log` and `chunks` are now among them rather than application-scoped: every one gets `ENABLE`/`FORCE ROW LEVEL SECURITY` and a single `tenant_isolation` policy, `run_events`' monthly partitions included by a partition rule rather than by name. The LangGraph checkpoint tables carry no `tenant_id` and so no policy; they are scoped by the app-level filter on the `runs` header.
- **RLS is defence-in-depth here, not the boundary — and this document will not call it fail-closed.** `rls_fail_closed` defaults to **`False`** (`backend/src/app/config.py`), and the run file does not override it, so the installed predicate is the fail-**open** one: `substring(current_setting('app.tenant_id', true) from '^[0-9]+$') IS NULL OR tenant_id = substring(...)::int`. An unbound, empty or non-numeric session GUC satisfies the first disjunct and the policy restricts nothing. What carries the boundary is the application's own `WHERE tenant_id = …` predicate on every scoped query, and no read path skips it — so this is an inert second layer rather than a leak. The fail-closed predicate exists (`_TENANT_ISOLATION_PREDICATE_CLOSED`, widening only on a positive `app.tenant_all = 'on'`), and turning it on is a configuration change, not a code change. Until it is on, "a query that forgets its tenant clause returns nothing" is a claim about the *available* posture, not the running one.
- **"Exposing private data to the LLM": always check input and output.** Only send the minimum necessary context; redact PII; never place private data + untrusted content + external comms together unguarded (lethal trifecta).
- Every autonomous action → the **audit log** (who/what/when/which model/trace id).

---

## 6. How to SHOW and MARKET it (this is where points are won)

Security only scores if the jury sees it. Build these into the demo + deck:

1. **Live blocked-injection demo.** Type a prompt-injection attempt on stage; show the input rail catch and block it in real time.
2. **Garak red-team results.** Run `python scripts/garak_scan.py` **on the day** (base-model baseline vs guarded `/query` surface); the committed report gives the real "N probes, block rate X%" number. Proof you tested adversarially, not just claimed safety — and honest that it's run live, not pre-baked.
3. **The one-page threat model.** Show the app mapped to OWASP LLM + Agentic Top 10 with mitigations. Concrete, professional, documentation-scoring.
4. **The Colang / guardrail policy artifact.** A human-readable policy file = "our security is codified, not vibes."
5. **The audit log view.** Show every action logged with its approver and trace id → "auditable and reviewable."
6. **The bounded-autonomy moment.** In the money-shot, the agent *pauses at the human gate* on a high-risk action. Narrate: *"the agent doesn't act unchecked — high-risk or low-confidence actions require human approval."**
7. **The enterprise-procurement framing.** Say it plainly: *"OWASP-aligned, guardrailed, PII-redacting, fully audited — this passes enterprise security review."* That's the sentence buyers (and TCS jurors) respond to.

The unifying line, tied to the trust stack: **"every autonomous action is uncertainty-bounded (conformal), explainable (SHAP), guarded (rails), and fully traced (OTel + audit log)."**

---

## 7. Implementation checklist

- [ ] Input rail: PII scan/redact + API injection classifier + schema validation.
- [ ] Output rail: schema validation + PII redaction + filter.
- [ ] Tool allowlists per persona; least-privilege everywhere.
- [ ] Human-in-the-loop gate wired to high-risk / high-uncertainty.
- [ ] Azure Spotlighting on retrieved content; validate before graph writes.
- [x] NeMo Guardrails Colang policy committed **and executable** (`config/rails/*.co` run via `LLMRails`; `GUARDRAILS_ENGINE=both` makes the programmatic pipeline and the Colang engine both judge every payload, and it is an opt-in — the default is `programmatic`). Injection defense has a deterministic signature backstop before the model classifier.
- [x] The rail is four stages, not two: `INPUT`, `OUTPUT`, `TOOL_RESULT` and `MEMORY_WRITE` (§3).
- [ ] Garak runner (`scripts/garak_scan.py`) executed on the day; real report committed to `scripts/garak_reports/` for the demo.
- [ ] Audit log table populated on every action.
- [ ] One-page OWASP threat model written.
- [ ] RBAC + separate admin/user surfaces enforced.

---

## 8. Agent directives

- Guardrails orchestration is **decided and wired**: the programmatic pipeline is the default engine, and `GUARDRAILS_ENGINE=both` runs the NeMo Colang engine over its output. NeMo caps `pandas<2.4` (reflected in `pyproject`); the injection rail runs a deterministic signature backstop before the cheap-model classifier.
- **Never** introduce a local guardrail *model* (RAM constraint) — use the API classifier + self-built checks.
- Security wraps **every** model interaction. No unguarded path to the model, ever.
