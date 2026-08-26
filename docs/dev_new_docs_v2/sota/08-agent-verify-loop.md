# 08 — The agent must act, observe, verify, and try again

> **Source of every claim here.** Each fact is marked `[SOURCE] path:line` (read in this
> repo at commit `5750eaf`, branch `docs/teaching-modules`), `[DOC] url` (read on the
> web), or `[UNVERIFIED]` (a design assertion or an inference that nothing in the repo
> or the literature settles).
>
> **There is no `[MEASURED]` mark in this document, and that is deliberate.** Writing
> this plan involved no server run, no `pytest` invocation and no browser session. Every
> claim about behaviour is a trace *read off the code*, not a trace *observed*. The
> Verification section at the end exists precisely to convert the load-bearing ones into
> measurements before anybody builds on them.

---

## 1. The owner is wrong about the fact and right about the experience

The belief is "there is no loop." There is one, it is the only cycle in the graph, and
it has a passing test.

**The graph has 17 executable nodes** — `NODE_LABELS` **[SOURCE]**
`aegis/src/aegis/agent/graph.py:102-120` — and **exactly one cycle**:

```
plan → (tool_calls?) → gate → [approval] → act → reflect → plan
```

wired at **[SOURCE]** `graph.py:1598-1621`: `plan` branches to `gate` or `generate`
(1598-1602), `gate` routes via `_route_gate` (1603-1607), `approval` routes on
`approved` (1608-1612), `act → reflect` is a plain edge (1616), and `reflect` routes via
`_route_reflect` back to `plan` or on to `generate` (1617-1621). `_route_reflect` itself
is at **[SOURCE]** `graph.py:234-245`.

The test is `aegis/tests/agent/test_self_repair_loop.py` — a failed first action produces
two `tool_call`s and two `reflection`s **[SOURCE]** `test_self_repair_loop.py:58-89`.

So why has nobody seen it work? Four reasons, all verified.

### (a) On the canonical demo the budget is already spent when the write fails

`max_plan_iterations` defaults to `2` in three places that agree:
`AgentConfig.max_plan_iterations: int = 2` **[SOURCE]** `aegis/src/aegis/agent/deps.py:358`;
the catalogue entry `agent.max_plan_iterations`, `default=2`, `bounds=(1, 10)`,
`merge=MergeRule.TIGHTEN_ONLY`, `stricter=Strictness.LOWER` **[SOURCE]**
`aegis/src/aegis/settings/spec.py:499-511`; and a contract test that pins it
**[SOURCE]** `backend/tests/agent/test_p0_autonomy_config.py:22`.

Now trace the demo. The desk adapter registers `find_requests` as `RiskLevel.LOW`,
`read_only=True` and `update_request_status` as `RiskLevel.HIGH` **[SOURCE]**
`backend/src/app/adapter/tools.py:695-733`. The write tools' descriptions all carry
`_ID_RULE` — *"The request_id must be an id that find_requests returned — call it first"*
**[SOURCE]** `tools.py:686-691`. So the planner is instructed to read before it writes,
and the trace is forced:

| Round | `plan_iterations` after `plan` | Proposed | `reflect` sees | Route |
|---|---|---|---|---|
| 1 | 1 | `find_requests` (LOW, read-only) | `acted == []` → `done=False`; `budget_left = 1 < 2` → **True** | → `plan` |
| 2 | 2 | `update_request_status` (HIGH) | write **fails** → `done=False`; `budget_left = 2 < 2` → **False** | → `generate` |

The arithmetic is `budget_left = iteration < budget` **[SOURCE]** `graph.py:1261`, with
`iteration = state.get("plan_iterations", 0)` **[SOURCE]** `graph.py:1243` and
`budget = config.max_plan_iterations` **[SOURCE]** `graph.py:1244`.

**Confirmed: on the canonical demo, a failed write can never be retried.** The budget
buys the read. Round 2 is the last round the loop is allowed, whatever happens in it.
The one thing the owner wants to see on stage — *the write failed, so it tried something
else* — is arithmetically unreachable at the shipped default.

### (b) The judge is mechanical and reads nothing back

**[SOURCE]** `graph.py:1259-1260`:

```python
acted = [r for r in results if not deps.tool_read_only(str(r.get("tool", "")))]
done = bool(results) and all(r["ok"] for r in results) and bool(acted)
```

`ok` is whatever the tool said about itself, forwarded from `ToolOutcome.ok` **[SOURCE]**
`graph.py:1200-1201`. **Nothing in the graph reads the record back.** A tool that
returns `ok=True` having changed nothing is indistinguishable from one that succeeded.

There is a second, quieter defect in the same line. `act` collapses two different
outcomes into one boolean: `ok = bool(ok) and allowed` **[SOURCE]** `graph.py:1222`,
where `allowed` is the §5.7 tool-result injection rail's verdict from
`screen_tool_result` **[SOURCE]** `graph.py:1218-1222`, `aegis/src/aegis/agent/rails.py:41`.
So *"the write failed"* and *"the write's output was blocked by a guardrail"* arrive at
`reflect` as the same `ok=False`. Today that is harmless because the loop cannot retry
anyway. **The moment the budget is raised it stops being harmless**: a rail block would
be retried, re-executing the same call to re-produce the same blocked output, burning
budget on a round that cannot possibly succeed. Any plan that raises the budget must fix
this first.

### (c) `plan` rebuilds the prompt from scratch and stringifies the past

`plan` constructs `messages` fresh every round — `[system, user]` and nothing else
**[SOURCE]** `graph.py:1052-1061`. The previous round's outcomes are folded into the
*user* string as prose:

```python
attempts = "\n".join(f"- {r['summary']} ({'ok' if r['ok'] else 'FAILED'})" for r in prior)
```

