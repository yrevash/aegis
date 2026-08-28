# Phase 4 — AUDIT GATE

**Target:** commit `db3eb71` *feat(compliance): the agentic Top 10 is in the endpoint, not just a markdown file*
**Branch:** `docs/wow-pass-plan` (audited at `HEAD` = `64072d8`)
**Plan:** `docs/dev_new_docs_v2/sota/06-compliance-asi-india.md`
**Verified titles:** `docs/dev_new_docs_v2/sota/phase-0-A0-owasp-asi.md`
**Date:** 2026-08-27 · report only, nothing fixed, no source modified.

---

## VERDICT: **PASS WITH FINDINGS**

The framework is real, served, correctly scoped, and renders. All ten titles are verbatim.
The totals are arithmetically exact. Nothing regressed — 788 tests pass.

But the honesty claim, which is the point of the phase, does not fully hold. **Two of the
three `enforced` rows were upgraded above the state the plan's own evidence review assigned
them**, and one of those upgrades asserts an invariant the plan explicitly warned does not
exist and that this repository's own code contradicts. A third row is now factually false
against the very process that serves it. Three checkable numbers in served `gap` text do not
match what the code produces when measured.

| Claim | Verdict |
|---|---|
| 1. Framework 13, ten controls ASI01–ASI10, **2 enforced / 8 partial** | **Framework and controls: TRUE. Ratio: FALSE — 3 enforced / 7 partial** |
| 2. Five wrong titles corrected, verbatim from the PDF | **TRUE** — all ten exact in both files |
| 3. Obsolete hedge retired | **TRUE**, but the rewrite preserved a parenthetical Phase 0 flagged as wrong |
| 4. Totals 124 / 40 enforced / 60 partial, README headline updated | **TRUE for the totals line. FALSE for the document** — README has no ASI section and still says "Twelve frameworks" |

Findings: **3 HIGH · 6 MEDIUM · 6 LOW**.

---

# A. OVERCLAIMING

## F1 · HIGH — ASI05 is `enforced` on an invariant that does not exist, and the platform's own MCP client contradicts the summary word-for-word

`backend/src/app/platform/compliance.py:2905-2921`

```python
        id="ASI05",
        title="Unexpected Code Execution (RCE)",
        state=ControlState.ENFORCED,
        summary=(
            "There is no code-execution tool and no shell. The registry is a closed set "
            "of typed, allowlisted callables with validated argument models, so there is "
            "no path by which generated text becomes an executed program."
        ),
```

**The plan wrote this row as `partial` and spent a paragraph explaining why the stronger
claim is not available** — `docs/dev_new_docs_v2/sota/06-compliance-asi-india.md:344-368`:

> **ASI05 — Unexpected Code Execution (RCE) → `partial`**
> **Read this before choosing `not_applicable`.** … the absence is a property of
> **the shipped adapter's registry**, not an invariant the core enforces: `TOOL_REGISTRY` is
> adapter-owned, and nothing in `aegis.agent` refuses a registered tool that shells out.
> …
> *Gap.* **The closure is the adapter's, not the core's.** … there is no capability
> declaration on `ToolSpec` that a registry-time check could enforce, and no sandbox if one
> were added. **The claim is "we ship none", not "one cannot exist".**

`enforced` rows carry no `gap` (`test_anything_short_of_enforced_names_what_is_missing`), so
upgrading the state is precisely the mechanism by which that named limitation stopped being
served. What ships instead is the sentence the plan said was unavailable.

**[MEASURED]** `ToolSpec` has no capability field. `backend/src/app/adapter/tools.py:626-655`
declares `name, description, args_model, handler, risk, read_only, destructive, idempotent`
— nothing a registry-time check could use to refuse a tool that shells out.

**[MEASURED] The word "closed" is false at runtime.** The tool surface is not
`TOOL_REGISTRY`. External MCP peer tools are discovered over the network and enter the
planner's payload under a qualified name — `backend/src/app/mcp/client.py:22-45`:

> Every external tool is qualified `EXTERNAL_PREFIX` + server id + `__` + the peer's own
> name — `mcp__acme__create_issue`.
> … **Per tool, never per server** — "trust everything from this peer" is an abdication
> rather than a control, **because the peer can add a tool tomorrow** that would inherit a
> decision nobody made about it. A tool that appears later starts at HIGH like every other.

The governance around those tools is genuinely good (HIGH by default, per-persona allowlist
checked before the network call, `TOOL_RESULT` rail on descriptions *and* argument schemas).
None of it makes the set closed, and "closed set … so there is no path by which generated
text becomes an executed program" is a claim about closure. Aegis's own ASI04 row concedes
peers as an attack surface in the same payload; ASI05 denies the surface exists.

