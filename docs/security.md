# security.md — Security Design & How to Show It

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
| Memory poisoning (if memory added) | Adversarial "fact" persists across sessions | Governed memory: validate before writing to memory |

> *ID note:* IDs follow the current **OWASP Top 10 for LLM Applications v2.0
> (2025)** — Sensitive Information Disclosure is **LLM02**, Improper/Insecure
> Output Handling is **LLM05**, Excessive Agency is **LLM06** — matching
> `docs/threat_model.md`. (The 2023 list numbered these LLM06 / LLM02 / LLM08.)

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

**Orchestration — two front doors over one policy (both real):** the fast, offline-testable programmatic rails (`app/guardrails/rails.py`) the agent graph calls by default, **and** a **NeMo Guardrails Colang policy** (`app/guardrails/config/*.co`) that is genuinely loaded and executed via `LLMRails` — set `GUARDRAILS_ENGINE=nemo` to make the readable Colang policy the enforcing engine (its custom actions delegate back to the same checks, so the two can't drift). Verified by `tests/guardrails/test_nemo_rails.py`, which runs the real engine and asserts the input/output rails block a jailbreak and redact PII. The Colang file doubles as a human-readable security artifact.

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
- **Postgres row-level security** is **enabled at startup** (`create_all` followed by a `bootstrap_rls()` routine — *not* a migration) on the `users`, `usage_ledger`, and `approvals` tables. The `audit_log` and `chunks` tables are **application-scoped** (isolation enforced in the query layer), not RLS-guarded.
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
- [x] NeMo Guardrails Colang policy committed **and executed** (`config/*.co` run via `LLMRails`; `GUARDRAILS_ENGINE=nemo`); programmatic twin is the default fast path. Injection defense has a deterministic signature backstop before the model classifier.
- [ ] Garak runner (`scripts/garak_scan.py`) executed on the day; real report committed to `scripts/garak_reports/` for the demo.
- [ ] Audit log table populated on every action.
- [ ] One-page OWASP threat model written.
- [ ] RBAC + separate admin/user surfaces enforced.

---

## 8. Agent directives

- Guardrails orchestration is **decided and wired**: NeMo Guardrails (Colang) is the declarative engine (`GUARDRAILS_ENGINE=nemo`), with the programmatic rails as the default fast twin. NeMo caps `pandas<2.4` (reflected in `pyproject`); the injection rail runs a deterministic signature backstop before the cheap-model classifier.
- **Never** introduce a local guardrail *model* (RAM constraint) — use the API classifier + self-built checks.
- Security wraps **every** model interaction. No unguarded path to the model, ever.
