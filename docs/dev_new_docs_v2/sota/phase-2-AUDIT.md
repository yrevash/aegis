# Phase 2 audit — the grounded verify loop (`fb61ee3`)

> **Verdict: PASS WITH FINDINGS — 2 blockers, 6 majors, 5 minors.**
>
> The node exists, it is wired, it is on the wire, the tests pass, and the safety
> invariant that mattered most (a retried HIGH-risk write raises a *second* human
> approval) holds under measurement. But the two tiers the commit message leads with
> are not doing what it says. **Tier 2 — "a read-only call that proves the write
> landed" — has no production binding and never fires: every write in the live app
> returns `UNVERIFIED / unverifiable`.** And when tier 2 *is* wired, a round with two
> writes reports `VERIFIED` while checking only the first — which is verbatim the
> defect the commit opens by saying it fixes.
>
> Three of the five new state keys are write-only or never written at all.

**Method.** Source read at `fb61ee3` on branch `docs/wow-pass-plan`; the plan doc
`08-agent-verify-loop.md`; grep sweeps for every read/write of each new state key;
`pytest` over `aegis/tests/agent/test_durable_exactly_once.py`,
`aegis/tests/agent/test_self_repair_loop.py` and all of `backend/tests/agent/`
(23 + 83 passed); nine purpose-built adversarial tests driven through the real
`run_agent` with fake deps (scratch, not committed); and three real runs against the
live stack (backend `:8110`, `northwind.admin`, persona `operations_lead`).
Claims are tagged `[MEASURED]` (I ran it and pasted the output) or `[SOURCE]`
(read off the code at `file:line`).

---

## Scorecard against the seven claims