**Recommended fix.** Restore `ControlState.PARTIAL` with the plan's gap: the closure is the
shipped adapter registry's, not the core's, and an operator-registered MCP peer can advertise
a tool that executes code — mitigated by HIGH-by-default plus the human gate, not prevented.
Then add the test the plan asked for and the commit did not (§F14).

---

## F2 · HIGH — the ASI07 gap is false against the running build

`backend/src/app/platform/compliance.py:2959-2965`

```python
        gap=(
            "That is a property of the architecture, not a control that was built: "
            "Aegis speaks no agent-to-agent protocol, so there is nothing yet to "
            "authenticate. A signed agent card and an authenticated peer surface are "
            "planned and absent, and until they exist this control is answered by 'we "
            "do not do the risky thing' rather than 'we do it safely'."
        ),
```

**[MEASURED]** against the live backend on `:8110`, the same process that serves this text:

```
$ curl -o /dev/null -w '%{http_code}' http://localhost:8110/.well-known/agent-card.json
200
$ # /v1/a2a is in the served OpenAPI path table
['/v1/a2a']
```

**[SOURCE]** `64072d8` on this branch — *"feat(a2a): Aegis speaks Agent2Agent"*:

> Served: a signed Agent Card at `/.well-known/agent-card.json` in the 1.0 shape … a JWKS
> beside it, and a JSON-RPC endpoint answering SendMessage and GetTask. … The signature is
> real. Verified independently against the published JWKS…

A juror reading `GET /v1/compliance` is told a signed agent card is "planned and absent"
while `/.well-known/agent-card.json` answers 200 from the same host. This is the single most
citable self-contradiction on the endpoint. Phase 6 shipped the capability and did not
revisit the row Phase 4 wrote about its absence.

**Recommended fix.** Rewrite the ASI07 summary and gap against what now exists: an authenticated
A2A surface with a signed card and JWKS, the routing-tenant/`app.tenant_id` separation, and
whatever remains genuinely absent (peer-to-peer message integrity between *Aegis* agents,
mutual authentication of an outbound peer). Cite `backend/tests/…` from `64072d8`. Consider
whether the state moves off `partial`.

---

## F3 · HIGH — ASI03 is `enforced` against the plan, its summary describes a measurement no test performs, and the same payload contradicts it two frameworks away

`backend/src/app/platform/compliance.py:2860-2878`

```python
        id="ASI03",
        title="Identity and Privilege Abuse",
        state=ControlState.ENFORCED,
        summary=(
            "Tenancy is enforced in Postgres RLS beneath the application filters, and "
            "the MCP surface re-resolves the caller's identity and authority on every "
            "call rather than once per connection — measured over one socket by swapping "
            "the bearer and watching the tool list and tenant scope change with it."
        ),
        evidence=[
            _f("aegis/src/aegis/governance/rls.py", "row-level security policies"),
            _f("backend/src/app/mcp/server.py", "resolve_caller, per call"),
            _t(
                "backend/tests/api/test_admin_governance.py"
                "::test_tenant_admin_cannot_read_other_tenant",
                "a tenant-bound caller cannot reach another tenant's rows",
            ),
        ],
```

Three separate problems.

**(a) The plan assigned `partial` with a specific, named gap that is now unserved.**
`06-compliance-asi-india.md:299-311`:

> **ASI03 — Identity and Privilege Abuse → `partial`**
> *Gap.* **The confused-deputy half is absent.** The agent runs with the caller's full role
> for the whole run — there is no step-down, no per-tool credential exchange and no scoped
> token issued to a sub-agent, so a lane that is talked into calling an allowed tool calls it
> with the human's authority rather than a narrowed one. Authentication also has no MFA, no
> lockout and no revocation…

The plan is also explicit that ASI02 was meant to be the only `enforced` row —
`06-compliance-asi-india.md:293-296`:

> *Why this one and only this one is enforced.* It is the only ASI category where the control
> runs on **every** relevant request, the failure mode is refusal rather than degradation, and
> a test fails if the mechanism is removed. That is the bar, **and nine other rows do not clear it.**

**(b) The cited test is adjacent, and the summary's measurement contradicts the test that
would actually support it.** `test_tenant_admin_cannot_read_other_tenant`
(`backend/tests/api/test_admin_governance.py:102-112`) asserts two HTTP 403s on
`/admin/users?tenant_id=2` and `/admin/usage?tenant_id=2`. It is a REST application-layer
authorization refusal. It exercises no MCP surface, no socket, no bearer swap, and no
per-call re-resolution. The summary's most specific claim is untested by its own citation.

