# SOTA 07 — The long-horizon ceiling: an enforced bound now, trajectory compaction later

> **STATUS: PLAN. Nothing here has been implemented.** Two tracks are specified below and
> they are **not** alternatives at the same price. Track A is roughly a day and closes an
> honesty gap. Track B is one to two weeks, adds a model call to the run path and opens a
> new security surface. **Do Track A. Read Track B before deciding you want it.**

> **Source discipline, inherited from `docs/dev_new_docs_v2/phase-11-langflow.md`.**
> **[VERIFIED]** — read in this repository at the anchor given, on 2026-08-27. **[DOC]** —
> read on the public web at the URL/identifier given. **[UNVERIFIED]** — stated because it
> is needed and not checked, with the check named. Arithmetic derived from verified
> constants is marked **[DERIVED]** and is not a measurement. **Nothing here is
> [MEASURED]: no run of this system was instrumented for trajectory size, and that absence
> is itself a finding (§4.4).**

---

## What this is, in one paragraph

Aegis has **no trajectory compaction**: nothing summarises, evicts or budgets a run's own
turn history. It has an excellent memory subsystem — a hard token budget with
lost-in-the-middle ordering, decay-based archival, bitemporal consolidation and a scheduled
hard delete — but that subsystem governs **the store, across turns**, and never sees the
trajectory a single run accumulates **[VERIFIED, §2]**. The run is bounded instead by
*counts and clocks*: two planning rounds, four steps per lane, 45 s per lane, 120 s per
fan-out **[VERIFIED]**. **No bound anywhere is denominated in tokens**, and a sub-agent
appends tool results into its message list verbatim **[VERIFIED
`aegis/src/aegis/agent/subagent.py:636`, `:703-705`]**. This document plans two things and
insists on the distinction: **(a)** an *enforced, documented* trajectory ceiling in
`AgentConfig` with a visible refusal when it is exceeded, plus the architecture-doc
paragraph that states the limit — the honest short-term answer; and **(b)** real trajectory
compaction, grounded in the 2026 literature. It also argues, with arithmetic, that for
Aegis's short fan-out-shaped runs the ceiling buys most of the value and compaction buys
little — while being explicit that for a coding harness the ordering is reversed.

---

## 1. Why this is a problem worth a document

The claim on the compliance surface is that Aegis bounds cascading failures and unbounded
consumption. The mechanisms it names are real, and they are all **cardinal**: how many
rounds, how many steps, how many seconds, how many dollars. None of them bounds **how much
context a run has accumulated when it makes its next model call**. Those are different
quantities, and the 2026 literature is unambiguous that the second one degrades quality
long before it hits a hard context limit (§3.1).

Concretely: a four-step lane that calls one tool returning a 300 KB record has a perfectly
legal step count, a perfectly legal wall clock, and a prompt nobody bounded. It will either
be truncated by the provider, cost far more than the budget model anticipated, or answer
worse than a two-step lane would have. Aegis will report it as `ok`.

---

## 2. What is actually there today — verified

### 2.1 The memory subsystem: real, and about the wrong thing

Everything the brief attributed to `aegis/src/aegis/memory/` is there.

| Claim | Verdict | Anchor |
|---|---|---|
| Token budget | **True.** `ctx_token_cap = 8000`, `answer_reserve = 1200`, `summary_max_tokens = 400`, and `per_tier_caps` as *independent ceilings, not a partition* — `profile .10 / facts .20 / skills .10 / summary .15 / rag .30 / episodic .15 / raw .25` | `aegis/src/aegis/memory/config.py:33-46`, `:105-112` **[VERIFIED]** |
| Lost-in-the-middle ordering | **True, and explicit.** `_LAYOUT = ("profile","facts","skills","summary","episodic","raw")` — "high value at the START and END, bulk in the MIDDLE" | `aegis/src/aegis/memory/working.py:47-48` **[VERIFIED]** |
| Deterministic eviction | **True.** `_EVICT_ORDER = ("raw","episodic","summary","skills","facts","profile")` — "shed the cheap/recoverable bottom first"; greedy per-tier fill then evict; **"Never a model call — regenerating a summary is a background consolidation job"** | `working.py:50-51`, `:11-14` **[VERIFIED]** |
| Decay-based archival | **True.** `prune_forgotten` closes a live fact in transaction time when confidence-weighted recency decays below `forget_floor = 0.05`, it has never been recalled, and it is older than `forget_min_age_days = 90`. **Soft-archival, never a hard delete**; logged as a `PRUNE` op | `aegis/src/aegis/memory/consolidate.py:997-1022`, `config.py:109-110` **[VERIFIED]** |
| Consolidation | **True.** mem0-style EXTRACT → RECONCILE on a background queue every `consolidation_every_n = 4` turns; Zep bitemporal ADD/UPDATE/INVALIDATE, never a delete; a mutating decision whose `target_id` cannot be resolved is **refused and audited**, never retargeted | `consolidate.py:1-38`, `config.py:97` **[VERIFIED]** |
| Retention | **True.** The one place performing an unconditional scheduled **hard delete**, deliberately narrow: episodic turns and emptied sessions past the horizon; only **already-closed** facts; never `memory_write_log` | `aegis/src/aegis/memory/retention.py:1-40` **[VERIFIED]** |
| Token counting is offline-safe | **True.** `tiktoken` when importable, else `len // 4`; "the budgeter only needs a consistent, monotone estimate" | `aegis/src/aegis/memory/tokens.py:1-6` **[VERIFIED]** |

