# SOTA 06 — OWASP Agentic (ASI01–10) and India's February-2026 AI governance layer

> **STATUS: PLAN. Nothing here has been implemented.** This document is an implementation
> plan for two additions to `backend/src/app/platform/compliance.py` and three corrections
> to documents that have gone stale. It writes no code.

> **Source discipline, inherited from `docs/dev_new_docs_v2/phase-11-langflow.md`.** Every
> claim carries a mark: **[VERIFIED]** — read in this repository at the anchor given, on
> 2026-08-27; **[DOC]** — read on the public web at the URL given; **[UNVERIFIED]** — stated
> because it is needed and *not* checked, with the check named. Where this plan makes an
> engineering judgement rather than reporting a fact, the sentence says so. Nothing here is
> marked [MEASURED] because nothing here was run.

---

## What this is, in one paragraph

Aegis serves a control-by-control compliance map at `GET /v1/compliance`: **114 controls
across 12 frameworks**, four honest states, every claim resolving to a real file, route or
pytest node id **[VERIFIED** `backend/src/app/platform/compliance.py:2814-2925`, README
headline `docs/compliance/README.md:24`**]**. It carries the OWASP LLM Top 10 and the OWASP
web Top 10 but **not the agentic list** — even though `docs/security/threat-model.md:35-44`
already maps all ten ASI categories, and `backend/src/app/platform/risk_map.py` is built on
the agentic themes. It carries DPDP, CERT-In and a MeitY/RBI/SEBI/BIS row but not the
instrument that actually became binding in February 2026: the **IT Amendment Rules 2026**,
in force 20 Feb 2026, which put synthetic-content labelling and provenance obligations into
Indian law. This plan adds **OWASP Top 10 for Agentic Applications** as a framework of ten
controls and **India's Feb-2026 AI layer** as a framework of six, maps each to what Aegis
*actually* enforces, and corrects an obsolete hedge, a wording drift and two stale prose
counts. The whole point is that it makes the page **worse-looking and more true**: the
honest distribution is one enforced row in twenty.

---

## 1. What is actually true today — every premise, checked

| Premise given to this plan | Verdict | Anchor |
|---|---|---|
| `compliance.py` maps 114 controls across 12 frameworks | **True** | `_FRAMEWORKS` at `compliance.py:2814-2925` holds exactly 12 tuples; 114 `ControlEntry(` constructions (115 grep hits minus the class definition at `:111`); README headline says `**Total: 114 controls — 37 enforced · 53 partial · 19 not implemented · 5 not applicable.**` at `docs/compliance/README.md:24` **[VERIFIED]** |
| It carries OWASP LLM (2025) and OWASP web but not the agentic list | **True** | `_OWASP_LLM` at `:214`, `_OWASP_WEB` at `:656`; no `_OWASP_AGENTIC` anywhere **[VERIFIED]** |
| `threat-model.md:35-44` maps ASI01–ASI10 | **True** | Ten rows, `ASI01`…`ASI10`, at exactly those lines **[VERIFIED]** |
| `owasp-agentic.md:20-25` carries an obsolete hedge | **True, at `:18-24`** | The "Naming note (honest)" block runs `:18` to `:24`, not `:20-25`. Its load-bearing sentence is *"numbering/wording **should be confirmed against the current OWASP publication** before quoting an `ASI0x` identifier"* **[VERIFIED]** |
| The agentic list was published 9 Dec 2025 | **True** | Published 9 December 2025 as the 2026 edition; ten ASI-prefixed categories **[DOC]** |
| Aegis serves India first | **True, and test-guarded** | `test_india_is_served_first` at `backend/tests/api/test_compliance.py` asserts India's frameworks occupy positions `0..n-1` contiguously **[VERIFIED]** |
| Aegis carries DPDP / CERT-In / MeitY-RBI-SEBI-BIS | **True** | framework ids `dpdp`, `cert-in`, `india-sectoral` at `compliance.py:2816-2853` **[VERIFIED]** |
| Aegis does *not* carry the India AI Governance Guidelines | **FALSE — correct this before planning anything** | `india-sectoral`'s version string is `"IAGG Nov 2025 · ITGRCA 2023 · CSCRF 2024 · IS 17428:2020"` and it already holds **four IAGG control rows** — `IAGG · Accountability`, `· Transparency`, `· Oversight`, `· Safety`, all `partial`, at `compliance.py:2626-2733`. The README's version footer names *"MeitY India AI Governance Guidelines (5 Nov 2025)"* at `docs/compliance/README.md:481` **[VERIFIED]** |
| Any Aegis text states the old EU AI Act 2 Aug 2026 high-risk date | **FALSE — no such text exists** | `grep -rn "2 August 2026\|2 Aug 2026\|August 2026"` across `docs/`, `backend/src`, `web/src`, `aegis/src` returns only an unrelated ADR line (`docs/adr/0002-nemo-guardrails.md:70`, "against current docs, Aug 2026"). `compliance.py`'s `_EU_AI_ACT` block (`:1821-1979`) and README §12 (`:352-372`) state **no applicability date at all** **[VERIFIED]** |

**The correction that changes the shape of this plan.** The India AI Governance Guidelines
are not a February-2026 instrument. MeitY published them on **5 November 2025**; they were
**foregrounded at the India AI Impact Summit, 16–20 February 2026**, with a PIB re-release
of the same document **[DOC]**. Aegis already serves four of their principles. Adding a
second "India AI Governance Guidelines" framework would therefore **double-count evidence**
— the precise defect `test_indias_rights_are_not_double_counted_under_gdpr` exists to
prevent for DPDP/GDPR **[VERIFIED** `backend/tests/api/test_compliance.py`**]**.

What genuinely arrived in February 2026 and is **absent** from Aegis is the **IT
(Intermediary Guidelines and Digital Media Ethics Code) Amendment Rules 2026**, notified by
MeitY and **effective 20 February 2026**, which define *synthetically generated information*
(SGI) and impose labelling, provenance-metadata and label-preservation duties, and compress
takedown clocks to as little as 2–3 hours **[DOC]**. So the new framework is **the Feb-2026
layer**, not a duplicate IAGG.

---

## 2. External research, verified

### 2.1 OWASP Top 10 for Agentic Applications

Published **9 December 2025** as the **2026 edition**, by the OWASP GenAI Security Project;
the first OWASP flagship list built for autonomous agents rather than for the models they
run on. Built from 2025 incidents including EchoLeak (CVE-2025-32711), the Amazon Q
compromise and Replit's agent deleting a production database **[DOC]**.

The official titles, cross-checked against two independent secondary sources **[DOC]**
(genai.owasp.org itself returns HTTP 403 to a programmatic fetch — **this plan did not read
the primary PDF, and the implementer should**, marked below):

| Id | Official title | Aegis's current wording (`threat-model.md`) | Drift |
|---|---|---|---|
| ASI01 | Agent Goal Hijack | Agent Goal Hijack | — |
| ASI02 | Tool Misuse and Exploitation | Tool Misuse & Exploitation | cosmetic |
| ASI03 | Identity and Privilege Abuse | Identity & Privilege Abuse | cosmetic |
| ASI04 | Agentic Supply Chain **Vulnerabilities** | Agentic Supply Chain | word dropped |
| ASI05 | Unexpected Code Execution **(RCE)** | Unexpected Code Execution | qualifier dropped |
| ASI06 | **Memory &** Context Poisoning | Context / Retrieval Manipulation | **wrong — see §5.2** |
| ASI07 | Insecure Inter-Agent Communication | Insecure Inter-Agent Communication | — |
| ASI08 | Cascading Failures | Cascading Failures | — |
| ASI09 | Human-Agent Trust Exploitation | Human-Agent Trust Exploitation | — |
| ASI10 | Rogue Agents | Rogue Agents | — |

