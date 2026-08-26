# Compliance readiness — what Aegis can defensibly claim, and what it cannot

> **This is compliance-readiness evidence. It is not certification.**
> Aegis holds no ISO 27001 certificate, no ISO/IEC 42001 certificate, no SOC 2
> report and no EU AI Act conformity assessment. Nobody independent has audited
> anything below. What this document is: a control-by-control map from published
> frameworks to **files, endpoints and tests in this repository**, with an honest
> state per control. A buyer's security reviewer can open every cell.

This file is the **authority**. `GET /v1/compliance` serves a typed projection of
it (`backend/src/app/platform/compliance.py`), and the Compliance screen in the
DevOps portal renders that. If the three ever disagree,
`backend/tests/api/test_compliance.py` fails — every evidence reference is
resolved against the real filesystem, the real served route table and the real
pytest node ids on each run.

**Twelve frameworks, India first.** Sections 2–5 are India's: the DPDP Act and its
2025 Rules, the CERT-In Directions, the MeitY / RBI / SEBI / BIS layer, and the
derived data-residency inventory the first two both depend on. Sections 6–14 are
the international frameworks. The ordering is by jurisdiction, not by where Aegis
scores well: for a deployment in India the first three are law and the rest are
practice, and a reviewer's own risk register is ordered the same way.

**Total: 124 controls — 40 enforced · 60 partial · 19 not implemented · 5 not
applicable.** Twenty-four of them are India's, and two of those twenty-four are
enforced. That ratio is the honest one and it is not flattered anywhere below.
**Two frameworks are now enforced in every control that applies to this deployment:
NIST AI RMF, all four functions, and MITRE ATLAS, nine of nine applicable — its tenth,
`AML.T0018 Backdoor ML Model`, cannot apply, because the only fitted model is trained
in-process from the host's own frame and there is no downloaded artefact to backdoor** —
see sections 8 and 9, and `docs/governance/`, the written artefacts that close Govern
and Map.

---

## 1. The four states

| State | Meaning | Rule for using it |
|---|---|---|
| **enforced** | A control runs in code, on every relevant request, and a test proves it. | Needs at least one *file* reference **and** one *test* reference. |
| **partial** | A real control runs, but a layer of it is advisory, opt-in, configuration-dependent, or narrower than the framework's control. | The `note` must say **which layer is missing**, not just "partially". |
| **not_implemented** | No control at this layer. Stated plainly. | Legitimate and respectable. Never dressed as partial. |
| **not_applicable** | The control governs something this system does not do. | The `note` must say why, or it is really `not_implemented`. |

Two rules that make the table worth reading:

1. **A reference is not a control.** `LLM04` appears once in the source tree; that
   single mention is a docstring, not a defence. Counting identifier occurrences
   measures documentation, not coverage. Every state below was decided by reading
   the mechanism.
2. **Scope is stated when it differs from the live posture.** `GET
   /v1/security/posture` reports the status of *the control it names*. This
   document reports the status of *the framework's control*, which is sometimes
   wider. Where the two differ (LLM04, LLM08) the difference is called out rather
   than reconciled by quietly lowering one of them.

---

## 2. India DPDP Act 2023 and DPDP Rules 2025

**Why this section is first.** For a deployment in India the DPDP Act is *law*; ISO/IEC
42001 and the OWASP lists are practice. Ordering the page by jurisdiction rather than by
where Aegis scores well puts the binding obligation at the top, which is the order a
reviewer's own risk register uses.

**Timing.** The Rules were notified on 14 November 2025. The Data Protection Board was
constituted 13 November 2025; Consent Manager registration opens 13 November 2026; the
remaining obligations — consent notices, data-principal rights, breach reporting and
Significant Data Fiduciary duties — bind from **13 May 2027**. Nothing below is late
yet. Everything below that says *not implemented* has a deadline.

**Role.** Aegis is a platform, not a Data Fiduciary. A deployment that decides the
purpose and means of processing is the Fiduciary; Aegis is what it processes with. The
rows say what Aegis *supplies to* that Fiduciary.

| § | Obligation | State | Evidence | What is missing |
|---|---|---|---|---|
| **s.5 / s.6** | Notice and consent | **not implemented** | — | No itemised notice, no consent record with a purpose attached, no withdrawal path, no Consent Manager integration (Rules 3 and 4). Aegis stores nothing that would prove consent was obtained. |
| **s.8(1)–(3)** | Fiduciary accountability and accuracy | **partial** | `aegis/src/aegis/memory/crud.py` — a belief is superseded, never overwritten, and carries who asserted and who corrected it; `aegis/src/aegis/retrieval/validation.py::validate_content` gates every write; `GET /v1/memory/writes`. | No processor agreement, no register of what is processed for which purpose, no reconciliation between what a Data Principal supplied and what the system later inferred. Accountability is per-record, not per-processing-activity. |
| **s.8(4)–(5)** | Technical, organisational and security safeguards (Rule 6) | **partial** | Postgres RLS with `FORCE ROW LEVEL SECURITY` and a `NOSUPERUSER NOBYPASSRLS` serving role audited at boot (`aegis/src/aegis/governance/rls.py`); Argon2id (`aegis/src/aegis/governance/security.py`); PII detection and masking on input, output and tool results (`aegis/src/aegis/guardrails/pii.py`). Test: `aegis/tests/governance/test_rls_enforcement.py::test_a_bypassing_role_stops_the_process_when_the_check_is_fatal`. | Rule 6 names **encryption**, obfuscation, masking and virtual tokens. Masking is real; encryption at rest is **absent across every store**, there is no key management or rotation, and authentication has no MFA. Access control is strong; cryptographic protection of stored data is not. |
| **s.8(6)** | Personal data breach intimation (Rule 7) | **not implemented** | — | Rule 7 wants intimation to every affected Data Principal without delay and a detailed report to the Board within **72 hours**. Aegis has the raw material — audit trail, SLA events, guardrail blocks, a durable inbox — and nothing that classifies an event as a personal-data breach or routes it outside the tenant. |
| **s.8(7)** | Storage limitation (Rule 8) | **partial** | `aegis/src/aegis/memory/retention.py::apply_retention` — the one unconditional, scheduled **hard delete** in the memory package: raw turns past the episodic horizon and facts closed past theirs. Horizons are per-tenant (`memory.retention_days`, default 90; `memory.closed_fact_retention_days`, default 30), with `0` documented as what a legal hold looks like. `GET /v1/memory/retention` previews it; `POST /v1/memory/retention/sweep` runs it. | The horizon is a **timer, not a purpose test** — Rule 8 asks for erasure when the specified purpose is no longer served, and nothing records a purpose to test against. Rule 8's advance intimation to the Data Principal does not exist. The sweep is deliberately narrow: `audit_log`, the usage ledger and `memory_write_log` are never swept, so personal data referenced there outlives the horizon **by design** — see s.16 and CERT-In Dir. (iv), where that same choice is the thing that helps. |
| **s.9** | Children's data, verifiable parental consent (Rule 10) | **not implemented** | — | Nothing in the system knows or asks how old a Data Principal is. No age signal, no parental-consent gate, and no per-subject switch that would disable behavioural tracking or targeted advertising — which Aegis does not do, but cannot *demonstrate* it does not do. |
| **s.10** | Significant Data Fiduciary — DPIA, independent audit, DPO (Rule 13) | **not implemented** | `GET /v1/risk-map` is a system-risk assessment and explicitly **not** a DPIA. | No annual DPIA, no independent data auditor, no DPO based in India. Rule 13's algorithmic due diligence — verifying that algorithmic software does not risk Data Principals' rights — is the one Aegis has material for, and even that is unassessed. |
| **s.11** | Right to access information about personal data | **partial** | `GET /v1/memory/facts`, `/memory/profile`, `/memory/sessions`, `/memory/writes` (why each belief is held) and four CSV exports. | s.11 asks for a **summary of the data processed and the processing activities undertaken**, plus the identities of other Fiduciaries it was shared with. Aegis serves the data; it does not serve the summary, holds no record of recipients, and has no single subject-access-request export. |
| **s.12(1)–(2)** | Right to correction and completion | **enforced** | `PATCH /v1/memory/facts/{fact_id}` corrects a stored belief; the timeline retains the supersession with the name of the person who made it rather than rewriting history, so the correction is itself auditable (`aegis/src/aegis/memory/crud.py`). Test: `backend/tests/api/test_memory_control.py::test_a_correction_supersedes_the_old_fact_and_names_the_person`. | — |
| **s.12(3)** | Right to erasure | **enforced** | `POST /v1/memory/forget` and `DELETE /v1/memory/facts/{fact_id}` perform a real **hard delete**, subject- and tenant-scoped and audited (`aegis/src/aegis/memory/crud.py::forget_fact(hard=True)`). The rest of the package archives rather than deletes; this is the one seam that removes, and it is where a Data Principal's request lands. Tests: `backend/tests/memory/test_e2e.py::test_forget_hard_erases_and_audits`, `aegis/tests/memory/test_crud.py::test_forget_fact_hard_removes_row_but_logs`. | — |
| **s.13** | Right of grievance redressal | **not implemented** | — | Rule 9 requires a published grievance mechanism and a stated response period. Aegis publishes no contact point, keeps no complaint record and runs no clock. The notification inbox is an *operator* surface; calling it grievance redressal would be the padding this page refuses. |
| **s.16** | Transfer of personal data outside India | **partial** | The derived inventory in section 5. Postgres, Qdrant, Neo4j and Redis all resolve to this host, so tenant documents, embeddings, the knowledge graph, memory and the audit trail are **at rest inside the deployment**. `backend/src/app/platform/residency.py`; tests `backend/tests/api/test_residency.py::test_the_local_deployment_keeps_every_store_on_the_host` and `::test_an_offshore_store_is_reported_external`. | **Nothing in code refuses an offshore gateway.** Prompts (after redaction), completions and the chunk text sent for embedding all travel to whatever `GATEWAY_BASE_URL` names — in this repository's own run file, an Azure endpoint in a **US** region. Web search sends the composed query to Tavily when a key is set. No transfer-impact assessment, no contractual safeguard. s.16 is a negative list and none is notified today, so this is lawful — it is not *local*. |