**[SOURCE]** `graph.py:1017-1019`. There is no assistant turn carrying the tool call and
no `tool`-role turn carrying its result. This is not an oversight; it is documented as a
deliberate constraint. `messages` is *"a per-planning-round scratch buffer, rebuilt from
scratch each time plan runs — not a transcript accumulated across nodes"* **[SOURCE]**
`aegis/src/aegis/agent/state.py:148-153`, and `tool_results` is *"replaced wholesale by
act"* because *"accumulating it would make reflect re-see an already-repaired failure and
burn the iteration budget"* **[SOURCE]** `state.py:162-166`. `tool_results:
list[dict[str, Any]]` carries no reducer **[SOURCE]** `state.py:138`.

The consequence: the model never sees its own action as an action. It sees a bullet list
about one. That is the difference between a chat completion and a ReAct trajectory.

### (d) Team runs are excluded from the loop entirely

Twice, deliberately. At the router: `_route_reflect` returns `"generate"` unconditionally
for a team run **[SOURCE]** `graph.py:241-244`. And inside the node: `will_retry = False`
if `_is_team_run(state)` **[SOURCE]** `graph.py:1270-1276`, with the reason *"team run:
the synthesis is the answer; the self-repair loop does not re-plan a fan-out."*
**[SOURCE]** `graph.py:1278-1282`. Confirmed.

### The loop the owner is describing already exists — in the wrong lane

`aegis/src/aegis/agent/subagent.py::_loop` **[SOURCE]** `subagent.py:510-637` is a real
ReAct loop:

* it appends a genuine assistant turn (`subagent.py:575-580`) and genuine `tool`-role
  turns via `_tool_message` (`subagent.py:705`, and appended at `:583`, `:608`, `:636`);
* it terminates when the model emits no tool calls — `if not calls: result.findings = …;
  return` **[SOURCE]** `subagent.py:570-573`;
* it is hard-capped at `spec.max_steps` **[SOURCE]** `subagent.py:547`, default `4`
  **[SOURCE]** `subagent.py:153`.

And it may not execute anything consequential. `_partition_calls` splits calls into
executable / proposed / refused, and a call at or above `gate_min_risk` is *proposed*,
not executed — the lane is told *"'X' is a {risk}-risk action. It has been proposed for
human approval and will be executed by the main graph if approved. Continue without its
result."* **[SOURCE]** `subagent.py:596-612`. Confirmed: **the good loop runs where the
high-risk write cannot, and the lane where the write happens has the bad loop.**

---

## 2. The design stance: an external verifier, not a self-critique node

The obvious move is a "critique" node that asks the model whether its own answer was
good. **Do not build it.** The evidence is unusually clean for this field.

The primary result is *Large Language Models Cannot Self-Correct Reasoning Yet*
(Huang, Chen et al., Google DeepMind + UIUC, ICLR 2024, submitted 2023-10-03, revised
2024-03-14) **[DOC]** <https://arxiv.org/abs/2310.01798>. Its abstract, read directly:
*"LLMs struggle to self-correct their responses without external feedback, and at times,
their performance even degrades after self-correction"*, where *intrinsic self-correction*
is *"an LLM attempt[ing] to correct its initial responses based solely on its inherent
capabilities, without the crutch of external feedback."* **[DOC]** same URL.

The 2026 follow-up position is that the field has moved. *Recursive Self-Improvement in
AI: From Bounded Self-Refinement to Autonomous Research Loops* (arXiv 2607.07663v1)
**[DOC]** <https://arxiv.org/html/2607.07663v1> states: *"absent external feedback, LLMs
largely cannot self-correct reasoning, and naive self-correction can make answers
worse"*, and — the sentence that decides this design — *"almost every new system in our
corpus grounds its critique in an external signal (execution, retrieval, a detector, a
solver), and 'intrinsic self-correction' papers have become rare."* It characterises the
shift as *"the field quietly moved from closed-loop self-critique to human-on-the-loop
verified refinement — a retreat from autonomy that improved reliability."* **[DOC]** same
URL.

Two further 2026 papers point the same way and are worth citing but were **not read in
full**, only located: *Beyond Output Critique: Self-Correction via Task Distillation*
(2026-02-03) **[DOC]** <https://arxiv.org/pdf/2602.00871> — title and date verified by
fetch, abstract text not extractable from the PDF; and *AutoPyVerifier: Learning Compact
Executable Verifiers for Large Language Model Outputs* **[DOC]**
<https://arxiv.org/pdf/2604.22937> — located via search only. **[UNVERIFIED]** for their
contents.

### What follows for Aegis

The external signal is already sitting in the tool registry. `find_requests` is
`read_only=True`, `RiskLevel.LOW`, `idempotent=True` **[SOURCE]** `tools.py:695-717`, and
its `text` filter is documented as *"Pass a known id here to confirm that request
exists."* **[SOURCE]** `tools.py:229-235`. Its summary is *"one line per matching request
— id, status, priority, category, assignee, age against SLA, title"* **[SOURCE]**
`tools.py:392-395`.

So after `update_request_status(request_id="R-104", status="resolved")` the agent can
call `find_requests(text="R-104", limit=1)` and **read the status back out of the record
it just claims to have changed.** That is execution feedback. It is grounded in the
store, not in the model's opinion of itself. It is also the thing that plays on stage:
"it wrote, then it looked, then it saw the write had not landed, and it went again" is a
demonstration; "it thought about whether it did a good job" is not.

**Design rule, stated once and enforced throughout section 4:** the model is allowed to
*interpret* evidence it did not produce. It is never allowed to *be* the evidence.

---

## 3. The one-line change that unlocks the demo, and why it is not enough on its own

Raise the platform default `max_plan_iterations` from `2` to `4`.

**Why 4.** With a `verify` node the verification does not consume a planning round — it
runs inside the round, after `act`. So the demo needs: R1 read, R2 write (verify fails),
R3 corrected write (verify passes). Three. Four leaves exactly one round of slack, which
is the difference between a demo that survives one surprise and one that does not.

**Why a tenant still cannot make it worse.** `agent.max_plan_iterations` is
`MergeRule.TIGHTEN_ONLY` with `stricter=Strictness.LOWER` **[SOURCE]** `spec.py:504-506`.
The fold is one expression — `winner = strictest(spec, candidate, value)` — and *"the
platform layer is always one of the arguments, so the fold cannot descend below it"*
**[SOURCE]** `aegis/src/aegis/settings/resolver.py:202-206`. `strictest` for
`Strictness.LOWER` takes the minimum **[SOURCE]** `spec.py:419` and is pinned by
`test_lower_is_stricter_picks_the_lower_value` **[SOURCE]**
`aegis/tests/settings/test_spec.py:213-217`. A live test already proves the exact
scenario — a tenant writes `4`, the platform then tightens to `2`, and the resolver
returns `(2, "platform")` while the tenant's row survives untouched **[SOURCE]**
`aegis/tests/settings/test_live.py:120-169`.

So raising the platform default **raises a ceiling only**. A tenant may still pin
themselves to `1` (single linear pass) and cannot pin themselves to `10` unless a
*platform* admin writes it — the platform layer may move in either direction because *"the
platform is what the floor is"* **[SOURCE]** `test_live.py:128-131`. `bounds=(1, 10)`
stays as it is **[SOURCE]** `spec.py:507`, pinned by **[SOURCE]**
`aegis/tests/settings/test_spec.py:71-72`.

### Files this one change touches

| File:line | Change |
|---|---|
| `aegis/src/aegis/settings/spec.py:502` | `default=2` → `default=4` |
| `aegis/src/aegis/agent/deps.py:358` | `max_plan_iterations: int = 2` → `= 4` |
| `aegis/src/aegis/agent/deps.py:321-324` | docstring: "the default 2 allows one re-plan" → the new arithmetic |
| `aegis/src/aegis/agent/harness.py:67-72` | the knob's description string repeats "the default 2" |
| `backend/tests/agent/test_p0_autonomy_config.py:22` | `assert cfg.max_plan_iterations == 2` → `== 4` |
| `aegis/tests/agent/test_self_repair_loop.py:126` | same assertion inside `test_iteration_budget_caps_planning_rounds`; the test's whole shape (2 rounds → cap) must be re-derived for 4 |
| `backend/tests/agent/test_self_repair_loop.py:142` | the backend mirror of the same test |
| `aegis/src/aegis/security/posture.py:206,212,220` | three LLM10 strings that quote the cap; they read it from config so they stay true, but the *claim* they make gets weaker as the number rises — see below |
| `backend/src/app/platform/risk_map.py:201-219` | `AA-08` residual likelihood `1` is justified by "hard cap ⇒ termination by construction"; still true, and now leaning on more bounds than one |

**The honest cost.** Raising the cap makes the security posture's unbounded-consumption
claim *quantitatively* weaker: `Loop hard-capped at max_plan_iterations=4` bounds twice
the spend that `=2` did. The posture text is generated from the live config so it will
not lie **[SOURCE]** `posture.py:162, 206`, but the argument for `AA-08`'s residual
likelihood of `1` should stop resting on one number. Section 5 gives it five.

**And on its own it does not fix anything.** Raising the budget with the judge of §1(b)
unchanged buys extra rounds in which a tool that self-reports `ok=True` still ends the
loop, and a rail-blocked result is now retried forever. **The budget raise is only safe
after the verifier lands.** Ship them together.

---

## 4. The `verify` node

### 4.1 Position

```
act → verify → reflect → (plan | generate)
```

Edit **[SOURCE]** `graph.py:1616` from `builder.add_edge("act", "reflect")` to
`add_edge("act", "verify")` + `add_edge("verify", "reflect")`. `reflect`'s conditional
edges (`graph.py:1617-1621`) are untouched.

**Why not fold it into `reflect`.** `reflect` owns the routing decision and emits the
`reflection` event that the harness (`harness.py:470-475`), the trace tab
(`web/src/components/trace/describeEvent.tsx:190-201`), the agent lane
(`web/src/components/console/agentLanes.ts:403-412`), the stage timeline
(`web/src/components/console/stageTimeline.ts:358-372`) and the pipeline declaration
(`aegis/src/aegis/pipelines/spec.py:636-642`) are all already built around. A separate
node is purely additive: `reflect` keeps its shape and its event, and simply stops
computing `done` itself — it reads `state["verification"]["verdict"]` instead. Every
existing assertion about the `reflection` event stays true.

### 4.2 The three tiers, cheapest first

`verify` runs at most one tool call and at most one model call, in that order, and stops
at the first tier that reaches a verdict.

**Tier 1 — deterministic. No tool call, no model call.**

| Check | Input | Verdict |
|---|---|---|
| any `ok=False` that is **not** a rail block | `tool_results[i]["ok"]` | `FAILED`, repairable |
| any rail block | new `tool_results[i]["rail_blocked"]` (see below) | `BLOCKED`, **not** repairable |
| argument validation error | `summary.startswith("Tool error:")` and the exception was a pydantic `ValidationError` — `act`'s catch-all is at `graph.py:1206-1208` | `FAILED`, repairable |
| repeated fingerprint | this round's fingerprint ∈ `attempt_fingerprints` | `OSCILLATING`, **not** repairable |
| no non-read-only call when one was needed | today's `acted` list, `graph.py:1259` | `GATHERED` — progress, not failure |
| every call read-only and every one `ok` **and** the query asked only for information | `GATHERED` + planner emitted no further calls | `VERIFIED` (nothing to read back) |

`BLOCKED` deserves its own row for the reason given in §1(b). To produce it, `act` must
stop collapsing the two signals. Change **[SOURCE]** `graph.py:1222-1227` so the result
row carries both:

```python
tool_ok = bool(ok)                       # what the tool said
allowed, summary = await screen_tool_result(...)
results.append({
    "call_id": call["id"], "ok": tool_ok and allowed, "summary": summary,
    "tool": call["name"], "rail_blocked": not allowed,
})
```

`ok` keeps its current meaning for every existing reader (the `tool_result` event, the
`reflect` judge, `run_summary`), so this is additive. **[UNVERIFIED]** that no consumer
outside `aegis/` constructs these rows positionally; grep `tool_results` before landing.

**Tier 2 — read the record back. One tool call, always audited.**

A new seam on `AgentDeps`, in the idiom of `tool_read_only` (`deps.py:493`, defaulting to
the behaviour every existing caller already had):

```python
#: aegis/src/aegis/agent/deps.py — beside ReadOnlyFn at :167
ReadBackFn = Callable[[str, Mapping[str, Any]], "ReadBack | None"]

