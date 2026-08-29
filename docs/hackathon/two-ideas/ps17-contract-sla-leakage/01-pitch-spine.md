# PS-17 — pitch spine

The narrative is: *"This is not one problem. It is seven. Here is what we built for each."*
Each sub-problem is named for a slide title, and each maps to a stated clause in the brief —
so the pitch doubles as a rubric-compliance checklist.

## The seven sub-problems

### SP-1 — Bitemporal Truth
*Brief §04 bullet 1.*
**Hard truth.** Every fact has two dates: when it was true in the world, and when you found out.
An invoice correction landing in September restates July.
**Failure mode.** You store one timestamp and `UPDATE` in place. The audit trail now says the
system always knew the right answer — a lie — and July's breach report cannot be reproduced.
**Mechanism.** Append-only ledger; every fact carries `(valid_from, valid_to, recorded_at,
superseded_at)`. Public API is one call: `as_of(decision_time, knowledge_time)`.
**Say on stage:** *an audit log is not bitemporality.* An audit log tells you the row changed. It
does not let you query the world as you understood it on 1 April.

### SP-2 — The Amendment Inject
*The graded moment. Decomposed separately below.*
**Hard truth.** An amendment is not a new document. It is a time-indexed patch to the rulebook,
and the events it governs have already happened and already been acted on.

### SP-3 — The Evidence Fog
*Brief §03 bullet 2, §04 bullet 4.*
**Hard truth.** Systems do not disagree by being empty. They disagree by being confidently
different. Ticketing says the outage was 47 minutes; monitoring says 63; the invoice already
credited for 30.
**Failure mode.** Last-writer-wins. The breach determination becomes a coin flip on ingestion
order.
**Mechanism.** Evidence is stored as **claims by a source**, never as facts. A `Fact` is derived,
with a resolution policy (usually the contract names the system of record), a confidence, and an
explicit `CONTESTED` state that **blocks automated action**. On screen: three coloured bars of
different length over one incident, and a red lock on the action button.

### SP-4 — The Silent Entitlement
*Brief §04 bullet 2 — "deadlines, dependencies, authority and expected value".*
**Hard truth.** The money is not lost when the SLA is breached. It is lost when nobody files the
claim inside the window. Most commercial SLAs make the credit *claimable, not automatic*.
**Failure mode.** A beautiful breach detector that recovers nothing, because the deadline was 30
days after the billing period and the alert fired on day 41. Compliant, and still poorer.
**Mechanism — the Entitlement Clock.** Every detected breach instantiates a claim object with its
own contractual deadline parsed from the remedy clause. The workspace prioritises by
**time-to-forfeiture × recoverable value × confidence**. The entitlement clock *is* the
next-best-action scheduler — say so, it maps the sub-problem straight onto the rubric.
**Why this is the leakage insight:** CLM sells obligation tracking ("did we do the thing").
Revenue assurance sells leakage recovery ("did we collect what we're owed"). Different discipline,
different department, different tooling. The seam between them is where the money sits and neither
side's product owns it.

### SP-5 — Provenance vs. Inference
*Brief §04 bullet 5 — the five-way separation is stated verbatim in the brief.*
**Hard truth.** "The SLA threshold is 99.5%" is either a quoted span in a signed PDF or a model's
guess, and a CTO must be able to tell which in one click.
**Design assumption, stated honestly:** extraction is wrong roughly one time in two at useful
recall. On CUAD (510 expert-annotated contracts, 13,101 annotations, 41 clause categories) the
best reported model is DeBERTa-xlarge at 47.8% AUPR, 44.0% precision at 80% recall, **17.8%
precision at 90% recall**.
**Mechanism.** Span-anchored extraction under a four-colour provenance model — RECORDED FACT /
AI INFERENCE / USER INPUT / HUMAN DECISION, plus AUTOMATED ACTION as a fifth. Every inference
carries `document_id` + character offsets and renders as a click-through highlight in the source
PDF. **Nothing tagged INFERENCE crosses an action gate without a human promotion event**, and that
promotion is itself a HUMAN DECISION record.

