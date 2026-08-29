# PS-17 — explainability, observability, autonomy and cryptographic provenance

Scored 35/40 against PS-04's 21/40. Sub-lanes: explainability 9–7, observability 9–5, autonomy
9–6, crypto-provenance 8–3.

## 1. The provenance type system

The brief demands, verbatim, "explicit separation of recorded fact, AI inference, user input,
automated action and human decision." Build it as a **first-class type system**, not a metadata
column, and map it to **W3C PROV-O** so it is a standard rather than a schema you invented.

| Type | Rule |
| --- | --- |
| `RecordedFact` | Sourced from a system of record, with content hash |
| `AIInference` | Carries `document_id` + character offsets, model id and version |
| `UserInput` | Attributed to a named human |
| `AutomatedAction` | Emitted by the system; always logged, never silent |
| `HumanDecision` | Typed, with an `authority_scope` |

**What the type system enables — the authority rule:**

> An `AutomatedAction` of type `notice_send` is **unconstructible** without a `HumanDecision`
> whose `authority_scope ⊇ notice`. Nothing tagged `AIInference` crosses an action gate without a
> human promotion event, and that promotion is itself a `HumanDecision` record.

This is an enforced type property, not a policy document. That distinction is the whole point.

## 2. Regulatory position — get this right

**`SR 11-7` no longer exists.** Superseded 17 Apr 2026 by **SR 26-2 / OCC Bulletin 2026-13**,
which also retires SR 21-8 and rescinds OCC Bulletin 2011-12. Every other team will cite the dead
guidance.

Footnote 3, verbatim: *"Generative AI and agentic AI models… are not within the scope of this
guidance… the principles described in this guidance apply to traditional statistical and
quantitative models and non-generative, non-agentic AI models."*

**Three things to say correctly:**

1. **PS-17 must NOT claim SR 26-2 exemption.** That guidance applies to banking organizations and
   never reached contract operations. Claiming it is an unforced error.
2. **The deterministic carve-out relocates burden, it does not remove it.** Escaping model-risk
   scope by pushing judgement into prose is detectable **scope arbitrage**. Close it with a cheap,
   demonstrable invariant: **the generative layer may restate, never originate** — enforced by a
   "no new numbers" test on generated narrative.
3. **PS-17 has no EU AI Act Annex III classification.** PS-04 does (creditworthiness, Annex III
   5(b)). This asymmetry is in PS-17's favour and worth one line — but note 5(b) says *natural
   persons*, and PS-04 is commercial lending, so do not overstate it.

**Where the residual risk actually lands, in both problems:** *where text becomes rules.* SLA
threshold extraction here; covenant extraction there. One gate, both problems. Design assumption
stays honest: CUAD's best reported model is **44% precision at 80% recall, 17.8% at 90%**.

## 3. OpenTelemetry over the reasoning loop

Build to the **OpenTelemetry GenAI semantic conventions** (`gen_ai.*` attributes) so the span
vocabulary is a standard.

**The click-path a judge follows — this is the demo:**

> conclusion on screen → the spans that produced it → the source bytes → the cryptographic proof

Every verdict links to its span tree; every span carries the provenance types from §1; every leaf
span points at a document id and character offsets.

**Windows-friendly backends, verified against release artefacts:**

| Backend | Status |
| --- | --- |
| **Jaeger** | Ships `windows-amd64.zip` ✅ |
| **Phoenix** | Pure-Python wheel ✅ |
| **Langfuse** | ❌ six services |

## 4. The autonomy ladder

