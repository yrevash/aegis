# Aegis AI policy

- **Applies to:** the Aegis platform as built in this repository, and to any deployment of it.
- **Owner:** Platform owner (`platform_admin`). See [`accountable-roles.md`](accountable-roles.md).
- **Reviewed:** every six months, and on any of the off-cycle triggers in
  [`review-cadence.md`](review-cadence.md).
- **Satisfies:** NIST AI RMF **GOVERN** 1.1–1.4, 2.1, 6.1. ISO/IEC 42001 A.2 and A.9 read on
  the same text; the 42001 rows stay `partial` because 42001 asks for a management *system*
  around this document and there is no certification body in the loop.

> **Every clause below names the mechanism that enforces it.** A policy sentence with no
> mechanism behind it is a wish, and this platform's entire argument is that it does not
> ship those. Where a clause is enforced only by a person and not by code, it says
> **"process only"** and that is the honest label.

---

## 1. What Aegis is for

Aegis is a **domain-agnostic agentic platform**: an LLM agent that reads a tenant's own
records and documents, proposes actions, and executes the ones a human allows. The domain
is a swappable adapter (`backend/src/app/adapter/README.md`); the core never learns it. In
this repository the adapter is enterprise **customer service** — service requests, case
notes and support documents (`backend/src/app/adapter/schema.py`).

The system exists to make an autonomous action **accountable**: bounded in cost, gated by a
human above a risk tier, traced end to end, and reversible where the tool allows.

**Intended users.** Five roles, and no others: `platform_admin`, `tenant_admin`, `ai_team`,
`devops`, `client`. Each holds a distinct portal and a distinct capability set — see
[`accountable-roles.md`](accountable-roles.md).

**Intended deployment.** A single-tenant-per-customer logical partition inside one
multi-tenant deployment operated by the platform owner, on infrastructure the owner
controls. Data residency for every store is derived, not asserted
(`backend/src/app/platform/residency.py`).

---

## 2. What Aegis may not be used for

These are the boundaries the platform owner will not deploy across. They are policy, and
where a mechanism exists it is named; where none exists, the clause is process only and the
deployment carries it.

| # | Prohibited use | Mechanism, or "process only" |
|---|---|---|
| 2.1 | **Fully autonomous consequential action.** No configuration may allow the agent to execute a HIGH-risk tool without a human decision. | `agent.gate_min_risk` in `aegis/src/aegis/settings/spec.py` is `TIGHTEN_ONLY` with `stricter=LOWER`: a tenant may gate *more* actions and can never raise the threshold above the platform's. An unregistered or hallucinated tool name resolves to HIGH (`backend/src/app/agent/deps.py`), so an invented tool cannot slip under the ceiling. |
| 2.2 | **Decisions about a person's rights, credit, employment, health or liberty.** Aegis is not built, evaluated or calibrated for them, and the EU AI Act treats several as high-risk with obligations this repository does not meet. | Process only. Nothing in the code inspects the *purpose* of a tool call. A deployment that retargets the adapter into such a domain leaves the scope of this policy and must redo the impact assessment first — that is the domain-swap trigger in [`review-cadence.md`](review-cadence.md). |
| 2.3 | **Processing children's data.** No age signal exists anywhere in the system and no parental-consent gate exists (DPDP s.9 / Rule 10, recorded `not_implemented` in `docs/compliance/README.md`). | Process only, and it is a real gap, not a formality. |
| 2.4 | **Presenting a model answer as a verified fact to an end customer without provenance.** | Every answer carries its retrieval provenance, and the grounding rail (`aegis/src/aegis/guardrails/grounding.py`) screens ungrounded output. The rail is configurable per tenant (`guardrails.grounding.block`), so this clause is enforced by default and weakenable by a tenant — which is why §4 makes ungrounded answers a named residual harm in [`context-and-impact.md`](context-and-impact.md). |
| 2.5 | **Cross-tenant use of one tenant's data to serve another.** | Postgres row-level security with `FORCE ROW LEVEL SECURITY` and a `NOSUPERUSER NOBYPASSRLS` serving role, audited at boot (`aegis/src/aegis/governance/rls.py`), under an application-layer tenant scope on every read. |
| 2.6 | **Suppressing or rewriting the audit trail to hide an action.** | The serving role holds no `UPDATE`/`DELETE` grant on `audit_log`, and the memory retention sweep is written never to touch it (`aegis/src/aegis/memory/retention.py`). **Honest limit:** the owner connection (`POSTGRES_ADMIN_DSN`) can still rewrite the table. Append-only is a privilege, not a cryptographic guarantee. |
| 2.7 | **Marketing Aegis as certified, audited or attested.** | `GET /v1/platform/standards` serves `certified: false` on every response and the disclaimer travels with the payload (`backend/src/app/platform/compliance.py`); `backend/tests/api/test_compliance.py` fails if the disclaimer stops saying "not certification". |