**ASI06 is the one that matters.** The official category is about *memory* as well as
retrieved context. Aegis's row names spotlighting only and says nothing about the memory
write path — which is where the actual poisoning risk in this codebase lives (§5.2).

### 2.2 India — the February 2026 layer

- **India AI Governance Guidelines**, MeitY, **5 Nov 2025**; techno-legal and
  principle-driven; seven *sutras* (trust; people-first; innovation over restraint;
  fairness and equity; accountability; understandability by design; safety, resilience and
  sustainability), adapted from the RBI's FREE-AI framework; six pillars across three
  domains (Enablement: infrastructure, capacity building · Regulation: policy &
  regulation, risk mitigation · Oversight: accountability, institutions). **No standalone
  AI Act** — a sectoral approach, with existing regulators (RBI, SEBI, IRDAI) writing their
  own rules. Proposes an **AI Governance Group (AIGG)**, a **Technology & Policy Expert
  Committee (TPEC)** and an **AI Safety Institute**; voluntary compliance and graded
  liability; a short/medium/long-term action plan whose short-term items include an
  **India-specific risk-classification framework** and whose medium-term items include an
  **AI incident database**. Re-released at the India AI Impact Summit, 16–20 Feb 2026
  **[DOC]**.
- **IT Amendment Rules 2026**, MeitY, **effective 20 Feb 2026**. Defines *synthetically
  generated information* as audio/visual/audio-visual content created or altered by
  computational means to appear authentic. Requires: a **user declaration** at upload;
  **prominent and visible labelling** of published SGI; **embedded provenance metadata
  where technically feasible**; **no removal or obscuring of such labels**; and compressed
  takedown/grievance clocks (as short as 2–3 hours for non-consensual deepfake nudity or
  impersonation, down from 24–36 hours under the 2021 Rules) **[DOC]**.