**Pick a framework, do not invent one.** The spine is **Sheridan & Verplank (1978) 10 levels**,
reproduced as Table I of Parasuraman, Sheridan & Wickens (2000) — put the verbatim scale on a
slide as an image. Governance framing from **NIST AI RMF 1.0** (`MAP 3.5`, `MEASURE 2.9`); legal
hook from **EU AI Act Art. 14** (14(3) oversight proportionate to "risks, **autonomy level**, and
context"; 14(4)(d) disregard/override/reverse; 14(4)(e) a stop button reaching "a safe state").

Our five levels are a **collapse of Sheridan 1–7**, with 8–10 excluded **by construction**:

| Level | Name | Sheridan | Meaning |
| --- | --- | --- | --- |
| **A0** | Observe | 1 | Raw evidence, no computed conclusion |
| **A1** | Analyse | 2–3 | Computes and narrows; presents candidates. Human selects |
| **A2** | Recommend | 4 | Asserts one conclusion with confidence and counterfactual. Human decides |
| **A3** | Act on approval | 5 | Executes only on an explicit recorded `HumanDecision` |
| **A4** | Act with veto window, then notify | 6–7 | Bounded veto, then necessarily informs. **Reversible, idempotent, low-materiality only** |
| **H** | Human-owned | — | Off the ladder. May prepare, may never execute |

> **The truncation is the argument.** Sheridan 8 ("informs the human only if asked"), 9 ("only if
> it, the computer, decides to") and 10 ("ignoring the human") are structurally unavailable: every
> action at every level emits a typed `AutomatedAction` and an OTel span. **There is no code path
> that acts silently.** Say this out loud — it converts an absence into a designed control.

### The mapping

| Process step | Level | Why |
| --- | --- | --- |
| Contract / amendment ingested, versioned | A4 | Reversible, idempotent, internal |
| Extract obligation — informational | A4 | Low materiality |
| Extract obligation — material (credit/notice/termination-bearing) | **A2** | 44% precision @ 80% recall. An unverified inference cannot found a credit claim |
| Map owners | A4 | Internal routing |
| Monitor evidence | A4 | Pure ingestion |
| Flag *potential* breach | **A2** | A flag is an internal hypothesis. Never A3+ — asserting a breach *is* interpretation |
| Raise contradiction / staleness / duplication | A4 | Raising doubt is always safe |
| Compute service credit (as arithmetic) | A4 | Deterministic over a versioned rule; replayable |
| Assert the credit as *owed* | **A2** | Material commercial quantum |
| Draft notice / credit memo | A4 to produce an inert artefact; A3 to attach for approval | Drafting has no external effect |
| **Send contractual notice** | **H** | Brief: notice remains human-owned |
| **Agree material settlement** | **H** | Brief: settlement remains human-owned |
| **Resolve legal interpretation** | **H** | Brief: interpretation remains human-owned |
| **Re-evaluate on amendment (the inject)** | **A4** | See the asymmetry rule |

### The asymmetry rule — the sharpest single idea in this lane

> **Autonomy to *retract* is safe. Autonomy to *assert* is not.**

Retraction, reopening, raising uncertainty and demoting an action's own authority all move the
system toward *more* human involvement, so they can run fully autonomously at A4 with no
over-reach risk. Assertion, quantification and external action move toward *less* human
involvement and are capped at A2/A3/H.

This resolves the inject cleanly: **the amendment triggers fully autonomous re-evaluation (A4)
because re-evaluation only ever withdraws conclusions and lowers authority.** Nothing is
auto-asserted under the new threshold; every affected case returns to A2 for a human. A CTO will
recognise this as a real safety invariant rather than a policy table.

### The demo move nobody else will do

Put a **"Send notice"** button on screen. Invite a judge to press it. **The system refuses**, and
shows why — the authority-check span, the missing `HumanDecision`, the unverified inference in the
closure.

> A system that visibly refuses to act is a far better trust demo than a system that acts.

The brief handed you a principled, externally-specified gate. You did not choose where to stop for
convenience — the problem statement did. Build it as an enforced type property and claim EU AI Act
Art. 14(4)(d)/(e) conformance *shape*.

## 5. Cryptographic provenance — load-bearing here, decorative in PS-04

**The uncomfortable truth first:** in a single-party system, a hash chain the operator both writes
and verifies proves nothing to a sceptic. It becomes load-bearing under three conditions:

1. Two parties who do not fully trust each other need the same record.
2. An external witness co-signs the tree head, so rewriting requires collusion.
3. The dispute is with your own past self — an auditor asking "prove this decision was made on
   exactly these inputs."

**PS-17 satisfies (1) by construction** — customer and supplier are adversarial about whether a
breach occurred and which SLA version governed it — **and (3) is its explicit brief requirement.**

Reinforcing this: SLA credits are **ASC 606 variable consideration** → a revenue input → in scope
for ITGC / AS 2201. The chain is a **control**, not a gimmick.

### The construction

The Merkle leaf is not a log line. It is a canonicalised `DecisionRecord` covering the **evidence
closure + rule version + model version**:

```json
{ "decision_id": "...", "action": "assert_breach | compute_credit | prepare_notice",
  "evidence_closure": [{"assertion_id":"...","type":"RecordedFact","content_sha256":"..."}],
  "rule_id": "SLA-UPTIME", "rule_version": "v1", "rule_effective_from": "2026-01-01",
  "model_id": "...", "model_version": "...", "prompt_hash": "...",
  "autonomy_level_requested": "A4", "autonomy_level_granted": "A2",
  "actor": "system|user:...", "decided_at": "..." }
```

`leaf = SHA256(0x00 || canonical_json(DecisionRecord))`.

Build to **RFC 9162 (Certificate Transparency v2)** leaf/node prefixes rather than a naive
`sha256(prev || payload)` chain. It gives real inclusion **and consistency** proofs, and lets you
say *"we implemented the Certificate Transparency Merkle structure"* instead of *"we made a hash
chain."*

**The inject is where it pays off:**

- **Inclusion proof** on a pre-amendment decision: this conclusion *was* computed under
  `rule_version=v1`, and that record has not changed.
- **Consistency proof** between `STH@t₁` and `STH@t₂`: the pre-amendment tree is a **prefix** of
  the post-amendment tree. Nothing was rewritten; v1 conclusions were **retracted by appending**,
  never edited away.

That is the difference between *"we handle amendments"* and *"we can prove we handled
amendments."*

### Smart contracts — the pun finally has a referent

An SLA service-credit schedule **is** a deterministic function: measured uptime → credit
percentage. Encode it as `SLATerms.creditBps(uint256 uptimeBps) → uint256` at a version-pinned
address. The amendment deploys `SLATerms_v2`; the effective-version resolver selects the address
by event date; **the number on screen is read back from the chain**, not computed by the app.

Legal backing: the **Law Commission of England and Wales, *Smart legal contracts: Advice to
Government* (25 Nov 2021)** concluded the existing legal framework "is clearly able to facilitate
and support the use of smart legal contracts" without statutory reform.

**State the caveat on stage.** Running on Anvil — an ephemeral dev chain — makes this a
*demonstration of the representation*, not a production settlement rail. Frame it as *"the
commercial term compiled to a versioned, independently-executable artefact."* The value is
determinism and version-pinning, not decentralisation. Overclaiming here is the fastest way to
lose a CTO jury.

### What runs on bare Windows — verified

| Technology | Status |
| --- | --- |
| Hand-rolled RFC 9162 Merkle log (Python + SQLite) | ✅ stdlib `hashlib`; `cryptography` ships Windows wheels for Ed25519 |
| `pymerkle` 6.1.0 | ✅ pure-Python wheel |
| **Anvil (Foundry)** | ✅ **via `foundry_v1.8.1_win32_amd64.zip` only** — `foundryup` needs Git Bash/WSL, *not* PowerShell |
| `py-evm` / `eth-tester` in-process EVM | ✅ pure-Python, MIT — the fallback if Anvil misbehaves |
| RFC 3161 timestamping | ⚠️ needs network to a TSA at demo time — pre-fetch a token |
| immudb | ⚠️ no release binaries since v1.10.0; BSL-licensed |
| Trillian | ❌ maintenance mode, multi-process, needs MySQL |
| AWS QLDB | ❌ discontinued — design reference only |
| A real chain node | ❌ do not attempt |

### Day-1 and day-2 versions, with a cut order

**Day 1 — the load-bearing minimum. Ship this or ship nothing.** (~1 dev-day)
Append-only Merkle log in SQLite with RFC 9162 hashing (~150 lines, hand-rolled — better claim
story, one less dependency). Leaf = canonical `DecisionRecord`. Ed25519 Signed Tree Head every *N*
appends. Two endpoints: `GET /proof/{decision_id}`, `POST /verify`. One UI panel: green
**"Verified — decided on exactly these inputs"**, plus a **"Tamper"** button that mutates an
evidence row and turns it red with the failing node highlighted.

**Day 2 — what makes the inject land.**
**Consistency proofs** across tree heads (highest-value single item — it proves the inject was
handled honestly) · a **second witness process** co-signing tree heads, converting "trust the
operator" into "collude with two" · `SLATerms_v1/v2` on Anvil · RFC 3161 timestamps (optional,
network-dependent).

**Cut order — last thing standing on the left:**
`inclusion proofs + Merkle log` ← `consistency proofs` ← `second witness` ← `smart contract` ←
`RFC 3161`
