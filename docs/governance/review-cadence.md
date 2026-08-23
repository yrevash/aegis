# Review cadence

- **Owner:** Platform owner (`platform_admin`).
- **Reviewed:** this document is reviewed with the annual cycle below, on the same date as
  the AI policy.
- **Satisfies:** NIST AI RMF **GOVERN** 1.3 and 1.5 (risk-management processes are
  periodically reviewed, and mechanisms exist to keep them current).

> **A cadence nobody performs is worse than none**, because it converts an honest absence
> into a false claim. What follows is sized for a **two-person team**: one calendar event
> per quarter, none of them longer than an hour, and every one of them producing either a
> diff or a dated line saying "reviewed, no change". The off-cycle triggers in §3 are the
> half that actually keeps these documents true — a scheduled review catches drift, a
> trigger catches breakage.

---

## 1. The scheduled cycle

| Artefact | Period | Reviewer | Done when |
|---|---|---|---|
| [`ai-policy.md`](ai-policy.md) | **Every 6 months** (Feb / Aug) | Platform owner | Every clause in §2 and §5 still names a mechanism that exists and still behaves as described. |
| [`accountable-roles.md`](accountable-roles.md) | **Quarterly** (Feb / May / Aug / Nov) | Platform owner | `ROLE_SECTIONS` and the `require_*` guards match §1 and §2 of the register; §4 still describes who really holds each role. |
| [`incident-response.md`](incident-response.md) | **Every 6 months** (Feb / Aug), **plus after every incident** | `devops` | Every signal in §2 still exists, and every containment lever in §4 has been confirmed to work at least in a dev deployment. |
| [`context-and-impact.md`](context-and-impact.md) | **Every 6 months** (Feb / Aug) | Platform owner with `ai_team` | The affected-party list and the harm table still match what the deployed adapter actually processes. |
| [`review-cadence.md`](review-cadence.md) — this file | **Every 6 months** (Feb / Aug) | Platform owner | The periods below are the ones actually being kept, and §4 records it. |
| [`incidents/`](incidents/README.md) | **Quarterly**, with the register above | `devops` | Every open follow-up has an owner and a date. |
| `docs/compliance/README.md` and its projection | **Continuously**, by CI | — | `backend/tests/api/test_compliance.py` resolves every evidence reference on every run; a stale claim fails the suite. No calendar entry needed, and that is the model the rest of this table is trying to approximate. |

**One hour, four times a year.** February and August are the two full passes (all five
documents); May and November are the short ones (the role register and the incident
register only). If a scheduled review is missed, it is recorded as missed rather than
back-dated.

---

## 2. What a review actually consists of

Not a re-read. Three checks, in this order:

1. **Run the tests.** `backend/tests/api/test_governance_docs.py` resolves every repository
   path these documents cite against the real filesystem and checks that each document still
   carries the specific commitments its compliance control claims. If it passes, no cited
   mechanism has been renamed or deleted since the last review.
2. **Diff the mechanisms.** `git log` since the last review date over `aegis/src/aegis/settings/spec.py`,
   `backend/src/app/api/routes.py`, `web/src/lib/portal.ts`, `aegis/src/aegis/guardrails/`,
   `backend/src/app/data/approvals.py` and `aegis/src/aegis/gateway/`. Those six paths carry
   every claim in the AI policy and the role register. A change in any of them is read
   against the document that describes it.
3. **Write the outcome.** Either a diff to the document, or one line at the bottom of this
   file's log in §4. A review that produces neither did not happen.

---

## 3. Off-cycle triggers — the ones that matter more than the calendar

Any of these starts a review of the named documents **within five working days**, regardless
of when the last one was:

| Trigger | Review |
|---|---|
| **A domain swap** — the adapter is retargeted (`SKILL.md`, `backend/src/app/adapter/README.md`) | [`context-and-impact.md`](context-and-impact.md) **first, before the swap ships**, and then the AI policy. A new domain means new affected people and new harms; the old impact assessment is not merely stale, it is about someone else. |
| **A change to the default gate tier, the platform prompt floor, or a `TIGHTEN_ONLY` merge rule** | AI policy §2–§4. These are the clauses that make "a tenant may tighten, never loosen" true. |
| **A new or changed model provider, or a new region for the gateway** | AI policy §5, and the residency inventory. |
| **A new external MCP peer or tool** | AI policy §3, and the impact assessment's third-party harm. |
| **A new role, a new portal section, or a changed `require_*` guard** | Role register §1–§2. |
| **Any S1 or S2 incident** | Incident-response plan, plus whichever document the post-incident review found to be wrong. |
| **A control changing state in the compliance table** | The document that cites it. A row moving *out* of `enforced` is the urgent direction. |
| **A regulatory date** — the DPDP obligations binding 13 May 2027 | All five documents, and the compliance map. |

---

## 4. Review log

One line per completed review. Empty until the first one, and left visibly empty rather
than seeded with a fabricated entry.

| Date | Artefacts reviewed | Reviewer | Outcome |
|---|---|---|---|
| 2026-08-23 | All five, at authoring | Platform owner | Initial version. |