---

## 3. The acceptable-use boundary, stated as one rule

**A tenant may make Aegis stricter. A tenant may never make it looser than the platform
floor.** That is not a slogan; it is the merge semantics of the settings catalogue.

- Every safety-relevant setting merges `TIGHTEN_ONLY` with an explicit direction
  (`aegis/src/aegis/settings/spec.py`): the gate threshold, the PII entity list, the
  denylists, the seat capabilities, the input size cap.
- The system prompt has two halves. The tenant owns the task half; the **platform floor**
  is appended by the platform and no tenant can edit it
  (`backend/src/app/adapter/prompts.py`).
- Seat capabilities are **revoke-only** (`seat.can_approve`, `seat.can_upload_documents`,
  `seat.can_edit_memory`, `seat.can_view_tenant_audit`, `seat.can_change_agent_mode`): a
  narrower scope can remove a capability and no scope can hand one back.
- External MCP tools are **HIGH risk until a platform admin lowers a named tool**, a
  disabled peer's tools leave the payload entirely, and a peer's return value passes the
  `TOOL_RESULT` rail before it reaches a prompt (`backend/src/app/mcp/client.py`).

---

## 4. Human oversight — the requirement, and exactly where it binds

**Requirement.** No action at or above the gate tier executes without a named human
decision, and no gate may be discharged by a timeout that behaves like an approval.

| Where | Mechanism |
|---|---|
| The gate fires | Tool risk tier ≥ `agent.gate_min_risk` (default `high`). Tool risk is the **only** gating signal — ML confidence never gates, by decision (`docs/adr/0007-conformal-autonomy-bands.md`). |
| The pause is durable | A `PENDING` row plus a LangGraph checkpoint keyed on the run id, so the pause survives a restart and can be resolved out of band (`backend/src/app/data/approvals.py`). |
| A decision is single-use | The `PENDING → RESUMING/REJECTED` transition is optimistic; only the caller that wins rowcount 1 proceeds, so a double-click cannot double-execute. |
| A timeout is not an approval | The SLA sweeper marks past-deadline rows `EXPIRED` and **auto-rejects HIGH-risk** ones. Fail-safe, not fail-open — `backend/tests/data/test_approvals.py::test_sla_sweeper_expires_and_auto_rejects_high`. |
| Who decided is recorded | The approver's identity is written to the audit row beside the action, the model and the trace id (`aegis/src/aegis/governance/audit.py`). |
| Who *may* decide | `require_admin` / `require_tenant_admin` in `backend/src/app/api/routes.py`, narrowed further by `seat.can_approve`. |

**Honest limit.** Oversight is *per action*, not *per outcome*. Nothing in the system asks
a human to review an answer the agent merely produced — only an action it proposed to take.
An ungrounded answer read by a support agent and repeated to a customer is not gated by
anything except the grounding rail, and that is carried as a residual harm in
[`context-and-impact.md`](context-and-impact.md) §4.3.

---

## 5. Model sourcing

**One gateway, no downloaded weights, no training on tenant data.**