**And here is the thing the brief is right about.** Every one of those mechanisms operates
on **material recalled from a durable store** and produces **one extra system context
block**. `AssembledMemory.text` is assembled *before* the run reasons; the assembler is
never called again during the run; and nothing in the package has a reference to the run's
own accumulating message list. The memory layer is a **cross-turn** system. The trajectory
is an **intra-run** object. They do not touch.

### 2.2 The trajectory, and what bounds it

**The main graph does not accumulate a trajectory at all.** `AgentState.messages` is
documented as *"a per-planning-round scratch buffer, rebuilt from scratch each time `plan`
runs — not a transcript accumulated across nodes"*, and is deliberately last-write-wins for
that reason; `tool_results` is *"replaced wholesale by `act`"*
**[VERIFIED `aegis/src/aegis/agent/state.py:24-36`]**. With `max_plan_iterations = 2` the
main path's worst case is two independent prompts, not a growing one. **This is a good
design and it is why the exposure is smaller than the brief's framing implies.**

**The sub-agent lane does accumulate one**, and it is the only place in the system that
does. `aegis/src/aegis/agent/subagent.py:532-640` **[VERIFIED]**:

```
messages = [system, user]                      # :532-535
for step in 1..spec.max_steps:                 # :547
    completion = await deps.complete(spec.model_role, messages, tools=…)   # :559-561
    if no tool calls: return                   # :572-574
    messages.append({"role": "assistant", …})  # :576-581
    for refused / proposed / executable:
        messages.append(_tool_message(call, summary))   # :583, :608, :636
```

Three verified properties of that loop:

1. **It is bounded by step count only.** `spec.max_steps` defaults to `4`
   (`subagent.py:153`, `AgentConfig.subagent_max_steps = 4` at `deps.py:381`).
2. **`_tool_message` performs no truncation.** `{"role":"tool", "tool_call_id":…,
   "content": content}` — the content is passed through
   (`subagent.py:703-705`).
3. **The content is the tool's own `summary`, screened but not sized.** `_execute` returns
   `str(outcome.summary)` after `screen_tool_result` — a *guardrail*, which may block or
   redact but is not a length bound (`subagent.py:676-697`).

So the one accumulating structure in the system admits arbitrary-length third-party text
up to four times, with no accounting.

### 2.3 The bounds that do exist

All verified at `aegis/src/aegis/agent/deps.py:356-391`:

| Knob | Default | What it bounds |
|---|---|---|
| `max_plan_iterations` | `2` | planning rounds — hard cap, guarantees termination |
| `subagent_max_steps` | `4` | one lane's ReAct iterations |
| `subagent_timeout_s` | `45.0` | one lane's wall clock; `TIMEOUT` is a **designed** terminal state |
| `team_wall_clock_s` | `120.0` | the fan-out's backstop, with the arithmetic written down: `(4/3 waves) × 45 s + 3 × 0.75 s stagger + 20 s pool = 110.75 s worst case` |
| `max_parallel_agents` | `4` | team width; clamps **down** only |
| `max_concurrent_agents` | `3` | gateway slots held at once |
| `agentic_retrieval_max_rounds` | `2` | the Self-RAG/FLARE retrieve→judge→reformulate loop |

Plus `BudgetExceededError` at the single gateway chokepoint — token/USD/RPM/TPM caps,
deliberately **not** swallowed by a lane (`subagent.py:440-452`) **[VERIFIED]**.

**Every one of those is a count or a clock. Not one is a token bound on the prompt about to
be sent.** The budget caps are the closest thing, and they are per-tenant spend, not
per-run context — they fire *after* an oversized prompt has been paid for, not before it is
assembled.

### 2.4 The one adjacent thing that is bounded in tokens

`_CONVERSATION_TURN_CAP = 12` in `working.py:59-63` **[VERIFIED]** caps the raw turns
exposed structurally as `AssembledMemory.conversation`, with an explicit rationale: *"a
40-turn window is still far more than a query rewriter needs to resolve a pronoun, and it
would be re-sent on every turn."* That is exactly the right instinct, applied to the recall
path and never to the trajectory. Track A is that instinct, moved one layer up.

### 2.5 Two absences already recorded elsewhere

- **"Checkpoint storage grows without bound. Nothing prunes LangGraph's checkpoint tables,
  and no `audit_log` retention or partitioning is documented either. Both are recorded as
  owed work rather than described as solved."** —
  `docs/architecture/system-architecture.md:370-372` **[VERIFIED]**. A trajectory ceiling
  bounds what one checkpoint *holds*; it does not bound how many checkpoints exist. Do not
  let Track A be described as closing that.
- **`grep -rn "compact" aegis/src backend/src`** returns eight hits, every one of them the
  English word in a docstring ("a compact summary", "render compactly"). **There is no
  compaction machinery of any kind in this repository [VERIFIED].**

---

## 3. The 2026 literature, verified

### 3.1 Context rot — why a bound is not just about the hard limit

The phenomenon: as tokens are added to an LLM's input, output quality decreases —
independently of whether the context window is exceeded. Chroma's 2025 study tested 18
frontier models and found **every one** degrades with input length, with meaningful
degradation at 50 K tokens on a 200 K-token model **[DOC]**. The positional half of it is
older and is the finding Aegis's memory assembler is already built on: accuracy is highest
when relevant information sits at the beginning or end and **degrades by >30 % when it sits
in the middle** **[DOC]** — which is precisely `_LAYOUT`'s justification at
`working.py:47`.

2026 work extends this to agents specifically: **LOCA-bench** (arXiv:2602.07962),
*Benchmarking Language Agents Under Controllable and Extreme Context Growth*, evaluates
agents in scenarios with dynamically growing context **[DOC]**; **Classifier Context Rot**
(arXiv:2605.12366) shows monitor/classifier performance itself degrades with context length
**[DOC]** — a direct concern for Aegis, whose injection screening and tool-result rail are
classifier-based.