- **DPDP glide path.** Rules notified 13 Nov 2025. Phase 1 (Data Protection Board)
  immediate; Phase 2 (consent managers) 13 Nov 2026; **Phase 3 — every substantive
  compliance obligation — 13 May 2027** **[DOC]**. Aegis's README already states 13 May
  2027 in two places **[VERIFIED** `docs/compliance/README.md:24-25` area and the "must not
  say" list**]** — **no correction owed**.

### 2.3 EU AI Act — the Digital Omnibus deferral

A provisional political agreement on **6 May 2026**, confirmed by Member State
representatives in Council on **13 May 2026**, defers the high-risk obligations:
**stand-alone Annex III systems from 2 August 2026 to 2 December 2027**; AI embedded in
regulated products under Annex I to **2 August 2028**. **Article 50 transparency and
Article 4 AI-literacy duties are unchanged** **[DOC]**.

**Aegis states no date, so nothing is wrong.** But the absence is now itself a defect: the
Art. 50 row (`compliance.py`, `_EU_AI_ACT`, `not_implemented`) and the new India SGI rows
describe **the same absence under two instruments that are both live today**, while the
articles Aegis is weakest on (Art. 9, 10, 15, 17) are the deferred ones. A reviewer reading
the page in 2026 cannot tell which obligations bite now. That is what §6 fixes.

---

## 3. The three honesty tests this plan must satisfy

Read these before writing one `ControlEntry`. They are not style guidance; they fail the
build.

**3.1 `backend/tests/api/test_compliance.py` [VERIFIED]**

- `test_every_evidence_reference_resolves` — every `file`/`doc` ref must exist on disk;
  every `route` ref must be in the **served route table** (the *unprefixed* path, e.g.
  `GET /compliance`, because `app.include_router(versioned, prefix=API_PREFIX)` at
  `backend/src/app/main.py:771` applies `/v1` at mount time); every `test` ref must name a
  function that a regex actually finds in that file.
- `test_enforced_needs_a_file_and_a_test` — `enforced` requires ≥1 `FILE` **and** ≥1 `TEST`
  evidence item. **A control may not be mapped as enforced without naming the code that
  enforces it and the test that fails when it stops working.**
- `test_anything_short_of_enforced_names_what_is_missing` — `gap` ≥ 20 chars for every
  non-enforced state, and **empty for `enforced`**.
- `test_every_control_says_something_concrete` — `summary` ≥ 20 chars, non-empty id/title.
- `test_control_ids_are_unique_within_a_framework`, `test_framework_ids_are_unique`.
- `test_coverage_counts_are_derived_not_authored` — counts must equal states present.
- `test_all_four_states_are_actually_used` — globally satisfied already.
- `test_india_is_served_first` — **the new India framework must be inserted inside the
  contiguous India block, i.e. at index 3 of `_FRAMEWORKS`, before `owasp-llm`.**
- `test_the_written_authority_exists` — for every framework, `name.split(" —")[0]` must
  appear verbatim in `docs/compliance/README.md`. **Choose names accordingly (§4.1).**

**3.2 `backend/tests/api/test_not_applicable_is_justified.py` [VERIFIED]**

Every `not_applicable` control's reason (`gap`, falling back to `summary`) must be **≥ 80
characters** *after* stripping the phrases `not applicable`, `n/a`, `does not apply`, `no
applicable`, `out of scope`. The reason must say **what about this deployment puts the
control out of reach** — because the public landing band claims completeness on an
*applicable-controls* denominator, and a cheap `not_applicable` is the cheapest way to
manufacture a headline. This plan proposes exactly **one** new `not_applicable` row (§4.2,
`ITAR · Due diligence`) and it is written to clear that bar with the "what a deployment
would still owe" shape the RBI/SEBI rows already use.

**3.3 `backend/tests/api/test_compliance_readme_totals.py` [VERIFIED]**

- `test_the_readme_headline_totals_are_the_tables_own_totals` — the README line
  `**Total: N controls — A enforced · B partial · C not implemented · D not applicable.**`
  is re-derived from the live table. **This test will fail the moment the first new
  `ControlEntry` lands and stays failing until the README headline is pasted from the
  failure message.** That is the mechanism working, not a problem.
- `test_the_readme_names_exactly_the_frameworks_enforced_in_full` — over- *and*
  under-claiming both fail. Neither new framework reaches completeness under this plan, so
  neither may be described as "enforced in full".

**3.4 The guards that pin specific rows [VERIFIED]** — `test_no_bfsi_compliance_is_claimed`
(RBI/SEBI may only ever be `not_applicable`, and must say what a deployment would still
owe), `test_the_dpdp_paperwork_obligations_stay_unimplemented` (s.5/s.6, s.8(6), s.10, s.13
stay `not_implemented`), `test_the_supply_chain_row_names_a_verdict_and_a_gate_not_just_an_inventory`.
§7 proposes two new guards in the same spirit.

---

## 4. Task A — add OWASP Top 10 for Agentic Applications

### 4.0 Before writing anything

**A0.** Read the primary publication. genai.owasp.org returned **HTTP 403** to this plan's
fetch, so every ASI title above is **[DOC]** from secondary sources and none is
**[VERIFIED]** against the OWASP PDF. Download the 2026 edition, confirm the ten titles
verbatim, and **use OWASP's exact wording in `ControlEntry.title`** — the framework's own
words, as `ControlEntry.title`'s field description requires (`compliance.py:113`).

### 4.1 The framework tuple

Append to `_FRAMEWORKS` **after `owasp-web`** (an International framework; ordering within
the International block is not test-guarded, and sitting beside the other two OWASP lists
is what a reviewer expects):

```
(
    "owasp-agentic",
    "OWASP Top 10 for Agentic Applications",
    "2026 edition, published 9 Dec 2025",
    JURISDICTION_INTERNATIONAL,
    "<scope sentence — see below>",
    _OWASP_AGENTIC,
),
```

**Name has no em dash on purpose.** `test_the_written_authority_exists` takes
`name.split(" —")[0]`; with no em dash the *whole* string must appear in the README, which
is a stronger assertion than matching a two-word prefix. `_mark()` in
`test_compliance_readme_totals.py` splits on `—` and likewise yields the whole name.

**Scope sentence** (goes in the framework tuple and, verbatim, in the new README section):

> *The agentic layer — what goes wrong when software that **acts** is attacked, rather than
> software that answers. Distinct from the LLM Top 10 above and not a superset of it: three
> of these categories (supply chain, code execution, memory poisoning) have no LLM-list
> equivalent, and two LLM rows Aegis enforces (LLM02, LLM05) have no ASI slot. One row
> enforced of ten.*

**`_MARKS` entry required.** `backend/src/app/api/routes_standards.py:_MARKS` maps framework
id → short public wordmark; an unknown id falls back to the full `name`, deliberately
**[VERIFIED** `routes_standards.py`, the `_MARKS` docstring**]**. Add
`"owasp-agentic": "OWASP Agentic Top 10"`.

### 4.2 The ten controls — state, and the code that decides it

Every state below is an **engineering judgement made by this plan**, defensible from the
anchors given. The implementer owns re-checking each one; a state that cannot be defended
by opening the file must be lowered, not argued.

---

**ASI01 — Agent Goal Hijack → `partial`**

*Summary.* Instruction injection is screened at all three rails and retrieved text is
spotlighted as data, not instructions; a hijacked plan cannot act on anything at or above
`gate_min_risk` without a named human.

*Gap.* **Blast radius is bounded; the hijack is not detected.** Nothing compares the plan
the agent produced against the objective it was given — there is no goal-integrity check
anywhere in `agent/graph.py`. Reported attack-success rates against frontier models remain
in the ~50–84% band even with best-effort defences, which
`docs/security/owasp-agentic.md:11-16` states in its own posture note **[VERIFIED]**;
injection is never marked solved on this platform.

*Evidence.* `_f` `aegis/src/aegis/guardrails/pipeline.py` · `_f`
`aegis/src/aegis/retrieval/spotlight.py` · `_f` `aegis/src/aegis/agent/graph.py` · `_t`
`aegis/tests/retrieval/test_spotlight.py::test_instruction_tells_model_marked_text_is_data`
**[VERIFIED exists]** · `_t`
`aegis/tests/agent/test_tool_result_rail.py::test_a_poisoned_tool_result_is_screened_before_it_enters_a_lanes_context`
**[VERIFIED exists]**.

---

**ASI02 — Tool Misuse and Exploitation → `enforced`**

*Summary.* A tool call must clear three independent gates before any side effect: it must
be in the persona's allowlist, in the sub-agent spec's own allowlist (the intersection is
what a lane may call), and strictly below `gate_min_risk` or it is *proposed* rather than
run. An unknown name raises before the handler. A hostile MCP peer's ungranted tool
defaults to HIGH and is never reached.

*Gap.* **Empty** — `enforced` rows may carry no gap sentence
(`test_anything_short_of_enforced_names_what_is_missing`).

*Evidence.* `_f` `aegis/src/aegis/adapter/tools.py` (or the live equivalent — **[UNVERIFIED]**,
confirm the module path holding `ALLOWLIST` / `run_tool` before typing it; the compliance
suite will fail the ref if it is wrong) · `_f` `aegis/src/aegis/agent/subagent.py` · `_t`
`aegis/tests/agent/test_subagent_gate.py::test_a_tool_outside_the_intersection_is_refused_not_run`
**[VERIFIED exists]** · `_t`
`aegis/tests/agent/test_subagent_gate.py::test_high_risk_call_is_proposed_and_never_executed`
**[VERIFIED exists]** · `_t`
`backend/tests/mcp/test_hostile_peer.py::test_an_ungranted_hostile_tool_defaults_to_high_and_is_never_reached`
**[VERIFIED exists]**.

*Why this one and only this one is enforced.* It is the only ASI category where the control
runs on **every** relevant request, the failure mode is refusal rather than degradation,
and a test fails if the mechanism is removed. That is the bar, and nine other rows do not
clear it.

---

**ASI03 — Identity and Privilege Abuse → `partial`**

*Summary.* Per-request `GovernanceContext` (tenant/user/role) threaded via contextvars;
Postgres RLS engages per connection through the `app.tenant_id` GUC; least-privilege
serving role; budgets and rate caps at the gateway chokepoint.

*Gap.* **The confused-deputy half is absent.** The agent runs with the caller's full role
for the whole run — there is no step-down, no per-tool credential exchange and no scoped
token issued to a sub-agent, so a lane that is talked into calling an allowed tool calls it
with the human's authority rather than a narrowed one. Authentication also has no MFA, no
lockout and no revocation, which the README already states **[VERIFIED**
`docs/compliance/README.md`, the "must not say" list**]**.

*Evidence.* `_f` `aegis/src/aegis/governance/rls.py` · `_f`
`backend/src/app/core/governance.py` **[UNVERIFIED path — confirm]** · `_f`
`scripts/sql/aegis-app-role.sql` **[VERIFIED — cited already by `IS 17428-1`]** · `_t`
`aegis/tests/agent/test_retrieval_tenant_scope.py::<name>` **[UNVERIFIED — grep a real
node id]**.

---

**ASI04 — Agentic Supply Chain Vulnerabilities → `partial`**

*Summary.* Hash-pinned dependencies, an OSV advisory feed with a verdict, a CycloneDX/SPDX
export and a CI gate that fails the build on an undecided advisory — the same machinery
that took `LLM03` to `enforced`. On the *agentic* surface specifically: MCP peer
registration refuses loopback, link-local, private and reserved addresses and
non-allowlisted schemes at the registry chokepoint, on **both** doors.

*Gap.* **Two named holes.** (1) **DNS is not resolved by the SSRF guard** — a hostname that
resolves inward still passes; this is stated, not hidden, at
`docs/architecture/system-architecture.md:373-375` **[VERIFIED]** and has its own test
asserting the limitation is deliberate. (2) **No provenance or build attestation** for
dependencies, and a peer's *tool descriptions* are unsigned third-party text that reaches
the planner.

*Evidence.* `_f` `backend/uv.lock` · `_f` `backend/src/app/platform/advisories.py` · `_f`
`.github/workflows/ci.yml` · `_f` `backend/src/app/mcp/registry.py` **[UNVERIFIED path]** ·
`_t` `backend/tests/mcp/test_peer_url.py::test_a_peer_pointed_inside_the_network_is_refused`
**[VERIFIED exists]** · `_t`
`backend/tests/mcp/test_peer_url.py::test_a_hostname_resolving_inside_is_not_caught_and_this_is_deliberate`
**[VERIFIED exists — cite it *as the gap's own evidence*, which is the honest move]**.

---

**ASI05 — Unexpected Code Execution (RCE) → `partial`**

**Read this before choosing `not_applicable`.** The tempting claim is that ASI05 cannot
apply because Aegis ships no interpreter, shell or `eval` tool — the same structural
argument that legitimately makes `AML.T0018 Backdoor ML Model` inapplicable. **It is not
the same argument.** The ATLAS row is structural because the only fitted model is trained
in-process from the host's own frame, so there is no artefact that *could* have been
backdoored. Here, the absence is a property of **the shipped adapter's registry**, not an
invariant the core enforces: `TOOL_REGISTRY` is adapter-owned, and nothing in
`aegis.agent` refuses a registered tool that shells out. `not_applicable` would be
asserting an invariant that does not exist.

*Summary.* The tool surface is a closed, typed registry of pydantic-validated callables;
an unknown name raises before any handler runs; there is no interpreter, shell or `eval`
tool in the shipped registry, and no path from model output to a subprocess.

*Gap.* **The closure is the adapter's, not the core's.** A host adapter may register a tool
that executes code and nothing in the agent core refuses it — there is no capability
declaration on `ToolSpec` that a registry-time check could enforce, and no sandbox if one
were added. The claim is "we ship none", not "one cannot exist".

*Evidence.* `_f` `aegis/src/aegis/adapter/tools.py` **[UNVERIFIED path]** · `_t`
`aegis/tests/agent/test_subagent_gate.py::test_a_tool_outside_the_intersection_is_refused_not_run`
**[VERIFIED exists]**. **Add** a new test that asserts the shipped registry contains no
tool whose handler reaches `subprocess`/`eval`/`exec` — §7.3.

---

**ASI06 — Memory & Context Poisoning → `partial`**

*Summary.* Retrieved chunks and recalled episodic turns are spotlighted (delimited +
datamarked) as data; tool results are screened at a dedicated third rail before they enter
a lane's context or the console; ingestion validates content before it is written to the
store; and the consolidator **refuses** a mutating decision whose `target_id` cannot be
resolved to a fact the model was actually shown, auditing the refusal rather than
retargeting onto the nearest neighbour **[VERIFIED**
`aegis/src/aegis/memory/consolidate.py:18-22`**]**.

*Gap.* **The durable facts tier is not spotlighted, and nothing re-screens a fact once
written.** In `aegis/src/aegis/memory/working.py`, only the episodic tier carries the
spotlight instruction (`:178-180`) and only episodic candidates are wrapped (`:322`)
**[VERIFIED]** — but those durable facts were *distilled by a cheap model from those same
untrusted turns* and are injected at the **top** of the block, the highest-attention
position under the lost-in-the-middle layout (`_LAYOUT` at `:47`). A fact admitted once at
`tau_extract = 0.55` (`memory/config.py:100`) is never re-validated; `prune_forgotten`
archives on decay, not on suspicion.

*Evidence.* `_f` `aegis/src/aegis/retrieval/spotlight.py` · `_f`
`aegis/src/aegis/memory/working.py` · `_f` `aegis/src/aegis/retrieval/validation.py` · `_f`
`aegis/src/aegis/memory/consolidate.py` · `_t`
`aegis/tests/agent/test_tool_result_rail.py::test_a_blocked_tool_result_is_withheld_rather_than_merely_reported`
**[VERIFIED exists]** · `_t`
`aegis/tests/retrieval/test_spotlight.py::test_spotlight_wraps_in_fences_and_datamarks`
**[VERIFIED exists]**.

*This row is the highest-value finding in the document.* It is a real, specific, fixable
hole that no existing Aegis text names, and it was invisible while the category was
mis-titled "Context / Retrieval Manipulation".

---

**ASI07 — Insecure Inter-Agent Communication → `partial`**

*Summary.* There is no inter-agent *network*: the fan-out is in-process, a single
orchestrator dispatches lanes and no lane can address another. Every hop is a typed OTel
span carrying `a2a.from` / `a2a.to` / `a2a.reason` / `a2a.protocol` **[VERIFIED**
`aegis/src/aegis/agent/subagent.py:537-544`**]**. The one real external agent surface —
MCP peers — is registered at a chokepoint with SSRF refusal and its returns pass the
tool-result rail.

*Gap.* **The peer channel is authenticated only as much as its URL is.** A registered
peer's tool descriptions and returns are unsigned third-party text; there is no mutual
authentication, no message signing and no replay protection on an MCP hop, and the DNS hole
in ASI04 is this row's hole too.

*Evidence.* `_f` `aegis/src/aegis/agent/subagent.py` · `_f`
`aegis/src/aegis/observability/semconv.py` **[UNVERIFIED path]** · `_t`
`aegis/tests/agent/test_span_tree.py::test_agent_run_emits_nested_span_tree`
**[VERIFIED exists]** · `_t`
`backend/tests/mcp/test_hostile_peer.py::test_a_compromised_peers_return_value_is_withheld_from_the_agent`
**[VERIFIED exists]**.

---

**ASI08 — Cascading Failures → `partial`**

*Summary.* Every loop in the system has a hard cap that guarantees termination:
`max_plan_iterations = 2`, `subagent_max_steps = 4`, `subagent_timeout_s = 45.0`,
`team_wall_clock_s = 120.0` with the arithmetic proving the backstop sits *above* the
per-lane bounds written down and tested, `max_parallel_agents = 4` clamping *down* only
**[VERIFIED** `aegis/src/aegis/agent/deps.py:356-391`**]**. Budget and rate caps raise
`BudgetExceededError` at the single gateway chokepoint, and that exception is deliberately
the one thing a lane does not swallow.

*Gap.* **Every bound is on steps and wall clock; nothing bounds the context a run
accumulates.** A sub-agent's `messages` list grows across its four steps with no token
accounting and tool summaries are appended verbatim (`aegis/src/aegis/agent/subagent.py:636`
and `_tool_message` at `:703-705`) **[VERIFIED]**, so one large tool result is admitted
whole. LangGraph's checkpoint tables are also unpruned
(`docs/architecture/system-architecture.md:370-372`) **[VERIFIED]**. **The enforced
trajectory ceiling planned in `docs/dev_new_docs_v2/sota/07-long-horizon-ceiling.md` is what
closes this gap; when it lands, this row's gap sentence must be rewritten in the same
change.**

*Evidence.* `_f` `aegis/src/aegis/agent/deps.py` · `_f` `aegis/src/aegis/agent/subagent.py`
· `_t`
`aegis/tests/agent/test_subagent_gate.py::test_the_step_cap_terminates_a_tool_hungry_agent`
**[VERIFIED exists]** · `_t`
`aegis/tests/agent/test_fanout_bounds_and_budget.py::test_the_default_team_wall_clock_fits_its_own_per_lane_bounds`
**[VERIFIED exists]** · `_t`
`aegis/tests/agent/test_fanout_bounds_and_budget.py::test_the_team_wall_clock_cuts_a_lane_that_outlives_it`
**[VERIFIED exists]**.

---

**ASI09 — Human-Agent Trust Exploitation → `partial`**

*Summary.* The approval dialog shows **what approving would actually run**, and `act`
executes only `approved_call_ids` — the ids the dialog enumerated — so a fan-out cannot
execute proposals the gate never showed **[VERIFIED** `aegis/src/aegis/agent/state.py`,
`approved_call_ids` docstring**]**. Silence is a refusal: the SLA sweeper auto-**rejects**
HIGH risk on timeout. `REJECTED` is a terminal run status distinct from `BLOCKED`, so "a
human said no" and "a rail fired" are never the same number
**[VERIFIED** `aegis/src/aegis/core/types.py:45-66`**]**. Every figure on the product
carries a `Receipt`, and an `Absence` where a figure cannot be sourced.

*Gap.* **Nothing detects a fabricated justification, and nothing tells the user they are
reading machine-generated text.** The reason string an agent gives for wanting an action is
model-authored and unchecked; no output path marks content as AI-generated. That last
absence is the same one recorded at EU AI Act Art. 50, at `IAGG · Transparency`, and at the
new `ITAR 2026 · SGI labelling` row — **four rows, one hole**, and §7.2 proposes the test
that keeps them honest together.

*Evidence.* `_f` `aegis/src/aegis/agent/graph.py` · `_f`
`backend/src/app/data/approvals.py` **[VERIFIED — cited by `IAGG · Oversight`]** · `_f`
`web/src/components/primitives/Receipt.tsx` **[VERIFIED — cited by `IAGG · Accountability`]**
· `_r` `GET /approvals` **[VERIFIED — cited by `IAGG · Oversight`]** · `_t`
`backend/tests/data/test_approvals.py::test_sla_sweeper_expires_and_auto_rejects_high`
**[VERIFIED — cited by `IAGG · Oversight`]** · `_t`
`aegis/tests/agent/test_gate_authorises_what_runs.py::<name>` **[UNVERIFIED — grep a real
node id from that file]**.

---

**ASI10 — Rogue Agents → `partial`**

*Summary.* Autonomy is bounded by construction rather than by monitoring: an allowlisted,
risk-tiered tool surface; a human gate above `gate_min_risk`; hard step and wall-clock
caps; a per-run trace of typed spans; an immutable audit row per autonomous or approved
action, and a `run_events` row per hop. A lane cancelled from outside is recorded as a
designed terminal state rather than vanishing
**[VERIFIED** `aegis/src/aegis/agent/subagent.py:105-117`**]**.

*Gap.* **There is no behavioural baseline and therefore no drift detection.** Nothing
notices an agent that stays entirely inside its allowlist while working on the wrong
objective, nothing scores a run against its own declared remit, and there is no per-agent
kill switch a human can pull mid-run other than cancelling the whole fan-out. The audit
log is also append-only **by database privilege on the serving role only** —
`POSTGRES_ADMIN_DSN` can still rewrite it **[VERIFIED**, README's "must not say" list**]**.

*Evidence.* `_f` `aegis/src/aegis/agent/subagent.py` · `_f`
`aegis/src/aegis/governance/audit.py` **[VERIFIED — cited by `IAGG · Accountability`]** ·
`_r` `GET /security/posture` **[VERIFIED — cited by `IAGG · Accountability`]** · `_t`
`aegis/tests/agent/test_terminal_outcome_and_cost.py::<name>` **[UNVERIFIED — grep]** ·
`_t` `aegis/tests/security/test_posture.py::test_no_threat_claimed_enforced_when_its_control_is_off`
**[VERIFIED — cited by `IAGG · Accountability`]**.

---

**Resulting distribution: 1 enforced · 9 partial · 0 not implemented · 0 not applicable.**
Ten new controls, one of them green. That is the honest picture and it should not be
improved by argument.

---

## 5. Task B — add India's February-2026 layer

### 5.1 The framework tuple

Insert at **index 3** of `_FRAMEWORKS` — after `india-sectoral`, before `owasp-llm` — so
`test_india_is_served_first`'s contiguity assertion still holds:

```
(
    "india-ai-2026",
    "India AI Governance Guidelines and IT Amendment Rules 2026",
    "IAGG 5 Nov 2025, foregrounded at the India AI Impact Summit 16–20 Feb 2026 · "
    "IT Amendment Rules in force 20 Feb 2026",
    JURISDICTION_INDIA,
    "<scope sentence — see below>",
    _INDIA_AI_2026,
),
```

**`_MARKS` entry:** `"india-ai-2026": "IAGG · IT Rules 2026"`.

**Scope sentence:**

> *The layer that became binding in February 2026, and the four IAGG principles Aegis
> already maps are **not repeated here** — they live in the MeitY/RBI/SEBI/BIS framework
> above, and counting the same evidence twice would inflate a page whose whole claim is
> that its numbers are derived. What is here is what February added: the IT Amendment
> Rules' synthetic-content duties, in force now, and the Guidelines' action-plan items —
> an India-specific risk classification and an AI incident database — that Aegis has
> nothing for. Nothing in this framework is enforced.*

### 5.2 The six controls

**`ITAR 2026 · r.3(1)(b) SGI labelling` → `not_implemented`**

*Summary.* No output path marks generated content as AI-generated. Answers, exports,
reports and streamed tokens all leave unlabelled.

*Gap.* The Rules require prominent, visible labelling of published synthetically generated
information from 20 Feb 2026. Aegis emits none, on any surface.

*Evidence.* `_d` `docs/security/overview.md` **[UNVERIFIED — pick a document that actually
discusses output paths; a `not_implemented` row needs no file/test, but a `doc` ref helps a
reviewer]**.

> **Resist the near-miss.** Aegis carries `Receipt` under every figure, citations on every
> answer and a `run_id`/`trace_id` on every run, and it is tempting to call that
> "provenance" and mark this `partial`. It is provenance **about sources**, not about the
> output being machine-generated — the substitution
> `test_the_dpdp_paperwork_obligations_stay_unimplemented` exists to catch in its own
> domain ("a notification inbox that is not grievance redressal, a risk map that is not a
> DPIA"). Keep it `not_implemented`, and say in the gap that the near-miss was considered
> and refused.

**`ITAR 2026 · r.3(1)(b) provenance metadata` → `not_implemented`**

*Summary.* No embedded provenance metadata on any generated artefact; no C2PA, no
watermark, no signed manifest.

*Gap.* The Rules ask for embedded metadata tracing origin and attesting authenticity where
technically feasible, and for such labels never to be removed or obscured. Aegis produces
no artefact carrying any of it, so the label-preservation duty has nothing to preserve.

**`ITAR 2026 · Intermediary due diligence and takedown clocks` → `not_applicable`**

*Summary.* These duties bind intermediaries that host or publish third-party content to the
public. Aegis publishes nothing publicly, hosts no user-generated content surface and has
no third-party posting path.

*Gap.* — and this must clear the 80-character substantive-reason bar of §3.2 —

> *Applicable only where the deployer is itself an intermediary publishing user content,
> and then it is that deployer's obligation rather than the platform's. What such a
> deployment would inherit from Aegis: an action-level audit trail with actor, approver and
> trace id, a derived inventory of every destination data reaches, database-enforced tenant
> isolation, and a human approval gate that auto-rejects on timeout. What it would still
> owe, and Aegis supplies none of: a grievance officer resident in India, a published
> complaint channel, a compliance report, and an automated clock that meets the two- and
> three-hour takedown windows — Aegis has no takedown mechanism of any kind and no timer
> that could be held to one.*

**`IAGG · Techno-legal evidence` → `partial`**

*Summary.* The Guidelines' techno-legal thesis — that governance should be demonstrable in
the system rather than asserted on paper — is the thing this platform is built as: control
status re-derived from live wiring on every request rather than declared, a residency
inventory derived from live configuration rather than asserted, and a compliance table
every one of whose claims is resolved against the real filesystem, route table and test
files on each run.

*Gap.* Demonstrability is not the same as conformity. Nobody has performed a
self-assessment against the Guidelines, no accountable-role register exists for a
deployment, and the Guidelines' graded-liability model presumes an assessment that has not
been done — the platform supplies evidence, and grades itself against nothing.

*Evidence.* `_r` `GET /security/posture` **[VERIFIED served]** · `_r` `GET /compliance`
**[VERIFIED served — cited already at `compliance.py:2489`]** · `_f`
`backend/src/app/platform/residency.py` **[VERIFIED]** · `_t`
`backend/tests/api/test_compliance.py::test_every_evidence_reference_resolves`
**[VERIFIED exists — the table auditing itself is the honest evidence for this row]**.

**`IAGG · India-specific risk classification` → `not_implemented`**

*Summary.* Aegis's risk taxonomy is OWASP- and MLCommons-derived. There is no India-specific
harm set, no Indian-language adversarial probe in the 48-attack battery, and no
classification of a deployment against an Indian risk tier.

*Gap.* The Guidelines' short-term action plan puts an India-specific risk-classification
framework and a regulatory gap analysis first. Aegis has neither, and the red-team battery
would not detect an India-specific harm because none is encoded in it. (This restates, for
the framework that asks for it, the gap already recorded at `IAGG · Safety` — the *state*
is counted once, here, because the two frameworks ask different questions of it.)

> **[UNVERIFIED — decide before merging.]** This row and `IAGG · Safety` in
> `india-sectoral` are close enough that a reviewer could call them a duplicate. Two
> defensible resolutions: (a) keep both, with each gap sentence explicitly cross-naming the
> other; or (b) drop this row and extend `IAGG · Safety`'s gap. This plan recommends **(b)
> if the implementer has any doubt** — the double-counting objection is the one this page
> cannot afford to lose. Choosing (b) leaves this framework with five controls.

**`IAGG · AI incident reporting` → `not_implemented`**

*Summary.* No path reports an AI incident to anybody. The notification inbox is an
operational surface, the audit log records actions, and neither is an incident report.

*Gap.* The Guidelines' medium-term plan creates a national AI incident database, and
`docs/governance/incident-response.md` already states in its own text that detection is a
human opening a screen, with no paging and no on-call, and that **CERT-In's six-hour clock
is not automated** **[VERIFIED**, README "must not say" list**]**. An incident-reporting
duty landing on top of that finds nothing to build on.

*Evidence.* `_d` `docs/governance/incident-response.md` **[VERIFIED exists — cited in the
README]**.

**Resulting distribution: 0 enforced · 1 partial · 4 not implemented · 1 not applicable**
(or 0/1/3/1 if resolution (b) above is taken).

---

## 6. Task C — the three stale texts

### 6.1 `docs/security/owasp-agentic.md:18-24` — the obsolete hedge

**Delete the "Naming note (honest)" block and replace it.** The publication it defers to
exists: 9 December 2025, ten ASI-prefixed categories **[DOC]**.

**But do not simply renumber the table below it.** The theme table at
`owasp-agentic.md:30-46` has **eight rows** and they do not map one-to-one onto ten ASI
ids **[VERIFIED by reading both]**:

| `owasp-agentic.md` theme | Maps to |
|---|---|
| 1 Excessive agency / autonomy | ≈ ASI10 Rogue Agents (and LLM06) |
| 2 Tool misuse / hijacking | ASI02 |
| 3 Prompt injection / jailbreak | ASI01 |
| 4 Sensitive-information disclosure | **no ASI slot** — LLM02 |
| 5 Insecure output handling | **no ASI slot** — LLM05 |
| 6 Identity / privilege abuse | ASI03 |
| 7 Untraceable / unaccountable actions | ≈ ASI09 |
| 8 Cascading failures / resource exhaustion | ASI08 |
| — | **unmapped: ASI04, ASI05, ASI06, ASI07** |

So the replacement text is a **dated statement plus a crosswalk**, not a renumber:

> **Numbering, resolved.** The OWASP Top 10 for Agentic Applications was published on
> **9 December 2025** as the 2026 edition, with ten categories ASI01–ASI10. The
> ASI-indexed mapping of this platform lives in
> [`threat-model.md`](threat-model.md) §2; the table below is organised by *theme*
> instead, and the two are not the same shape — three ASI categories (supply chain, code
> execution, memory poisoning) have no theme row here, and two theme rows (sensitive-info
> disclosure, insecure output handling) belong to the LLM Top 10 rather than the agentic
> list. The crosswalk is: [table above]. Where Aegis's own code annotates the older LLM
> Top 10 (`LLM02`, `LLM06`), those annotations are kept so the lineage is traceable.

**`backend/src/app/platform/risk_map.py` needs no change.** It is grounded verbatim in this
document (`risk_map.py:3`) and uses **its own `AA-01`…`AA-09` ids** (`:56-227`)
**[VERIFIED]**, which are a deployment-risk scheme, not ASI ids. Confirm its docstring at
`:3-9` still reads true after the edit; do **not** renumber `AA-0x`, because
`backend/tests/api/test_platform_surfaces.py:247-335` asserts on that surface.

**Also regenerate `docs/security/owasp-agentic.html`** — it is a built twin of the markdown
(`owasp-agentic.html:128` carries the same heading) **[VERIFIED]** and will otherwise
contradict the source.

### 6.2 `docs/security/threat-model.md:35-44` — wording drift

Three titles are wrong against the published list (§2.1). Fix all three; **ASI06 is not
cosmetic.**

- `:38` `ASI04` — "Agentic Supply Chain" → **"Agentic Supply Chain Vulnerabilities"**.
- `:39` `ASI05` — "Unexpected Code Execution" → **"Unexpected Code Execution (RCE)"**.
- `:40` `ASI06` — "Context / Retrieval Manipulation" → **"Memory & Context Poisoning"**,
  **and rewrite the mitigation cell**, which currently names spotlighting and
  validate-before-write only. It must also say what the memory write path does (the
  consolidator's refusal to retarget an unresolvable `target_id`) and what it does not: the
  durable-facts tier is injected **unspotlighted at the top of the working-memory block**
  (§4.2, ASI06). A threat model that names a category by the wrong title is how that hole
  stayed invisible.

Regenerate `docs/security/threat-model.html` alongside it.

### 6.3 `docs/compliance/README.md` — the authority

This document is the **authority** the compliance module projects (`compliance.py:70`,
`DOC_REF`) **[VERIFIED]**, and three tests read it. Every edit below is mandatory.

1. **Two new sections.** The IT Rules / IAGG section belongs in the India block (currently
   §§2–5) and OWASP Agentic in the international block (§§6–14). **Every subsequent section
   number shifts** — that is a large, mechanical, unavoidable diff. Section titles must
   contain the framework `name` verbatim (`test_the_written_authority_exists`).
2. **`:17` — "Twelve frameworks, India first"** → **fourteen** (or thirteen under
   resolution (b) of §5.2 — no, the framework count is fourteen either way; only the control
   count moves).
3. **`:24` — the headline totals.** Do not compute them by hand. Run the suite, take the
   exact line from `test_the_readme_headline_totals_are_the_tables_own_totals`'s failure
   message — it is written to be a paste — and put it in.
4. **`:25` — "Twenty-four of them are India's, and two of those twenty-four are enforced"**
   → re-derive. Under this plan India gains six controls and **zero** enforced ones, so the
   ratio gets worse and the sentence must say so.
5. **`:481-489` — the framework-versions footer.** Add *OWASP Top 10 for Agentic
   Applications (2026 edition, published 9 December 2025)* and *IT (Intermediary Guidelines
   and Digital Media Ethics Code) Amendment Rules 2026 (in force 20 February 2026)*. The
   footer already names *MeitY India AI Governance Guidelines (5 Nov 2025)* — leave it,
   and do not add a second, contradictory date.
6. **§15's honest summary.** Add one line to "What Aegis must not say": *no synthetic-content
   labelling exists, and the IT Amendment Rules' obligation is live now rather than
   deferred.*
7. Regenerate `docs/compliance/README.html` if one exists in the built set.

### 6.4 EU AI Act — add the timeline that is missing

No date is wrong; a date is **absent**, and the absence now misleads (§2.3).

- **`compliance.py`, the `eu-ai-act` framework scope string (`:2895-2903`)** — extend to
  name the deferral: *"The May 2026 Digital Omnibus deferred the Annex III high-risk
  obligations from 2 August 2026 to **2 December 2027**, and Annex I embedded systems to
  2 August 2028; **Article 50 transparency and Article 4 AI literacy were not deferred**.
  The articles Aegis is weakest on are the deferred ones; the one it does not implement at
  all — Art. 50 — is live."*
- **`_EU_AI_ACT`'s `Art. 50` row gap** — add that this obligation is **not deferred** and is
  the same absence as the new `ITAR 2026 · SGI labelling` row.
- **`docs/compliance/README.md` §12 classification note** — the same two sentences.
- **[UNVERIFIED]** The Digital Omnibus's final adopted text was checked here through
  secondary legal-practice sources reporting the 6 May political agreement and 13 May
  Council confirmation **[DOC]**. Confirm against the Official Journal citation before
  writing a specific date into a compliance surface, and if publication has not yet
  happened, say *"agreed, pending publication"* rather than stating it as settled law.

---

## 7. New tests — the guards this plan owes

Write these in `backend/tests/api/test_compliance.py`, beside the existing row guards.

**7.1 `test_every_asi_category_is_present_exactly_once`**

```
agentic = next(f for f in _MAP.frameworks if f.id == "owasp-agentic")
assert [c.id for c in agentic.controls] == [f"ASI{n:02d}" for n in range(1, 11)]
```

Rationale: a "Top 10" that serves nine rows is a mapping with a hole in it, and the hole is
invisible on a screen that renders whatever it is sent.

**7.2 `test_the_synthetic_content_absence_is_stated_consistently`**

Four rows describe the same hole — EU AI Act `Art. 50`, `india-sectoral`'s
`IAGG · Transparency`, the new `ITAR 2026 · SGI labelling`, and the new ASI09's gap. Assert
that **none** of the first three is `enforced` unless all three are:

```
states = {art50.state, iagg_transparency.state, itar_sgi.state}
assert ControlState.ENFORCED not in states or states == {ControlState.ENFORCED}
```

Rationale: one output-labelling feature would satisfy all three at once. If a future change
flips one and not the others, the page is simultaneously claiming and denying the same
mechanism, and this is the only place that would notice.

**7.3 `test_no_intermediary_compliance_is_claimed`**

Modelled exactly on `test_no_bfsi_compliance_is_claimed`: the `ITAR 2026 · Intermediary due
diligence` row may **only ever** be `not_applicable`, and its gap must contain `"owe"` or
`"still"`. Aegis has no takedown mechanism and no grievance officer; "compliant with the IT
Rules" is the second most saleable overclaim on this page after "compliant with RBI".

**7.4 `test_the_shipped_tool_registry_executes_no_code`** — in
`aegis/tests/agent/`, not the compliance suite. Assert that no handler in the shipped
`TOOL_REGISTRY` resolves to a callable whose module imports `subprocess` / uses `eval` /
`exec`. This is the test that makes ASI05's `partial` honest rather than rhetorical, and
without it the ASI05 summary is an assertion nobody re-checks. **[UNVERIFIED]** — the exact
introspection shape depends on `ToolSpec`; read `adapter/tools.py` first.

**7.5 Do not add a test that pins ASI01 to non-`enforced`.** It is tempting — injection is
never solved — but `LLM01` *is* `enforced`, on screening rather than on prevention, and a
guard that forbade the sibling row would be asserting a contradiction. The gap sentence is
the control here, and `test_anything_short_of_enforced_names_what_is_missing` already
requires it.

---

## 8. VERIFICATION SECTION — mandatory

Nothing below is optional, and none of it is satisfied by reading the diff.

### 8.1 Endpoints, with expected responses

Backend on `:8000` (`scripts/dev-native.sh:55`) **[VERIFIED]**; every product route is
served under `/v1` (`backend/src/app/main.py:758-771`) **[VERIFIED]**; seeded accounts use
`AEGIS_SEED_PASSWORD` or the documented default `demo`
(`backend/src/app/seed.py:104-109`) **[VERIFIED]**.

**Get a token.** `GET /v1/compliance` is guarded by
`require_platform_security_reader`; the `devops` principal passes and `client` does not
**[VERIFIED** `test_compliance_route_serves_the_map_to_devops` /
`test_compliance_route_refuses_a_business_role`**]**.

```bash
TOKEN=$(curl -s -X POST localhost:8000/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"username":"devops","password":"demo"}' | jq -r .token)
```
*Expected:* a non-empty JWT. `LoginResponse.token` is the field
(`backend/src/app/api/schemas.py:686`) **[VERIFIED]**.

**The map, and the two new frameworks.**

```bash
curl -s localhost:8000/v1/compliance -H "authorization: Bearer $TOKEN" \
  | jq '{n: (.frameworks|length), total: .coverage.total, cov: .coverage,
         ids: [.frameworks[].id]}'
```
*Expected:* `n == 14`; `ids` begins `["dpdp","cert-in","india-sectoral","india-ai-2026",…]`
— **India first and contiguous**; `"owasp-agentic"` present after `"owasp-web"`;
`total == 130` (114 + 10 + 6) or `128` under §5.2 resolution (b); `coverage.enforced ==
38` (37 + ASI02). **Whatever the numbers are, they must equal the README headline** — that
equality is the point, not the specific integers.

```bash
curl -s localhost:8000/v1/compliance -H "authorization: Bearer $TOKEN" \
  | jq '.frameworks[] | select(.id=="owasp-agentic")
        | {version, coverage, controls: [.controls[] | {id, state}]}'
```
*Expected:* exactly ten controls, ids `ASI01`…`ASI10` in order; `ASI02` `enforced`, the
other nine `partial`; `coverage == {enforced:1, partial:9, not_implemented:0,
not_applicable:0, total:10}`.

```bash
curl -s localhost:8000/v1/compliance -H "authorization: Bearer $TOKEN" \
  | jq '.frameworks[] | select(.id=="india-ai-2026")
        | {jurisdiction, controls: [.controls[] | {id, state, gap_len: (.gap|length)}]}'
```
*Expected:* `jurisdiction == "India"`; the `not_applicable` row's `gap_len` well above 80
even after the restatement-stripping of §3.2 — **check this by running the test, not by
counting characters.**

**Authorisation is unchanged.**

```bash
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/v1/compliance          # 401
CLIENT=$(curl -s -X POST localhost:8000/v1/auth/login -H 'content-type: application/json' \
  -d '{"username":"client","password":"demo"}' | jq -r .token)
curl -s -o /dev/null -w '%{http_code}\n' localhost:8000/v1/compliance \
  -H "authorization: Bearer $CLIENT"                                            # 403
```

**The public summary — no token, and no gap map.**

```bash
curl -s localhost:8000/v1/platform/standards \
  | jq '{certified, n: (.frameworks|length), coverage,
         marks: [.frameworks[] | {id, mark, enf: (.enforced_controls|length),
                                  claimed: .coverage.enforced}]}'
```
*Expected:* `200` with **no** `authorization` header; `certified == false`; `n == 14`;
every `mark` a short label, **specifically `"OWASP Agentic Top 10"` and `"IAGG · IT Rules
2026"` rather than the long fallback `name`** — that is what proves the `_MARKS` edit
landed; for every framework `enf == claimed`; and `jq '.frameworks[].controls'` must be
`null` everywhere, because this endpoint serves no control detail
(`routes_standards.py`, `build_standards`) **[VERIFIED]**.

```bash
curl -s localhost:8000/v1/platform/standards | grep -ci 'not_implemented\|"gap"\|evidence'
```
*Expected:* `0`. The gap map must never leak onto the public surface.

**The live-wiring twin, unchanged.**

```bash
curl -s localhost:8000/v1/security/posture -H "authorization: Bearer $TOKEN" | jq 'length'
curl -s localhost:8000/v1/risk-map -H "authorization: Bearer $TOKEN" | jq '[.risks[].id]'
```
*Expected:* posture unchanged by this work; risk-map ids still `AA-01`…`AA-09` — **if they
changed, §6.1 was misread and `risk_map.py` was renumbered when it should not have been.**

### 8.2 Tests to write, and where

| Test | File | What it stops |
|---|---|---|
| `test_every_asi_category_is_present_exactly_once` | `backend/tests/api/test_compliance.py` | a Top 10 served with nine rows |
| `test_the_synthetic_content_absence_is_stated_consistently` | same | one feature satisfying three rows while only one flips |
| `test_no_intermediary_compliance_is_claimed` | same | "compliant with the IT Rules" |
| `test_the_shipped_tool_registry_executes_no_code` | `aegis/tests/agent/` | ASI05's `partial` being rhetoric |

**Tests to run, and what each proves.**

```bash
backend/.venv/bin/python -m pytest backend/tests/api/test_compliance.py \
  backend/tests/api/test_not_applicable_is_justified.py \
  backend/tests/api/test_compliance_readme_totals.py -q
```
Every new evidence ref resolves · every `enforced` row has a file *and* a test · the one
new `not_applicable` reason is substantive · India stays first · the README headline
matches. **Expect `test_the_readme_headline_totals_are_the_tables_own_totals` to fail on
the first run and to pass after the paste. A green first run means the new controls did not
load.**

```bash
backend/.venv/bin/python -m pytest backend/tests/api/test_standards.py \
  backend/tests/api/test_platform_surfaces.py -q
backend/.venv/bin/python -m pytest backend/tests -q
cd aegis && ../aegis/.venv/bin/python -m pytest tests -q   # [UNVERIFIED runner path]
```

```bash
cd web && node --test tests/landing/standardsBand.test.mjs && npm run typecheck
```
The band test sweeps `StandardsBand.tsx` and `standardsSummary.ts` for bare two-to-four
digit integers and for control-identifier shapes (`/\bLLM\d{2}\b/`, `/\bArt\.\s?\d/`, …)
in **comment-stripped** source (`strip` at `standardsBand.test.mjs:61`) **[VERIFIED]**.
**`ASI\d{2}` is not currently one of the swept shapes — add it**, or the first person to
paste `ASI01` into the shortlist will not be caught by the guard that exists for exactly
that mistake.

### 8.3 Frontend surfaces affected

Both read this data; neither needs a data-shape change, because both are derived.

**The DevOps compliance screen — `web/src/components/compliance/ComplianceView.tsx` (431
lines).** Groups frameworks by `jurisdiction` in **the order the server sent**
(`:261-281`), renders a picker card per framework with its own coverage strip
(`:170-207`), and a board of totals at `:331-341` **[VERIFIED]**. Fully data-driven: the
two new frameworks appear with no component change, India's group grows to four.
**Two edits owed anyway:**
- **`:211` — the component docstring says "nine published frameworks"**, which was already
  wrong at twelve **[VERIFIED]**. Replace the count with derived language ("every mapped
  framework"); do not swap nine for fourteen and re-arm the same trap.
- Check the picker at ≥ 4 India cards at 390 px — `:361-365` records that the framework
  names were a width problem at that breakpoint once already **[VERIFIED]**, and `IAGG · IT
  Rules 2026` is a long mark.
- `web/src/components/compliance/ResidencyPanel.tsx` and `CoverageStrip.tsx` are unaffected.

**The public landing band — `web/src/components/landing/StandardsBand.tsx` (418) and
`standardsSummary.ts` (239).** `SHORTLIST` names three groups over `eu-ai-act`,
`owasp-llm`, `dpdp`+`privacy` (`standardsSummary.ts:98-105`) **[VERIFIED]**; the new
frameworks are **not** in it, so the band prints no new cards — but it **does** print
global totals off the wire, and `completeFrameworks` re-derives the "enforced in full" row
on every read (`:141-161`). Adding twenty controls of which one is enforced **moves the
public denominator**. That is correct and intended; confirm on the rendered page that the
"N of M" figures moved and that no framework newly appears — or disappears from — the
in-full row.
- **`standardsSummary.ts:82` says "all twelve frameworks and all 114 mapped controls"** in
  a **doc comment** **[VERIFIED]**. `strip()` removes comments before the digit sweep, so
  the test will not catch it — **fix it by hand or delete the numbers**, because a stale
  count in the file whose entire docstring argues against stale counts is the worst
  possible place for one.
- `web/src/lib/api/standards.ts` and `web/src/lib/api/generated/schema.d.ts` need **no**
  regeneration: no Pydantic model changes, only data. Confirm with the OpenAPI snapshot
  check in CI.

### 8.4 Definition of done

- [ ] The ten ASI titles are taken from the OWASP PDF, not from this document.
- [ ] `GET /v1/compliance` serves 14 frameworks; India's four are contiguous and first.
- [ ] Exactly one new control is `enforced`, and it names a file and a test.
- [ ] Every new evidence ref resolves — the suite says so, not a reader.
- [ ] The one new `not_applicable` reason clears the 80-char substantive bar.
- [ ] The README headline totals were pasted from the test failure, not typed.
- [ ] `GET /v1/platform/standards` shows both new marks and leaks no `gap`.
- [ ] `docs/security/owasp-agentic.md`'s hedge is gone and a dated crosswalk replaces it.
- [ ] `threat-model.md`'s ASI06 row is retitled **and its mitigation cell rewritten**.
- [ ] `risk_map.py`'s `AA-0x` ids are untouched and `/v1/risk-map` is unchanged.
- [ ] The EU AI Act deferral is stated in both the module and the README.
- [ ] `ComplianceView.tsx:211` and `standardsSummary.ts:82` no longer carry stale counts.
- [ ] `ASI\d{2}` is in the landing band's swept identifier shapes.

---

## 9. Risks, stated plainly

1. **The ASI titles in this document are `[DOC]`, not `[VERIFIED]`.** genai.owasp.org
   returned HTTP 403; every title came from secondary sources. A compliance page that
   misquotes a framework's own identifiers is worse than one that omits the framework.
   **Read the PDF.** This is the single largest correctness risk in the plan.
2. **The India premise handed to this plan was wrong**, and a plan that had not checked it
   would have shipped a duplicate IAGG framework and inflated the coverage totals — the
   exact defect the DPDP/GDPR separation exists to prevent. Assume the next premise is
   wrong too.
3. **Twenty new rows, nineteen of them not green, on a page a jury reads.** This is the
   intended outcome and it will still feel like a regression on the day. The mitigation is
   the scope sentences: each says *why* the framework is new and unimplemented. The
   alternative — mapping a control as enforced without naming the code — is the one failure
   this whole surface exists to avoid.
4. **`compliance.py` grows by ~500 lines** in a 2,983-line module already at the edge of
   readable. Consider splitting `_FRAMEWORKS`' control tuples into a package
   (`app/platform/compliance/`) **in a separate commit**, never in the same change as new
   claims, or the diff that adds twenty controls becomes unreviewable.
5. **README section renumbering is a large mechanical diff** across a 489-line authority
   document, and `test_the_written_authority_exists` only checks that names appear — it
   cannot catch a section body left under the wrong heading.
6. **The Digital Omnibus citation is `[DOC]` from practitioner commentary**, not from the
   Official Journal. If the final text has not published, write "agreed, pending
   publication" — a compliance surface stating a date that later moves is precisely the
   defect §6.4 is fixing.
7. **ASI06's finding is a real hole, not just a mapping gap.** Recording it as a `partial`
   with an honest sentence is correct and cheap; *fixing* it (spotlighting the facts tier,
   or re-screening on write) is not in this plan and should not be smuggled into it.

---

## 10. What this plan does **not** cover

- **It fixes nothing it maps.** No control moves from `partial` to `enforced` here. In
  particular: no synthetic-content labelling is built (four rows would flip at once if it
  were — §7.2), the facts tier is not spotlighted, no goal-integrity check is added, no DNS
  resolution is added to the SSRF guard, no agent behavioural baseline exists, and no
  per-tool credential step-down is designed.
- **No EU AI Act conformity work.** Only the timeline sentence changes.
- **No DPDP work.** Consent, notice, breach notification, DPIA and grievance remain
  `not_implemented` and are pinned there by an existing test.
- **No certification of anything.** `DISCLAIMER` is unchanged and still says so on every
  response.
- **The trajectory ceiling that ASI08's gap points at** is planned in
  `docs/dev_new_docs_v2/sota/07-long-horizon-ceiling.md`, not here. When it lands, ASI08's
  gap sentence must be rewritten in the same change — a gap sentence that outlives the gap
  is the same class of defect as a stale total.