**Coverage: 2 enforced · 5 partial · 5 not implemented.**

---

## 3. CERT-In Directions (No. 20(3)/2022-CERT-In, 28 April 2022)

Issued under s.70B of the IT Act 2000 and binding on every body corporate serving users
in India. Six directions; one has a mechanism in this repository.

| Direction | Requirement | State | Evidence | What is missing |
|---|---|---|---|---|
| **(i)** | Synchronise clocks to NIC or NPL NTP | **not implemented** | — | Every timestamp is UTC from the host clock or the database's `now()` — internally consistent and completely silent about provenance. No configuration, check or startup assertion names a time source. A deployment can satisfy this at the OS level; Aegis cannot show that it did. |
| **(ii)** | Report cyber incidents to CERT-In within **6 hours** | **not implemented** | `GET /v1/notifications` is raw material, not an incident process. | The clock starts on *noticing*. Aegis has audit rows, SLA events, budget refusals and guardrail blocks, and none of the three things the Direction needs: a definition of which of those is a reportable incident, a person accountable for the clock, and a path to CERT-In's form. |
| **(iii)** | Designated point of contact; furnish information on order | **not implemented** | — | No point of contact is named anywhere in the system or its documentation. ISO/IEC 42001 A.3 records the same absence from the other side. |
| **(iv)** | Maintain ICT system logs **180 days**, **within Indian jurisdiction** | **partial** | **Volume:** every autonomous or approved action writes an `audit_log` row with actor, model, `trace_id`, payload, approver and tenant (`aegis/src/aegis/governance/audit.py`); runs are OpenTelemetry traces; every model call is on the usage ledger. **Place:** the derived inventory (section 5) shows the log stores resolving to this host, and `aegis/src/aegis/memory/retention.py` is written to *never* sweep `audit_log`, the usage ledger or the write log. Tests: `aegis/tests/governance/test_audit.py::test_record_audit_pulls_tenant_from_context`, `backend/tests/api/test_residency.py::test_the_local_deployment_keeps_every_store_on_the_host`. | The 180-day window is met **by the absence of a deleter, not by a control.** No retention policy on `audit_log`, no partitioning, no archival job, and no test that a 180-day-old row is still there. Append-only is now a **database privilege**: after the bulk grant, `grant_serving_role` revokes `UPDATE, DELETE` on `audit_log`, `usage_ledger`, `run_events` and every `run_events` monthly partition, so the serving role holds `SELECT, INSERT` there and `DELETE FROM audit_log` returns `permission denied for table audit_log` (`aegis/src/aegis/governance/rls.py`, `_APPEND_ONLY_TABLES`). What is still owed is the *retention* half: no archival job, no partition retention schedule for `audit_log`, and no test that a 180-day-old row is still there. The jurisdiction half is a **live read of configuration**, not a guarantee: re-point `POSTGRES_DSN` offshore and the logs follow — which is exactly what the inventory would then say. |
| **(v)–(vi)** | Subscriber KYC and transaction records — data centres, VPS, cloud, VPN, virtual-asset providers | **not applicable** | — | Aegis rents nobody infrastructure and custodies no virtual asset: no subscriber to KYC, no five-year transaction record. Listed rather than omitted, because a reader counting six directions should find six answers. |

**Coverage: 0 enforced · 1 partial · 3 not implemented · 1 not applicable.**

**The retention tension, stated once.** DPDP s.8(7) wants personal data *deleted* when
its purpose ends; CERT-In (iv) wants security logs *kept* for 180 days. Aegis resolves
this by corpus rather than by compromise: the retention sweep deletes raw conversation
turns and closed facts, and is written never to touch the audit trail. That is the right
shape — and the audit trail still carries payloads that can contain personal data, which
is an unresolved conflict rather than a solved one.

---

## 4. India — MeitY, RBI, SEBI and BIS

Grouped deliberately. Four separate tables would each be mostly "not applicable"; one
table answers the question a reviewer actually asks — *what does this mean for an Indian
deployment, and for a bank?*

The two BFSI rows are **not applicable on purpose**. Aegis has earned no banking or
securities compliance and must not imply it. What each row does say is what a regulated
deployment would inherit and what it would still owe.

| Framework | Control | State | Evidence | What is missing |
|---|---|---|---|---|
| **MeitY IAGG** (5 Nov 2025) | Accountability across the AI value chain | **partial** | Actor, model and `trace_id` on every action (`aegis/src/aegis/governance/audit.py`); a `Receipt` naming the origin of every figure (`web/src/components/primitives/Receipt.tsx`); `GET /v1/security/posture` re-derived from live wiring, guarded by `aegis/tests/security/test_posture.py::test_no_threat_claimed_enforced_when_its_control_is_off`. | Accountability is technical, not organisational: no accountable-role register, no self-assessment against the Guidelines, no grievance path. The Guidelines are voluntary and graded; nobody has graded this. |
| **MeitY IAGG** | Transparency and explainability | **partial** | `refs[]` of importable symbols behind every posture status, a `control_ref` on every risk row, a stated `Absence` where a figure cannot be sourced, and a model card reporting **empirical** conformal coverage on a held-out split. Test: `aegis/tests/security/test_posture.py::test_all_refs_are_importable`. | Transparency to the *operator* is real; to the *end user* it is not. Generated content is not marked as AI-generated on any output path (the EU AI Act Art. 50 gap), and there is no disclosure that an AI system is being interacted with. |
| **MeitY IAGG** | Human oversight and redressal | **partial** | The strongest control on the platform: the graph interrupts at `gate_min_risk` and waits for a named person; the inbox shows what approving would run; the SLA sweeper auto-**rejects** on timeout, so silence is a refusal rather than consent. Test: `backend/tests/data/test_approvals.py::test_sla_sweeper_expires_and_auto_rejects_high`. | The Guidelines pair oversight with grievance redressal, and redressal is absent — no channel, no complaint record, no clock. Same gap as DPDP s.13. |
| **MeitY IAGG** | Risk mitigation by design | **partial** | Guardrails on all three rails; a 48-attack battery with 11 benign controls so the block rate is quoted beside a false-positive rate; an inherent-versus-residual risk map where no risk is ever marked solved. Test: `aegis/tests/redteam/test_redteam.py::test_control_category_blockrate_is_false_positive_rate`. | The hazard taxonomy is MLCommons and OWASP. No India-specific harm set, no Indian-language adversarial probe, no evaluation against the deepfake and synthetic-content harms the Guidelines emphasise. |
| **IS 17428-1:2020** | Data privacy assurance — engineering and management requirements | **partial** | Privacy by design in the redaction rails, database-enforced tenant isolation, bounded retention with a scheduled hard delete, correction and erasure endpoints, least-privilege database roles (`scripts/sql/aegis-app-role.sql`). Test: `backend/tests/memory/test_e2e.py::test_delete_single_fact_erases_and_audits`. | The **management** half is entirely absent — no privacy policy, no privacy function or officer, no records of processing, no internal privacy audit, no continual-improvement cycle. Part 1's management requirements are the mandatory ones, so a certification attempt would fail on process, not on code. |
| **RBI** Master Direction on IT Governance, Risk, Controls and Assurance (7 Nov 2023, effective 1 Apr 2024) | Applicable only inside an RBI-regulated entity | **not applicable** | `aegis/src/aegis/governance/rls.py`, `backend/src/app/platform/residency.py` — what such a deployment inherits. | Aegis is not a bank, NBFC or co-operative bank and claims **no BFSI compliance**. A regulated deployment would inherit database-enforced tenant isolation, least-privilege roles, an action-level audit trail, a fail-safe human gate and the derived destination inventory. It would still owe, and Aegis supplies none of these: a board-approved IT strategy and IT Strategy Committee, an independent Information Systems audit, documented and *tested* BCP/DR, change and incident management processes, and the RBI's own storage expectation for payment-system data. |
| **SEBI** Cyber Security and Cyber Resilience Framework (2024) | Applicable only inside a SEBI-regulated entity | **not applicable** | — | Aegis is not a SEBI-regulated entity and holds no CSCRF compliance. CSCRF's core demands are organisational and continuous: a Security Operations Centre with defined monitoring, periodic VAPT with closure timelines, a cyber-audit report to SEBI, and a tested cyber-crisis management plan. Aegis has no SOC, no VAPT record and no incident-response procedure — every one of which such a deployment would still owe. It contributes evidence; it satisfies none of the framework. |

