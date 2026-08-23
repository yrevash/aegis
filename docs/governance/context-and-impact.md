# Context of use, and impact on affected individuals

- **Owner:** Platform owner (`platform_admin`), with `ai_team` for the capability half.
- **Reviewed:** every six months, **and before any domain swap ships**. See
  [`review-cadence.md`](review-cadence.md) §3 — a retargeted adapter means different
  affected people, so this document is about someone else the moment the domain changes.
- **Satisfies:** NIST AI RMF **MAP** 1.1–1.6 (context established), 2.1–2.3 (categorisation),
  3.1–3.2 (capabilities and limitations), 5.1–5.2 (impacts to individuals and groups).
  ISO/IEC 42001 A.5 reads on §4 of this document.
- **This is not a DPIA.** A DPIA under DPDP s.10 / Rule 13 requires a Significant Data
  Fiduciary, an independent auditor and an annual cycle, none of which exist here. That row
  stays `not_implemented`.

> **An impact assessment that finds no risks is not credible.** Thirteen harms are named
> below. Four of them have **no mitigation in this repository** and are marked
> `NONE` — those four are the useful part of this document.

---

## 1. What the system is, in context

| Question | Answer |
|---|---|
| **What decision does it participate in?** | Resolving an inbound customer **service request**: classifying it, retrieving the relevant policy or history, drafting a response, and proposing actions such as updating a record or escalating. |
| **Does it decide, or propose?** | It **proposes**. Any tool at or above `agent.gate_min_risk` (default `high`) becomes a durable approval a human resolves. Below that tier it acts, and those tools are the read-and-annotate ones the adapter registers. |
| **Who is in the loop?** | A tenant's own support staff (`client`) and its administrators (`tenant_admin`). Aegis staff never see tenant content in normal operation — the guards in [`accountable-roles.md`](accountable-roles.md) §2 refuse it. |
| **What data does it read?** | Service requests, case notes, customer records and the tenant's own uploaded documents (`backend/src/app/adapter/schema.py`), plus durable memory the tenant's users accumulate. |
| **What is the AI component?** | An LLM agent over one gateway (`aegis/src/aegis/gateway/llm.py`), a retrieval layer over the tenant's corpus, and a small gradient-boosted model fitted in-process (`aegis/src/aegis/ml/model.py`) that predicts a resolution signal. It trains on a deterministic **synthetic** frame, not on tenant records (`backend/src/app/adapter/ml_spec.py`), and its output is **advisory and never gates** (`docs/adr/0007-conformal-autonomy-bands.md`). |
| **Where is it deployed?** | One multi-tenant deployment on infrastructure the platform owner controls. Every store's location is derived, not asserted (`backend/src/app/platform/residency.py`); the **model gateway is the exception** and in this repository's run file points at a US region. |
| **What is it not?** | Not a decision system for rights, credit, employment, health or liberty — prohibited by [`ai-policy.md`](ai-policy.md) §2.2. Not a system of record: it reads and annotates the tenant's records, and the tenant's own systems remain authoritative. |
| **Maturity** | Pre-production. It has never served a real end customer. Every figure on every screen in this repository is from seeded or synthetic data unless the screen says otherwise. |

---

## 2. Who is affected

Four groups, in descending order of how little control they have.

1. **A tenant's end customers.** People who filed a service request, or who are named in a
   case note or an uploaded document. **They never interact with Aegis, never consent to it,
   and in most deployments will never be told it exists.** Their data is read, embedded,
   summarised, and may be written into durable memory as a "fact" about them. They bear
   almost every harm in §4 and hold none of the controls.
2. **Third parties named in a document.** A person mentioned in a case note who is not the
   requester — a family member, another customer, an employee. They are processed with no
   relationship to the tenant at all, and no mechanism in this system distinguishes them.
3. **A tenant's support staff.** Their work is routed, drafted for, and measured. The
   plausible harm to them is de-skilling and misplaced blame: acting on a confident wrong
   answer, then owning the consequence.
4. **The tenant as an organisation, and the platform owner.** Financial and reputational
   exposure. Named here because a document that only lists commercial risk is the failure
   mode this section exists to avoid — they are last, not first.

---

## 3. What the system is good at, and where it fails

Stated from measurements, not from intent (NIST MAP 3):

- **Measured:** the red-team battery reports a block rate **beside a false-positive rate**
  against per-suite floors and keeps the probes it is known to miss in the suite rather than
  curating them out (`aegis/src/aegis/redteam/battery.py`). The eval gate has declarative
  per-metric thresholds (`aegis/src/aegis/evals/regression.py`). The model card reports
  conformal coverage as **empirical coverage on a held-out split**, and null when nothing
  was held out (`GET /v1/ml/model-card`).
