# Incident-response plan

- **Owner:** `devops`. The platform owner is the escalation point and the only person who
  may decide to notify a tenant.
- **Reviewed:** every six months, **and after every incident** (post-incident review inside
  five working days). See [`review-cadence.md`](review-cadence.md).
- **Satisfies:** NIST AI RMF **GOVERN** 1.5 and 4.3, and **MANAGE** 4.1 (response and
  recovery). It does **not** satisfy CERT-In Direction (ii) or DPDP s.8(6) — §6 says exactly
  why, and those rows stay `not_implemented` in `docs/compliance/README.md`.
- **Register:** [`incidents/`](incidents/README.md). Empty at the time of writing, with the
  entry format fixed so the first incident does not have to invent one.

> **What makes this plan real rather than decorative:** every detection signal below is a
> thing this system already emits, named by the file or route that emits it. A plan whose
> first step is "monitor for anomalies" against a system with no anomaly signal is theatre.

---

## 1. What counts as an incident

An **incident** is any event in the list below. Everything else is a defect and goes to the
backlog.

| Sev | Definition | Examples grounded in this system |
|---|---|---|
| **S1** | Confidentiality or integrity of tenant data is, or may be, breached. | A row from tenant A reaching tenant B; the RLS boot audit finding a bypassing serving role; a leaked `JWT_SECRET` or database credential; a successful prompt injection that caused a HIGH-risk tool to execute. |
| **S2** | The agent took, or nearly took, a consequential action it should not have. | A gate that did not fire for a tool that should have been HIGH; an approval executed twice; an auto-rejected HIGH-risk action executing anyway; a tool acting on a hallucinated record id. |
| **S3** | A safety control is down or degraded, and the system is still serving. | The injection rail unavailable and refusing every request unexamined; a guardrail rail disabled in configuration without a decision; a red-team run whose block rate falls below its suite floor; budget enforcement bypassed. |
| **S4** | Availability or cost, with no safety or confidentiality dimension. | `/readyz` refusing traffic; a store unreachable; the notification bus degraded to in-process; spend running ahead of forecast. |

**Not incidents:** a guardrail correctly blocking an attack (that is the control working),
an approval correctly rejected, a budget correctly refusing a call. These are events, and
their volume is a metric, not an alert.

---

## 2. Detection — the signals that actually exist

| Signal | Where it comes from | What it detects |
|---|---|---|
| **Audit trail** | `audit_log`, written by `aegis/src/aegis/governance/audit.py`; read via `GET /v1/audit` with SQL-side filters on actor, action, tenant and time. | Who did what, with which model, under which trace, and who approved it. The primary forensic surface for S1 and S2. |
| **Approval events** | `backend/src/app/data/approvals.py` — `PENDING`, `RESUMING`, `REJECTED`, `EXPIRED`. | A gate that expired unresolved, an auto-rejected HIGH-risk action, an approval storm. |
| **SLA sweeper** | `run_sla_sweeper` in the same module. | Deadline breaches, which are the earliest sign that nobody is watching the queue. |
| **Guardrail verdicts** | `aegis/src/aegis/guardrails/pipeline.py`, surfaced per run and per rail. A refusal that no screen could examine is filed under a *distinct* layer (`injection_unavailable`) so "we blocked an attack" and "we could not check" never merge. | S3: a rail that is failing closed because it is unavailable. |
| **Security posture** | `GET /v1/security/posture`, derived from live wiring on every request. | A control switched off. It cannot report green while disabled — there is a test for that. |
| **Red-team runs** | `aegis/src/aegis/redteam/battery.py`, `POST /v1/redteam/runs`, with history and trend. Block rate is reported beside a false-positive rate against per-suite floors. | A regression in defensive coverage between releases. |
| **Health probes** | `/readyz` and the component table (`backend/src/app/api/routes_health.py`). Only `down` refuses traffic. | S4, and the state of every store. |
| **Alert inbox** | Durable `Notification` rows (`backend/src/app/data/notifications.py`) fanned out over Redis pub/sub (`backend/src/app/notifications.py`). The bus reports its own mode and logs at WARNING when it degrades. | Job failures, and the transport's own health. |
| **Budget refusals** | `aegis/src/aegis/governance/enforcement.py` — a clean terminal `BudgetExceededError` before spend. | Runaway consumption, whether a loop or an attack. |
| **RLS boot audit** | `aegis/src/aegis/governance/rls.py` — the serving role's attributes are checked at startup and a bypassing role stops the process when the check is fatal. | The single highest-severity misconfiguration, caught before traffic. |

**Honest limit on detection.** All of the above are **pull** surfaces. Nothing pages
anybody: there is no alerting integration, no on-call rotation and no threshold that fires
an email or a message. Detection today means a human opening the DevOps portal, or CI
failing. For a two-person hackathon-stage platform that is the truth, and the plan is
written around it rather than pretending otherwise.

---

## 3. Triage

The first responder is whoever holds `devops`. Triage is time-boxed to **30 minutes** and
produces exactly three things: a severity, a scope, and a containment decision.