**Coverage: 0 enforced · 5 partial · 2 not applicable.**

---

## 5. Data residency — where this deployment's data actually goes

DPDP s.16 and CERT-In Dir. (iv) both turn on one question that is not a legal one:
**where does the data go?** A hand-written answer is worth nothing — it is true the day
it is typed and false the first time somebody edits an environment variable.

So this is **derived, not asserted** (`backend/src/app/platform/residency.py`, served
inside `GET /v1/compliance` as `residency`). Every field on `app.config.Settings` that
names a network destination is declared exactly once with the sentence saying what
travels through it; at read time the live value is parsed, credentials are stripped and
the host is classified. `backend/tests/api/test_residency.py::test_every_destination_setting_is_declared`
walks `Settings.model_fields` and fails if a destination-bearing field exists that no
channel claims — so a new outbound dependency cannot be added without either declaring
it here or breaking the suite.

**Data at rest — all six destinations local in the default and shipped configuration.**

| Destination | Carries | Setting |
|---|---|---|
| PostgreSQL | Every tenant row: documents, chunks, memory facts and raw turns, approvals, the usage ledger, the audit trail | `POSTGRES_DSN` |
| PostgreSQL (bootstrap) | Schema and role provisioning | `POSTGRES_ADMIN_DSN` |
| PostgreSQL (console role) | A closed set of parameterised `SELECT`s over a `SELECT`-only role | `AEGIS_DB_CONSOLE_DSN` |
| Qdrant | Chunk embeddings and payloads, mirrored from the embedding of record | `QDRANT_URL` |
| Neo4j | Entities and relations extracted from tenant documents | `NEO4J_URI` |
| Redis | Retrieval and answer caches (query text, embeddings, cached answers), web-search results, rate-limit counters | `REDIS_URL` |

**Egress — what actually leaves, and it is not nothing.**

| Destination | What leaves | Setting |
|---|---|---|
| **Model gateway** | The one channel carrying tenant content off the host: prompts **after** PII redaction, completions, and **the chunk text sent for embedding**. Every model call in the platform goes through here and nowhere else (`aegis/src/aegis/gateway/llm.py`). | `GATEWAY_BASE_URL` |
| Tavily | The composed search query only. No document, memory or tenant row. With no key the run degrades to internal evidence and says so. | `TAVILY_API_KEY` |
| PyPI | Package names already in the lockfiles, on an explicit operator action. No tenant data. | `POST /v1/stack/patch-check` |
| Hugging Face | A one-way ONNX cross-encoder download on first load, cached on disk. Nothing is uploaded. | `RERANK_LOCAL` |
| MCP peers | Tool names and arguments an agent chose, to whichever peer a platform admin declared. Unconfigured in this deployment. | `AEGIS_MCP_CLIENT_SERVERS` |

**What this claim can say.** Tenant documents, embeddings, the knowledge graph, memory,
approvals and the audit trail are at rest on the deployment host, and that is *checked
on every read* rather than promised. Re-pointing any store at an outside host flips the
surface to `external` on the next read; a test proves it does.

**What it cannot say.** Three things, and they are the honest limits:

1. **The model gateway is a real egress.** Chunk text goes out to be embedded. Prompts
   go out after redaction, which reduces but does not eliminate personal data. In this
   repository's own run file the gateway is an **Azure endpoint in a US region** —
   `genailab.tcs.in`, an Indian host, is present but commented out. A deployment that
   needs Indian processing must point `GATEWAY_BASE_URL` at an Indian endpoint, and
   **nothing in the code enforces that**.
2. **It reports addressing, not geography.** It parses a configured host. It does not
   geolocate an IP, and it cannot resolve a cloud region to a country. A destination
   reported `external` is definitely off-host; a destination reported `local` is on this
   machine or its private network, and where *that* machine sits is the operator's fact.
3. **It does not inspect payloads.** The "what leaves" column is read from the code by a
   human and reviewed against it, not extracted from traffic.

---

## 6. OWASP Top 10 for LLM Applications (2025)

The live, wiring-derived version of rows LLM01/02/04/05/06/07/08/09/10 is
`GET /v1/security/posture` (`aegis/src/aegis/security/posture.py`). Its statuses
flip with configuration; the states here are the framework-level judgement.