@dataclass(frozen=True, slots=True)
class ReadBack:
    tool: str                      # must be read-only AND below gate_min_risk
    args: dict[str, Any]
    expect: str                    # substring the read-back summary must contain
    describe: str                  # one human sentence for the verification event
```

and on `AgentDeps`, defaulted so nothing existing changes meaning:

```python
#: Given a write that just executed, the read-only call that proves whether it landed.
#: ``None`` (the default, and every test fake) means tier 2 is INCONCLUSIVE and the
#: verifier falls through to tier 3 — never that a write is assumed to have worked.
read_back_for: ReadBackFn = field(default=lambda _name, _args: None)
```

The host binds it in `AgentDeps.default()` **[SOURCE]** `backend/src/app/agent/deps.py:439-461`
next to `tool_read_only=_default_tool_read_only` (`:447`). The desk adapter's
implementation is three lines of table:

```python
# backend/src/app/adapter/tools.py
READ_BACKS = {
    "update_request_status": lambda a: ReadBack(
        tool="find_requests", args={"text": a["request_id"], "limit": 1},
        expect=f"status={a['status']}",
        describe=f"look {a['request_id']} up again and check its status is {a['status']}",
    ),
    "assign_request": ...,
}
```

**Four rules the read-back execution must obey, and each gets a test.**

1. **Read-only, checked twice, independently.** Refuse unless
   `deps.tool_read_only(rb.tool)` is `True` **and**
   `risk_at_least(deps.tool_risk(rb.tool), config.gate_min_risk)` is `False`. Either
   check failing means the seam is misconfigured; log loudly and return `INCONCLUSIVE`.
   Never execute. Two checks because they assert different things — `tools.py:632-639`
   spells out exactly why: *"LOW risk means 'cheap to get wrong', which is not the same
   claim as 'changes nothing'."*
2. **Through `deps.run_tool`, never around it.** That is what makes the read-back appear
   in the audit log — `find_requests` writes an audit row naming the filter and the ids
   returned **[SOURCE]** `tools.py:426-434` — and what keeps it inside whatever scoping
   the host put on the store **[SOURCE]** `tools.py:378-383`.
3. **Screened like any other tool output.** The read-back's text goes into the transcript
   and (at tier 3) into a model prompt. Call `screen_tool_result` on it, exactly as `act`
   does **[SOURCE]** `graph.py:1218-1222` and as every sub-agent lane does **[SOURCE]**
   `subagent.py:673-677`. A read-back whose output is rail-blocked is `INCONCLUSIVE`,
   not `FAILED`.
4. **Visible.** Emit `tool_call` / `tool_result` for it like any other execution
   (`graph.py:1181`, `:1223`), plus the new `verification` event naming its `call_id`.
   Do **not** add a `purpose` field to `tool_call` — that is a wire change across six
   files (§6) for something the `verification` event already carries.

**Exactly-once is not at risk here, and the reason is worth writing down.** The read-back
is read-only and, for `find_requests`, declared `idempotent=True` **[SOURCE]**
`tools.py:715-717`. A node that re-executes on resume can therefore run it twice with no
consequence beyond a second audit row that honestly records a second look. That property
is a *requirement* of rule 1, not a happy accident: it is why a read-only-only rule makes
a tool call safe inside the loop at all.

**Tier 3 — LLM judge. `ModelRole.REASONING`, one call, no tools.**

`ModelRole.REASONING = "reasoning"` is the declared role for *"hard reasoning steps,
LLM-as-judge"* **[SOURCE]** `aegis/src/aegis/core/models.py:21`.

Runs **only** when tier 1 is clean and tier 2 returned `INCONCLUSIVE` (no seam
registered, or an ambiguous read-back). Prompt carries: the user's goal, the executed
call and its args, the read-back text if there was one, and asks for one token —
`VERIFIED` / `NOT_VERIFIED` / `INCONCLUSIVE` — plus one sentence.

**The most important rule in this document: `INCONCLUSIVE` does not retry.** An
inconclusive judge is precisely the ungrounded self-critique the 2026 evidence warns
about **[DOC]** <https://arxiv.org/html/2607.07663v1>, and retrying on it spends metered
money to act on a signal the literature says is worse than nothing. `INCONCLUSIVE` →
finalise, and say so in the `reflection` reason: *"could not verify; finalising with the
action reported as done but unconfirmed."* That sentence is more valuable on stage than a
confident wrong one.

### 4.3 `reflect` after the change

`reflect` stops computing `done` from `ok` flags and reads the verdict:

```python
verification = state.get("verification") or {}
verdict = verification.get("verdict", "INCONCLUSIVE")
done = verdict in {"VERIFIED", "BLOCKED", "OSCILLATING"}   # terminal, not necessarily good
repairable = verdict in {"FAILED", "GATHERED"}
```

`BLOCKED` and `OSCILLATING` are terminal-without-success: the loop stops but `done` in
the `reflection` event must still report the truth. Add a `verdict` field to the
`reflection` event? **No** — that is a wire change to an event six surfaces already parse
(§6). Put it on the new `verification` event and leave `reflection` byte-compatible.

---

## 5. State fields and their reducers

`state.py` has an explicit contract about reducers: accumulator keys carry
`Annotated[..., operator.add]`, last-write-wins keys are only safe *"while no LangGraph
superstep runs two nodes at once"*, and *"adding a parallel branch that writes either of
those keys requires giving it a reducer first"* **[SOURCE]** `state.py:139-168, 176-178`.
Existing accumulators: `plan_iterations` (`:139`), `prompt_tokens`/`completion_tokens`
(`:155-156`), `cost_usd` (`:157`).

### The new keys

```python
# ── The grounded verify loop ──────────────────────────────────────────────────
transcript: Annotated[list[dict[str, Any]], operator.add]
attempt_fingerprints: Annotated[list[str], operator.add]
repair_iterations: Annotated[int, operator.add]
verification: dict[str, Any]
loop_deadline_ts: float
loop_budget_left_s: float
```

| Key | Reducer | Written by | Why that reducer |
|---|---|---|---|
| `transcript` | `operator.add` | `act`, `verify` | The fix for §1(c). Real assistant + `tool`-role turns, in the shape `subagent.py:705` already builds. Accumulating is the whole point; a last-write-wins transcript is what `messages` already is. |
| `attempt_fingerprints` | `operator.add` | `act` | One `sha256(tool_name + canonical_json(args))` per executed call, appended. Must accumulate or oscillation detection cannot see round 1 from round 3. |
| `repair_iterations` | `operator.add` | `verify` | Returns `1` only for a round it judged `FAILED`-and-repairable, `0` otherwise. Mirrors `plan_iterations`' delta-return idiom exactly (`graph.py:1076`). |
| `verification` | none (last-write-wins) | `verify` **only** | Exactly one writer, which is the precedent the fan-out keys already set: *"All last-write-wins, and all safe as such: exactly one node writes each of them"* **[SOURCE]** `state.py:161-164`. Latest verdict is the only one `reflect` needs; history reaches the UI on the wire, not through state. |
| `loop_deadline_ts` | none | `guard_input` (once), `approval` (re-stamp on resume) | Two writers, never in the same superstep, and they are on mutually exclusive paths. Document that. |
| `loop_budget_left_s` | none | `approval` (at interrupt) | See the park trap below. |

### `messages` vs `transcript` — and the docstring that must be amended

`plan` becomes:

```python
messages = [{"role": "system", ...}, {"role": "user", "content": user_content},
            *state.get("transcript", [])]