- **Known failure modes:** retrieval that returns nothing relevant and an answer generated
  anyway; an injection embedded in an uploaded document rather than in the user's message;
  a tool called with a plausible but wrong record id; a fluent answer about a policy the
  corpus does not contain.
- **Not evaluated at all:** performance across demographic groups, languages other than
  English, or accessibility of the output. There is no fairness evaluation in this
  repository and no dataset that would support one. Saying "no bias was found" would mean
  "no bias was looked for".

---

## 4. Harms, and the mitigation that exists for each

`Mitigation` cites only mechanisms that exist in this repository. `Residual` is what is left
after it. **`NONE`** means exactly that.

### 4.1 A wrong answer is acted on

- **Who bears it:** the end customer, then the support agent.
- **Mitigation:** provenance on every answer, so the agent can check the source; the
  grounding rail (`aegis/src/aegis/guardrails/grounding.py`); an eval gate on retrieval
  quality; the ML signal shown as supporting evidence with a calibrated interval, never as
  an instruction.
- **Residual: high.** Human oversight in this system is **per action, not per answer**. An
  answer the agent merely produced is gated by nothing but the rails, and a fluent wrong
  answer is the failure most likely to reach a customer. The grounding rail's blocking
  behaviour is a per-tenant setting (`guardrails.grounding.block`), so a tenant can turn the
  one mitigation down.

### 4.2 Cross-tenant disclosure

- **Who bears it:** the end customers of both tenants.
- **Mitigation:** Postgres RLS with `FORCE ROW LEVEL SECURITY` over a `NOSUPERUSER
  NOBYPASSRLS` serving role, audited at boot and fatal on failure
  (`aegis/src/aegis/governance/rls.py`); an application-layer tenant scope on every read;
  fine-role guards that refuse a tenant admin the process-wide surfaces
  (`backend/src/app/api/routes_health.py`); a dedicated cross-tenant test suite
  (`backend/tests/api/test_cross_tenant_holes.py`).
- **Residual: low, and it is the strongest control in the platform.** What remains is the
  shared-process surface: cache hit rates and latency percentiles are one number over every
  tenant that shared a worker, which is why those screens are platform-staff only rather
  than filtered.

### 4.3 An ungrounded claim presented as fact

- **Who bears it:** the end customer.
- **Mitigation:** the grounding rail (`aegis/src/aegis/guardrails/grounding.py`), which
  screens an answer against what was actually retrieved; retrieval provenance under every
  figure; the `Receipt` / `Absence` discipline that makes an unsourceable number render as a
  stated absence rather than a zero.
- **Residual: high**, and it is the same residual as §4.1 seen from the output side. The
  rail screens the answer; it does not verify the claim.

### 4.4 A high-risk action executed on the wrong record

- **Who bears it:** the end customer whose record is changed.
- **Mitigation:** the human gate above the risk tier, with an unregistered tool name
  resolving to HIGH so a hallucinated tool cannot slip under it
  (`backend/src/app/agent/deps.py`); per-persona tool allowlists; the optimistic
  `PENDING → RESUMING` transition that makes a decision single-use
  (`backend/src/app/data/approvals.py`); the SLA sweeper auto-rejecting expired HIGH-risk
  actions; an audit row naming the actor, the model, the trace and the approver.
- **Residual: medium.** The gate shows a human *what* is proposed; it cannot tell them the
  record id is wrong. Approval fatigue is a real failure mode and nothing in the system
  measures it.

### 4.5 Prompt injection from an uploaded document

- **Who bears it:** the tenant, and any customer whose record the hijacked action touches.
- **Mitigation:** the injection rail with a deterministic layer and a classifier
  (`aegis/src/aegis/guardrails/pipeline.py`); write-time content validation before anything
  enters the graph or vector store (`aegis/src/aegis/retrieval/validation.py`); retrieved
  text marked as data rather than instructions; external tool results passed through the
  `TOOL_RESULT` rail; the gate as the last line.
- **Residual: medium.** The red-team battery reports which probes it misses rather than
  hiding them, and indirect injection through a long document is the hardest of them.

### 4.6 Personal data reaching the model provider

- **Who bears it:** the end customer, and any third party named in a document.
- **Mitigation:** PII detection and masking on input, output and tool results
  (`aegis/src/aegis/guardrails/pii.py`), with a `TIGHTEN_ONLY` entity list a tenant can
  extend and cannot shrink below the platform floor.
- **Residual: high.** Masking is pattern- and model-based and will miss unusual identifiers.
  Prompts, completions and retrieved chunk text leave the deployment through the gateway,
  which in this repository's run file is in a US region. Whether the provider retains them
  is a contract, not a control this repository holds.

### 4.7 Spend exhaustion

- **Who bears it:** the tenant, and its customers through denial of service.
- **Mitigation:** budgets enforced at the gateway chokepoint **before** spend, raising a
  clean terminal error rather than crashing (`aegis/src/aegis/governance/enforcement.py`);
  per-role model routing; a durable usage ledger per call; plan-iteration and fan-out caps;
  an in-process circuit breaker so a dead deployment is not paid for on every retry.