| ID | Risk | State | Evidence | What is missing |
|---|---|---|---|---|
| **LLM01** | Prompt Injection | **enforced** | `aegis/src/aegis/guardrails/classifier.py` (`deterministic_injection` signature backstop + fail-closed `classify_injection`); screened at all three rails — `aegis/src/aegis/guardrails/pipeline.py` (`check_input`, `check_output`, `check_tool_result`); indirect injection tested at the rail it would actually arrive on. 16 of 48 attack probes carry `owasp="LLM01"`. Tests: `aegis/tests/redteam/test_stages_and_suites.py::test_the_indirect_injections_are_caught_at_the_tool_result_rail`, `aegis/tests/security/test_posture.py::test_injection_enforced_when_model_layer_wired`. | Degrades to **partial** when no model completer is wired — 10 battery probes are semantic-only and leak offline **by design**, and the report says so rather than hiding it. Injection is never marked solved (`backend/src/app/platform/risk_map.py`, AA-03). |
| **LLM02** | Sensitive Information Disclosure | **enforced** | `aegis/src/aegis/guardrails/pii.py` (`redact`, `scan`; Presidio or anchored-regex+Luhn engine) applied inbound *before the classifier call*, outbound before the answer, and on tool results. Red-team suite `disclosure` (5 probes). Test: `aegis/tests/security/test_posture.py::test_pure_code_controls_are_enforced`. | — |
| **LLM03** | Supply Chain | **enforced** | Inventory, verdict and gate. *Inventory:* hash-pinned lockfiles (`backend/uv.lock` + `aegis/uv.lock`, 6,367 `sha256` digests between them) and a live SBOM resolved from the **actually installed** distributions at request time (`backend/src/app/platform/stack.py` → `GET /v1/stack`), exported as CycloneDX 1.6 and SPDX 2.3 from one inventory pass (`backend/src/app/platform/sbom.py` → `GET /v1/stack/sbom`) so a buyer's own scanner can read it. *Verdict:* a live OSV.dev query gives each installed version a vulnerability verdict (`backend/src/app/platform/advisories.py` → `POST /v1/stack/advisories`), holding the same honesty rule as the patch check — never `clean` without a real answer, and `passed` is false whenever anything is vulnerable **or** unknown. Freshness stays a separate question (`POST /v1/stack/patch-check`). *Gate:* `.github/workflows/ci.yml` runs the audit and fails the build on any advisory not recorded in `backend/known_advisories.json`, and on any package the feed could not be asked about. Tests: `backend/tests/api/test_supply_chain.py`, `backend/tests/api/test_platform_surfaces.py`. | Two advisories are currently outstanding and both are pinned by upstream libraries — `presidio-anonymizer` holds `cryptography` under 49 (fixes land in 49/50), `arize-phoenix` pins `strawberry-graphql==0.314.3` (fixes land in 0.315.4/0.315.7). They are reported as **vulnerable** on `POST /v1/stack/advisories` and recorded in `backend/known_advisories.json` with what would release each; the acknowledgement stops the build failing and changes nothing else. No in-toto/SLSA build provenance — the one assurance here that per-file `sha256` pinning does not substitute for. |
| **LLM04** | Data & Model Poisoning | **partial** | Retrieval side is gated at write time: `aegis/src/aegis/retrieval/validation.py::validate_content` rejects embedded injection payloads, oversized and non-printable blobs *before* the store — and the gate is now **attacked rather than asserted**, by six poisoning probes at a fourth battery stage aimed at it (MITRE ATLAS AML.T0020; `aegis/tests/redteam/test_atlas_families.py`). Five of six are refused before the store. | The sixth probe leaks and is marked semantic-only: a poisoned **fact** in ordinary policy prose carries no instruction for a deterministic gate to match. And the ML spine still trains from a host-supplied frame (`aegis/src/aegis/ml/dataset.py`) with **no integrity digest**, so nothing records which frame produced a given fitted model. Partial at the framework level. |
| **LLM05** | Improper Output Handling | **enforced** | `aegis/src/aegis/guardrails/schema.py` (`validate_output_format` → `content_filter`) on the outbound path, always on, pure code. Test: `aegis/tests/security/test_posture.py::test_pure_code_controls_are_enforced`. | — |
| **LLM06** | Excessive Agency | **enforced** | Risk-tiered typed tools (`aegis/src/aegis/core/types.py::RiskLevel`) and a durable human gate: `aegis/src/aegis/agent/graph.py` interrupts at `gate_min_risk`; `backend/src/app/data/approvals.py` holds the queue, an optimistic `PENDING→RESUMING` lock giving exactly-once resume, and an SLA sweeper that **auto-rejects** HIGH risk on timeout (fail-safe, not fail-open). External MCP tools default to HIGH (`backend/src/app/mcp/server.py`). Red-team suite `excessive-agency`. ADR `docs/adr/0005`, `docs/adr/0007`. | Per-persona tool allowlist is enforced host-side (`backend/src/app/adapter/tools.py`), not introspectable from `aegis` core — which is why the live posture reports `AGENTIC-TOOL-MISUSE` as partial. |
| **LLM07** | System Prompt Leakage | **enforced** | `aegis/src/aegis/guardrails/schema.py::content_filter` carries leakage signatures on the outbound path. Red-team suite `disclosure` includes 3 meta-prompt-extraction probes. | — |
| **LLM08** | Vector & Embedding Weaknesses | **partial** | Both ends of RAG are defended: `aegis/src/aegis/retrieval/spotlight.py::build_spotlighted_context` fences and datamarks retrieved spans as untrusted DATA (Microsoft Spotlighting, arXiv:2403.14720), and `validate_content` screens every write. Every retrieval arm applies a tenant predicate, and a null tenant is a positive match on the shared corpus rather than a wildcard — both tested cross-tenant (`aegis/tests/retrieval/test_tenant_isolation.py`). | The vector tier is one **shared** Qdrant collection (`aegis/src/aegis/retrieval/lightrag_backend.py::DEFAULT_CHUNK_COLLECTION`) and the predicate is a payload filter written by application code. The difference from RLS is the failure direction: under Postgres RLS a query that forgets its tenant clause returns **nothing**; here it returns **everything**. Per-tenant collections, or a database-enforced predicate, is what would close this. |
| **LLM09** | Misinformation | **partial** | `aegis/src/aegis/guardrails/grounding.py::check_grounding` now returns **two** findings, because they deserve different answers. An answer the retrieved passages **contradict** — retrieval found the fact and the answer says the opposite — is a hard BLOCK by default, `grounding_block` or not: there is no legitimate turn of that shape. An answer that is merely **unsupported** stays an advisory FLAG, because most of those are fine and a rail that blocks them is one an operator switches off. Backed by a per-metric eval regression gate (`aegis/src/aegis/evals/regression.py`) and content-safety hazard screening (`aegis/src/aegis/guardrails/content_safety.py`). Tests: `aegis/tests/guardrails/test_grounding.py`, `aegis/tests/security/test_posture.py::test_misinformation_is_honestly_partial`. | Both findings are semantic entailment judgements with **no deterministic backstop**, so with no completer wired the rail is a no-op and this control is worth nothing. The zero-retrieval case is reported and deliberately never blocked — plenty of legitimate turns answer with no passages — so an answer invented out of the model's own knowledge is flagged, not stopped. |
| **LLM10** | Unbounded Consumption | **enforced** | The self-repair loop is *always* hard-capped (`AgentConfig.max_plan_iterations` — guaranteed termination). Token/USD/RPM/TPM caps are enforced **before spend** at the single gateway chokepoint (`aegis/src/aegis/governance/enforcement.py::enforce_governance`), with a 429 admission gate on job submission. A budget control that fails open is not a control, so both ways it could fail to bind are refused **at boot** outside dev by `Settings.ensure_spend_caps_bind` (`backend/src/app/config.py`, called from `create_app`): `BUDGET_FAIL_OPEN=true` will not start, and neither will a deployment whose composition root has lost the governance hook at the gateway. The refusal names that variable rather than `GATEWAY_BUDGET_FAIL_OPEN`, which is the standalone gateway's own knob and is inert here because `app.core.llm` injects a config. Tests: `backend/tests/core/test_startup_guard.py`, `aegis/tests/security/test_posture.py::test_consumption_enforced_with_budget_hook`, `::test_consumption_partial_when_budget_fail_open`. | Dev may still run fail-open — the same asymmetry the JWT guard uses, because a guard that blocks the local loop is one somebody deletes. The live posture reports such a box as `partial` rather than green. |

**Coverage: 7 enforced · 3 partial · 0 not implemented.**

### What changed in this pass

`LLM03` previously had **zero** references anywhere in `aegis/src` or
`backend/src` — the SBOM, the lockfile hashes and the registry patch-check were
all real and none of them was labelled as supply-chain assurance. Labelling them
was the first pass, and it stayed **partial** on the reasoning that *inventory
plus freshness is not vulnerability management*. That reasoning was right, and
the answer to it was to build the missing half rather than to soften the
sentence: an OSV.dev advisory verdict, a CycloneDX/SPDX export a third party can
read, and a CI step that fails the build. `LLM10` moved for the same reason —
a budget control that fails open is not a control, so a fail-open production
boot is now refused rather than reported.

Three rows did **not** move, and each is worth reading for why. `LLM04`'s corpus
gate is now attacked by six probes instead of asserted, and five of six are
refused — but a poisoned *fact* in ordinary prose has no signature, and the ML
spine's training frame still carries no integrity digest. `LLM08` gained a
sharper sentence rather than a better state: the tenant predicate on the vector
tier is real and tested, and it is still a payload filter over one shared
collection, so a forgotten clause returns everything where RLS would return
nothing. `LLM09` learned to hard-block an answer its retrieved passages
*contradict* — the case where blocking costs nothing — and stays partial because
the rail is a model judgement with no deterministic backstop underneath it.

---

## 7. OWASP Top 10:2025 (web / API surface)

SSRF is no longer its own category in the 2025 list; the MCP peer surface is
recorded under A01 where it belongs.