```

`messages` stays a per-round scratch buffer rebuilt each time — the reason
`state.py:148-153` gives for its lack of a reducer is still exactly right, and stays
right, *because* the transcript is a separate accumulating key rather than a change to
this one. **The module docstring at `state.py:140-178` must be edited in the same
commit.** It currently asserts that three keys are deliberately last-write-wins and
explains each; leaving it unedited means the next reader concludes the rule was quietly
broken. The added paragraph should say: `transcript` accumulates the *executed* history
as real turns; `tool_results` still does not accumulate, for the reason already given
(*"accumulating it would make reflect re-see an already-repaired failure"* — `state.py:164-166`),
and `verify` now reads the current round's results before `reflect` sees them.

### The park trap in the wall clock

A naive `deadline_ts = now + loop_wall_clock_s` written once is **wrong**, and wrong in a
way that would only show up on stage. A run can sit at the human gate indefinitely —
`approval_park_timeout` defaults to `None`, *"waits indefinitely — the live money-shot
gate"* **[SOURCE]** `deps.py:317-320` — and a parked run resumes from a durable checkpoint
in a different process minutes or hours later (`orchestrator.py:324-352`). A wall-clock
deadline that ran while a human was thinking would make every approved run finalise the
instant it resumed.

So the deadline measures **loop time, not run time**:

* `guard_input` writes `loop_deadline_ts = time.time() + config.loop_wall_clock_s`
  (epoch seconds, not `perf_counter` — the checkpoint crosses processes).
* `approval`, immediately before `interrupt(...)` (`graph.py:1153`), writes
  `loop_budget_left_s = max(0, loop_deadline_ts - time.time())`.
* `approval`, on the line after `interrupt` returns, writes a fresh
  `loop_deadline_ts = time.time() + loop_budget_left_s`.

`approval` re-executes on resume by design — *"the node re-executes on resume"*
**[SOURCE]** `graph.py:1146-1148` — so both writes happen on the correct side of the
pause without any extra machinery.

---

## 6. Termination guarantees — five independent bounds

Every model call is metered at one chokepoint and the run is budget-capped
(`orchestrator.py:396-402` ends a run cleanly on `BudgetExceededError`). A loop with one
bound is a loop with one bug away from unbounded. Five, and any one of them alone
terminates the loop.

| # | Bound | Mechanism | Config |
|---|---|---|---|
| 1 | **Absolute iteration cap** | `plan_iterations < max_plan_iterations`, unchanged **[SOURCE]** `graph.py:1261` | `agent.max_plan_iterations`, default 4, bounds (1,10) |
| 2 | **Repair cap** | `repair_iterations < max_repair_iterations`. Only a `FAILED`-repairable round increments it; a `GATHERED` round does **not** | new `agent.max_repair_iterations`, default 2, bounds (0,5), `TIGHTEN_ONLY` / `Strictness.LOWER` |
| 3 | **Oscillation** | this round's fingerprint already in `attempt_fingerprints` → terminal regardless of remaining budget | none — always on |
| 4 | **Spend** | `run_usage(run_id).cost_usd >= config.max_run_usd` → terminal | new `agent.max_run_usd`, default `None` |
| 5 | **Wall clock** | `time.time() >= loop_deadline_ts` → terminal | new `agent.loop_wall_clock_s`, default 90.0 |

**On bound 2's separation from bound 1.** This is the fix for the trap in §1(a). A read
round is progress; today it costs a plan round and there is only one spare. Splitting the
counters means the budget that matters for "how many times may it try again after
failing" is a number nobody has to compute by subtracting lookups from a total.

**On bound 3.** The oscillation case is not hypothetical here. `graph.py:1020-1036`
records an observed one: the planner *"called `find_requests`, got real ids back, was told
it had not achieved the goal, and 'corrected' by running a wider `find_requests`. It
looked things up twice and never acted."* That is the loop the fingerprint check catches.

**On bound 4.** `run_usage(run_id)` **[SOURCE]** `aegis/src/aegis/core/run_context.py:202`
returns the metered spend for the run — *"the same numbers, call for call, that
`usage_ledger` is written from"* **[SOURCE]** `orchestrator.py:446-450`. It is the honest
figure: the graph's own `operator.add` reducers under-report, measured at `$0.0172955`
against a ledger holding `$0.0205096` over 24 calls **[SOURCE]** `orchestrator.py:453-456`.
Read `run_usage`, not `state["cost_usd"]`.

`calls == 0` means the gateway metered nothing — a lite deployment or a test stub
**[SOURCE]** `run_context.py:135-138`, `orchestrator.py:459-463`. In that case bound 4 is
**inert, and must be inert rather than tripping**: a run that cannot be metered must not
be terminated for having spent nothing. Default `None` for the same reason — the tenant
budget at the gateway is the real cap and it already works fail-closed; a per-run ceiling
is a second, tighter bound so one runaway run cannot eat a tenant's daily allowance.
Check it **before** the tier-3 judge call, so the judge is not the call that breaches it.

**On bound 5.** Model it on `team_wall_clock_s = 120.0` and its written-down arithmetic
**[SOURCE]** `deps.py:381-389`, which is the repo's own lesson about this: `90.0` did not
fit, `120.0` did, and the derivation is in the comment so *"the next change to any of the
four numbers can be checked against it."* The loop deadline must be a **backstop above**
the per-round bounds, never a tighter competing deadline — otherwise a run gets cut by
the clock and the `reflection` reason says "budget exhausted" about a run that in fact
timed out, which is the exact dishonesty that comment exists to prevent. **[UNVERIFIED]**
`90.0`: I have no measurement of a 4-round run's wall time on this hardware. Derive it
from `4 × (planner p95 + judge p95 + tool p95)` measured on the box, write the arithmetic
in the comment, and do not ship the guess.

### Telling the model how much budget is left

Append to `plan`'s `user_content` (the block at `graph.py:1013-1046`):

```
Round {iteration + 1} of {budget}. Rounds remaining after this one: {n}.
```

and on the last permitted round:

```
This is your final round. Do not propose another lookup — take the action the
question asks for, or answer with what you already have.
```

This is external information about the environment, not the model judging itself, so it
does not fall under the §2 prohibition. It is also the direct countermeasure to the
observed failure at `graph.py:1026-1031`, where the planner spent its last round on a
second lookup.

**[UNVERIFIED]:** nothing in this repo measures whether budget-in-prompt changes planner
behaviour. It is a hypothesis. §9 says how to measure it.

---

## 7. Safety invariants that must not move

### 7.1 A retried HIGH-risk write raises a SECOND approval interrupt

**Structurally this already holds**, and the plan's job is to keep it holding and prove it.

* `gate` recomputes from `state["tool_calls"]` every time, with no memory of any prior
  approval **[SOURCE]** `graph.py:1096-1108`.
* `gate → approval` is inside the cycle, so a second round's HIGH proposal hits it again
  **[SOURCE]** `graph.py:1603-1607`.
* The orchestrator's stream loop is `while True` and handles an interrupt per iteration
  **[SOURCE]** `orchestrator.py:229-352`, resuming with `Command(resume=...)` at
  `orchestrator.py:349-351`. There is no "one gate per run" assumption anywhere in it.

**The thing that could break it.** `approved_call_ids` is last-write-wins **[SOURCE]**
`state.py:141`, and `_authorised_calls` admits a call purely on id membership **[SOURCE]**
`graph.py:1805-1821`. Provider-generated call ids are not guaranteed unique across
rounds — `subagent.py:592-595` documents lanes *"routinely hand[ing] back the same
provider-generated call id"*, which is why sub-agent proposals are namespaced. If a
second round re-used `call_1` with **different arguments** and reached `act` while
`gated` was still `True` from a stale round, one approval would authorise a call whose
arguments the human never read.

**Close it by construction, extending the argument the code already makes.**
`approval`'s docstring says *"the set the human is shown and the set that executes are
the same set, which is enforced structurally"* **[SOURCE]** `graph.py:1138-1145`. Make
that true of the arguments too:

* `approval` returns `approved_fingerprints` alongside `approved_call_ids` — the same
  `sha256(tool + canonical_json(args))` used for oscillation detection, computed over the
  exact `actions` list it rendered into the interrupt payload (`graph.py:1160-1170`).
* `_authorised_calls` requires **both** id membership and fingerprint match.

An approval then authorises *this tool with these arguments*, once. Cheap, and it removes
a class of bug rather than a bug.

### 7.2 Exactly-once holds

`aegis/tests/agent/test_durable_exactly_once.py` already covers *"a rejected gate is never
reported as approved"* in a genuinely multi-round run with escalating risk **[SOURCE]**
`test_durable_exactly_once.py:378-429` — it sets `deps.config.max_plan_iterations = 2`
(`:407`), runs a MEDIUM round 1 that fails, then a HIGH round 2 that the human rejects,
and asserts nothing executed after the gate (`:419-422`).

Extend that file with a §6 (its sections are numbered — §4 at `:374`, §5 at `:431`):

* `test_a_verified_failure_raises_a_second_independent_gate` — round 2's HIGH write is
  approved and fails verification; round 3 proposes a corrected HIGH write. Assert
  **two** `approval_required` events with **distinct** `approval_id`s, two
  `enqueue_approval` calls, and the write tool executed exactly twice total — once per
  approval, never twice for one.
* `test_one_approval_cannot_authorise_a_changed_call` — round 3 re-uses round 2's call id
  with different args; assert `act` executes nothing (§7.1's fingerprint rule).
* `test_a_read_back_is_never_gated` — configure a `ReadBack` naming a HIGH-risk tool;
  assert zero extra `approval_required` events and that the read-back did **not** run.

### 7.3 The read-back cannot become the action

Covered by §4.2 rules 1–4. The single most important line: the read-back is refused
unless it is *both* `read_only` *and* strictly below `gate_min_risk`. One of those checks
alone is insufficient, for the reason `tools.py:632-639` gives.

---

## 8. The SSE wire — every touchpoint the `verification` event must reach

The `reflection` event was on the wire and invisible for a whole phase. Two tests exist
solely because of it, and both of their docstrings name it. `runReducer.ts:371-379`,
quoted verbatim:

```
      // Compile-time exhaustiveness. `event` narrows to `never` here only while every
      // member of the union above has a `case`; add a variant to `StreamEvent` without a
      // branch and this assignment is a type error rather than a silent discard. That
      // silent discard is exactly what dropped `reflection`, `routing` and `memory` on
      // the floor while they were live on the wire.