- **Residual: low for cost, medium for availability.** A budget doing its job *is* a denial
  of service to that tenant's users, and nothing distinguishes "cap reached because of an
  attack" from "cap reached because of a busy Monday".

### 4.8 A false accusation of attack — over-blocking

- **Who bears it:** the end customer or agent whose legitimate request is refused.
- **Mitigation:** benign control suites in the red-team battery measure the false-positive
  rate as a first-class number (`aegis/src/aegis/redteam/battery.py`); a refusal that **no
  screen could examine** is filed under a distinct layer (`injection_unavailable`) rather
  than reported as a finding (`aegis/src/aegis/guardrails/pipeline.py`), so a deployment with
  no reachable gateway cannot tell every user their question was an attack.
- **Residual: low**, and this is one of the few harms where the mitigation is aimed
  precisely at the individual rather than at the operator.

### 4.9 A false "fact" persists about a person

- **Who bears it:** the end customer the fact is about.
- **Mitigation:** memory writes are validated; a belief is **superseded, never overwritten**,
  and carries who asserted and who corrected it (`aegis/src/aegis/memory/crud.py`);
  `PATCH /v1/memory/facts/{id}` corrects and
  the correction is itself auditable; `POST /v1/memory/forget` and
  `DELETE /v1/memory/facts/{id}` hard-delete with a receipt.
- **Residual: medium.** Every one of those is exercised **by the tenant, not by the person
  the fact is about** — see §4.10.

### 4.10 The affected person cannot exercise any right directly — `NONE`

- **Who bears it:** every end customer and every named third party.
- **Mitigation: NONE.** There is no notice, no consent record, no grievance channel, no
  subject-access export and no way for a data principal to reach this system at all. The
  correction and erasure mechanisms in §4.9 are operated by the tenant on the person's
  behalf, if the tenant chooses to. DPDP s.5/s.6, s.13 and the s.11 processing summary are
  recorded `not_implemented` in `docs/compliance/README.md`, and this is what that costs the
  individual.

### 4.11 The affected person is never told AI was involved — `NONE`

- **Who bears it:** every end customer.
- **Mitigation: NONE.** Transparency in this platform points at the **deployer** — the model
  card, the OpenAPI contract, the provenance on every answer, the audit trail — all of which
  a tenant reads. Nothing points at the person whose request was processed. There is no
  disclosure surface, and Aegis has no channel to one.

### 4.12 Stored personal data is exposed by host compromise — `NONE`

- **Who bears it:** every end customer in every tenant.
- **Mitigation: NONE at the application layer.** **Nothing is encrypted at rest** in any
  store, there is no key management and no rotation. Column-level encryption was assessed
  and deliberately refused rather than overlooked, with the reasoning recorded
  (`backend/src/app/platform/at_rest.py`); the control owed is a deployment one — full-disk
  or storage-layer encryption on the host. Masking protects what leaves; it protects nothing
  at rest.

### 4.13 A child's data is processed — `NONE`

- **Who bears it:** a child, and their parent.
- **Mitigation: NONE.** Nothing in the system knows or asks how old a data principal is;
  there is no age signal and no parental-consent gate (DPDP s.9 / Rule 10). The only
  protection is the tenant's own intake process, which is outside this repository.

---

## 5. Aggregate reading

- **The controls are strongest where the operator is the victim** (cross-tenant leakage,
  spend, tool misuse) and **weakest where the individual is** (notice, rights, disclosure,
  data at rest). That is not an accident of implementation; it is what happens when a
  platform's controls are built from the operator's threat model, and naming it is the point
  of doing this exercise.
- **Four harms have no mitigation at all**, and three of those (§4.10, §4.11, §4.13) are the
  same absence seen from three angles: this system has **no relationship with the person
  whose data it processes**. Closing any one of them means building a channel to the data
  principal, which is a product decision, not a control.
- **The single highest-value mitigation this repository does not have** is per-answer human
  review or an abstention path for a low-confidence answer — §4.1 and §4.3 are the harms
  most likely to actually occur, and both are residual-high.

## 6. What this assessment did not cover

- No fairness or demographic-performance evaluation (§3), and no dataset that would support
  one.
- No environmental impact assessment of model inference.
- No assessment of harms from **absence** of the system — the status quo the tenant would
  operate without it.
- No third-party or supply-chain assessment beyond the SBOM, the hash-pinned lockfiles and
  the registry patch check; there is no CVE feed and no advisory matching, which is why
  OWASP LLM03 stays `partial`.
- No assessment of the **model provider's** own processing, retention or training practices.
  That is a contract with a provider, and this repository can neither verify nor claim it.