| # | Claim | Verdict |
|---|---|---|
| 1 | `verify` node, three tiers | **Partly.** Tiers 1 and 3 work. **Tier 2 is unreachable in production** (F-1) and wrong when reachable (F-2). |
| 2 | Refusal is terminal, never retried | **Holds.** `[SOURCE]` `graph.py:1230-1247`, `1345-1352`, `1512-1520`; pinned by `test_a_rail_refusal_stops_the_loop_instead_of_retrying_it`. |
| 3 | Budget 2 → 4, safe because the verifier landed | **Not established.** The verifier does not bound the loop (F-3, F-4); measured cost of a real 4-round run is **$0.132 / 75,248 prompt tokens** for one query that never performed the action asked (F-5). |
| 4 | Five new state keys | **Two of five work.** `transcript` is written and never read (F-6). `repair_iterations` is written and never read (F-4). `loop_deadline_ts` / `loop_budget_left_s` are never written *or* read (F-3). |
| 5 | Oscillation stops at three identical attempts | **Holds for identical args, and is trivially evaded.** `[MEASURED]` F-7. |
| 6 | `repairable` vs `charge` | Code is right; **the test that names this behaviour asserts nothing, and what it claims to assert is the opposite of the implementation** (F-8, blocker for the test suite's credibility). |
| 7 | 14 wire touchpoints + `ReadBack` seam | Wire is complete end to end. **Nothing renders it** (F-9). The seam has no host binding (F-1). |

---

## BLOCKERS

### F-1 — Tier 2 has no production binding. Every write in the live app is `UNVERIFIED`.
**Severity: blocker.** `aegis/src/aegis/agent/deps.py:531`, `backend/src/app/agent/deps.py:429-460`

`read_back_for` defaults to `lambda _name, _args: None` and **`AgentDeps.default()`
never overrides it.** Complete grep of the repo:

```
$ grep -rn "read_back_for" --include="*.py" . | grep -v docs/
aegis/src/aegis/agent/deps.py:531:    read_back_for: ReadBackFn = field(default=lambda _name, _args: None)
aegis/src/aegis/agent/graph.py:1403:            plan = deps.read_back_for(name, args)
```

That is the declaration, the default, and the one call site. There is no
`READ_BACKS` table anywhere, no host wiring, and **no test in the repo exercises
`method == "read-back"`** (`grep -rn "read-back" aegis/tests backend/tests` → zero
hits outside `tests/runs/_stream.py`, a fixture).

`[MEASURED]` — live run, the canonical money-shot demo (HIGH-risk write behind the
human gate, approved via `POST /v1/approvals/{id}/decision`):

```
tool_call     update_request_status {"request_id":"req-000004","status":"resolved"} risk=high
tool_result   ok=true "Status new → resolved"
verification  {"outcome":"UNVERIFIED","method":"unverifiable",
               "reason":"the write reported success and this deployment has no read-only
                         call that could confirm it. Reported as unverified rather than
                         assumed to have worked.","evidence":"","round":2}
reflection    {"done":true,"reason":"goal met: every action succeeded."}
```

A second live run of the LOW-risk write path (`add_case_note`) produced the identical
`UNVERIFIED / unverifiable`. **Tier 2 has never executed on this deployment.**

The commit message's second paragraph — "then a read-only call that proves the write
landed" — and the plan's central promise ("a demo that shows the record actually
changed is not the same artefact as one that shows a spinner") are both
currently unsupported by anything that runs.

This is not hard to fix, which makes it worse: `find_requests` is already registered
`read_only=True, risk=LOW` (`backend/src/app/adapter/tools.py:695-717`) and
`find_requests(text="req-000004")` returns `req-000004 | resolved | ...`, which is
exactly the proof the tier wants.

**Fix.** Add a `READ_BACKS` mapping in `backend/src/app/adapter/tools.py` — at minimum
`update_request_status → ReadBack(tool="find_requests", args={"text": request_id},
expect=f"| {status} |", describe=...)` and `assign_request` — and pass
`read_back_for=_default_read_back_for` in `AgentDeps.default()`. Add one test that
asserts a live verdict with `method == "read-back"`, so this cannot silently regress
to `unverifiable` again.

---

### F-2 — With tier 2 wired, a round with two writes reports `VERIFIED` after checking only the first.
**Severity: blocker (latent behind F-1).** `graph.py:1398-1442`

```python
for row in writes:
    name = str(row.get("tool", ""))
    args = next((c.get("args", {}) for c in calls if c.get("name") == name), {})   # 1400
    plan = deps.read_back_for(name, args)                                          # 1403
    ...
    return verdict("VERIFIED", "read-back", plan.describe, repairable=False, ...)   # 1427
```

Two independent defects in four lines:

1. **Args are matched by tool *name*, not by `call_id`** — even though every result row
   carries `call_id` (`graph.py:1234-1246`). Two calls to the same tool in one round
   both resolve to the **first** call's args.
2. **The loop `return`s on the first write that yields a plan.** Every later write in
   the round is never checked, and the round's single verdict is decided by write #1.

`[MEASURED]` — a round writing R1 (which lands) and R2 (which silently does not):

```
verdict:     [('VERIFIED', 'read-back', 'read R1 back', 'R1 status=resolved')]
reflection:  [(True, 'goal met: every action succeeded.')]
read-backs performed: [{'request_id': 'R1'}]     # R2 was never read back
```

This is the commit message's own opening sentence, reproduced *by the new verifier*:
"A tool that updated the wrong record and returned success was 'goal met'."

It is reachable today under fan-out: `run_team` aggregates every lane's proposals into
one `tool_calls` list and one `act` round (`_gated_calls`, `graph.py:1111-1126`), so
three lanes proposing three writes is exactly the multi-write round this mishandles.

**Fix.** Match by `call_id`:
`args = next((c.get("args", {}) for c in calls if c.get("id") == row.get("call_id")), {})`.
Then check **every** write and fold the verdicts — any `FAILED` wins, all-`VERIFIED`
is `VERIFIED`, a mix of `VERIFIED` and no-plan is `UNVERIFIED` — instead of returning
on the first.

---

## MAJOR

### F-3 — Two of the five specified bounds were not built; `loop_deadline_ts` is a declaration with no code behind it.
**Severity: major.** `aegis/src/aegis/agent/state.py:184-189`

The plan specifies five independent bounds
(`08-agent-verify-loop.md:495-505`). Implemented: **three**.

| # | Bound | Status |
|---|---|---|
| 1 | Iteration cap (`max_plan_iterations`) | **Built** — `graph.py:1505,1549` |
| 2 | Repair cap (`repair_iterations`) | **Counted, never read** — see F-4 |
| 3 | Oscillation | **Built** — `graph.py:1365`, and evadable (F-7) |
| 4 | Per-run spend | **Not built** at loop level |
| 5 | Wall clock (`loop_deadline_ts`) | **Not built at all** |

`[SOURCE]` complete grep — every occurrence in the repo is a declaration or a doc:

```
$ grep -rn "loop_deadline_ts\|loop_budget_left_s" --include="*.py" --include="*.ts" .
aegis/src/aegis/agent/state.py:188:    loop_deadline_ts: float
aegis/src/aegis/agent/state.py:189:    loop_budget_left_s: float
docs/dev_new_docs_v2/sota/08-agent-verify-loop.md:432,433,442,443,477,480,482,503
```

Neither key is written by `guard_input`, re-stamped by `approval`, or read anywhere.
There is no `loop_wall_clock_s` setting. The 19-line comment above them in `state.py`
describing the two writers, the superstep-safety argument and the "park trap"
documents a mechanism that does not exist. A reader auditing termination would
reasonably conclude a wall-clock bound is in force. It is not.

Partial mitigation (worth stating): a gateway-level `BudgetExceededError` aborts the
whole run at `aegis/agent/orchestrator.py:396`, so spend is not literally unbounded —
but that is a tenant cap, not a loop bound, and it fires at the tenant's monthly
ceiling, not this run's.

**Fix.** Either build them (write `loop_deadline_ts` in `guard_input`, re-stamp in
`approval`, check it in `_route_reflect`; add `agent.loop_wall_clock_s`) or **delete
the two keys and the comment**. Leaving a documented-but-absent safety bound in the
state type is worse than not having it, because it reads as implemented.

---

### F-4 — `repair_iterations` is a counter that counts and does nothing. The commit's "separate budgets" claim is unsupported.
**Severity: major.** `graph.py:1324`, `state.py:176-179`

The commit message: *"Repair rounds are counted separately so a lookup no longer pays
for the write's mistake."* There is one budget, and `repair_iterations` is not it.

`[SOURCE]` — every occurrence in the repo:

```
$ grep -rn "repair_iterations" --include="*.py" --include="*.ts" .
aegis/tests/agent/test_self_repair_loop.py:266:  # (a docstring)
aegis/src/aegis/agent/graph.py:1281:            # (a docstring)
aegis/src/aegis/agent/graph.py:1324:  "repair_iterations": 1 if (billed and outcome != "VERIFIED") else 0,
aegis/src/aegis/agent/state.py:178:    repair_iterations: Annotated[int, operator.add]
```

**One write, zero reads.** `reflect` gates only on
`budget_left = iteration < budget` where `iteration = state["plan_iterations"]`
(`graph.py:1467-1505`). The `charge` parameter, the `billed` variable
(`graph.py:1321`) and the `repairable`/`charge` split exist entirely to maintain a
number nothing consults.

The *behavioural* half of the fix is real and does work: a `GATHERED` read-only round
returns `repairable=True`, which is what lets the loop reach round two and write
(`[MEASURED]`, live run 2: `GATHERED` → `add_case_note` → `UNVERIFIED`, goal reached).
But that comes from `repairable`, not from any budget separation. The arithmetic
argument in the commit message ("at 2 the canonical demo was unretryable") is
therefore an argument for raising the iteration cap, not for a second budget — and the
second budget was not built.

**Fix.** Read it: in `reflect`, add
`repairs_left = state.get("repair_iterations", 0) < config.max_repair_iterations`
and `and repairs_left` into `will_retry`, with a distinct `reason` string. Or delete
the key, `charge`, and `billed`, and say plainly in the commit trail that the
separation is behavioural, not budgetary.

---

### F-5 — The raised budget is measurably expensive, and the loop can spend all of it without ever performing the requested action.
**Severity: major.** `[MEASURED]`, live stack.

Query: *"Find the oldest open request and add a case note saying I reviewed it today."*

```
tool_call events: 12      verification events: 4      planning rounds: 4/4
run_finished: {"status":"completed","prompt_tokens":75248,
               "completion_tokens":2605,"cost_usd":0.13227273}
```

Round-by-round (abridged):

```
r1  find_requests {"status":"open",...,"limit":1}   → FAILED (enum: 'open' invalid)
r2  5 × find_requests, limit 5   (2 ok, 3 "no match")→ FAILED
r3  5 × find_requests, limit 25  (2 ok, 3 "no match")→ FAILED
r4  find_requests {"status":"open",...,"limit":25}  → FAILED (same enum error as r1)
reflection r4: {"done":false,"will_retry":false,
                "reason":"iteration budget exhausted (4/4)"}
run_finished: status "completed"
```

Four observations, each independently worth fixing:

* **$0.13 and 75k prompt tokens for one query, and the case note was never added.**
  The run consumed the whole raised budget on lookups.
* **Round 4 repeated round 1's exact mistake** (`status: "open"`), because `plan`
  feeds back only the *previous* round's results (`graph.py:1018-1053`). This is
  precisely what `transcript` was added to fix — and `transcript` is never read (F-6).
* **`ok=False` for an empty result set drives the loop.** `find_requests` returns
  `ok=False, "No service requests match that filter"` for a legitimate empty match
  (`run1` rounds 2 and 3). Tier 1's `failed = [r for r in results if not r.get("ok")]`
  (`graph.py:1353`) treats one empty lookup among five successful ones as a repairable
  round failure. An empty result is not a failure and should not buy a repair round.
* **`run_finished.status` is `completed`** while the last `reflection` says
  `done: false` and the goal was not met. The wire tells two stories.

**Fix.** (a) Wire `transcript` into `plan` (F-6). (b) Make an empty result set `ok=True`
with an empty summary in `find_requests`, or have tier 1 ignore rows whose failure is
"no match". (c) Consider a per-run tool-call cap in addition to the round cap — twelve
executions in one turn is not what "four rounds" sounds like.

---

### F-6 — `transcript` is written by `verify` and read by nobody. The defect its own comment describes is not fixed.
**Severity: major.** `graph.py:1326-1328`, `state.py:161-171`

`state.py:161-166` says:

> "`transcript` is the fix for a real defect: `plan` rebuilt its prompt from scratch
> every round and stringified prior outcomes into the user turn, so the model never
> saw its own past as conversation. This accumulates the EXECUTED history as real
> assistant + `tool`-role turns."

`[SOURCE]` — `plan` (`graph.py:972-1085`) still builds `messages` from scratch as
`[system, user]` (`graph.py:1054-1062`) and still stringifies **only**
`state["tool_results"]` — the last round — into the user turn (`graph.py:1018-1023`).
`grep -rn "transcript" aegis/src/aegis/agent/` shows the only non-comment occurrence
outside `state.py` is the write at `graph.py:1326`.

So the accumulator grows one row per round into every checkpoint, costs storage, and
buys nothing. F-5's round-4 repeat is the measured consequence.

**Fix.** In `plan`, splice `state.get("transcript")` into `messages` between the system
turn and the user turn on a re-plan — or delete the key and the comment.

---

### F-7 — Oscillation detection is defeated by any argument that varies; a varying planner burns the full budget every time.
**Severity: major.** `graph.py:1248-1255`, `1365`

The fingerprint is `sha256(name \x00 canonical-json(args))`. Any differing argument —
including a cosmetic one like `limit` — produces a different hash, so `seen.count(f)`
never reaches 2.

`[MEASURED]`, fake deps, `run_tool` always fails, planner varies `request_id` each round:

```
BUDGET: 4
tool_calls: 4        planner rounds: 4
verifications: [('FAILED','deterministic',1), ('FAILED',...,2),
                ('FAILED',...,3), ('FAILED',...,4)]
reflections:   [(1,True),(2,True),(3,True),(4,False)]
```

Four rounds, four failures, oscillation never fires. And it is not hypothetical:
live `run1` (F-5) shows the model changing `limit: 1 → 25` between two otherwise
identical failing calls, which is enough to evade it.

Same story for a read-only planner: `[MEASURED]` four consecutive `GATHERED` rounds,
four tool calls, budget-exhausted — the loop went round four times gathering and never
acted, and `repair_iterations` stayed at 0 throughout.

**Is that acceptable?** It terminates, so it is not a live cost bug in the runaway
sense. But it means **the effective bound in the common case is the iteration cap
alone**, and that cap was just doubled. The commit's safety argument — "progress
detection … is now the bound that fires first" — is true only for a planner that
repeats itself byte-for-byte. It was not true in the very first real run I drove.

**Fix.** Fingerprint the `(tool, failure-class)` pair alongside the args — three
failures of the same tool with the same *error* is stuck regardless of args. Or add a
per-tool call cap for the run.

---

### F-8 — The test named for the commit's headline bugfix asserts nothing, and what it claims to assert is false.
**Severity: major.** `aegis/tests/agent/test_self_repair_loop.py:254-281`

```python
async def test_a_read_only_round_does_not_spend_the_repair_budget(make_deps):
    """...``verify`` returns ``repair_iterations: 0`` for a round that only read."""
    deps = make_deps(propose_tool=True, high_risk=False)      # tool_read_only defaults to False
    ...
    for check in checks:
        if check["outcome"] == "GATHERED":
            assert check["repairable"] is False
```

Two problems:

1. **It never asserts `repair_iterations`** — the thing its name, docstring and the
   commit message are all about. `repair_iterations` never appears in any test.
2. **The loop body never executes, and would fail if it did.** `make_deps` leaves
   `tool_read_only` at its `lambda _name: False` default, so no round is ever
   classified read-only.

`[MEASURED]` — the repo fixture as-is:

```
verdicts under the repo fixture: [('UNVERIFIED', False)]      # no GATHERED, loop is dead code
```

`[MEASURED]` — the same test with the one missing line, `tool_read_only=lambda _n: True`:

```
verdicts: [('GATHERED', True), ('GATHERED', True), ('GATHERED', True), ('GATHERED', True)]
AssertionError: the repo test asserts GATHERED.repairable is False;
                the implementation emits repairable=True
```

The implementation is right (`graph.py:1393` — `GATHERED` on a successful read is
`repairable=True, charge=False`, which is the whole point). **The test asserts the
opposite of the code and passes only because it never runs.** This is the single
guardrail against F-4/F-6-style rot, and it is inert.

**Fix.** Pass `tool_read_only=lambda n: n == "find_requests"` (or equivalent) into the
fixture, assert `GATHERED` is emitted, assert `repairable is True`, and assert the
`repair_iterations` delta once F-4 gives it a reader.

Related, minor: `test_iteration_budget_caps_planning_rounds` (line 116) no longer tests
what it is named — its assertions are now about oscillation stopping at three.

---

### F-9 — Nothing renders the verification event. The demo claim is unsupported in the UI.
**Severity: major.** `web/src/state/runReducer.ts:105,184,251,317-326`

`[SOURCE]` — complete frontend grep:

```
$ grep -rn "verifications" web/src --include="*.ts" --include="*.tsx"
web/src/state/runReducer.ts:105:  verifications: Verification[]
web/src/state/runReducer.ts:184:  verifications: [],
web/src/state/runReducer.ts:251:        verifications: [],
web/src/state/runReducer.ts:325:        verifications: [...state.verifications, event],
```

Four hits, all inside the reducer. **No component reads `state.verifications`** —
`TraceTab.tsx` and `HarnessView.tsx` both read `reflections` and neither reads
`verifications`. There is **no web test** referencing the event either.

So the outcome, the tier that decided, the reason and the evidence — the entire payload
the phase exists to produce — reach the browser's memory and stop there. The `verify`
node does appear in `web/src/config/graphTopology.json` so the node lights up, but the verdict text
is invisible. The reducer's own comment ("This branch exists because the `default` case
silently discards what it does not recognise, which is how `reflection` stayed
invisible") describes the trap it has half-escaped: the event is now kept instead of
discarded, and still not shown.

**Fix.** Render the verdict in `TraceTab.tsx` next to each `reflection` — outcome badge
(`VERIFIED` / `FAILED` / `BLOCKED` / `OSCILLATING` / `GATHERED` / `UNVERIFIED`), the
method as a subtitle, `reason` as the line, `evidence` in a disclosure. Add one web
test asserting a `verification` event reaches the DOM.

---

## MINOR

### F-10 — Read-back evidence bypasses the output rail and reaches the wire verbatim.
**Severity: minor today, major once F-1 is fixed.** `graph.py:1410-1441`

Trace the two evidence paths:

* **Tier 1** (`graph.py:1374`, `1381`): `evidence` is `failed[0]["summary"]`, and that
  summary was reassigned by `screen_tool_result` in `act` (`graph.py:1218-1221`).
  **Screened. Correct.**
* **Tier 2** (`graph.py:1418`, `1433`, `1440`): `summary = str(proof.summary)` straight
  off `deps.run_tool`, sliced to 400 chars, written to the event. **`screen_tool_result`
  is never called on it.**

`[MEASURED]` — read-back returns a record containing an Aadhaar and a card number, with
an output rail installed that redacts both:

```
evidence on the wire: 'status=resolved. Contact: aadhaar 1234-5678-9012, card 4111111111111111'
AssertionError: raw read-back text reached the wire unscreened
```

The rail never saw it. The same 400 chars are the record a read-back is *most* likely to
contain — a customer row. Currently unreachable only because of F-1.

**Fix.** Route `proof.summary` through `screen_tool_result(..., tool_name=plan.tool,
deps=deps, writer=writer)` before it becomes `evidence`, exactly as `act` does.

### F-11 — The read-back executes a tool with no `tool_call`/`tool_result` event and no OTel span.
**Severity: minor.** `graph.py:1410-1421`

`verify` calls `deps.run_tool` directly. Unlike `act` (`graph.py:1187-1215`) it emits no
`tool_call`, no `tool_result`, and opens no `SpanKind.TOOL` span — but it *does* write an
audit row (`ctx.audit=record_audit` in `_default_run_tool`). A glass-box console that
claims to show every tool execution will be missing one, and the span tree will
disagree with the audit log.

**Fix.** Emit `tool_call` / `tool_result` (flagged, e.g. `verification: true`) and open a
TOOL span, or state explicitly on the trace that verification calls are out-of-band.

### F-12 — `ReadBack`'s "below the gate threshold" invariant is documented but not enforced.
**Severity: minor.** `deps.py:182-183` vs `graph.py:1406`

The docstring: *"Must be read-only AND below the gate threshold, so verification can
never itself become an action or raise an approval."* The code checks only
`deps.tool_read_only(plan.tool)`. A tenant tightening `agent.gate_min_risk` to `LOW`
(permitted — `MergeRule.TIGHTEN_ONLY`, `settings/spec.py:450`) makes every tool gated,
and the read-back would still execute ungated with `approver=None`.

Credit where due, and I tried to break both: the `tool_read_only` guard **is** fail-safe
(production default returns `False` for an unregistered name → `continue`, `deps.py:576-589`),
and the persona allowlist **is** enforced inside `run_tool` before any side effect
(`backend/src/app/agent/deps.py:773-778`), so a misconfigured `READ_BACKS` cannot execute
a write or escape the persona. Only the gate-threshold half of the stated invariant is
missing.

**Fix.** Add `if risk_at_least(deps.tool_risk(plan.tool), config.gate_min_risk): continue`
beside line 1406.

### F-13 — Fingerprints are recorded for calls that were never executed.
**Severity: minor.** `graph.py:1291`, `1325`

`fingerprints = [_fingerprint(c) for c in calls]` is computed from `state["tool_calls"]`
(every proposal) and returned by **every** verdict branch — including
`if not results: → GATHERED` (`graph.py:1332-1339`). But `act` executes
`_authorised_calls(state)` (`graph.py:2073-2087`), which on a gated run is only the
approved ids.

In practice the two sets coincide, because `approval` always enumerates and returns
*every* call (`graph.py:1170-1173`) — there is no partial approval. The one divergence
is a resume from a checkpoint written before `approved_call_ids` existed: `act` runs
nothing, and `verify` still records a fingerprint for each un-run call, inflating the
oscillation count toward a premature `OSCILLATING` stop.

**Fix.** Derive fingerprints from the rows that ran:
`fingerprints = [_fingerprint(c) for c in calls if c.get("id") in {r["call_id"] for r in results}]`.

### F-14 — `reflect`'s reason string contradicts the verdict it was given.
**Severity: minor, but demo-facing.** `graph.py:1496-1558`

Two reason strings the console shows are wrong for the verdict that produced them:

* `UNVERIFIED` → `done = True` → `reason = "goal met: every action succeeded."`
  `[MEASURED]`, live run 3 — the verification event says *"Reported as unverified rather
  than assumed to have worked"* and the very next event says *"goal met"*. On today's
  deployment this is **every write** (F-1), so the one honest string the phase added is
  immediately contradicted by the one the UI actually renders.
* `GATHERED` (a clean, successful lookup) → `reason = "an action failed or was
  insufficient; re-planning (round 1/4)."` `[MEASURED]`, live runs 2 and 3.

**Fix.** Derive the reason from `checked["outcome"]`: `UNVERIFIED` →
"the action reported success; nothing here could confirm it"; `GATHERED` → "looked up
what it needed; acting next".

### F-15 — 353 lines of unrelated ML work rode in on this commit.
**Severity: minor (traceability).**

`fb61ee3` also contains `aegis/src/aegis/ml/model.py` (+310), `backend/src/app/ml/__init__.py`
(+59) and a new `backend/tests/ml/test_foreign_artifact_refused.py` (+79) — SHAP
`_MemberExplainer`, `_member_family`, `_unwrap_member`, TreeExplainer/LinearExplainer
routing. None of it is mentioned anywhere in a commit message that otherwise documents
its reasoning in exhaustive detail, and none of it relates to the verify loop. A future
bisect over the agent loop will land on the explainability path.

**Fix.** Nothing to undo; note it, and split next time.

---

## What I tried to break and could not

Stated so the "no findings" areas are not mistaken for unexamined ones.

* **A retried HIGH-risk write raises a SECOND approval.** `[MEASURED]` — planner proposes
  a HIGH-risk write in each of three rounds, every one fails:
  `approval_required count: 3` / `executions: 3` — one interrupt per execution.
  The `interrupt` at `graph.py:1157` re-raises on every re-entry to the node; the raised
  budget does **not** let one approval cover a retry. **Invariant holds.**
* **One approval authorising N executions.** By design, and structurally sound:
  `approval` returns exactly the ids it rendered (`graph.py:1172`) and `_authorised_calls`
  (`graph.py:2073-2087`) filters `act` to that set, defaulting to *nothing* when the key
  is absent. The interrupt payload enumerates every action, in risk order, in both the
  structured `actions` list and the prose `rationale`. **Holds.**
* **Test suites at budget 4.** `aegis/tests/agent/test_durable_exactly_once.py` +
  `test_self_repair_loop.py`: **23 passed**. All of `backend/tests/agent/`: **83 passed**.
  `test_durable_exactly_once.py` pins its own budget explicitly (lines 407, 456), so it is
  insulated from the default change. **Holds.**
* **Read-back becoming an action / escaping the persona.** Blocked twice over — see F-12.
* **Rail refusal retried.** Terminal at three independent points; one attempt only,
  confirmed by the repo's own (genuine, non-vacuous) test.
* **Non-termination.** I could not construct a run that fails to terminate. Every path
  I drove ended at `run_finished`. The iteration cap is real and does fire. The finding
  is the *cost* of the widened bound (F-5/F-7), not unboundedness.

---

## Recommended order

1. **F-1** — wire `READ_BACKS` + `read_back_for`. Without it the phase's headline is
   inert and the demo shows `unverifiable` on the money shot.
2. **F-2** — match by `call_id`, check every write. Do this *with* F-1; shipping F-1
   alone activates a verifier that reports `VERIFIED` on unchecked writes.
3. **F-8** — repair the vacuous test, then **F-10** (screen the evidence) before F-1
   reaches production data.
4. **F-9** — render it. **F-3 / F-4 / F-6** — build the three dead keys or delete them;
   either is honest, the current state is not.
5. **F-5 / F-7** — the empty-result-as-failure fix is a one-liner in the adapter and
   removes most of the measured waste.