1. **Fix the severity** using §1. When in doubt between two levels, take the higher one and
   downgrade later in writing.
2. **Establish scope.** Every run carries a `run_id` that is also the LangGraph
   `thread_id` and an OpenTelemetry trace id. Given one, pull:
   - the audit rows for that trace (`GET /v1/audit`, filtered in SQL, not in the page);
   - the approval row and its decision path;
   - the guardrail verdicts for the run;
   - the usage-ledger rows, which say which model saw what.
   Record which **tenants** and which **data subjects** are in scope. If the answer is "more
   than one tenant", it is S1 regardless of what §1 suggested.
3. **Decide containment** using §4, and write the decision down before executing it.

---

## 4. Containment — the levers that exist

In descending order of preference (least collateral damage first):

| Lever | Effect | How |
|---|---|---|
| **Gate everything** | Set `agent.gate_min_risk` to the lowest tier for the affected tenant. Every proposed action then needs a human. `TIGHTEN_ONLY` means this always works and never needs a platform override. | Settings, per tenant |
| **Revoke a seat capability** | `seat.can_approve`, `seat.can_upload_documents`, `seat.can_edit_memory` are revoke-only and take effect at the guard. | Settings, per seat |
| **Zero the budget** | A tenant or user cap of zero refuses every model call *before* spend, which stops the agent without stopping the platform. | Admin budgets |
| **Disable an MCP peer** | A disabled peer's tools leave the tool payload entirely, so the model cannot even propose them. | `backend/src/app/mcp/client.py` |
| **Rotate `JWT_SECRET` and restart** | Invalidates **every** outstanding token at once. This is the only session-revocation mechanism that exists — there is no per-session revoke and no lockout. Tokens live 12 hours (`aegis/src/aegis/governance/security.py`), so without rotation a compromised token stays valid for up to that long. | Environment + restart |
| **Stop serving** | `/readyz` refuses traffic when a component is `down`; the deployment can also simply be stopped. The blunt lever, reserved for S1. | Process |

**Honest limits on containment.** There is no per-user disable that is not a budget or a
seat change; no IP blocking; no rate limit per principal beyond the fleet limiter; **no
backup and no restore procedure**, so "recover to a known-good state" is not a step this
plan can offer. Recovery for a data-integrity incident means correcting records through
the same audited endpoints a human would use — and memory correction supersedes rather than
overwrites, so the correction is itself auditable.

---

## 5. Notification

| Who | When | How |
|---|---|---|
| **Platform owner** | Immediately for S1/S2; at triage close for S3/S4. | Direct. |
| **Affected `tenant_admin`** | S1 always, and S2 where an action executed in their tenant. Inside **24 hours** of triage close. The platform owner decides; `devops` does not notify a tenant unilaterally. | Direct contact, plus a durable notification in their inbox. |
| **Affected end customers** | Not directly. Aegis holds no relationship with a tenant's end customers and no channel to reach them; the tenant is the Data Fiduciary and owns that decision. This plan supplies the tenant with the scope it needs to make it. | Via the tenant. |
| **Regulators** | See §6. | Not automated. |

---

## 6. What this plan does **not** discharge

Stated as its own section because burying it would be the exact failure this document exists
to prevent.

- **CERT-In Direction (ii) — 6-hour reporting is not automated, and is not met.** The
  Direction requires a reportable cyber incident to be reported to CERT-In within six hours
  of noticing it. This plan supplies two of the three things that needs — a definition of
  what counts (§1) and a person accountable for the clock (`devops`, escalating to the
  platform owner). The third is missing: **there is no registered point of contact with
  CERT-In, no path to the reporting form, and no timer anywhere in the system that starts
  when an S1 is opened.** The clock is a human's watch. The compliance row stays
  `not_implemented` and must not be moved on the strength of this document.
- **DPDP s.8(6) / Rule 7 — personal-data breach intimation is not implemented.** Nothing
  classifies an event as a *personal-data* breach, nothing routes it outside the tenant, and
  there is no intimation to a Data Principal or report to the Board. This binds on
  13 May 2027.
- **No independent forensic capability.** The audit trail is append-only by database
  privilege on the *serving* role; the owner connection can still rewrite it. In an insider
  scenario the trail is evidence, not proof.
- **No paging, no on-call rotation, no SLA to the tenant.** Response times above are
  intentions of a two-person team, not contractual commitments.

---

## 7. Post-incident review

Within **five working days** of closing an S1 or S2, and at the next scheduled review for
S3/S4:

1. Write the incident into [`incidents/`](incidents/README.md) using the fixed format.
2. Answer one question in writing: **which control should have caught this, and did it
   exist?** One of three outcomes, and the third is a legitimate answer:
   - the control existed and failed → fix it, and add the test that would have failed;
   - the control did not exist → build it, or record the gap in `docs/compliance/README.md`
     with the state it deserves;
   - the risk is accepted → say so, with the reason and the person accepting it.
3. If the AI policy, this plan, or the impact assessment turns out to have been wrong, that
   is an **off-cycle review trigger** — do it now, not at the next cadence date.