1. **Every model call leaves through one gateway** — LiteLLM against a custom
   OpenAI-compatible endpoint (`aegis/src/aegis/gateway/llm.py`, `docs/adr/0001-litellm-as-gateway.md`).
   There is no second path to a provider anywhere in the codebase.
2. **Models are chosen by role, never by a hard-coded id** (`aegis/src/aegis/gateway/routing.py`),
   and a fallback chain is bounded by the tenant's entitlement: a downgrade can never
   promote to a model the tenant is not entitled to.
3. **No third-party model weights are loaded.** The only fitted model in the platform is
   trained in-process from the host's own frame (`aegis/src/aegis/ml/model.py`), so there is
   no downloaded artefact to have been tampered with.
4. **No tenant data is used to train or fine-tune any model.** Nothing here fine-tunes an
   LLM at all, and the one fitted model trains on a **deterministic synthetic world**
   generated at train time, not on tenant records
   (`backend/src/app/adapter/ml_spec.py::training_frame`). Whether the *gateway provider*
   retains prompts is a contractual question about that provider and **not** a claim this
   repository can make on its behalf.
5. **Spend is refused before it happens.** Budgets are enforced at the gateway chokepoint
   and raise a clean terminal `BudgetExceededError` rather than a crash
   (`aegis/src/aegis/governance/enforcement.py`).

**Honest limit.** In this repository's own run file the gateway points at a US region.
Prompts, completions and retrieved chunk text leave through it. That is lawful under DPDP
s.16's negative-list model today; it is **not** data localisation and must never be
described as such (`docs/compliance/README.md`).

---

## 6. Data handling

- **PII is detected and masked on input, on output and on tool results**
  (`aegis/src/aegis/guardrails/pii.py`); the entity list merges `TIGHTEN_ONLY`.
- **Content is validated before it is written** to the graph or vector store
  (`aegis/src/aegis/retrieval/validation.py`), so retrieval poisoning has a gate.
- **Retention is a per-tenant horizon with a real hard delete**
  (`aegis/src/aegis/memory/retention.py`); the sweep deliberately never touches `audit_log`,
  the usage ledger or the write log, because the 180-day security-log expectation and the
  storage-limitation duty point in opposite directions and this repository resolves that
  conflict in favour of the audit trail, in writing.
- **A data principal's correction and erasure are real endpoints** — `PATCH /v1/memory/facts/{id}`
  supersedes rather than overwrites, `POST /v1/memory/forget` and `DELETE /v1/memory/facts/{id}`
  hard-delete and leave an audit receipt.
- **Nothing is encrypted at rest**, and column-level encryption was assessed and refused
  rather than overlooked (`backend/src/app/platform/at_rest.py`). The control owed is a
  deployment one — full-disk or storage-layer encryption on the host.

---

## 7. Security posture this policy assumes

Stated so a reader is not surprised by an absence later:

- Authentication is Argon2id password plus a 12-hour HS256 JWT
  (`aegis/src/aegis/governance/security.py`). **No MFA, no lockout, no per-session
  revocation.** The only revocation lever is rotating the signing secret, which invalidates
  every outstanding token at once — see [`incident-response.md`](incident-response.md) §4.
- The security posture screen is **derived from live wiring on every request**
  (`GET /v1/security/posture`), so a control that is switched off cannot keep reporting
  green; a test enforces exactly that.
- CI gates lint, three test suites, the type check and the OpenAPI snapshot
  (`.github/workflows/ci.yml`). It gates **tests, not supply chain**: no dependency
  scanning, no SAST, no provenance, no branch protection requiring it to pass.

---

## 8. Enforcement of this policy

- A change to any mechanism named above requires a matching edit here **in the same
  change**, and the review cadence exists to catch the ones that slip
  ([`review-cadence.md`](review-cadence.md)).
- Every repository path cited in this document is resolved against the real filesystem by
  `backend/tests/api/test_governance_docs.py` on every test run. A clause whose mechanism was
  renamed or deleted fails the suite rather than sitting here as a false claim.
- A breach of §2 is an incident under [`incident-response.md`](incident-response.md) and is
  triaged as one.