| ID | Risk | State | Evidence | What is missing |
|---|---|---|---|---|
| **A01:2025** | Broken Access Control | **partial** | Role guards on every route (`require_auth` / `require_admin` / `require_devops` / `require_platform_admin` / `require_platform_security_reader` in `backend/src/app/api/routes.py`); Postgres RLS with `FORCE ROW LEVEL SECURITY` and a `NOSUPERUSER NOBYPASSRLS` serving role whose exemption status is **audited at boot** (`aegis/src/aegis/governance/rls.py::report_rls_enforcement`, `RlsBypassError`); per-request tenant binding; portal catalogue and served route table are related by a test so the browser cannot offer a control the backend refuses. Tests: `backend/tests/api/test_cross_tenant_holes.py`, `backend/tests/api/test_roles_rbac.py`, `backend/tests/data/test_rls_serving_role.py`, `backend/tests/api/test_route_coverage.py`. | **The SSRF gap is closed**: a declared MCP peer's URL is validated at the registry chokepoint every declaration path passes through (`backend/src/app/mcp/client.py::validate_peer_url`, called from `register_server` and `update_server`) — `http`/`https` only, a host required, and loopback / link-local / private / reserved literals refused unless the deployment sets `AEGIS_MCP_ALLOW_PRIVATE_PEERS`. `POST /v1/mcp/servers` with `http://169.254.169.254/latest/meta-data/` answers 400. Test: `backend/tests/mcp/test_peer_url.py`. **Residual, stated:** no DNS is resolved, so a hostname whose answer is a private address is accepted and dialled — closing that needs resolution at connect time with the address pinned, and anything checked at declare time is a TOCTOU window against rebinding. |
| **A02:2025** | Security Misconfiguration | **partial** | Misconfiguration is *surfaced rather than assumed*: `GET /v1/security/posture` reads dev-JWT-secret, `budget_fail_open`, `rls_fail_closed` and PII engine from live wiring and downgrades the posture accordingly. The DB console is off unless two env vars are both set (`backend/src/app/api/routes_db.py`). Request models are `extra="forbid"` (`backend/tests/api/test_request_models_forbid_extras.py`). | The documented **dev JWT secret is still in force in the default (`APP_ENV=dev`) run** — the posture says so, and that is the honest state, not a passing one. What changed is that it can no longer reach production: `Settings.ensure_secure_secrets` refuses to boot a non-dev deployment on the built-in default, on a secret under 32 characters, on a known placeholder (`change-me`, `changeme`, `supersecret`, … — matched as substrings, so padding one to the length floor does not get through) or on one drawing on fewer than 12 distinct characters, which is what `"x" * 48` did while passing the old length-only check (`backend/src/app/config.py`, test `backend/tests/core/test_startup_guard.py`). No hardening baseline (CIS/benchmark), no TLS configuration owned in-repo. |
| **A03:2025** | Software Supply Chain Failures | **partial** | Same evidence as LLM03 above: hash-pinned lockfiles, a live SBOM with CycloneDX/SPDX export, an OSV.dev vulnerability verdict, and a CI step that fails the build on an unrecorded advisory. | Kept **partial** here rather than promoted with LLM03, because this row covers the whole software supply chain and not only the Python dependency graph: there is no in-toto/SLSA build provenance, the npm side of `web/` is not audited by the gate, and no artefact this repository produces is signed. |
| **A04:2025** | Cryptographic Failures | **partial** | Argon2id password hashing and signed JWTs (`aegis/src/aegis/governance/security.py::hash_password`, `create_access_token`). MCP peer credentials are held in the serving process only and never written to the database or returned by any route (`backend/src/app/api/routes_mcp.py`). | Symmetric HS256; the dev-default secret ships but a non-dev boot now refuses it (see A02). **Nothing is encrypted at rest, and column-level encryption was assessed and deliberately refused** — the reasoning is in `backend/src/app/platform/at_rest.py`: one uploaded document comes to rest in eight places (original bytes and parsed text in `document_storage/`, `chunks.text`, the embeddings, the graph store, the semantic cache, `chat_messages`/`memory_message`, and all the Postgres ones again in the WAL and every backup), column encryption reaches three, and the two holding the most content are the two that cannot be encrypted without removing retrieval and semantic recall. It also defends only a stolen dump, never a compromised application, which holds the key by construction. The control is transparent volume encryption plus encrypted backups, which is a deployment control. What the code does is refuse to claim it: the posture is derived from live wiring and reads `none` until an operator declares it (`AEGIS_STORAGE_ENCRYPTION`), and a declaration is labelled *declared*, never *measured*. No key rotation. |
| **A05:2025** | Injection (SQL/command) | **enforced** | Every query is SQLAlchemy-parameterised. The DB console is a **closed set** of parameterised reads over a role holding `SELECT` and nothing else (`scripts/sql/aegis-readonly-role.sql` revokes `INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER` and re-grants column-level `SELECT` on `users`), with the tenant clause written by the server, never the client (`backend/src/app/api/routes_db.py`). Identifiers interpolated into DDL are validated against a narrower-than-Postgres pattern and *refused* rather than escaped (`aegis/src/aegis/governance/rls.py`, `_SAFE_ROLE_NAME`). CSV/DDE formula injection is neutralised on export (`aegis/src/aegis/reports/writer.py`). Test: `backend/tests/api/test_db_console.py`. | — |
| **A06:2025** | Insecure Design | **partial** | A written threat model (`docs/security/threat-model.md`), an inherent-vs-residual risk map with a `control_ref` per row (`backend/src/app/platform/risk_map.py` → `GET /v1/risk-map`), nine ADRs, fail-closed defaults throughout, and an adversarial battery run against the design. | No formal design review or abuse-case process; the risk-map figures are stated engineering judgement, and the module says so in its own docstring. |
| **A07:2025** | Authentication Failures | **partial** | Argon2id, signed JWT with `{sub, role, tenant_id}`, fine-grained portal roles, and a regression test specifically for auth bypass (`backend/tests/api/test_auth_backdoor.py`, `backend/tests/api/test_login_fine_role.py`). | **No MFA. No account lockout or login rate limiting. No password policy. No token revocation list** — a leaked token is valid until it expires. |
| **A08:2025** | Software or Data Integrity Failures | **partial** | Hash-pinned lockfiles (above); an append-only audit trail; exactly-once approval resume via an optimistic lock and an `approval_id` idempotency key. | No signed build artefacts and no SLSA provenance — CI now exists (`.github/workflows/ci.yml`) but produces neither. The audit trail's append-only property is now a database privilege rather than a convention (see A09). |
| **A09:2025** | Security Logging and Alerting Failures | **partial** | `audit_log` records every autonomous or approved action with actor, model, `trace_id`, payload, approver and tenant (`aegis/src/aegis/governance/audit.py::record_audit`); reads filter in SQL, never in the page (`aegis/tests/governance/test_audit_filters.py`); OpenTelemetry traces end to end; a durable notification inbox raises SLA and auto-decision events (`backend/src/app/data/approvals.py`). | **"Append-only" is now a database guarantee**: the serving role holds `SELECT, INSERT` on `audit_log`, `usage_ledger` and `run_events` (and each `run_events` monthly partition, which is separately addressable by name), so `DELETE FROM audit_log` on the connection every request arrives on returns `permission denied for table audit_log` (`aegis/src/aegis/governance/rls.py`, `_APPEND_ONLY_TABLES`; test `aegis/tests/governance/test_rls_enforcement.py::test_the_append_only_ledgers_end_at_select_insert_not_full_dml`). **What it does not stop:** the owner/DDL role still holds full DML, so anyone with `POSTGRES_ADMIN_DSN` can rewrite the trail — this makes tampering require the owner connection, not impossible. `memory_write_log` is deliberately excluded and keeps `DELETE`, because the DPDP/GDPR erasure route must reach it from a request handler and the alternative would put an RLS-bypassing connection in the request path. No SIEM export. No documented `audit_log` retention or partitioning (recorded as owed work in `docs/dev_new_docs_v2/backlog-post-hackathon.md`). |
| **A10:2025** | Mishandling of Exceptional Conditions | **partial** | Fail-closed by construction in the places that matter: an unparseable or unavailable injection classifier is **treated as injection**; RLS admits no rows when the tenant GUC is unset; the SLA sweeper auto-**rejects** rather than auto-approves; the patch-check refuses to report "current" without a real registry answer; the MCP client enforces a deadline a cancel-resistant SDK teardown cannot outlive (`backend/src/app/mcp/client.py::_bounded`). Server refusals surface the server's own sentence (`web/src/lib/api/apiError.ts`). | Not systematically audited across every handler; there is no error-budget or chaos testing. |

**Coverage: 1 enforced · 9 partial · 0 not implemented.**

---

## 8. MITRE ATLAS

Only techniques the **battery actually exercises** are claimed. 59 probes: 48
attacks and 11 benign controls that measure the false-positive rate — a block rate
quoted without one is the number a vendor quotes. Four families were added because
four of these rows said, in writing, that nothing tested them; each runs inside the
default `owasp-full` suite, so its leaks are in the headline block rate and cannot be
excluded from the report a reviewer reads. Offline, that headline is **38 of 48
blocked (79%) with a 0% false-positive rate**, and every one of the ten leaks is a
probe the battery declares semantic-only.