**The consequence for this plan:** a ceiling is not merely a crash guard. A run that stays
under the provider's limit but drifts into the degraded regime answers *worse*, silently,
and Aegis's conformal abstain band will not catch it because the prediction is not
degenerate — it is confidently wrong.

### 3.2 The three papers, verified individually

All three named in the brief exist, and each was read to at least abstract depth.

**CompactionRL: Reinforcement Learning with Context Compaction for Long-Horizon Agents** —
arXiv:2607.05378, Yujiang Li, Zhenyu Hou, Yi Jing, Jie Tang, Yuxiao Dong (Tsinghua),
6 July 2026 **[DOC]**. Makes compaction part of *rollout collection* rather than a
serving-time patch: when the interaction history approaches the context limit, the agent
emits a compact summary of the trajectory so far and resumes from a reconstructed context
of **summary + a short tail of recent interaction**; task execution and summary generation
are jointly optimised with token-level loss normalisation and cross-trajectory GAE.
Consistent gains on agentic coding tasks.
**Relevance to Aegis: the *shape* (threshold-triggered, summary + recent tail) is directly
usable. The *method* (RL fine-tuning) is not — Aegis does not train the models it calls.**

**Self-GC: Self-Governing Context for Long-Horizon LLM Agents** — arXiv:2607.00692, Xubin
Hao, Hongjin Meng, Xin Yin, Jiawei Zhu, Chenpeng Cao, 1 July 2026 **[DOC]**. Treats context
management as **runtime lifecycle control over structured objects** rather than text
cleanup: user interactions and tool outputs become indexed, recoverable objects a planner
can fold, mask or prune. Reported: 43.95 % of prefix tokens removed with task continuity
preserved in 84.85 % of cases.
**Relevance to Aegis: "recoverable objects" maps cleanly onto `SubAgentResult.tool_calls`,
which already retains every call's `id`, `name`, `args`, `ok` and `summary`
(`subagent.py:692-701`) [VERIFIED]. Aegis can drop a tool message from `messages` and still
have the object — the retrieval half of Self-GC is nearly free here.**

**Beyond Compaction: Structured Context Eviction for Long-Horizon Agents** —
arXiv:2606.11213, Andrew Semenov and Svyatoslav Dorofeev, submitted 1 May 2026 **[DOC]**.
Introduces **Context Window Lifecycle (CWL)**: work is structured into typed,
dependency-linked episodes, and when the budget is exceeded the system evicts
**"oldest-and-most-recoverable content according to the dependency graph rather than
oldest-in-time regardless of relevance."** Its argument against summarisation-based
compaction is the most useful paragraph in the three papers, and it names four failure
modes: **unpredictable information loss, destruction of causal relationships, blocking on
model cost, and hallucinations induced by compression.** Evaluation: 89 sequential tasks
across 80 M tokens with no degradation against isolated sessions.
**Relevance to Aegis: this is the paper that argues Track B should start with eviction, not
summarisation — and it is why §6 orders it that way.**

**Also found and named, not read [DOC]:** *Slipstream: Trajectory-Grounded Compaction
Validation for Long-Horizon Agents* (arXiv:2605.08580) — a validation harness for
compaction, which is the missing piece if Track B is ever built; *Parallel Context
Compaction for Long-Horizon LLM Agent Serving* (arXiv:2605.23296); *LCM: Lossless Context
Management* (arXiv:2605.04050).

### 3.3 The taxonomy this plan uses