```

And `backend/tests/api/test_stream_union_mirror.py:9-13`: *"Three variants the backend
emits (`reflection`, `routing`, `memory`) never reached the TypeScript union at all, so
the reducer's `default` branch silently discarded the self-repair loop, the supervisor
hand-off and every memory recall — three of the most demoable things the system does, on
the wire and invisible."*

### The complete list, in dependency order

| # | File | Change | Guard if you skip it |
|---|---|---|---|
| 1 | `aegis/src/aegis/agent/events.py` | `def verification(...)` builder beside `reflection` (`:203-224`) | `verify_agent_pipeline` raises `PipelineDriftError` at import once #10 lands |
| 2 | `backend/src/app/agent/events.py` | add to the import block (`:20-40`) **and** to `__all__` (`:45-67`) | silent `AttributeError` at first emit |
| 3 | `backend/src/app/api/schemas.py` | `class Verification(_BaseEvent)` beside `Reflection` (`:384-404`) | — |
| 4 | `backend/src/app/api/schemas.py` | add to the `StreamEvent` union (`:518-538`) | `events.stamp` raises on validation; `test_stream_union_mirror` fails |
| 5 | `web/src/lib/stream.ts` | `export interface Verification extends BaseEvent` mirroring #3's fields exactly (`:328-347` is the `Reflection` pattern) | `test_stream_union_mirror` compares **fields**, not just variants (`:19-22`) |
| 6 | `web/src/lib/stream.ts` | add to `export type StreamEvent =` (`:432-452`) | same test |
| 7 | `web/src/state/runReducer.ts` | five edits: `verifications: Verification[]` on `RunState` (~`:96`), `[]` in the initial state (~`:174`), reset in `case 'run_started'` (~`:240`), `case 'verification':` (~`:299`), and a `signalForRunEvent` entry (~`:208`) | `const unhandled: never` at `:375` is a `tsc` error; `web/tests/state/runReducerCoverage.test.mjs` fails under `npm test` |
| 8 | `web/src/components/trace/describeEvent.tsx` | `case 'verification'` beside `reflection` (`:190-201`) | trace tab renders nothing |
| 9 | `web/src/components/console/agentLanes.ts` | `case 'verification'` beside `reflection` (`:403-412`) | the lane view drops it |
| 10 | `aegis/src/aegis/agent/graph.py` | `NODE_LABELS["verify"] = "Verify the result"` (`:102-120`) | `verify_agent_pipeline` raises |
| 11 | `aegis/src/aegis/pipelines/spec.py` | `PipelineStage(name="verify", …, emits=(_stream("verification", …),))` between `act` (`:650-654`) and `reflect` (`:636-642`) | `verify_agent_pipeline` raises `PipelineDriftError` **at import**, so the backend does not start |
| 12 | `web/src/components/console/stageTimeline.ts` | add `'verify'` to `LOOP_NODES` (`:203`); attach the verdict to the `verify` stage the way `reflection` attaches to `reflect` (`:358-372`) | the verify row gets no round number and falls outside the attempt band |
| 13 | `aegis/src/aegis/agent/harness.py` | a `verifications` block beside `iterations` (`:465-475`) | the harness's run summary omits it |
| 14 | `web/src/lib/api/generated/schema.d.ts` | **regenerate**, never hand-edit (`:8899` shows it is generated) | type drift |

**Not needed, and worth saying so:** `aegis/src/aegis/core/stream_names.py` /
`web/src/lib/streamNames.ts`. That pair is the AG-UI `CustomEvent` name table for a
different protocol — `reflection` is not in it either **[SOURCE]** `stream_names.py:9-60`
(grep for `reflect` returns nothing) — and it has its own mirror test
`backend/tests/api/test_stream_name_mirror.py`. Adding `verification` there would be
adding a name to a protocol that does not carry it.

**#11 is the tripwire that makes this list self-enforcing.** `verify_agent_pipeline()`
raises `PipelineDriftError` if the declared stage set differs from `NODE_LABELS`, if any
label differs from the one `_timed` streams, or if a declared stream event names something
`aegis.agent.events` cannot build **[SOURCE]** `aegis/src/aegis/pipelines/bindings.py:108-141`.
So #1, #10 and #11 must land in one commit or the process will not import. `GET
/agent/topology` reads the compiled graph's real shape **[SOURCE]**
`backend/src/app/api/routes.py:3959-3979`, `aegis/src/aegis/agent/topology.py:113-125`, so
it picks up `verify` for free once the node exists.

### The event's payload

```python
def verification(*, verdict: str, tier: str, evidence: str, reason: str,
                 read_back_call_id: str | None = None,
                 repair_iteration: int = 0, max_repairs: int = 0,
                 spend_usd: float = 0.0, spend_cap_usd: float | None = None,
                 seconds_left: float | None = None) -> dict[str, Any]:
```

`tier` is `"deterministic" | "read_back" | "judge"` — it is what lets the console say
*"proved by reading the record back"* rather than *"the model thinks so"*, which is the
whole claim of §2 made visible.

---

## 9. The UI: "it tried, it failed, it tried again" must be watchable

**The attempt band already exists. Reuse it; do not add state.**

Verified. `stageTimeline.ts` already derives round and budget per stage:

* `Stage.round: number | null` and `Stage.maxRounds: number | null` **[SOURCE]**
  `stageTimeline.ts:221-229`;
* `LOOP_NODES = new Set(['plan', 'gate', 'approval', 'act', 'reflect'])` **[SOURCE]**
  `stageTimeline.ts:203`, used to stamp `round` at `:329`;
* the round is advanced from the wire, not counted locally — *"`Reflection.iteration`
  names the round that just closed, so the round a stage belongs to is read rather than
  counted by watching node names"* **[SOURCE]** `stageTimeline.ts:222-226`, implemented at
  `:358-371`;
* `RunTiming.rounds` and `RunTiming.roundBudget` are already exported **[SOURCE]**
  `stageTimeline.ts:269-272, 405, 415`.

And `RunStages.tsx` already renders the band:

* `openRound` / `roundBudget` props **[SOURCE]** `RunStages.tsx:103-107`;
* the header — a `RotateCw` icon, `Round {openRound} of {roundBudget}`, and for rounds
  after the first *"it judged the previous round insufficient and went again"*
  **[SOURCE]** `RunStages.tsx:143-160`;
* computed by comparing each stage's `round` with its predecessor's **[SOURCE]**
  `RunStages.tsx:366-380`, with the comment *"eight identical-looking rows read as
  duplicated noise rather than as an agent that judged its own first attempt insufficient
  and went again."*

**So the entire UI change is:**

1. `stageTimeline.ts:203` — add `'verify'` to `LOOP_NODES`. The verify row now sits inside
   the band and carries the round number.
2. `stageTimeline.ts` — a `if (event.type === 'verification')` branch beside the
   `reflection` one at `:358-372`, writing `stages[i].verdict = event.evidence` onto the
   most recent `verify` stage. `verdict` is already rendered as the row's `detail`
   **[SOURCE]** `RunStages.tsx:130-136` — so "status is still `open`, expected `resolved`"
   appears under the bar with no new component.
3. `RunStages.tsx:151-153` — the band already says `Round N of M`; extend `roundBudget` to
   `timing.roundBudget` unchanged, and add the repair counter as a second clause when the
   `verification` event reports one: `Round 3 of 4 · repair 1 of 2`.

That is three edits and no new React state. Anything more is rebuilding a component that
already works.

**One deliberate visual rule.** `describeEvent.tsx:190-201` gets the tone right for
`reflection` and the same rule applies to `verification`: *"`done` is the goal being met;
`will_retry` is the agent choosing to go round again inside its own hard cap — both are
wins, and neither is an error, so neither is ever painted as one."* A `FAILED`
verification that leads to a successful repair must **not** render red. Reserve the block
palette for `BLOCKED` and terminal `OSCILLATING`.

---

## 10. Verification

Everything below is a procedure to run, not a result. **Nothing in this section has been
executed.**

### 10.1 Bring the stack up and get a token

```bash
bash /Users/yrevash/aegis/scripts/dev-native.sh        # backend on 127.0.0.1:8000
```

**[SOURCE]** `scripts/dev-native.sh:55-82` (port 8000, health-gated).

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/login \
  -H 'content-type: application/json' \
  -d '{"username":"admin","password":"demo"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
```