The test that *does* test per-call re-resolution exists and is **not cited** —
`backend/tests/mcp/test_streamable_http.py:231-260`,
`test_scope_is_resolved_per_call_not_per_connection`. Its docstring directly contradicts the
summary's account of the experiment:

> **The authority changes underneath a live connection. The bearer is unchanged
> and still valid — it still says `tenant_id: <tenant_a>`.**

The measurement moves the *user* between tenants with the bearer held constant. The compliance
summary says the opposite — "by swapping the bearer". **[MEASURED]** no test in
`backend/tests/mcp/` swaps a bearer on one socket; `test_two_tenants_see_different_data_over_the_same_server`
uses two separate sessions. And no cited test shows the *tool list* changing at all; that
property lives in `test_tools_follow_the_callers_role_not_an_env_var:175-197`, also uncited.

**(c) The same response says the opposite about identity, two frameworks away.** The served
`owasp-web` table, `docs/compliance/README.md:261`:

> | **A07:2025** | Authentication Failures | **partial** | … | **No MFA. No account lockout
> or login rate limiting. No password policy. No token revocation list** — a leaked token is
> valid until it expires. |

and the README's own "What Aegis must not say" list, `docs/compliance/README.md:456`:

> - Authentication has no MFA, no lockout and no revocation.

A reviewer who opens both rows finds "Identity and Privilege Abuse — enforced" beside
"Authentication Failures — partial, no MFA, no lockout, no revocation".

**Recommended fix.** Return ASI03 to `partial` with the plan's confused-deputy gap. If it
stays `enforced`, at minimum (i) cite
`backend/tests/mcp/test_streamable_http.py::test_scope_is_resolved_per_call_not_per_connection`
and `::test_a_deactivated_principal_loses_authority_mid_connection`, and (ii) delete "by
swapping the bearer" — the mechanism described is not the mechanism tested.

---

## F4 · MEDIUM — the commit message and the phase claim say "two enforced, eight partial"; the code ships three and seven

**[MEASURED]** over `_FRAMEWORKS` at `HEAD`:

```
owasp-agentic   OWASP Top 10 for Agentic Applications  v=2026  n=10  {'partial': 7, 'enforced': 3}
enforced_controls = ['ASI02', 'ASI03', 'ASI05']
```

**[SOURCE]** `db3eb71` commit message:

> **Two enforced, eight partial, and the ratio is the point.**

The message then justifies ASI07, ASI09 and ASI10 individually and never mentions that ASI05
was promoted to `enforced` against the plan. The ratio the message calls "the point" is not
the ratio in the file. Note the arithmetic is internally consistent (114+10=124, 37+3=40,
53+7=60) — it is the narrative that is wrong, which is worse, because the narrative is the
restraint argument.

**Recommended fix.** Either take ASI03/ASI05 back to `partial` (F1, F3), which makes the
message true, or amend the claim wherever it is repeated.

---

## F5 · MEDIUM — the ASI06 gap says three of four poisoning probes are refused; measured, it is two

`backend/src/app/platform/compliance.py:2933-2939`

```python
        gap=(
            "Three of four poisoning probes are refused; the fourth is the honest limit "
            "and it is declared rather than curated out. …
```

**[MEASURED]** running the real battery offline (`run_redteam()`, no completer), filtered to
`Stage.MEMORY_WRITE`:

```
mem-01 expects=block  verdict=pass    neutralized=False success=False
mem-02 expects=block  verdict=block   neutralized=True  success=True
mem-03 expects=block  verdict=redact  neutralized=True  success=True
mem-04 expects=pass   verdict=pass    neutralized=False success=True
```

There are four probes at that stage, but **`mem-04` is a benign control that must *not* be
refused** — `aegis/src/aegis/redteam/battery.py`, its own description:

> The control. A rail that refuses this is a wall, and a wall is trivially safe and useless —
> the store exists to remember exactly this.

So of three actual poisoning probes, **two are neutralised** (one by `block`, one by `redact`)
and one leaks. "Three of four poisoning probes are refused" is true only under a reading where
the benign control's correct *pass* counts as a refusal — which inverts its meaning. The
sentence flatters the row by one probe and mislabels the negative control as an attack.

**Recommended fix.** "Two of the three poisoning probes are refused before the store — one
blocked, one redacted — and a fourth benign probe is deliberately allowed through so the rail
is not merely a wall. The third leaks: …" (the rest of the gap is accurate and good).

---

## F6 · MEDIUM — the ASI01 gap's probe count matches neither measurement

`backend/src/app/platform/compliance.py:2824-2829`

```python
        gap=(
            "Injection is never marked solved on this platform and is not marked solved "
            "here. Ten battery probes are semantic-only and leak with no model completer "
            "wired; …
```