- **Reactive compaction** — triggered when accumulated tokens cross a threshold
  (CompactionRL's shape). Cheap, predictable, no cost when the threshold is never reached.
- **Periodic compaction** — every N steps regardless. Pays on every run; wrong for a
  four-step lane.
- **Structured eviction** — drop content by recoverability and dependency, not by age
  (CWL / Self-GC). **No model call at all**, which is why it belongs first.
- **Summarisation** — a model call producing lossy prose. Last resort, and the one that
  introduces a new hallucination surface.

---

## 4. How much this actually matters **here** — the honest sizing

This section exists so the plan cannot be read as "Aegis urgently needs compaction". It
does not.

### 4.1 Aegis's runs are short and fan-out-shaped

**[DERIVED from verified constants.]** Worst case for one turn:

- **Main path:** ≤ 2 planning rounds, each a *rebuilt* prompt (`state.py:24-27`). Not a
  growing trajectory. Additional bounded model calls: depth classifier, query rewrite,
  ≤ 2 agentic-retrieval rounds, team planner, synthesis.
- **Fan-out:** ≤ 4 lanes × ≤ 4 steps = **≤ 16 sub-agent model calls**, each lane's prompt
  growing across at most four steps.
- **Whole turn wall clock:** ≤ 120 s (`team_wall_clock_s`).

Per lane, the accumulated trajectory is: system prompt + working-memory block (≤ 8000 −
1200 = **6800 tokens**, `config.py:107-108`) + task + at most four `(assistant, tool…)`
rounds. **If tool summaries were bounded at, say, 2 000 tokens, a lane's worst-case prompt
on its final step is roughly 7 K + 4 × 2 K ≈ 15 K tokens** — comfortably inside any
modern window, though already inside the degraded regime Chroma reports (§3.1).

**The point of that arithmetic is what it exposes: the growth term is `steps ×
tool_summary_size`, and only one of those two factors is bounded.** Aegis's exposure is not
turn count. **It is one large tool result.** That reframing is the central finding of this
document, and it is why Track A's second knob (`max_tool_result_tokens`) matters more than
its first.

### 4.2 Compare a coding harness

A coding agent runs hundreds of turns, reads whole files, accumulates diffs and test output,
and routinely exceeds any window mid-task. Compaction there is **existential** — it is the
difference between finishing a task and not. That is the setting CompactionRL and CWL are
evaluated in (89 tasks / 80 M tokens). **Aegis is not that system, and a plan that borrowed
that urgency would be borrowing someone else's problem.**

### 4.3 So what is the honest claim?

> *Aegis's runs are bounded to at most two planning rounds and four four-step lanes inside a
> 120-second wall clock. Trajectory compaction — summarising a run's own history to keep it
> inside a context window — solves a problem those bounds mostly prevent. What those bounds
> do **not** prevent is a single unbounded tool result entering a lane's context, and what
> Aegis lacks is any bound denominated in tokens. That is the gap, and a ceiling closes it.*

That paragraph, or something close to it, is what §5.5 puts in the architecture document.

### 4.4 The measurement this plan did not take, and owes

**No token-size distribution of real trajectories was measured.** Nothing in the repository
records the size of the `messages` list a lane sends on its final step; `SubAgentResult`
accrues `prompt_tokens` and `completion_tokens` per lane (`subagent.py:826-827`)
**[VERIFIED]**, which is the *sum across steps*, not the peak prompt. **Task A0 (§5.1) is to
log the peak, because a ceiling chosen without one is a guess.**

---

## 5. Track A — the enforced ceiling (the honest short-term answer)

**Cost: roughly one day.** Two config fields, two enforcement points, one enum value, one
architecture paragraph, four tests, one frontend surface. **No model call. No new failure
mode. No behaviour change on any run that does not exceed the ceiling.**

### A0 — Measure first (half a day, and it gates everything else)

Add a debug-level log line (or, better, an OTel span attribute on the existing
`subagent.<role>` span at `subagent.py:536-545`) recording
`count_tokens(json.dumps(messages))` immediately before each `deps.complete` call. Reuse
`aegis.memory.tokens.count_tokens` — it already degrades to `len // 4` offline
(`tokens.py:1-6`) **[VERIFIED]**, and a monotone estimate is all a ceiling needs.

Run the demo flows and the eval goldset. **Set the default ceiling at roughly 3× the
observed p99, not at a round number**, and write the observed figure into the docstring so
the next person can check whether it still holds. **Mark it [MEASURED] when you do — this
document cannot.**

### A1 — Two fields on `AgentConfig`

In `aegis/src/aegis/agent/deps.py` (`AgentConfig` at `:294-391`):

```python
#: Hard ceiling on the tokens one sub-agent lane's trajectory may reach before its
#: next model call. Counts the whole `messages` list, estimated with
#: aegis.memory.tokens.count_tokens. Exceeding it ends the lane at CEILING — a
#: designed terminal state like TIMEOUT, not an error. The lane's findings so far
#: are kept and the synthesis names it as cut short.
max_trajectory_tokens: int = <from A0>

#: Hard ceiling on ONE tool result's contribution to a lane's trajectory. A longer
#: summary is truncated with an explicit marker before it is appended, and the full
#: text remains available on SubAgentResult.tool_calls — the model loses the tail,
#: the record does not.
max_tool_result_tokens: int = <from A0>
```

**Both must also be added to `as_dict()`** (`deps.py:401-420`) or
`test_as_dict_lists_every_config_field` fails **[VERIFIED
`aegis/tests/agent/test_harness_config.py:44`]**.

### A2 — Two `_KNOB_SPECS` entries (not optional)

`aegis/src/aegis/agent/harness.py:53-55` **[VERIFIED]** carries the comment *"here exactly
once (guarded by `test_harness_config_covers_every_knob`)"*. Four tests bind:

- `test_harness_config_covers_every_knob` (`test_harness_config.py:55`) — every effective
  key needs a knob.
- `test_every_knob_carries_a_doc_string` (`:100`).
- `test_every_knob_declares_the_type_its_value_actually_has` (`:115`).
- `test_every_numeric_knob_is_bounded_and_the_bounds_admit_the_default` (`:135`) —
  **declare `minimum` and `maximum`, and make sure the default sits inside them.**

And the harness screen counts uncovered keys on the page: `HarnessView.tsx:237` builds
`new Set(config.knobs.map(k => k.key))` and renders *"N effective keys with no knob"* at
`:294` **[VERIFIED]**. Skipping A2 puts a visible defect on a screen whose entire argument
is that the knob surface is complete.

### A3 — A tenant-tightenable binding

`aegis/src/aegis/settings/agent.py:88-110` **[VERIFIED]** wires catalogue keys to
`AgentConfig` fields via `_Binding`, folded per run by `resolve_agent_config`, **taking
whichever value is stricter** (`deps.py:302-309`). Add:

```python
_Binding(key="agent.max_trajectory_tokens", field="max_trajectory_tokens"),
_Binding(key="agent.max_tool_result_tokens", field="max_tool_result_tokens"),
```

with `TIGHTEN_ONLY` merge in the settings catalogue, so a tenant may ask for a smaller
ceiling and can never raise the platform's. **[UNVERIFIED]** — read
`aegis/src/aegis/settings/spec.py` for the `SettingSpec` shape and the catalogue location
before writing the entries; this plan did not read it.

### A4 — Enforce it, in two places, fail-closed

**Per lane, before each model call** — in `_run_subagent_loop`, immediately before
`deps.complete` at `subagent.py:559-561`:

```
if count_tokens(render(messages)) > config.max_trajectory_tokens:
    result.status = SubAgentStatus.CEILING
    result.error = f"trajectory ceiling {config.max_trajectory_tokens} tokens exceeded at step {step}"
    writer(events.agent_status(..., status="ceiling", detail=result.error))
    result.findings = _last_assistant_text(messages)   # keep what was learned
    return
```

**Per tool result, before appending** — at `subagent.py:636` and the two sibling appends at
`:583` / `:608`, truncate `summary` to `max_tool_result_tokens` with an explicit marker
(`"… [truncated: N tokens omitted; full text retained on the run record]"`). The untruncated
text still reaches `result.tool_calls` at `:692-701`, so the **record** is complete even
though the **prompt** is not. That asymmetry is the design and it must be stated in the
docstring, or someone will later "fix" it by truncating both.

**Which of the two is load-bearing?** The tool-result cap. It is the term that actually
grows (§4.1). The trajectory ceiling is the backstop that catches everything else, including
a pathological system prompt or an oversized working-memory block.

### A5 — The refusal must be visible

`SubAgentStatus` is `OK | FAILED | TIMEOUT | CANCELLED`, and its docstring already
establishes the precedent: *"`TIMEOUT` is a **designed** terminal state, not an error: the
synthesis names the agent as omitted and says why, which is what turns a spinning card into
visible, graceful degradation"* (`subagent.py:105-117`) **[VERIFIED]**. **Add
`CEILING = "ceiling"` there.** It is a small, local enum change with an established meaning.

**Do not add a `RunStatus` value.** `RunStatus` is `COMPLETED | BLOCKED |
AWAITING_APPROVAL | REJECTED | ERROR` and is *"re-exported by the host's API schema layer"*
(`aegis/src/aegis/core/types.py:38-67`) **[VERIFIED]** — a wire contract. Adding to it costs
the OpenAPI snapshot, the run-list and console renderers, and every analytics grouping keyed
on status, for a case that is a *degradation of one lane*, not a terminal state of the run.
The whole-run outcome stays `COMPLETED` with the synthesis naming the cut-short lane,
exactly as a `TIMEOUT` behaves today.

**[UNVERIFIED — decide explicitly.]** The single-pass (non-team) path has no lane and
therefore no place to put a `CEILING`. Because the main graph rebuilds `messages` every
round (§2.2), the ceiling there can only be tripped by an oversized *assembled prompt*,
which is already bounded by the memory layer's `ctx_token_cap`. **This plan's
recommendation: enforce the ceiling on the lane path only, and say so in the docstring and
the architecture paragraph.** Enforcing it on a path where it cannot fire is worse than not
enforcing it, because it reads as coverage.

### A6 — Frontend: one line each in two files

- `web/src/components/console/agentLanes.ts` **[VERIFIED]** — the lane status union at
  `:43`, the `TERMINAL` set at `:48` (`['done','failed','timeout','blocked']`), and the
  failure-tone predicate at `:57` (`status === 'failed' || status === 'timeout'`). Add
  `'ceiling'` to the union and to `TERMINAL`. **Judgement: give it its own tone rather than
  folding it into the failure predicate** — a lane that hit a ceiling produced findings and
  is not a failure, which is the same distinction `TIMEOUT` earns.
- `web/src/components/harness/HarnessView.tsx` — no code change; the knob table at
  `:305-340` renders whatever the endpoint sends. **Verify the two new rows appear and the
  "effective keys with no knob" counter stays at zero.**

### A7 — The architecture paragraph (this is the deliverable, not a footnote)

Add to `docs/architecture/system-architecture.md` §10, *"What this document does not
claim"* (`:359-375`) **[VERIFIED]**, immediately after the checkpoint bullet it belongs
beside:

> - **There is no trajectory compaction, and there is now a ceiling instead.** Nothing in
>   Aegis summarises or evicts a run's own turn history. The memory subsystem
>   (`aegis/src/aegis/memory/`) budgets and orders *recalled* material across turns; it
>   never sees the trajectory a single run accumulates. That trajectory exists in exactly
>   one place — a sub-agent lane's `messages` list — and it is now bounded twice: by
>   `AgentConfig.max_trajectory_tokens` before each model call, and by
>   `AgentConfig.max_tool_result_tokens` on each tool result before it is appended. A lane
>   that reaches the ceiling ends at `SubAgentStatus.CEILING`, keeps the findings it
>   already has, and is named as cut short by the synthesis — the same designed terminal
>   state a timeout gets. **The ceiling is a refusal, not a compaction:** the lane stops
>   rather than continuing on a summarised history, because summarising would put a model
>   call, and a compression-hallucination surface, on the run path. Long-horizon runs that
>   would need compaction are therefore **out of scope by design**, not merely unbuilt —
>   see `docs/dev_new_docs_v2/sota/07-long-horizon-ceiling.md` for what building it would
>   cost.

Regenerate `docs/architecture/system-architecture.html` alongside it.

### A8 — Update the compliance row that points here

`docs/dev_new_docs_v2/sota/06-compliance-asi-india.md` maps **ASI08 Cascading Failures** as
`partial` with the gap *"every bound is on steps and wall clock; nothing bounds the context
a run accumulates"*, and names this document as the fix. **When Track A lands, that gap
sentence must be rewritten in the same change.** A gap sentence that outlives its gap is
the same defect class as a stale total, and the compliance suite cannot detect it —
`test_anything_short_of_enforced_names_what_is_missing` only checks the sentence exists.

**Whether ASI08 may then move to `enforced` is a judgement, and this plan's answer is no**:
checkpoint storage is still unbounded (§2.5), so a real layer of the framework's control is
still missing. Tighten the sentence; do not flip the state.

---

## 6. Track B — real trajectory compaction

**Cost: one to two weeks including evaluation, plus a permanent new failure mode.** Read
§6.4 before committing.

### B1 — Where it would live

A new module `aegis/src/aegis/agent/compaction.py`, called from exactly one place: the
sub-agent loop, immediately before the ceiling check of A4. Not from `plan` — the main graph
rebuilds its prompt each round and has nothing to compact (§2.2).

**Keep it a pure function of `(messages, config, tool_call_records)` returning new
`messages` plus a decision record.** The lane loop stays readable and the module is
testable with scripted fakes, which is the shape the rest of `aegis` uses
(`consolidate.py`'s DI of `complete`/`embed`/`spec`).

### B2 — Eviction before summarisation (the CWL argument)

Structured eviction costs **no model call** and CWL's four objections to summarisation
(§3.2) all apply to a lane. Order the passes:

1. **Drop recoverable tool results.** `SubAgentResult.tool_calls` already retains every
   call's `id`/`name`/`args`/`ok`/`summary` (`subagent.py:692-701`) **[VERIFIED]**, so a
   tool message removed from `messages` is **not lost** — it is exactly Self-GC's
   "indexed, recoverable object". Replace it with a one-line stub naming the tool and its
   `ok`, and — if a `load_tool_result` tool is added — the model can fetch it back. **This
   single pass is most of the win, and it is deterministic.**
2. **Drop refusal and proposal messages first.** `:583` (refused tool) and `:608` (proposed
   for approval) are template strings the model has already acted on; their causal effect
   is in the assistant turn that follows. Highest recoverability, lowest cost — CWL's
   "oldest-and-most-recoverable" rule picks these first.
3. **Only then summarise**, and only the prefix, keeping the CompactionRL shape: **summary
   + a short tail of recent interaction**, never a summary alone.

### B3 — Reactive, never periodic

Trigger on `count_tokens(messages) > compaction_threshold`, where the threshold sits **below**
`max_trajectory_tokens` from Track A. **Track A's ceiling remains, as the backstop for when
compaction fails or is disabled.** A run that never crosses the threshold pays nothing —
which for Aegis's four-step lanes is most runs, and is exactly why periodic compaction is
the wrong choice here.

### B4 — The four things that make this genuinely expensive

**(a) The summariser is a model call on the run path, and it must be governed.** Route it
through the same chokepoint as everything else: `ModelRole.CHEAP`, subject to
`enforce_governance`, writing a `usage_ledger` row. **An ungoverned compaction call is the
identical defect Phase 11's L3 task exists to fix** — a model call that skips budget,
ledger and rails because it was made from an unexpected place. It must also be visible: a
compaction that quietly doubles a lane's model calls while the console shows four steps is
a lie by omission.

**(b) It puts an LLM on a path this codebase has kept deterministic.** `working.py:11-14`
states the rule for the read path: *"Deterministic budget (BLOCKER 3 — no LLM on the read
path) … Never a model call — regenerating a summary is a background consolidation job"*
**[VERIFIED]**. The trajectory is a different path, so this is not a violation — **but the
same discipline applies**: compaction must be **off by default**, behind a flag, with the
deterministic eviction passes (B2.1, B2.2) usable **without** it.

**(c) It opens a laundering surface, and it is ASI06.** A trajectory containing a screened
tool result gets fed to a summariser; the summary is prose the model then reads as
established fact. `screen_tool_result` ran on the *original* text
(`subagent.py:681-683`) **[VERIFIED]** — nothing re-screens the *summary*. A sufficiently
clever poisoned record could survive redaction and re-emerge, laundered, as the agent's own
narration. **Mitigation, non-negotiable: the compaction summary is spotlighted (the same
`aegis.retrieval.spotlight` wrapper the episodic tier uses at `working.py:322`) and passed
back through the tool-result rail before it re-enters `messages`.** Doc 06 maps ASI06 as
`partial`; Track B *widens* that gap unless this is built with it.

**(d) It needs its own evaluation, and Aegis has no harness for one.** "Did compaction lose
something the run needed?" is not answerable by the existing eval goldset, which scores
answers rather than trajectories. Slipstream (arXiv:2605.08580) is precisely a
trajectory-grounded compaction validator **[DOC, not read]** and is the right place to
start. **Budget the eval, not just the feature.** Without it, compaction is a silent quality
regression with a plausible-looking implementation — the worst possible shape for this
codebase.

### B5 — Cost, side by side

| | Track A — ceiling | Track B — compaction |
|---|---|---|
| Effort | ~1 day (+ ½ day measurement) | 1–2 weeks + evaluation |
| Model calls added | **zero** | one cheap call per compaction event |
| New failure modes | none | compression hallucination; laundered injection; ungoverned spend |
| Behaviour on a normal run | unchanged | unchanged (below threshold) |
| Value for Aegis's runs | closes a real, unbounded input | small — the bounds mostly prevent the problem |
| Value for a coding harness | a crash guard | **existential** |
| Reversible? | yes, one flag | yes, but the eval debt is not |

**Recommendation: build A. Do not build B for the hackathon.** Write B down — which this
document is — so the decision is recorded as a decision rather than as an omission, and so
nobody re-derives it in a week.

---

## 7. VERIFICATION SECTION — mandatory

### 7.1 Endpoints, with expected responses

Backend on `:8000` (`scripts/dev-native.sh:55`); `/v1` prefix applied at mount
(`main.py:771`); seed password `AEGIS_SEED_PASSWORD` or `demo` (`seed.py:104-109`) — all
**[VERIFIED]**.

**`GET /v1/harness/config`** is the surface that proves Track A exists. It is guarded to
admin / ai_team (`backend/src/app/api/routes.py:3943-3956`) **[VERIFIED]**.

```bash
TOKEN=$(curl -s -X POST localhost:8000/v1/auth/login \
  -H 'content-type: application/json' \
  -d '{"username":"admin","password":"demo"}' | jq -r .token)

curl -s localhost:8000/v1/harness/config -H "authorization: Bearer $TOKEN" \
  | jq '{n: (.knobs|length),
         new: [.knobs[] | select(.key|test("trajectory|tool_result"))],
         orphans: [(.effective|keys[]) as $k
                   | select([.knobs[].key] | index($k) | not) | $k]}'
```
*Expected:* `orphans == []` — **this is the assertion that matters**, because a non-empty
list is what the harness screen renders as *"N effective keys with no knob"*
(`HarnessView.tsx:294`) **[VERIFIED]**. `new` contains exactly two entries, each with
`type: "int"`, a non-empty `doc`, and **both a `minimum` and a `maximum` bracketing its
`default`**.

```bash
curl -s localhost:8000/v1/harness/config -H "authorization: Bearer $TOKEN" \
  | jq '.effective | {max_trajectory_tokens, max_tool_result_tokens,
                      subagent_max_steps, max_plan_iterations, team_wall_clock_s}'
```
*Expected:* the two new values present and equal to the `default` on a fresh config
(`test_harness_config_defaults_match_fresh_config`, `test_harness_config.py:61`)
**[VERIFIED]**.

**The refusal, end to end.** Drive a query whose lane trips the ceiling — the reliable way
is a scripted fake tool returning a large payload, in a test; **[UNVERIFIED]** whether any
seeded demo tool can produce one, so do not plan a manual curl reproduction around it.
Against the SSE `/v1/query` stream, the expectation is:

```bash
curl -sN -X POST localhost:8000/v1/query -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"query":"<a query that fans out>"}' \
  | grep -E 'agent_status|run_finished'
```
*Expected:* an `agent_status` event with `status: "ceiling"` and a `detail` naming the
ceiling and the step; the run still finishes `completed`; the synthesis names the lane as
cut short; **and the lane's partial `findings` are present, not discarded.** A ceiling that
throws away what the lane already learned is a bug, not a bound.

**Nothing else moves.**
```bash
curl -s localhost:8000/v1/compliance -H "authorization: Bearer $TOKEN" | jq '.coverage'
curl -s localhost:8000/v1/security/posture -H "authorization: Bearer $TOKEN" | jq 'length'
```
*Expected:* identical to before Track A **unless** A8's ASI08 edit landed in the same
change, in which case only that row's `gap` string differs and the four state counts are
unchanged.

### 7.2 Tests to write, and where

| Test | File | What it stops |
|---|---|---|
| `test_a_lane_over_the_trajectory_ceiling_ends_at_ceiling_not_ok` | `aegis/tests/agent/test_subagent_gate.py` | a bound that is configured but not enforced |
| `test_a_ceilinged_lane_keeps_the_findings_it_already_had` | same | throwing away work at the bound |
| `test_an_oversized_tool_result_is_truncated_before_it_enters_messages` | same | the growth term (§4.1) staying unbounded |
| `test_the_full_tool_result_is_still_on_the_run_record_after_truncation` | same | truncating the record along with the prompt |
| `test_the_ceiling_is_a_designed_terminal_state_not_an_error` | `aegis/tests/agent/test_terminal_outcome_and_cost.py` | `CEILING` leaking out as `RunStatus.ERROR` |
| `test_a_tenant_may_tighten_but_never_raise_the_trajectory_ceiling` | `aegis/tests/settings/` **[UNVERIFIED dir]** | `TIGHTEN_ONLY` being decorative |

**Volume restraint, deliberately.** Six tests, each pinning one load-bearing claim or its
failure mode. Do not write a test per knob value; `test_harness_config.py` already sweeps
the knob surface generically (`:55`, `:100`, `:115`, `:135`) **[VERIFIED]** and the two new
fields inherit all four for free.

**Existing tests that must still pass unchanged** — these are the guard that Track A changed
a bound and not a behaviour:

```bash
cd aegis && python -m pytest \
  tests/agent/test_harness_config.py \
  tests/agent/test_subagent_gate.py \
  tests/agent/test_fanout_bounds_and_budget.py \
  tests/agent/test_team_fanout.py \
  tests/agent/test_terminal_outcome_and_cost.py \
  tests/agent/test_self_repair_loop.py -q
```
Specifically: `test_the_step_cap_terminates_a_tool_hungry_agent`,
`test_the_team_wall_clock_cuts_a_lane_that_outlives_it`,
`test_the_default_team_wall_clock_fits_its_own_per_lane_bounds`,
`test_a_saturated_team_reports_timeouts_not_wall_clock_cancellations`,
`test_budget_exceeded_is_the_one_exception_allowed_out` — all **[VERIFIED to exist]**. The
last one matters most: `BudgetExceededError` must **still** be the one exception that
escapes a lane. A ceiling check placed above the `except BudgetExceededError` handler at
`subagent.py:440` would change that, and the test is what catches it.

Then the full suites and the frontend:
```bash
cd aegis && python -m pytest tests -q          # [UNVERIFIED runner path]
backend/.venv/bin/python -m pytest backend/tests -q
cd web && npm run typecheck && node --test tests/**/*.test.mjs
```

### 7.3 Frontend surfaces affected

- **`web/src/components/console/agentLanes.ts:43,48,57` [VERIFIED]** — the only file that
  must change for the new lane status. The status union, the `TERMINAL` set, and the
  failure-tone predicate. **Check the rendered lane card**: a `ceiling` lane must reach a
  terminal visual state (not a spinner), must be visually distinct from `failed`, and must
  still show its findings.
- **`web/src/components/harness/HarnessView.tsx` [VERIFIED]** — no code change. Verify: the
  knob count at `:267` increases by two; the two new rows render with their bounds via
  `fmtConstraint` (`:52-58`); the changed-from-default list (`:211`) is unaffected on a
  fresh config; and the *"N effective keys with no knob"* warning at `:294` stays absent.
- **`web/src/components/settings/settingsCatalogue.ts` [VERIFIED exists]** — if A3's
  catalogue keys are added, the tenant settings screen renders them. Confirm they show as
  `TIGHTEN_ONLY` and that a tenant cannot type a value above the platform's.
- **Compliance surfaces** — `ComplianceView.tsx` and `StandardsBand.tsx` change **only** if
  A8's ASI08 gap edit lands. No count moves; the `partial` state is unchanged; the landing
  band never prints a `gap`.

### 7.4 Definition of done

- [ ] A0's peak-prompt measurement exists, is recorded in the docstring, and is marked
      `[MEASURED]` by whoever took it.
- [ ] Both fields are on `AgentConfig`, in `as_dict()`, and in `_KNOB_SPECS` with bounds
      that admit their defaults.
- [ ] `GET /v1/harness/config` reports zero orphan effective keys.
- [ ] A lane over the ceiling ends at `CEILING`, **keeps its findings**, and the run still
      completes with the synthesis naming it.
- [ ] An oversized tool result is truncated in the prompt and **whole** on the record.
- [ ] `BudgetExceededError` is still the one exception that escapes a lane.
- [ ] No `RunStatus` value was added.
- [ ] `system-architecture.md` §10 states the limit, states that compaction is out of scope
      **by design**, and links here.
- [ ] The console renders `ceiling` as terminal and distinct from `failed`.
- [ ] If A8 landed: ASI08's gap sentence no longer names a bound that now exists, and its
      state did **not** move to `enforced`.

---

## 8. Risks, stated plainly

1. **The default ceiling is a guess until A0 runs.** Set too low, it cuts good runs short
   and the demo degrades visibly; too high, it is decoration. **A0 gates A1 — do not invert
   them because measurement is boring.**
2. **A ceiling makes a previously-invisible failure visible.** Lanes that quietly sent
   oversized prompts will now stop. On stage that reads as a regression unless the synthesis
   says *why*. The wording of the cut-short sentence is a demo-critical detail, not
   cosmetics.
3. **`count_tokens` degrades to `len // 4` without `tiktoken`** (`tokens.py:1-6`)
   **[VERIFIED]**. The estimate is monotone, which is all eviction needs — but a ceiling is
   a *refusal*, and refusing on a 4-chars-per-token heuristic will fire early or late on
   non-English text. **Either pin `tiktoken` as a real dependency on the ceiling path, or
   state the heuristic in the knob's `doc` string so the bound is honest about its own
   precision.** This plan did not check whether `tiktoken` is installed in the shipped
   environment — **[UNVERIFIED]**, check `backend/uv.lock`.
4. **The tool-result truncation could hide the very thing a run needed.** Mitigated by the
   full text staying on `SubAgentResult.tool_calls`, but a model that never sees the tail
   cannot ask for it unless a fetch-back tool exists. **B2.1's `load_tool_result` is
   arguably a Track A item**; this plan leaves it in B and flags the judgement.
5. **Track B's compaction call is spend on the run path.** If it is ever built and is not
   routed through `enforce_governance`, it is an ungoverned model call — the exact defect
   Phase 11's L3 exists to fix, reintroduced from a new direction.
6. **Track B laundering (§6.4c) is a real security regression, not a theoretical one.** A
   summariser downstream of a screened tool result with no re-screening is a bypass of the
   tool-result rail. Do not ship B without the spotlight-and-re-screen step.
7. **The peak-prompt figure is unmeasured and §4.1's arithmetic is `[DERIVED]`.** If real
   tool summaries are two orders of magnitude larger than assumed, the sizing conclusion in
   §4 ("compaction matters little here") is wrong and Track B moves up. **A0 is what
   settles it.**
8. **Checkpoint growth is not addressed and must not be described as if it were**
   (`system-architecture.md:370-372`) **[VERIFIED]**.

---

## 9. What this plan does **not** cover

- **The main graph.** It rebuilds `messages` per planning round and accumulates no
  trajectory; the ceiling is a lane-path control and §5's A5 says so explicitly.
- **Checkpoint pruning, `audit_log` retention and partitioning.** Named as owed work in the
  architecture document; untouched here.
- **Memory-subsystem changes of any kind.** No new tier, no cap change, no eviction-order
  change. `ctx_token_cap`, `per_tier_caps`, `_LAYOUT` and `_EVICT_ORDER` are correct as they
  stand and are not this document's business.
- **Multi-turn conversation length.** `raw_window_turns = 40` and
  `_CONVERSATION_TURN_CAP = 12` already bound what a *session* re-sends
  (`config.py:92`, `working.py:63`) **[VERIFIED]**. A session is not a trajectory.
- **RL training of any kind.** CompactionRL's method requires fine-tuning models Aegis does
  not train. Only its *shape* is borrowed.
- **A compaction evaluation harness.** Named as required for Track B (§6.4d) with
  Slipstream as the starting point; not designed here.
- **Any measurement.** Nothing in this document was run. §4.4 and A0 say what is owed and
  who owes it.