`POST /auth/login` **[SOURCE]** `backend/src/app/api/routes.py:1384-1409`; `LoginRequest`
is `{username, password}` **[SOURCE]** `backend/src/app/api/schemas.py:649-655`; the seed
defines the `admin` principal **[SOURCE]** `backend/src/app/seed.py:236` and the
documented dev password is `demo` **[SOURCE]** `backend/src/app/seed.py:38`.

### 10.2 The happy path — a write that verifies

```bash
curl -N -X POST http://127.0.0.1:8000/query \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"query":"close the oldest resolved billing request","persona":"operations_lead"}'
```

`QueryRequest` is `extra="forbid"` **[SOURCE]** `schemas.py:706-745` — an unknown field is
a 422 naming it, so do not add one speculatively.

Expected ordered subsequence (the loop's own tests use an ordered-subsequence helper —
**[SOURCE]** `aegis/tests/agent/test_self_repair_loop.py:22-24`):

```
run_started
node_started{node:plan} … reasoning … node_finished{node:plan}
node_started{node:gate} node_finished{node:gate}
node_started{node:act} tool_call{name:find_requests} tool_result{ok:true} node_finished{node:act}
node_started{node:verify} verification{verdict:"GATHERED",tier:"deterministic"} node_finished{node:verify}
reflection{iteration:1,max_iterations:4,done:false,will_retry:true}
node_started{node:plan} …
node_started{node:gate} node_finished{node:gate}
approval_queued  approval_required{risk:"high"}
node_started{node:act} tool_call{name:update_request_status} tool_result{ok:true} node_finished{node:act}
node_started{node:verify}
  tool_call{name:find_requests}       ← the read-back, same call shape, audited
  tool_result{ok:true}
  verification{verdict:"VERIFIED",tier:"read_back",read_back_call_id:"…"}
node_finished{node:verify}
reflection{iteration:2,done:true,will_retry:false}
node_started{node:generate} … token … run_finished{status:"completed"}
```