| Technique | State | Evidence |
|---|---|---|
| **AML.T0051.000** Direct Prompt Injection | **enforced** | 4 `prompt_injection` probes at the input rail — `aegis/src/aegis/redteam/battery.py`, suite `prompt-injection`. |
| **AML.T0051.001** Indirect Prompt Injection | **enforced** | 3 `indirect_injection` probes fed to `check_tool_result`, the rail a poisoned tool return actually arrives on — `aegis/tests/redteam/test_stages_and_suites.py::test_the_indirect_injections_are_caught_at_the_tool_result_rail`. |
| **AML.T0054** LLM Jailbreak | **enforced** | 4 `jailbreak` probes, one of which is deliberately semantic-only and marked `needs_llm=True` so the report explains an offline leak instead of hiding it. |
| **AML.T0056** LLM Meta Prompt Extraction | **enforced** | 3 `system_prompt_leak` probes, suite `disclosure`. |
| **AML.T0057** LLM Data Leakage | **enforced** | 3 `pii_extraction` (input) + 2 `output_disclosure` (output rail) probes, suite `disclosure`. |
| **AML.T0053** LLM Plugin Compromise | **enforced** | A hostile peer is stood up and the platform is made to refuse it, end to end: a real in-process `MCPServer` over the SDK's own transport whose tool description, argument schema and return value each carry an attack, screened by the **real** `TOOL_RESULT` rail rather than an injected stub (`backend/tests/mcp/test_hostile_peer.py`). The poisoned tool is dropped at discovery so its text never reaches the planner's prompt; the poisoned result is withheld from the agent's context and from the audit row. The battery's `plugin-compromise` suite runs the same constants the peer serves, so the two cannot drift apart. External MCP tools remain HIGH by default and stop at the human gate (`backend/src/app/mcp/server.py`). The fourth probe is the finding: a peer that returns a *plausible wrong answer* passes the rail, and what stops it is the tier, not the text. |
| **AML.T0020** Poison Training Data | **enforced** | Six poisoning probes at a fourth battery stage (`Stage.INGEST`) aimed at the write-time gate — the only rail a poisoning attack ever meets, since it arrives as a *document* months before the question it is meant to answer. An override in a handbook page, a forged SYSTEM turn, a stored macro instructing a later exfiltration, an oversized blob and a non-printable one are all refused before the store; the sixth — a poisoned *fact* in ordinary policy prose — leaks, is marked semantic-only, and sets the suite's floor at the reach the gate actually has (`aegis/tests/redteam/test_atlas_families.py`). |
| **AML.T0024** Exfiltration via AI Inference API (model extraction) | **enforced** | Both halves are refused. **Channel:** `aegis/src/aegis/guardrails/schema.py::exfiltration_channel` blocks an answer that carries data out through a URL nobody clicked — an auto-loading markdown or HTML image, or a link, pointing at an external host with an encoded payload in its query, path or fragment. Three probes exercise it; an ordinary documentation link is not a false positive. **Volume:** `aegis/src/aegis/security/extraction.py` reduces a query to a template by masking ids and numbers, and refuses a principal running one template over many distinct values — or touching an abnormal breadth of subjects — with 429 before the stream opens, wired as the first act of `POST /v1/query` (`refuse_if_extracting`). The finding is audited under the **masked** template, so no swept id enters the trail, and raised to the tenant's admins as an alert. It blocks rather than flags: a flag that lets query 31 through lets 500 through, and this technique completes by volume. Four benign controls hold the false-positive rate at 0% and each is saved by a different clause. Tests: `aegis/tests/redteam/test_atlas_families.py::test_extraction_by_query_volume_is_detected_per_principal_and_refused`, `::test_ordinary_work_that_looks_like_enumeration_is_not_flagged`, `backend/tests/api/test_extraction_gate.py::test_the_alert_reaches_the_tenants_admin_and_nobody_elses`. **What still leaks, probed rather than assumed:** the detector is rate-shaped, so a sweep paced under the threshold completes unobserved — two probes are exactly that, carrying `beyond_rails` rather than `needs_llm` because no completer closes them either. The window is in-process and non-durable (a restart clears it; a second API worker halves what each principal is seen doing), it is keyed per principal (two credentials halve an attacker's observed rate), and there is no service-account exemption, so an authorised bulk job is behaviourally indistinguishable from enumeration and will be refused. |
| **AML.T0043** Craft Adversarial Data | **enforced** | The model an attacker actually crafts data against here is the **injection detector** on the request path, and it is attacked: five evasion probes take one override and perturb it until the signature layer stops matching — hex, percent-encoding, ROT13, plain reversal — each of which walked straight through before `aegis/src/aegis/guardrails/classifier.py::_decoded_candidates` learned to decode and screen it as the instruction it carries. The fifth is a paraphrase; it leaks, it is marked semantic-only, and it is the boundary between the deterministic layer and the model one. Base32, Morse and a separator-spelled instruction are asserted as remaining misses rather than left to be assumed covered (`aegis/tests/guardrails/test_injection_evasion.py`). The forecasting ensemble is a different model and not a security control — its inputs are the host's own records, and its quality is gated by the eval regression thresholds. |
| **AML.T0018** Backdoor ML Model | **not_applicable** | Aegis loads no third-party model weights; the only fitted model is trained in-process from the host's frame (`aegis/src/aegis/ml/model.py`). |

**Coverage: 9 enforced · 0 partial · 0 not implemented · 1 not applicable** — every
technique that applies to this deployment is enforced.

---

## 9. NIST AI RMF 1.0

| Function | State | Evidence | What is missing |
|---|---|---|---|
| **GOVERN** | **enforced** | **This function is documentation and process — that is the form NIST asks for, so the documents are the control.** `docs/governance/ai-policy.md` (purpose, prohibited use, the acceptable-use boundary, model sourcing, and where human oversight binds — every clause naming the mechanism that enforces it); `docs/governance/accountable-roles.md` (an owner for each of the five roles the software actually guards, mapped to `web/src/lib/portal.ts` and the `require_*` guards); `docs/governance/incident-response.md` (detection, triage, containment, notification, review — keyed to `audit_log`, the SLA sweeper, `/readyz`, the guardrail verdicts and the red-team battery); `docs/governance/review-cadence.md` (period, reviewer and off-cycle triggers). Over nine ADRs, a written threat model and the per-tenant policy catalogue (`aegis/src/aegis/settings/spec.py`). Test: `backend/tests/api/test_governance_docs.py` resolves every repository path these documents cite against the real filesystem and checks each carries the commitments this row claims. | — |
| **MAP** | **enforced** | `GET /v1/risk-map` places each agentic risk at an inherent **and** residual point with the control that moved it and a `control_ref` naming a real file; `GET /v1/stack` is the system inventory; `GET /v1/ml/model-card` states task, features, data source, calibration and training sizes; `aegis.pipelines` declares each flow's stages and is verified against the code before it is served. `docs/governance/context-and-impact.md` adds the two halves that were missing: the deployment context, and the people affected — a tenant's end customers, whose service requests and documents this system reads, plus third parties merely named in a case note. Thirteen harms, each paired with the mitigation that exists in this repository, and **four marked as having none** (no channel to the data principal, no disclosure that AI was involved, nothing encrypted at rest, no age signal). Test: `backend/tests/api/test_governance_docs.py::test_the_impact_assessment_pairs_every_harm_with_a_mitigation_or_says_there_is_none`. | — |
| **MEASURE** | **enforced** | This is Aegis's strongest function and it is measured, not asserted. Red-team battery reports block rate **and** false-positive rate against per-suite offline/live floors (`aegis/src/aegis/redteam/`, `POST /v1/redteam/runs`, history and trend). Eval regression gate with declarative per-metric thresholds and direction (`aegis/src/aegis/evals/regression.py`). Conformal coverage reported as **empirical coverage on a held-out split**, null when nothing was held out (`aegis/src/aegis/ml/types.py::ModelCard`). Latency percentiles from real samples, honest empty state instead of fake zeros. Security posture derived from live wiring on every request, with `test_no_threat_claimed_enforced_when_its_control_is_off` as the guard. | Measurement is offline/pre-deployment; there is no continuous production monitoring of guardrail efficacy. |
| **MANAGE** | **enforced** | Human approval gate with a durable queue, SLA deadlines and fail-safe auto-rejection; conformal abstention as a terminal band rather than an over-confident act; budget enforcement before spend with a clean terminal event instead of a crash; tiered LLM-Ops release with rollback (`aegis/src/aegis/ops/release.py`); append-only audit trail; notification inbox. | No documented residual-risk acceptance sign-off. |

---

## 10. ISO/IEC 42001 (AI management system) — Annex A

