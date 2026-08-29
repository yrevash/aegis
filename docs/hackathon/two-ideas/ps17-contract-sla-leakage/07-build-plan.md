# PS-17 — seven-day build plan

**29 Aug → 4 Sep 2026.** Assumes three or more engineers (see the gate in
`../00-decision/recommendation.md`). Roles below assume BE1, BE2, FE, and a floating
pitch/data owner — collapse as needed.

## Day 0 — before anything (half a day, parallel)

| Owner | Task | Why it is day 0 |
| --- | --- | --- |
| BE1 | **Spike: does PG18 allow a partial unique index carrying `WITHOUT OVERLAPS`?** | OR-3. Determines the schema. Two-table design sidesteps it either way |
| BE1 | Install PostgreSQL 18 + pgmq + DBOS Transact on the actual Windows demo box | Every stack claim in `02-architecture.md` must be true on *that* machine |
| FE | `visx` spike: render a static 2-D bitemporal plane with fake data | De-risks the day-4 gate on day 0 |
| Pitch | **Read the five unread Capital One siblings** (17/516,329/338/340/345/436) | OR-1. Blocks the patent slide |
| Data | Synthetic corpus design: contracts, amendments, SLA records, invoices, service events, credits, notices, renewals, owner actions | Everything downstream needs it |

**The corpus must include, by construction:** one amendment whose *effective date precedes its
signature date*; at least one flagged breach that predates that effective date and must **not**
change; and at least one already-executed irreversible action (notice served) on a conclusion the
amendment vacates. Without those three, the demo has no inject.

## Days 1–2 — the spine

- **BE1:** bitemporal ledger. Append-only assertion log + current-belief projection.
  `as_of(decision_time, knowledge_time)` as the single public call.
- **BE2:** action ledger + idempotency keys + compensation catalogue (reversible / compensable /
  irreversible). Durable execution via DBOS.
- **FE:** screens 1–2 (worklist, obligation tree with span-anchored highlights).
- **Data:** corpus generated and loaded.

## Day 3 — extraction and evidence

- **BE1:** LLM extraction → **typed norm object** with document id + character offsets. The
  evaluator is deterministic and separate. Build the "unplug the model" path deliberately — it is
  a demo beat.
- **BE2:** claim/counter-claim evidence model, `CONTESTED` state, contested-evidence action gate.
- **FE:** screen 5 (evidence reconciliation — three bars, red lock).
- **Pitch:** first full narrative pass using `01-pitch-spine.md`.

## Day 4 — ⚠ THE GATE

- **FE:** the bitemporal as-of plane rendering **real data**.
- **BE1:** provenance-directed re-evaluation — valid-time delta + lineage-bounded subset.
- **BE2:** Merkle log day-1 version (RFC 9162, `DecisionRecord` leaf, Ed25519 STH, tamper button).

> **GATE CHECK, end of day 4.** If the plane is not rendering real data: cut it, promote the
> conclusion diff to hero, demote the plane to a 1-D knowledge-time slider. Decide this *on day 4*,
> not day 6. The fallback is in `03-experience.md` and costs ~1.5 points, not the recommendation.

## Day 5 — the inject

- **BE1:** effective-version resolver + decision re-derivation + **action reconciliation**
  (irreversibility triage). This is the graded moment; it gets a whole day.
- **FE:** the conclusion diff / re-adjudication ledger, with the grey locked row.
- **BE2:** OTel spans over the reasoning loop; Jaeger or Phoenix wired; conclusion → span → source
  bytes click-path.
- **Verify:** re-evaluation count is *bounded* (2 of 1,140, not 1,140 of 1,140) **and** the
  pre-effective-date breach does not change. If either fails, the inject demo is wrong.

## Day 6 — governance, resilience, polish

- **BE2:** autonomy ladder enforced as a **type property**; the "Send notice" refusal path.
- **BE1:** chaos path — kill a worker mid-action, restart, prove no double-send. Make it a
  one-keystroke stage move.
- **FE:** screen 6–7 (action ledger + autonomy panel, span waterfall). Light theme, clarity pass.
- **Optional if ahead:** Merkle day-2 (consistency proofs ← highest value; second witness;
  `SLATerms_v1/v2` on Anvil from the **zip**, not `foundryup`).
- **Pitch:** deck built; objection map rehearsed against `01-pitch-spine.md`.

## Day 7 — freeze and rehearse

- **Morning:** feature freeze. Nothing new. Bug-fix and seed-data determinism only.
- **Rehearse the five-minute storyboard end to end at least four times**, including the two
  interactive moments a judge might drive (the "Send notice" refusal, the worker kill).
- **Pre-flight:** demo box cold-boot test; pre-fetch any RFC 3161 token; verify Anvil runs from the
  unzipped binary; confirm no step needs network.
- **Prepare the Q&A answers** for the five objections, especially #4 (prior art) — with the
  Capital One family read and the honest line from `04-differentiation.md`.

## Cut order under time pressure

Last thing standing on the left:

`bitemporal ledger + inject re-adjudication + action reconciliation`
← `conclusion diff`
← `provenance types + span-anchored extraction`
← `idempotency chaos demo`
← `Merkle inclusion proofs`
← `bitemporal plane`
← `autonomy refusal demo`
← `consistency proofs`
← `SLA burn-down`
← `smart contract`
← `RFC 3161`

## Definition of done

The demo is ready when a judge can, unassisted:

1. Click any number and land on the character span in the signed PDF that produced it.
2. Drop in the amendment and watch a **bounded** re-evaluation, with the pre-effective-date breach
   correctly unchanged.
3. See one row refuse to change because the notice already went out.
4. Drag knowledge-time back and reproduce what the system believed last month.
5. Press "Send notice" and watch the system refuse, with the reason shown.
6. Kill a worker and watch it not double-send.