### SP-6 — Exactly-Once in the Real World
*Brief §04 bullets 3 and 6, verbatim.*
**Hard truth.** Sending a breach notice twice is worse than sending it zero times — and the
failure that makes you retry is precisely the failure that stops you knowing whether the first
one landed.
**Mechanism — the Action Ledger.** Content-addressed idempotency key (`contract_id +
obligation_version_id + event_window + action_type`), two-phase intent→commit, and a
**compensation catalogue** classifying every action as reversible (draft, internal task) or
irreversible (notice served, credit note posted, payment made).
**Pitch value:** idempotency is invisible in a demo, which is exactly why nobody builds it and
exactly why *showing* it — kill the worker mid-action, restart, watch it not double-send — wins
backend points no other team will contest.

### SP-7 — The Autonomy Boundary
*Brief §02 and §05 — the brief itself removes legal interpretation, contractual notice and
material settlement from the machine.*
**Hard truth.** The system's job is to make the human's decision **cheap and defensible**, not to
make it.
**Failure mode.** A team builds an agent that "resolves the dispute end to end" and is marked
down for violating an explicit stated constraint while thinking it is impressing the jury.
**Mechanism.** Five-level autonomy matrix bound to action types, rendered as a live UI panel. See
`06-governance.md`.

### SP-8 (optional) — The Sealed Notice
Load-bearing, not decorative: the counterparty's first challenge after a retroactive amendment is
*"you changed the record after the fact."* A hash chain over the decision ledger with a daily
signed root makes "what we knew on 12 July" cryptographically attestable, with no chain runtime on
the Windows box. See `06-governance.md` for the 1-day and 2-day versions.

## The inject, decomposed

> *"A contract amendment changes an SLA threshold after potential breaches were flagged. The
> system must re-evaluate each event using the correct effective version."*

One sentence, three failures. Most teams see only the first.

| # | Failure | Why teams hit it | What correct looks like |
| --- | --- | --- | --- |
| 1 | **Overwrite** | Re-run everything under the new threshold | Prior conclusions preserved and queryable |
| 2 | **Prospective-only** | Apply from signature date — the *industry-standard default* | Effective date can **precede** signature (`nunc pro tunc`); retroactive is ordinary commercial practice |
| 3 | **No reconciliation** | Re-evaluate correctly, never ask what you already *did* | Credit note posted, notice served, escalation opened — each triaged |

**Failure 2 is worth naming explicitly on stage.** Temporal's `patched()` documentation states
that patching "applies a code change to new Workflow Executions while avoiding disruptive changes
to in-progress Workflow Executions." The correct behaviour for PS-17 is the **inverse** of the
durable-execution industry default. Demonstrating that you know the default and know why it is
wrong here is a strong signal to a CTO panel.

**Failure 3 is the graded moment and the pitch's "oh no" beat.**

> **The line:** *"Re-evaluating the past is the easy half. Reconciling the past with what you
> already did about it is the half that decides whether you get sued."*

It has a legal spine — courts decline retroactive effect where it prejudices intervening rights.

## The incumbent gap — demonstrable, not asserted

- **Icertis's own amendment documentation** covers creating, inheriting and diffing amendment
  attributes, and is **silent** on retroactive effect and on re-evaluating pre-amendment events.
- **Agiloft's December 2025 "enterprise-grade obligation management" launch** — the market
  frontier — is described as extract → assign → deadline → remind → escalate.
- Nobody publicly claims decision **re-derivation**.

The market's frontier is task tracking. That is the sentence that justifies the whole build.

## Objection map

The five hardest questions a CTO will ask, rated honestly.

| # | Question | Rating | Answer |
| --- | --- | --- | --- |
| 1 | "Isn't this just an audit log?" | **Defensible** | An audit log tells you the row changed; it cannot answer "what did we believe on 1 April". Different pattern, different query. Demo the two-axis slider. |
| 2 | "Your LLM hallucinated a threshold and you served a notice on it." | **Defensible** | The LLM never renders a verdict. It emits a typed norm object with span offsets; a deterministic evaluator decides. Unplug the model on stage — verdicts unchanged. |
| 3 | "How is this different from Icertis?" | **Defensible** | Cite their own amendment docs' silence on retroactivity. Frontier is task tracking; we do decision re-derivation. |
| 4 | "Bitemporal databases are 40 years old. What's new?" | **Partially** | Correct, and Capital One holds a granted patent on retroactive recomputation at the storage layer. Novelty is one layer up: re-adjudication of normative verdicts under effective-dated rules + irreversibility triage. Do **not** overclaim — see `../00-decision/open-risks.md` OR-1. |
| 5 | "Show me it doesn't double-send." | **Defensible — if pre-empted** | Only if the chaos moment is built. Kill the worker mid-action on stage. If not built, this question is a loss. |