| Control | State | Evidence / why |
|---|---|---|
| **A.2** Policies related to AI | **partial** | `docs/security/` and nine ADRs carry the position; there is no signed AI policy document or review cycle. |
| **A.3** Internal organization | **not_implemented** | Aegis has a *system* role model (`web/src/components/admin/roleCatalog.ts`, seats in `aegis/src/aegis/settings/spec.py`) — that is access control, not an organisational AI-governance structure. No reporting line for AI concerns. |
| **A.4** Resources for AI systems | **enforced** | `GET /v1/stack` (live SBOM tied to the branded module each component powers), `aegis.pipelines` declarations, `GET /v1/ml/model-card` (data source, training and calibration sizes), the settings catalogue. Every one is resolved from the running system, not a maintained list. |
| **A.5** Assessing impacts of AI systems | **partial** | `GET /v1/risk-map` is a real assessment of how the *system* can go wrong, with inherent and residual positions. It is not an assessment of impact on individuals or society, which is what A.5 asks for. |
| **A.6** AI system life cycle | **partial** | Verification is genuine: eval regression gate, red-team battery, three test suites (`aegis`, `backend`, `web`), tiered release with rollback, ADRs at each decision. There are no documented per-release acceptance criteria beyond the metric thresholds. |
| **A.7** Data governance | **partial** | Write-time content validation, PII redaction on all three stages, per-tenant RLS, configurable retention with a stated reason for the merge rule (`memory.retention_days`, `memory.closed_fact_retention_days`), hard erasure. No provenance record for the ML training frame; no data classification scheme. |
| **A.8** Information for interested parties | **partial** | The model card, the OpenAPI contract, the `Receipt`/`Absence` discipline that puts an origin under every figure, and 29 teaching documents. There is no external incident-reporting channel. |
| **A.9** Use of AI systems | **partial** | Human gate, conformal autonomy bands, per-persona tool allowlists, per-tenant settings with scope attribution. No stated intended-use / prohibited-use documentation per deployment. |
| **A.10** Third-party and customer relationships | **partial** | One vetted model gateway (ADR 0001); an MCP peer registry where every external tool is HIGH risk until a platform admin lowers a **named** tool, a disabled peer's tools leave the payload entirely, and whatever a peer returns passes the `TOOL_RESULT` rail before it reaches a prompt. No supplier assessment record. |

---

## 11. ISO/IEC 27001:2022 Annex A — the controls that actually map

Deliberately not all 93. A control with no mechanism in this repository is not listed.