**[MEASURED]** two independent ways:

```
needs_llm (the "semantic-only" marker) = 9
  ['ind-03','jb-04','pii-03','agency-03','cs-06','poison-06','adv-05','peer-04','mem-01']
actual offline leaks over 53 attack probes = 11
  the 9 above + ['exfil-06','exfil-07'] (beyond_rails, not semantic-only)
```

Nine, or eleven. Not ten, under either reading. `docs/compliance/README.md:209` carries the
same "10 battery probes are semantic-only and leak offline" — so this is a pre-existing stale
figure that Phase 4 copied into the served endpoint rather than re-measuring. It is exactly
the class of number a jury checks, because the battery is runnable.

**Recommended fix.** "Nine battery probes are marked semantic-only and leak with no model
completer wired (eleven leak in total; two are declared beyond the rails' reach)". Fix
`README.md:209` in the same pass.

---

## F7 · MEDIUM — ASI09 and ASI10 cite the same tamper-evidence test for entirely different properties, and neither cited test asserts what ASI09's summary claims

ASI09, `compliance.py:3004-3016`:

```python
        summary=(
            "Every consequential action stops for a named human and is recorded with its "
            "approver and trace id, and the console shows the evidence an answer stands "
            "on rather than asking to be believed."
        ),
        …
            _t("aegis/tests/governance/test_audit.py"
               "::test_the_chain_verifies_and_a_tampered_row_is_caught",
               "the trail is verifiable, not merely append-only"),
```

**[MEASURED]** `aegis/tests/governance/test_audit.py:66-100` writes three audit rows, asserts
`verify_audit_chain(1).intact` with `checked == 3`, then edits one field and asserts the
tamper is located. It asserts nothing about an approval gate stopping an action, nothing about
an `approver` field, and nothing about a console. It is a hash-chain test standing in for a
human-oversight claim — the same substitution pattern already caught once in this phase.

ASI10 (`compliance.py:3040-3044`) cites `::test_a_deleted_row_breaks_every_row_after_it` from
the same file for a summary listing four mechanisms ("an allowlisted tool set, a risk gate, a
budget cap enforced at the gateway, and an audit trail"). The citation covers the fourth only;
the budget cap named in the evidence file `gateway/llm.py` has no test cited.

Lower severity than F1–F3 because both rows are `partial` and both carry honest, specific gaps
("Nothing addresses anthropomorphism itself"; "Bounded is not monitored") — those gaps are the
best-written in the set. The problem is the evidence, not the state.

**Recommended fix.** ASI09 should cite the approval-gate test that actually exists
(`aegis/tests/agent/test_gate_authorises_what_runs.py`, or the MCP gate test
`backend/tests/mcp/test_streamable_http.py::test_high_risk_call_lands_in_the_human_approval_gate`)
alongside the chain test. ASI10 should add a gateway-budget test or drop the budget clause
from the summary.

---

## Gap quality — the rest

Assessed against "does the gap name something real and specific, or is it hedging prose that
would let almost any implementation pass?"

| ID | Gap verdict |
|---|---|
| ASI01 | **Specific and good** — names the mechanism (deterministic layer catches phrasing, not intent) and states the gate is decisive *because* the rails are insufficient. Only the number is wrong (F6). |
| ASI04 | **Specific and good** — names AgBOM absence and artefact attestation separately. Note it omits the plan's stronger, verified hole: the SSRF guard does not resolve DNS (`06-…:325-330`), which is a live agentic-supply-chain gap with its own deliberate-limitation test. |
| ASI06 | Specific; the "ordinary business sentence" example is the strongest gap prose in the set. Number wrong (F5). |
| ASI07 | Was specific and honest **when written**; now false (F2). |
| ASI08 | **Specific and good** — names spend and wall-clock bounds as unbuilt, and the different-call-each-round escape from progress detection. |
| ASI09 | **Best in set.** "no measure of whether a reader over-trusts a fluent answer, no confidence calibration shown beside prose, and no control for an operator approving by reflex" — three checkable absences. |
| ASI10 | **Specific.** "Bounded is not monitored… no kill switch that stops an in-flight agent other than the budget refusing its next call." |
| ASI02 | `enforced`, no gap. The state is **justified** — cited test genuinely asserts the summary's property. |

No gap in the set is empty hedging. ASI02 is the one row where state, summary, code and test
line up cleanly.

---

# B. ARE THE TITLES VERBATIM? — **YES. CLEAN.**

**[MEASURED]** all ten ids and titles, in `compliance.py:2812-3045`, in
`docs/security/threat-model.md:37-46`, and served live on `/v1/compliance`, compared against
the verbatim table in `phase-0-A0-owasp-asi.md` §2:

| ID | Official (PDF) | compliance.py | threat-model.md |
|---|---|---|---|
| ASI01 | Agent Goal Hijack | ✅ | ✅ |
| ASI02 | Tool Misuse and Exploitation | ✅ | ✅ |
| ASI03 | Identity and Privilege Abuse | ✅ | ✅ |
| ASI04 | Agentic Supply Chain Vulnerabilities | ✅ | ✅ |
| ASI05 | Unexpected Code Execution (RCE) | ✅ | ✅ |
| ASI06 | Memory & Context Poisoning | ✅ | ✅ |
| ASI07 | Insecure Inter-Agent Communication | ✅ | ✅ |
| ASI08 | Cascading Failures | ✅ | ✅ |
| ASI09 | Human-Agent Trust Exploitation | ✅ | ✅ |
| ASI10 | Rogue Agents | ✅ | ✅ |

**The `&` vs `and` question is resolved correctly.** Both files use `and` for ASI02/ASI03 —
the form Phase 0 recommended, matching the PDF's Table of Contents and section headings — and
keep the ampersand in ASI06, where the PDF has no variant. Punctuation, capitalisation and the
`(RCE)` suffix all match. Zero divergence found. The substantive ASI06 correction landed, and
`threat-model.md:42` correctly rewrote the mitigation cell under the *poisoning* framing
(adding `GuardStage.MEMORY_WRITE`) rather than leaving retrieval-framed prose under a new
title — which is the check Phase 0 asked for.

**LOW note.** The framework `version` is `"2026"`. Phase 0 §4 cautions: *"if a date is ever
shown, it is published December 2025, 2026 edition — not 'published 2026'."* The UI renders
this string bare under the framework name, where a reader will read it as a year of
publication. `threat-model.md:32` gets it right in prose. Consider `"Version 2026 (published
December 2025)"`.

---

# C. DO THE TOTALS HOLD?

## Arithmetic — **YES [MEASURED]**

Recomputed over `_FRAMEWORKS` and cross-checked against the live endpoint's `coverage`:

```
FRAMEWORKS: 13   TOTAL: 124
{'enforced': 40, 'partial': 60, 'not_implemented': 19, 'not_applicable': 5}
```

`docs/compliance/README.md:24`: **"Total: 124 controls — 40 enforced · 60 partial · 19 not
implemented · 5 not applicable."** Exact match. The delta from 114/37/53 is exactly the ten
new controls (+3 enforced, +7 partial). `test_compliance_readme_totals.py` passes.

## F8 · MEDIUM — the README is declared the authority and does not contain the framework

`docs/compliance/README.md:10-15`:

> This file is the **authority**. `GET /v1/compliance` serves a typed projection of it
> (`backend/src/app/platform/compliance.py`) … **If the three ever disagree,
> `backend/tests/api/test_compliance.py` fails**

**[MEASURED]** the README's section headings:

```
## 6. OWASP Top 10 for LLM Applications (2025)
## 7. OWASP Top 10:2025 (web / API surface)
## 8. MITRE ATLAS
…
## 15. The honest summary
```

There is **no OWASP Agentic section**. `grep -n "ASI" docs/compliance/README.md` returns one
hit — line 481, inside a list of framework names. Phase 4 updated the README's total to
include ten controls the README does not document, and left line 17 unchanged:

> **Twelve frameworks, India first.** … Sections 6–14 are the international frameworks.

Thirteen frameworks; the section numbering was never extended. The stated invariant is false
here, and the reason no test caught it is that `test_compliance_readme_totals.py` pins only
(a) the four state totals and (b) which frameworks are enforced in full — it never asserts
that each framework in `_FRAMEWORKS` has a README section, nor that the prose framework count
equals `len(_FRAMEWORKS)`.

**Recommended fix.** Add the ASI section to the README (renumbering 8–15 → 9–16, or appending
as a new §15 before "The honest summary"), change "Twelve" to "Thirteen" and "6–14" to the new
range. Then extend the totals test with a third claim: every framework id in `_FRAMEWORKS`
must appear as a README heading, and the prose framework count must equal `len(_FRAMEWORKS)`.
That is the assertion that would have caught this.

## F9 · MEDIUM — the endpoint's own OpenAPI description says "Nine frameworks"

`backend/src/app/api/routes_compliance.py:44-47`

```
    Nine frameworks — OWASP LLM Top 10 (2025), OWASP Top 10:2025, MITRE ATLAS, NIST AI
    RMF 1.0, ISO/IEC 42001 Annex A, ISO/IEC 27001:2022 Annex A, the EU AI Act, SOC 2
    Trust Services Criteria, and GDPR/DPDP — each control carrying a four-valued state
```

This docstring is the route's `description` in the served `/openapi.json` and in any API-docs
UI. It was stale before Phase 4 (twelve) and is staler now (thirteen); it omits CERT-In, the
India sectoral layer, OWASP web *and* the new agentic list. Pre-existing, but Phase 4 is the
change that made it wrong by two, and it sits one file away from the edit.

**Recommended fix.** Replace the enumeration with "Thirteen frameworks, India first — see
`docs/compliance/README.md`", so it cannot go stale again.

## F10 · MEDIUM — stale `114` / `twelve` across docs

**[MEASURED]** `grep -rn "114" docs/ web/src/` plus framework-count greps:

| File:line | Text | Rendered? |
|---|---|---|
| `docs/README.md:78` | "**114 controls across 12 frameworks**, every claim resolving to a file, route or test" | markdown + the generated `docs/README.html:288` |
| `docs/security/overview.md:7` | "evidence map (**114 controls**, every claim resolving to a file, route or test)" | markdown + `docs/security/overview.html:134` |
| `web/src/lib/portal.ts:289` | `tooltip: 'Twelve frameworks mapped control by control to a file, route or test'` | **YES — rendered in the DevOps sidebar** |
| `web/src/components/landing/standardsSummary.ts:80` | "all **twelve frameworks** and all **114 mapped controls** stay in `docs/compliance/README.md`" | comment only |
| `web/src/components/landing/StandardsBand.tsx:337` | "out of a hundred and fourteen mapped across **twelve frameworks**" | comment only |
| `web/src/components/compliance/ComplianceView.tsx:213, 265` | "nine published frameworks"; "a flat list of twelve names" | comments only |
| `backend/src/app/api/routes_standards.py:82` | "the right label above a twelve-row grid" | comment; refers to DPDP's 12 rows — **not stale** |

`portal.ts:289` is the one a user sees. The `.html` twins are generated artefacts that will
regenerate from their `.md` sources.

**Recommended fix.** Update `portal.ts:289`, `docs/README.md:78`, `docs/security/overview.md:7`
and regenerate the two `.html` files. The comments are lower priority but
`standardsSummary.ts:80` is ironic — it is the stale hand-typed count inside the module whose
docstring exists to argue against hand-typed counts.

---

# D. THE LIVE ENDPOINT

## Served — **YES, correctly [MEASURED]**

```
$ POST /v1/auth/login {admin/demo}          → 200
$ GET  /v1/compliance  (platform_admin)     → 200, 13 frameworks, 124 controls
    owasp-agentic | OWASP Top 10 for Agentic Applications | 2026 | International | 10 controls
    coverage: {enforced: 3, partial: 7, not_implemented: 0, not_applicable: 0, total: 10}
    ASI01 partial … ASI10 partial  (all ten present, titles verbatim, gaps and evidence intact)
$ GET  /v1/compliance  (northwind.admin)    → 403
$ GET  /v1/compliance  (northwind.client)   → 403
$ GET  /v1/platform/standards (all three)   → 200
```

Framework ordering is correct: `owasp-agentic` sits between `owasp-llm` and `owasp-web`,
inside the `International` jurisdiction group, after the three India frameworks. `scope` text
renders. Evidence arrays serve `kind`/`ref`/`label` for files and tests.

## F11 · INFO — the audit brief's northwind.admin premise cannot hold, and that is by design

`/v1/compliance` is guarded by `require_platform_security_reader` and **[MEASURED]** returns
403 to `northwind.admin`. Separately, `web/src/lib/portal.ts:431` grants the `compliance`
section to the **devops** portal only:

```ts
  devops: ['dashboard','stack','patch','security','compliance','redteam','cache','latency','audit','settings'],
  tenant_admin: ['dashboard','analytics','documents','approvals','governance','roles','forecast','jobs','audit','console','llmops','memory','settings'],
```

`northwind.admin` has no Compliance screen and could not reach the data if it did. This is
intentional and documented (`StandardsBand.tsx`: *"that map lives behind `GET /v1/compliance`,
which platform staff sign in to read"*), and `test_compliance_route_refuses_a_business_role`
pins it. **Not a defect.** Recorded because the brief asked for a check that is not possible;
the correct login for that screen is `devops`.

## Rendered — **YES, by construction**

The browser check could not be completed: the Claude-in-Chrome extension is not connected
("Browser extension is not connected"), so no screenshot was taken. Verified by reading the
render path instead, which is conclusive for this question:

`web/src/components/compliance/ComplianceView.tsx:272-285` groups **whatever the payload
contains** by `framework.jurisdiction`, in server order, with no framework allowlist:

```ts
    for (const framework of data?.frameworks ?? []) {
      const key = framework.jurisdiction || 'International'
      …
```

`web/src/lib/api/platform.ts:332-338` types `ComplianceFramework.id` as a plain `string` (not
a union of known ids), the picker maps over `group.frameworks`, and the header figure is
`COUNT.format(data.frameworks.length)` — derived, not authored. Coverage strips come from
`framework.coverage`. **A new framework id cannot be dropped by this component.** The only
hand-authored artefacts on the screen are the two stale comments in F10 and the stale sidebar
tooltip.

**Residual risk: LOW.** Recommend one manual pass as `devops` on `:3001` to confirm the new
picker row's label does not wrap badly — the agentic framework's `name` is the longest in the
set at 37 characters, and `FrameworkButton` (`ComplianceView.tsx:172-207`) notes a 390px
layout constraint.

---

# E. THE LANDING BAND

## F12 · LOW — the agentic framework will never appear on the landing page, and the brief's premise about derivation is half-true

`StandardsBand.tsx` reads `GET /v1/platform/standards`, and **[MEASURED]** that endpoint does
serve the new framework:

```
owasp-agentic  mark='OWASP Top 10 for Agentic Applications'  enf=3/10  enf_ctrls=['ASI02','ASI03','ASI05']
```

But *which* frameworks the band draws is **not** derived. Two paths reach the page:

1. `completeFrameworks` — derived, requires every applicable control `enforced`. Agentic is
   3/10, so it does not qualify.
2. `SHORTLIST` — **hand-authored**, `standardsSummary.ts:97-104`:

```ts
export const SHORTLIST: readonly ControlGroup[] = [
  { title: 'Record-keeping, transparency, oversight', frameworks: ['eu-ai-act'] },
  { title: 'The LLM attack surface', frameworks: ['owasp-llm'] },
  { title: 'Data-principal rights, India and the EU', frameworks: ['dpdp', 'privacy'] },
]
```

`owasp-agentic` is in neither. **The landing page does not and will not show it.** The module
docstring's own claim — *"a framework it stops serving simply drops out"* — is true for
removal and false for addition. Phase 4 added a framework to two endpoints and to the DevOps
screen, and the public page it is most likely to impress a juror on is unchanged.

Whether that is a defect is an editorial call, and the file argues its case well (the unit is
a control, only `enforced` reaches the page, denominators are printed). But it is a decision
nobody appears to have made for this framework: adding
`{ title: 'The agent attack surface', frameworks: ['owasp-agentic'] }` would print
`ASI02 — Tool Misuse and Exploitation`, `ASI03 — …`, `ASI05 — …` as *three of ten*, which is
exactly the shape the band was designed for — **and would put F1/F3's two contested `enforced`
rows on the public landing page**, which raises the stakes on fixing those first.

## Stale counts on the landing path
Comments only, no rendered number is stale — `FullPicture` takes `shown`, `enforced`,
`frameworks`, `mapped` off the wire. See F10 for `standardsSummary.ts:80` and
`StandardsBand.tsx:337`.

## F13 · LOW — no `_MARKS` entry, so the wordmark cell gets the full title

`backend/src/app/api/routes_standards.py:91-104` has no `"owasp-agentic"` key, so the fallback
fires and `mark` is the full 37-character `"OWASP Top 10 for Agentic Applications"` — every
other mark is a short wordmark (`"OWASP LLM Top 10"`, `"MITRE ATLAS"`, `"SOC 2 TSC"`). The
fallback is deliberate and documented (*"a framework this map has not met yet must still
appear on the page — long label and all — rather than silently vanish"*), so nothing breaks.
The plan specified the entry at `06-compliance-asi-india.md:239`:
`"owasp-agentic": "OWASP Agentic Top 10"`. It was not added.

**Recommended fix.** One line in `_MARKS`. Needed before F12 is acted on.

---

# F. DID ANYTHING REGRESS? — **NO [MEASURED]**

```
$ PYTHONPATH=src:../aegis/src .venv/bin/python -m pytest \
    tests/api/test_compliance.py tests/api/test_not_applicable_is_justified.py \
    tests/api/test_compliance_readme_totals.py tests/api/test_standards.py \
    tests/api/test_governance_docs.py -q

788 passed in 2.86s
```

All twenty-two cited evidence paths resolve on disk, and all ten cited pytest node ids exist:

```
FOUND aegis/tests/redteam/test_stages_and_suites.py::test_each_probe_is_screened_by_the_rail_its_stage_names
FOUND aegis/tests/agent/test_gate_authorises_what_runs.py::test_one_gate_authorises_exactly_the_actions_it_enumerated
FOUND backend/tests/api/test_admin_governance.py::test_tenant_admin_cannot_read_other_tenant
FOUND backend/tests/api/test_supply_chain.py::test_an_audit_that_could_not_run_does_not_pass
FOUND backend/tests/adapter/test_allowlist.py::test_client_cannot_run_admin_tool
FOUND aegis/tests/memory/test_consolidate.py::test_a_poisoned_fact_is_refused_and_audited_rather_than_stored
FOUND aegis/tests/agent/test_team_fanout.py::test_a_subagent_proposal_gates_and_resumes_through_the_existing_path
FOUND aegis/tests/agent/test_self_repair_loop.py::test_an_identical_call_failing_three_times_stops_the_loop
FOUND aegis/tests/governance/test_audit.py::test_the_chain_verifies_and_a_tampered_row_is_caught
FOUND aegis/tests/governance/test_audit.py::test_a_deleted_row_breaks_every_row_after_it
```

`_authorised_calls` exists at `aegis/src/aegis/agent/graph.py:2094`; `resolve_caller` at
`backend/src/app/mcp/server.py:489`. Symbols named in evidence labels are real.

**The gap the suite leaves.** `test_every_evidence_reference_resolves` proves a citation
*exists*. Nothing proves a citation is *about* the claim. That is why F1, F3 and F7 pass a
green suite. The commit message says so itself — *"One citation it did accept was still wrong…
and that was corrected by reading it rather than by the suite catching it."* Three more of the
same class survived the reading.

---

# G. TWO SMALLER ITEMS

## F14 · LOW — the plan's §7.3 test was not written

`06-compliance-asi-india.md:367-368`:

> **Add** a new test that asserts the shipped registry contains no tool whose handler reaches
> `subprocess`/`eval`/`exec` — §7.3.

`db3eb71 --stat` shows no test file touched (`compliance.py`, four docs, two binaries). **[MEASURED]**
no such test exists. Had it been written, it would have made the honest version of ASI05
defensible — as a claim about *this* registry, which is what it is.

## F15 · LOW — the retired hedge kept the parenthetical Phase 0 flagged as wrong

`docs/security/owasp-agentic.md`, the rewritten note ends:

> Where Aegis's own code annotates the older LLM Top 10 (e.g. `LLM02` insecure output
> handling, `LLM06` sensitive-info disclosure), those annotations are kept so the lineage
> stays traceable.

Phase 0 §5 caution 2 called this out explicitly:

> **The parenthetical inside the hedge is itself wrong.** … Under OWASP Top 10 for LLM
> Applications v2.0 (2025) … LLM02 is *Sensitive Information Disclosure*, LLM05 is *Improper
> Output Handling*, and LLM06 is *Excessive Agency*. So the two are **swapped and
> mis-assigned**…

**[MEASURED]** the live endpoint agrees with Phase 0 and not with the doc — `owasp-llm`
serves `LLM02 Sensitive Information Disclosure`, `LLM05 Improper Output Handling`,
`LLM06 Excessive Agency`. The rewrite carried the error through the very edit that touched
those lines. Phase 0 correctly scoped verifying the LLM v2.0 ids out of A0, but the doc now
contradicts the endpoint. Caution 1 (do not leave the eight-row theme table implying ASI
alignment) **was** handled well — the new text says the themes are "organised by subject
rather than by ASI number".

---

# SUMMARY OF RECOMMENDED FIXES, IN ORDER

1. **F1** — ASI05 → `partial`, restore the plan's gap; the tool surface is not closed (MCP peers).
2. **F2** — rewrite ASI07 against the shipped A2A surface; it is currently false against its own host.
3. **F3** — ASI03 → `partial` with the confused-deputy gap; at minimum fix the citation and delete "by swapping the bearer".
4. **F5, F6** — re-measure the two probe counts; fix `README.md:209` in the same pass.
5. **F8** — add the ASI section to the README, fix "Twelve frameworks", and extend `test_compliance_readme_totals.py` so a framework without a section fails.
6. **F4** — the "two enforced, eight partial" claim becomes true once 1 and 3 land.
7. **F9, F10** — stale counts: `routes_compliance.py:44`, `portal.ts:289`, `docs/README.md:78`, `docs/security/overview.md:7`.
8. **F7** — ASI09/ASI10 citations.
9. **F13, F12** — `_MARKS` entry, then decide the landing-band question (after 1 and 3).
10. **F14, F15** — the registry no-exec test; the LLM02/LLM06 parenthetical.

---

*Audit performed 2026-08-27 against `HEAD` = `64072d8` with the stack running on :8110/:3001.
Report only — no repository source was modified. Browser rendering verified by render-path
reading, not by screenshot; the Chrome extension was unavailable.*
