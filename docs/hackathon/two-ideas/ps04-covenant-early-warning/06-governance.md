# PS-04 — explainability, autonomy and governance

Scored 21/40 against PS-17's 35/40. But this is the lane where PS-04's **regulatory grounding** is
strongest, and where getting one citation right beats every other team in the room.

## 1. `SR 11-7` no longer exists — and its replacement disclaims what you are building

**Superseded 17 Apr 2026 by SR 26-2 / OCC Bulletin 2026-13**, which also retires SR 21-8 and
rescinds OCC Bulletin 2011-12. Verified from both the Fed PDF and the OCC.

**Footnote 3, verbatim:**
> *"Generative AI and agentic AI models… are not within the scope of this guidance… the principles
> described in this guidance apply to traditional statistical and quantitative models and
> non-generative, non-agentic AI models."*

Every other team will cite SR 11-7. **The winning line is the gap, not the compliance.**

### The three zones this creates, and the architecture it dictates

| Zone | Under SR 26-2 | Your components |
| --- | --- | --- |
| **Deterministic** | Excluded from "model" by definition | Ratio arithmetic, covenant AST evaluation, headroom tests |
| **Statistical / non-generative AI** | **In scope** — full model-risk burden | The hazard model, calibration, driver attribution |
| **Generative** | Out of scope of the guidance | Narrative assembly only |

**The architecture this dictates:** *the statistical model produces the prediction and carries the
model-risk burden; the LLM is confined to assembling narrative over already-computed numbers, and
never produces the risk figure itself.*

### The trap: scope arbitrage

Pushing judgement into prose to escape model-risk scope is **detectable scope arbitrage**, and a
CTO on a bank board will spot it.

**Close it with a cheap, enforced, demonstrable invariant:**

> **The generative layer may restate, never originate** — checked by a **"no new numbers" test** on
> generated narrative. Every numeral in the LLM's output must appear in the computed input set.

Ship the check, show it failing on a deliberately tampered prompt, and the objection is dead.

### Where the residual risk actually lands

**Where text becomes rules** — covenant extraction here, SLA threshold extraction in PS-17. One
gate, both problems. This is PS-04's **one true provenance gate**, and it is why covenant
extraction sits at A2 in the ladder below.

## 2. EU AI Act — PS-04 is named, PS-17 is not

**Annex III 5(b)** names creditworthiness assessment as a high-risk use case. PS-17 has no
equivalent classification.

**The nuance that keeps this honest:** 5(b) says ***natural persons***, and PS-04 is **commercial**
lending. So the classification is adjacent rather than certain. Do not overclaim in either
direction — say precisely this, and a finance-literate judge will credit the precision.

**Art. 14 is the hook to build to regardless:** 14(3) oversight proportionate to "risks,
**autonomy level**, and context"; 14(4)(d) the human can "disregard, override or reverse the
output"; 14(4)(e) a stop that brings the system "to a halt in a safe state."

**Other binding stack to cite:** EBA §54(c) backtesting and §54(d) "control mechanisms, model
overrides and escalation procedures"; IFRS 9 forward-looking ECL and SICR staging; RBI FREE-AI.

## 3. Explainability techniques — and what to say about SHAP

**SHAP is suspect for correlated financial ratios**, and its failure modes are documented. Leading
with a SHAP waterfall in front of a technical judge reads as a default library call.

**Better answers, in order:**
1. **Counterfactual explanation** — *"if receivable days revert to 52, this alert clears."*
   Actionable, verifiable, and it is what an RM actually needs.
2. **Calibration as explanation** — reliability diagrams in the product, not an appendix.
3. **The full line-item trail** through the covenant AST — the part a judge can check by hand.
4. SHAP, *with its limitations stated out loud.*

**The deliverable is a reason to act that survives a lawyer.** Under **UCC §1-309**, a party with
an at-will acceleration/insecurity right "has power to do so only if that party in good faith
believes that the prospect of payment or performance is impaired." **An unexplained model output is
not a good-faith belief** — which is the actual reason bank EWS programmes underdeliver, and the
reason the Evidence Dossier (SP-6) is the product rather than the score.

## 4. Observability

Build to the **OpenTelemetry GenAI semantic conventions** (`gen_ai.*`). Backends verified for bare
Windows: **Jaeger** (`windows-amd64.zip`) ✅, **Phoenix** (pure-Python wheel) ✅, **Langfuse** ❌
(six services).