| Control | State | Evidence |
|---|---|---|
| **A.5.15** Access control | **enforced** | Role guards per route + RLS; `backend/tests/api/test_roles_rbac.py`. |
| **A.5.18** Access rights | **enforced** | Named seats as revoke-only grants; admin CRUD with a last-platform-admin lockout guard (`aegis/src/aegis/governance/enforcement.py`); `backend/tests/api/test_roles_rbac.py::test_cannot_demote_last_platform_admin`. |
| **A.5.34** Privacy and PII protection | **enforced** | Presidio/regex redaction on input, output and tool results; hard erasure; retention horizons. |
| **A.8.2** Privileged access rights | **enforced** | The serving role is provisioned `LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE` and owns no objects (`scripts/sql/aegis-app-role.sql`); the console role holds `SELECT` and nothing else (`scripts/sql/aegis-readonly-role.sql`). |
| **A.8.3** Information access restriction | **enforced** | `FORCE ROW LEVEL SECURITY` (removes the owner's exemption) + per-request tenant binding + a boot-time audit that raises `RlsBypassError` when the serving role could bypass; `aegis/tests/governance/test_rls_enforcement.py`. |
| **A.8.5** Secure authentication | **partial** | Argon2id + signed JWT. No MFA, no lockout, no revocation. |
| **A.8.8** Technical vulnerability management | **partial** | `POST /v1/stack/patch-check` against the live registry. No advisory feed, so this is patch *freshness*, not vulnerability management. |
| **A.8.12** Data leakage prevention | **enforced** | PII redaction before the model *and* before the user; output content filter; CSV/DDE neutralisation on export. |
| **A.8.15** Logging | **enforced** | `audit_log` + OpenTelemetry traces + run events; `aegis/tests/governance/test_audit.py`. |
| **A.8.16** Monitoring activities | **partial** | Latency window, ops diagnose, notification inbox, SLA sweeps. No security alerting rules and no SIEM. |
| **A.8.24** Use of cryptography | **partial** | Password hashing and token signing only; nothing encrypted at rest, no key management. Column-level encryption was assessed and refused with the reasoning recorded (`backend/src/app/platform/at_rest.py`); the control owed is transparent volume encryption plus encrypted backups, which the deployment declares and Aegis reports without claiming to have verified. |
| **A.8.26** Application security requirements | **partial** | Typed contracts end to end, `extra="forbid"` request models, a generated OpenAPI the frontend types are derived from, and three test suites run against all of it. No documented security requirements per feature. |
| **A.8.28** Secure coding | **partial** | ADRs, threat model, fail-closed defaults, a conformance suite where every check descends from a defect this repository actually shipped (`aegis/src/aegis/conformance/`), and a CI pipeline that runs ruff and all three test suites on every push and pull request (`.github/workflows/ci.yml`). No SAST, no dependency scanning. |
| **A.8.31** Separation of development, test and production | **not_implemented** | No environment separation is expressed in this repository. |
| **A.8.32** Change management | **partial** | ADRs, the tiered LLM-Ops release path with rollback, the four-state prompt lifecycle, and a merge gate: `.github/workflows/ci.yml` runs ruff, the backend and aegis suites against a real PostgreSQL service, `tsc --noEmit`, the web suite and the OpenAPI snapshot check on every pull request. **No change-approval record** — nothing requires a review before merge, and no branch protection is configured in this repository. |
| **A.5.7** Threat intelligence | **not_implemented** | No feed. The MLCommons hazard taxonomy and the OWASP lists are static references, not intelligence. |
| **A.5.23** Cloud services security | **not_applicable** | Aegis is deployed natively on a single host, no Docker and no cloud control plane (`INSTALL.md`). |

---

## 12. EU AI Act

**Classification note.** Aegis is a domain-agnostic platform, not a deployed AI
system. Its risk class under the Act is decided by what a deployment does with
it. The rows below are the articles a limited- or high-risk deployment would have
to satisfy, and what Aegis brings to that deployment.

| Article | Requirement | State | Evidence / gap |
|---|---|---|---|
| **Art. 9** | Risk management system | **partial** | A real risk assessment exists (`GET /v1/risk-map`, `docs/security/threat-model.md`) and is exercised adversarially. It is not a documented, iterative lifecycle RMS with review triggers. |
| **Art. 10** | Data and data governance | **partial** | Write-time validation, retention horizons, erasure, PII redaction, tenant isolation. No training-data governance record, no bias examination. |
| **Art. 11 + Annex IV** | Technical documentation | **partial** | `GET /v1/ml/model-card`, `docs/architecture/`, nine ADRs, `aegis.pipelines` declarations, the generated OpenAPI contract, 29 teaching documents. Substantial, but not assembled in Annex IV form. |
| **Art. 12** | Record-keeping (automatic logging) | **enforced** | Every autonomous or approved action writes an `audit_log` row carrying actor, model, `trace_id`, payload, approver and tenant; runs are OpenTelemetry traces; the usage ledger records every call. Reads are tenant-scoped in SQL. |
| **Art. 13** | Transparency and information to deployers | **enforced** | The product's core discipline: `Receipt` under every figure, `Absence` where a figure cannot be sourced, `control_ref` on every risk row, `refs[]` of importable symbols behind every posture status, and this document's own refusal to claim certification. |
| **Art. 14** | Human oversight | **enforced** | A consequential action cannot execute alone: the graph interrupts at `gate_min_risk` and waits for a named person; the approvals inbox shows *what approving would run* and why the gate fired; the SLA sweeper auto-**rejects** HIGH risk on timeout; the conformal abstain band declines rather than acting on a degenerate prediction. |
| **Art. 15** | Accuracy, robustness, cybersecurity | **partial** | Accuracy is measured, not claimed (empirical conformal coverage on a held-out split; per-metric eval thresholds). Robustness is measured by the red-team battery with a false-positive control. Cybersecurity is sections 6–7 above. Missing: adversarial robustness of the ML spine, and any production-time accuracy monitoring. |
| **Art. 17** | Quality management system | **not_implemented** | No QMS. |
| **Art. 50** | Transparency for certain AI systems (AI-interaction and synthetic-content disclosure) | **not_implemented** | Aegis does not mark generated content as AI-generated or watermark outputs. |
| **Art. 72** | Post-market monitoring | **partial** | Latency, analytics, notifications and audit give a deployment the telemetry; there is no post-market monitoring *plan*. |

---

## 13. SOC 2 Trust Services Criteria

| Criterion | State | Evidence / gap |
|---|---|---|
| **CC5.2 / CC4.1** Control activities and monitoring | **partial** | Unusual strength: `GET /v1/security/posture` re-derives control status **from live wiring on every request** and is guarded by `test_no_threat_claimed_enforced_when_its_control_is_off`, so a disabled control cannot keep reporting green. There is no independent control-effectiveness review. |
| **CC6.1** Logical access | **enforced** | RBAC + RLS + least-privilege database roles (section 11). |
| **CC6.2 / CC6.3** Registration, authorisation, role changes | **partial** | Admin CRUD writes an audit row for every grant change; the last-platform-admin lockout is guarded. No periodic access review. |
| **CC6.6** Boundary protection | **partial** | Single gateway chokepoint; MCP peers explicitly declared and risk-tiered. The unvalidated peer URL (section 7, A01) is the hole. |
| **CC6.7** Restricting data transmission and removal | **partial** | PII redaction on every path; hard erasure; CSV export sanitisation; peer credentials never persisted or returned. No DLP on exports beyond formula neutralisation. |
| **CC7.1** Vulnerability identification | **partial** | Registry patch-check. No scanning. |
| **CC7.2** Anomaly monitoring | **partial** | Notification inbox, SLA sweeps, budget refusals as clean terminal events. No security-specific alerting. |
| **CC7.3 / CC7.4** Incident evaluation and response | **not_implemented** | No incident-response procedure. |
| **CC8.1** Change management | **partial** | ADRs, tiered release with rollback, a large test suite, and a CI merge gate over all of it (`.github/workflows/ci.yml`). **No change-approval record** — nothing requires a reviewer, and branch protection is not configured here, so the gate reports rather than blocks until somebody turns that on. |
| **A1.2** Availability — backup and recovery | **not_implemented** | No backup or restore procedure in this repository. |
| **C1.1 / C1.2** Confidentiality | **partial** | Tenant isolation, redaction, erasure. No data classification and no encryption at rest. |

The Privacy (P) series is not repeated here as a control row; sections 2 and 14 are the
answer to it, and duplicating it would inflate the coverage count for the same
evidence twice.

---

## 14. GDPR (Regulation (EU) 2016/679)

India's equivalents live in section 2 and are **not repeated here**. A row that
appeared in both tables would count the same evidence twice and inflate the coverage
numbers on a page whose whole claim is that its numbers are derived.

| Right / obligation | State | Evidence / gap |
|---|---|---|
| **GDPR Art. 5(1)(c)** data minimisation | **partial** | PII is redacted *before* the model and before the classifier call; only minimum-necessary context is injected per request; `memory.retention_days` (default 90) and `memory.closed_fact_retention_days` (default 30) bound what is kept, with `0` documented as what a legal hold looks like. No formal minimisation review per field. |
| **GDPR Art. 5(1)(b)** purpose limitation | **not_implemented** | No purpose register; nothing binds stored data to a declared purpose. |
| **GDPR Art. 15** access | **partial** | `GET /v1/memory/facts`, `/memory/profile`, `/memory/sessions` and four CSV report exports let a subject's data be read out. There is no single subject-access-request export endpoint. |
| **GDPR Art. 16** rectification | **enforced** | `PATCH /v1/memory/facts/{id}` corrects a stored belief, and the belief timeline retains the supersession rather than silently rewriting history (`aegis/src/aegis/memory/crud.py`). |
| **GDPR Art. 17** erasure | **enforced** | `POST /v1/memory/forget` and `DELETE /v1/memory/facts/{id}` perform a real **hard delete**, subject- and tenant-scoped and audited (`aegis/src/aegis/memory/crud.py::forget_fact(hard=True)`); `aegis/src/aegis/memory/retention.py::apply_retention` is the one place that does an unconditional scheduled hard delete of raw turns. |
| **GDPR Art. 30** records of processing | **not_implemented** | The audit log records *actions*, which is not a ROPA. |
| **GDPR Art. 32** security of processing | **partial** | Sections 7 and 11 are the answer; the gaps there are the gaps here — notably no encryption at rest. |
| **GDPR Art. 33/34** breach notification | **not_implemented** | No procedure. |
| **GDPR Art. 35** DPIA | **not_implemented** | The risk map is a system-risk assessment, not a DPIA. |

---

## 15. The honest summary

**What Aegis can say to a buyer today, without lying:**

- Every OWASP LLM Top 10 item is *addressed*; five are enforced with a test that
  fails if the control is switched off, five are partial with the missing layer
  named.
- The security posture is **derived from live wiring on every request**, not
  declared in a file — a control that is turned off cannot keep reporting green,
  and there is a test that enforces exactly that.
- The red-team battery reports a false-positive rate beside its block rate and
  keeps five probes it is known to miss offline, rather than curating them out.
- Human oversight, record-keeping and transparency (EU AI Act Arts. 12–14) are
  the strongest rows on the board and would survive a reviewer opening them.
- NIST AI RMF is **enforced in all four functions** — the only framework here that
  is. Measure and Manage are code; Govern and Map are the written artefacts in
  `docs/governance/`, which is the form of compliance NIST asks those two functions
  for. Every repository path those documents cite is resolved on every test run, so a
  policy clause cannot outlive the mechanism it names.
- **For India specifically:** the two DPDP rights a Data Principal is most likely
  to exercise — correction (s.12(1)–(2)) and erasure (s.12(3)) — are enforced with
  real endpoints, a real hard delete, an audit row and tests. Correction supersedes
  rather than overwrites, so the correction is itself auditable.
- **Data residency is derived, not asserted.** Every store — Postgres, Qdrant,
  Neo4j, Redis — resolves to the deployment host, checked on every read, and a test
  proves the verdict flips when one is moved offshore. That is a real answer to
  DPDP s.16 and to CERT-In Dir. (iv)'s "within Indian jurisdiction".

**What Aegis must not say:**

- Nothing here is certified, audited or attested by anyone.
- CI now exists (`.github/workflows/ci.yml`) and gates ruff plus all three test
  suites, the type check and the OpenAPI snapshot. It gates **tests, not supply
  chain**: no dependency scanning, no SAST, no provenance, and no branch protection
  requiring it to pass before a merge.
- The audit log is append-only **by database privilege** on the serving role, not
  against the owner connection: `POSTGRES_ADMIN_DSN` can still rewrite it.
- **Nothing is encrypted at rest**, and column-level encryption was assessed and
  deliberately refused rather than overlooked — see A04 in section 8 and
  `backend/src/app/platform/at_rest.py`. The control owed is a deployment one.
- Authentication has no MFA, no lockout and no revocation.
- There is now a written incident-response plan (`docs/governance/incident-response.md`),
  and it says in its own text what it does not discharge: **no paging and no on-call**
  (detection is a human opening a screen), **no backup and no restore**, and **CERT-In's
  six-hour clock is not automated**. Still no DPIA, no ROPA, no QMS.
- **Aegis is not DPDP compliant, and a deployment using it is not compliant by
  installing it.** There is no consent artefact, no notice, no breach-notification
  path, no grievance channel and no children's-data handling. Five of the twelve
  DPDP rows are *not implemented*, and they bind on 13 May 2027.
- **No CERT-In posture.** No NTP source is asserted, no incident is classified, no
  point of contact is named. The 180-day log window is met by the absence of a
  deleter rather than by a control, and the serving role can still `DELETE` an
  audit row.
- **Nothing forces the model gateway to be in India.** Prompts, completions and
  chunk text leave through it, and in this repository's own run file it points at a
  US Azure region. That is lawful under s.16's negative list today; it is not
  data localisation, and it must never be described as such.
- **No BFSI compliance.** The RBI and SEBI rows are *not applicable* on purpose.
  Aegis is not a regulated entity, has not been audited as one, and supplies none
  of the board-level governance, IS audit, BCP/DR or SOC obligations either
  framework turns on.

---

*Framework versions referenced: OWASP Top 10 for LLM Applications v2.0 (2025);
OWASP Top 10:2025 (web); OWASP Top 10 for Agentic Applications (ASI, 2026);
MITRE ATLAS; NIST AI RMF 1.0; ISO/IEC 42001:2023 Annex A; ISO/IEC 27001:2022
Annex A; Regulation (EU) 2024/1689; AICPA Trust Services Criteria (2017, rev.
2022); Regulation (EU) 2016/679. India: Digital Personal Data Protection Act 2023
and DPDP Rules 2025 (notified 14 Nov 2025); CERT-In Directions No.
20(3)/2022-CERT-In (28 Apr 2022); MeitY India AI Governance Guidelines (5 Nov
2025); RBI Master Direction on Information Technology Governance, Risk, Controls
and Assurance Practices (7 Nov 2023); SEBI Cyber Security and Cyber Resilience
Framework (2024); IS 17428 (Part 1):2020, Bureau of Indian Standards.*