**The three assertions that matter:** a `verification` event appears at all (if it does
not, §8 has a hole); `tier == "read_back"` on the write round (if it says `"judge"` the
seam is not wired and §2's whole claim is theatre); and the read-back's `tool_call` is
`find_requests` and never `update_request_status`.

Resolve the gate out of band if the socket parks:

```bash
curl -s -X POST http://127.0.0.1:8000/approvals/$APPROVAL_ID/decision \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"decision":"approve"}'
```

**[SOURCE]** `routes.py:2308-2327` (admin only, `seat.can_approve`, idempotent).

### 10.3 The forced-failure scenario — proving each bound fires

Every bound needs a scenario where **only that bound** can stop the loop. Run each as a
`pytest` with `build_fake_deps` **[SOURCE]** `aegis/tests/agent/conftest.py:75-91`, whose
`_failing_run_tool(fail_first_n)` fake **[SOURCE]** `test_self_repair_loop.py:44-55` is
already the right shape.

| Bound | Forced scenario | Assertion |
|---|---|---|
| 1 iteration cap | `run_tool` always `ok=False`, `read_back_for` returns `None`, all other bounds set wide | exactly `max_plan_iterations` `tool_call`s; last `reflection.reason` contains `"budget exhausted"` |
| 2 repair cap | `max_plan_iterations=10`, `max_repair_iterations=2`, always-failing write | exactly 3 write attempts (1 + 2 repairs); reason names the **repair** budget, not the plan budget |
| 3 oscillation | `run_tool` fails; a stub planner that returns the **identical** tool call every round; caps wide | exactly 2 executions; `verification.verdict == "OSCILLATING"`; `will_retry` false with budget provably remaining |
| 4 spend | monkeypatch `run_usage` to report `cost_usd` above `max_run_usd` after round 2 | loop stops at round 2 with `verdict` naming spend; **and** a second test where `run_usage` reports `calls == 0` proves the bound is inert, not tripped |
| 5 wall clock | `loop_wall_clock_s=0.01`, an always-failing tool | stops at round 1; reason names the clock and **not** the iteration budget — the honesty requirement from §6 |

Plus the park test, which is the one most likely to be got wrong:
`test_a_parked_gate_does_not_consume_the_loop_deadline` — `loop_wall_clock_s=5.0`, park
the gate for 10 s of fake time, resume, and assert the loop still runs its remaining
rounds.

### 10.4 Tests to write, and where

| File | Add |
|---|---|
| `aegis/tests/agent/test_verify_loop.py` **(new)** | the three tiers in isolation; `INCONCLUSIVE` does **not** retry; `BLOCKED` does **not** retry; `GATHERED` does not increment `repair_iterations` |
| `aegis/tests/agent/test_verify_readback_safety.py` **(new)** | a `ReadBack` naming a non-read-only tool is refused; one naming a tool at/above `gate_min_risk` is refused; the read-back goes through `deps.run_tool`; its output is passed to `screen_tool_result` |
| `aegis/tests/agent/test_self_repair_loop.py` | update `:126` and the round arithmetic in `test_iteration_budget_caps_planning_rounds` (`:118-136`) for the new default; add a case where a `VERIFIED` write ends the loop with rounds still available |
| `backend/tests/agent/test_self_repair_loop.py` | the mirror of the above (`:142`) |
| `aegis/tests/agent/test_durable_exactly_once.py` | §6, the three tests in §7.2 |
| `aegis/tests/agent/test_harness_config.py` | `test_harness_config_covers_every_knob` will fail until the three new `AgentConfig` fields have `_KNOB_SPECS` entries (`harness.py:53-95`) — that failure **is** the guard |
| `aegis/tests/settings/test_forbidden_controls.py` | add `agent.max_repair_iterations` to the strictness table (`:143`) and to the caps chain (`:420-424`) |
| `aegis/tests/settings/test_spec.py` | bounds for the new keys |
| `aegis/tests/pipelines/test_pipeline_spec.py` | the `verify` stage's declaration |
| `backend/tests/api/test_stream_union_mirror.py` | nothing to write — it fails on its own if #3–#6 are incomplete |
| `web/tests/state/runReducerCoverage.test.mjs` | nothing to write — it fails on its own if #7 is incomplete |
| `web/tests/console/stageTimeline.test.mjs` | a scripted log with `verify` stages across three rounds; assert each carries the right `round` and the verdict lands on the right stage |

Restraint, per the repo's own norm: test the load-bearing claim and its failure mode. The
load-bearing claims are *read-back can never write*, *a retry re-gates*, *each bound fires
alone*, and *the event reaches the reducer*. Do not test every branch of the tier ladder.

### 10.5 Verifying the attempt bands in the browser

1. `npm run dev` in `web/`; open the console and run the query from §10.2 as `admin`.
2. In the Run panel, expect three band headers: `Round 1 of 4`, `Round 2 of 4`,
   `Round 3 of 4` — each with the `RotateCw` icon, and rounds 2+ carrying the *"it judged
   the previous round insufficient and went again"* line **[SOURCE]**
   `RunStages.tsx:143-160`.
3. Each band must contain a `Verify the result` row (this is the check that `'verify'`
   reached `LOOP_NODES`; if the row appears **outside** the bands, edit #12 in §8 is
   missing).
4. The failing round's verify row must read its evidence under the bar — e.g. *"read back
   R-104: status is still `open`, expected `resolved`"* — and must **not** be painted with
   the block palette.
5. Open the Trace tab: the `verification` entries must be present and must name their
   `tier`. Absent ⇒ edit #8. Present in the raw event log but absent from every rendered
   surface ⇒ edit #7 landed but #8/#9 did not.
6. `curl -s http://127.0.0.1:8000/agent/topology -H "authorization: Bearer $TOKEN"` must
   list `verify` with an edge `act → verify → reflect`. It reads the compiled graph
   **[SOURCE]** `routes.py:3959-3979`, so a mismatch here means the node is not wired.

---

## 11. What this plan does not cover

* **Team-run loops.** A fan-out still routes straight to `generate`, twice over
  **[SOURCE]** `graph.py:241-244` and `graph.py:1270-1276`. `verify` will run on the team
  path — it sits on the `act → reflect` edge and `synthesize → gate → act` joins that tail
  **[SOURCE]** `graph.py:1595` — so a team run's approved write **will** be verified and
  its verdict **will** stream. It just cannot be repaired, because there is no planning
  round to return to. That asymmetry must be stated in the `reflection` reason rather than
  left to look like a bug. Making a fan-out repairable means deciding whether to re-run
  one lane or the whole team, and that is a separate design.
* **Guardrail re-generation.** A `BLOCKED` verdict — the tool-result rail rejecting a
  tool's output **[SOURCE]** `graph.py:1218-1222` — is terminal here. Re-prompting to
  produce output that passes the rail is a different loop with a different safety
  argument, and it is the one place where a retry could plausibly be the model working
  around a control.
* **Retrieval's own bounded loop.** `agentic_retrieval_max_rounds` **[SOURCE]**
  `deps.py:369` is a separate cycle inside `retrieve` and is untouched.
* **The sub-agent ReAct loop.** `subagent.py::_loop` is left exactly as it is. This plan
  brings the main graph up to its standard; it does not merge the two.
* **Persisting verification to `run_events`.** Node timings and stream events are not
  persisted — *"every duration above is streamed, never persisted, so there is no p95 to
  aggregate an hour later"* **[SOURCE]** `aegis/src/aegis/pipelines/spec.py:678-684`.
  Verification verdicts inherit that. Making them durable is a schema change.
* **Wiring `read_back_for` for anything but the desk adapter.** The seam is
  domain-agnostic; the table is domain content, and only `backend/src/app/adapter` gets
  one here.

---

## 12. Build order

1. `act` records `rail_blocked`; `state.py` gains the six keys, their reducers and the
   amended module docstring. No behaviour change yet.
2. The `verification` event across all 14 touchpoints in §8, emitted from a `verify` node
   that does **tier 1 only**. The pipeline-spec tripwire forces 1/10/11 together.
3. `plan` reads `transcript`; `act`/`verify` write it. §1(c) closed.
4. `read_back_for` seam + the desk adapter's table + the four execution rules. Tier 2.
5. Tier 3, with the `INCONCLUSIVE`-does-not-retry rule as its first test.
6. The five bounds and their three new settings; the budget-remaining prompt line.
7. **Only now** raise `max_plan_iterations` to 4 and update the seven files in §3.
8. `approved_fingerprints` in `approval` and `_authorised_calls`; the §7.2 tests.
9. `'verify'` into `LOOP_NODES`; the verdict onto the stage; the repair clause in the band.

Steps 1–3 are shippable without changing a single run's outcome, which makes the wire and
the UI verifiable before any behaviour depends on them. Step 7 is the only one that
changes what a run costs, and it deliberately comes after every bound that contains it.

---

## 13. The demo sentence this earns

> *"It looked the request up, proposed the write, and a human approved it. The write came
> back saying it succeeded — so the agent read the record again, saw the status had not
> actually moved, and said so. It corrected the call, came back to the human a second
> time, and that write it checked and confirmed. It never told itself it had done well;
> it went and looked."*

---

## 14. Risks, stated plainly

1. **`INCONCLUSIVE` is going to be the common verdict at first**, because only the desk
   adapter gets a `read_back_for` table. If tier 2 is unwired for the tool the demo uses,
   the whole external-grounding claim collapses to a `ModelRole.REASONING` call judging the
   model's own work — the exact thing §2 forbids. **Assert `tier == "read_back"` in the
   demo test**, not just `verdict == "VERIFIED"`.
2. **Raising the cap weakens the unbounded-consumption argument** at
   `posture.py:206-220` and `risk_map.py:201-219`. The five bounds are the replacement
   argument; if they are not all wired and tested, the posture text quietly overstates.
3. **`loop_wall_clock_s = 90.0` is a guess** **[UNVERIFIED]**. `team_wall_clock_s`'s
   history (`deps.py:381-389`) is the repo's own record of what happens when this number
   is guessed: the wrong bound fires and the reported reason names the wrong cause.
4. **The park trap.** A wall clock that runs while a human is at the gate would make every
   approved run finalise on resume. §5's two-write scheme is untested and is the single
   most likely thing in this plan to be got wrong.
5. **Provider call-id reuse across rounds** is documented in the sub-agent path
   (`subagent.py:592-595`) and is unmeasured on the main path **[UNVERIFIED]**. §7.1's
   fingerprint check assumes it can happen.
6. **Fourteen touchpoints for one event.** The tripwires (`PipelineDriftError`, the union
   mirror, the reducer coverage test, `tsc`) catch nine of them. Items 8, 9, 12 and 13 —
   `describeEvent`, `agentLanes`, `stageTimeline`, `harness` — have **no guard** and will
   fail silently. They are exactly the shape of the original `reflection` bug.
7. **`verify` adds a node to every run**, including runs with no tool call at all. It must
   return `{}` and emit nothing on those, or every existing golden trace changes — the
   same reason the memory nodes are wired plain rather than through `_timed`
   **[SOURCE]** `graph.py:1514-1518`. **[UNVERIFIED]:** whether `verify` should be wired
   plain or timed. A timed node that always emits `node_started`/`node_finished` is more
   watchable; a plain one preserves the existing stream byte-for-byte on the no-tool path.
   Decide this explicitly before step 2, because it is very hard to change afterwards.