**Structural weakness, stated honestly:** PS-04's compute is a nightly batch with a shallow call
graph. A trace view over it is less impressive than PS-17's — there is simply less structure to
show. Scored 5/10 against PS-17's 9/10. Do not over-invest here; spend the time on the dossier.

## 5. The autonomy ladder

Same spine as PS-17 — a collapse of **Sheridan & Verplank (1978) levels 1–7**, with 8–10 excluded
by construction because every action emits a typed record and a span. There is no code path that
acts silently. Governance framing from **NIST AI RMF 1.0**; legal hook from **EU AI Act Art. 14**.

| Level | Name | Meaning |
| --- | --- | --- |
| A0 | Observe | Raw evidence, no computed conclusion |
| A1 | Analyse | Computes and narrows; presents candidates |
| A2 | Recommend | Asserts one conclusion with confidence and counterfactual; human decides |
| A3 | Act on approval | Executes only on a recorded human decision |
| A4 | Act with veto window, then notify | Reversible, idempotent, low-materiality only |
| H | Human-owned | May prepare, may never execute |

### The mapping

| Action | Level | Why |
| --- | --- | --- |
| Borrower & covenant intake | A4 | Reversible ingestion |
| **Extract covenant definition, threshold, testing frequency, exceptions** | **A2** | A wrong threshold poisons everything downstream, silently. **PS-04's one true provenance gate** — requires human confirmation with the source clause visible |
| Compute financial ratios | A4 | Deterministic arithmetic; outside SR 26-2's "model" definition |
| Deterministic covenant test | A4 | Fully replayable, no judgement |
| Signal monitoring / ingest | A4 | Ingestion |
| **Score breach probability at 30/60/90** | **A1–A2** | A2 when calibrated and in validated range; **A1 when out-of-distribution** — present a range, not a point estimate. EBA §54(d) requires overrides and escalation |
| Identify drivers | A2 | Explanation is a recommendation |
| **Rank portfolio** | **A2** | A ranking allocates human attention and must be reviewable and overridable |
| Raise alert / place on watch list | A4 | Internal, reversible, and *explicitly required* by EBA §§270–272. Safe — it raises human involvement |
| **Recommend intervention** | **A2** | System proposes; credit officer decides |
| **Contact borrower** | **H** | EBA §273: contact "should have regard to their individual circumstances." An automated deterioration email to a corporate borrower is a relationship and potentially a legal event |
| **Reclassify facility / IFRS 9 stage transfer** | **H** (A3 at most, internal grades only) | A Stage 1→2 migration converts 12-month ECL to lifetime ECL — a provisioning and financial-reporting judgement. Never automatic |
| Escalate to head of risk / management body | A4 | Required by EBA §272; raises involvement |
| Maintain auditable history | A4 | Logging |

**Note the shape: PS-04's ladder is bimodal.** Everything is either A4 (deterministic, reversible)
or A2/H (judgement). Very little sits at A3, because credit risk has few *reversible external
actions* — you either compute internally, or you touch a customer relationship or the balance
sheet. Saying this out loud demonstrates you derived the ladder rather than copied one.

**The asymmetry rule still applies and is worth stating:** *autonomy to retract is safe; autonomy
to assert is not.* Raising an alert, widening uncertainty and escalating all increase human
involvement, so they run at A4 safely.

## 6. Cryptographic provenance — build the event store, skip the Merkle tree, say so

**Load-bearing? No.** Single-party setting: the bank owns the model, the data, the log, and hires
the auditor. Tamper-evidence against yourself is satisfied by an immutable, versioned event store —
which PS-04 needs regardless.

**The one honest use:** bind a **decision record** (evidence set + covenant version + model version
+ calibration snapshot) so an SR 26-2 outcomes-analysis review or an EBA §54(c) backtest can replay
a historical alert exactly. **Worth building as an event store. Not as a Merkle tree, and not
worth a slide.**

**Smart contracts: no.** Covenant thresholds as on-chain conditions fail on the **oracle problem** —
the inputs are private, off-chain and unverifiable by the chain, so the on-chain condition is only
as trustworthy as whoever posted the input, which returns you to trusting the bank. And no bank
will put borrower leverage ratios on a ledger.

**The best move is to say this out loud:**

> *"We evaluated a hash-chained ledger for covenant decisions and concluded it would be decoration
> — the adversary model doesn't support it. Here is the immutable event store that does the actual
> work."*

**Restraint reads as judgement to a CTO panel, and it inoculates you against the "why no
blockchain?" question.**
