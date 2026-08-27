# Phase 7 — AUDIT GATE

**Commit under audit:** `763fe2b` — *feat(platform,agent): an agent bill of materials, and a bound on a run's own history*
**Also present at close of audit:** `d3aa5c6` — *fix(agent): the per-result token ceiling was a field nothing read* (landed mid-audit; resolves F-5, changes nothing else — every other finding was re-checked against it)
**Branch:** `docs/wow-pass-plan`
**Plans:** `docs/dev_new_docs_v2/sota/05-sbom-agbom.md`, `docs/dev_new_docs_v2/sota/07-long-horizon-ceiling.md`
**Audited:** 2026-08-27, against a live stack (backend :8000 native, web :3001, Postgres/Redis/Neo4j/Qdrant all up)
**Auditor role:** find what is wrong. Nothing was fixed. No repo source was modified.

Evidence markers: **[MEASURED]** = I ran it and this is the output. **[SOURCE]** = read from the file at the cited line. **[DOC]** = read from a plan/spec document.

---

## VERDICT: **FAIL**

Two of the four claims hold up. Two do not, and the failures are of the specific kind this build has told itself it is guarding against.

| # | Claim | Result |
|---|---|---|
| 1 | `agbom.py` builds a CycloneDX 1.6 AgBOM; `GET /v1/platform/agbom` serves it; a download button sits on the devops **stack** screen | **PARTIAL** — the document is real and RBAC-gated; the button is on the **Patch Check** screen, not the stack screen |
| 2 | Tools emitted as `application` not `tool`, because `tool` is not a CycloneDX component type | **HOLDS** — verified against the published schema, both directions |
| 3 | Models reported as `requested`, with `usage_ledger` named as where the answering model lives | **FAILS** — the stated reason is factually false, and the model list is non-deterministic within one process |
| 4 | A trajectory ceiling with `CEILING` as a designed terminal state; the lane stops, what it found is kept, the synthesis says it was cut short | **FAILS** — the lane reports `done` on the wire, its findings are discarded, and the synthesis says "returned nothing usable" |

**Why FAIL and not PASS WITH FINDINGS.** F-1 and F-2 are not gaps in coverage; they are statements in the commit message and in a module docstring that the running system contradicts. `no-dishonest-fallbacks` is the standing rule for this repo. A docstring that explains *why* a design is honest, whose premise is false, is worse than no docstring — it inoculates the next reader against checking. F-1 is reproducible in six lines.

---

## Severity index

| ID | Severity | One line |
|----|----------|----------|
| **F-1** | **CRITICAL** | The `requested`-vs-observed rationale is false: `.env` **is** in `os.environ`, and the AgBOM's model list changes inside one process |
| **F-2** | **CRITICAL** | A `CEILING` lane emits `status="done"`, its findings are dropped, and the synthesis says "returned nothing usable" |
| **F-3** | **HIGH** | The AgBOM emits model ids that are not in `_FLEET_DECLARATION`, and omits ones that have demonstrably answered |
| **F-4** | **HIGH** | No schema-validation test. Plan task **S4** — the task that settles the `tool` question — was not built |
| **F-5** | **RESOLVED** | `max_tool_result_tokens` was declared-but-unbound at `763fe2b`; **fixed by follow-up `d3aa5c6`, which landed during this audit** |
| **F-6** | **MEDIUM** | The ceiling measures `json.dumps(..., ensure_ascii=True)`; Hindi content is inflated **2.12×**, cutting a lane at ~17k real tokens instead of 36k |
| **F-7** | **MEDIUM** | The AgBOM omits MCP peer tools, the local ML spine, knowledge sources, and memory — and its own docstring claims knowledge sources are in it |
| **F-8** | **MEDIUM** | The button is on **Patch Check**, not the **stack** screen the commit names, and is gated behind an ~11 s network call |
| **F-9** | **MEDIUM** | Plan deliverables A3, A6, A7, A8 not built: no tenant binding, no `'ceiling'` in the web union, no architecture paragraph, ASI08 gap sentence stale |
| **F-10** | **LOW** | `Content-Type: application/json`, not `application/vnd.cyclonedx+json` — the plan's acceptance criterion 1, and the sibling SBOM gets it right |
| **F-11** | **LOW** | An intermittent tenant-isolation failure in `tests/mcp/` — reproduced once in two full backend runs |
| **F-12** | **LOW** | No `serialNumber`, no `metadata.tools`, no component `version` — the AgBOM cannot be diffed, which is what the litellm story is about |
| **F-13** | **LOW** | `downloadAgbom` has no `.catch`; a failed fetch is a silent no-op plus an unhandled rejection |

---

# A. Does the AgBOM actually validate as CycloneDX 1.6?

**Yes. This part of the commit is true, and I verified the negative claim too.**

Schema fetched from the canonical source: `https://raw.githubusercontent.com/CycloneDX/specification/master/schema/bom-1.6.schema.json` (262,666 bytes).

**[MEASURED]** Validation of the document produced by `build_agbom()`, and of the bytes actually served over HTTP, and of the file the browser actually downloaded:

```
ERRORS: 0                       # build_agbom() in-process
HTTP doc errors: 0              # GET /v1/platform/agbom, devops bearer
downloaded doc validates: True  # aegis-agbom.cyclonedx.json, saved by the browser
```

The validator is not vacuously passing. Positive and negative controls **[MEASURED]**:

```
type=tool errors: 1
    ['components', 0, 'type'] 'tool' is not one of ['application', 'framework',
    'library', 'container', 'platform', 'operating-system', 'device',
    'device-driver', 'firmware', 'file', 'machine-learning-model', 'data',
    'cryptographic-asset']
bogus root key errors: 1        # additionalProperties: false is enforced
missing bomFormat errors: 1     # required is enforced
external refs in schema: ['jsf-0.82.schema.json#/definitions/signature', 'spdx.schema.json']
```

The two external `$ref`s are only reachable from `signature` and SPDX license expressions, neither of which this document uses, so no resolver was needed and no branch was silently skipped.

### The specific checks requested

| Check | Result |
|---|---|
| `bom-ref` values valid? | **Yes.** `refType` is `{"type":"string","minLength":1}` **[SOURCE schema]**. `tool/find_requests`, `service/aegis` etc. are legal. The only SHOULD is "do not start with `urn:cdx:`" — respected. |
| `dependencies` well-formed? | **Yes.** Every `ref` and every `dependsOn` entry resolves to a component `bom-ref` or to `metadata.component`'s `service/aegis`. **[MEASURED]** — the 14-entry graph was walked; zero dangling refs. Every component also appears as its own empty-`dependsOn` element, which is what the spec asks for ("components that do not have their own dependencies must be declared as empty elements"). |
| `properties` correctly shaped? | **Yes.** `{"name": str, "value": str}`, `name` required. Every value is stringified at the source, including the booleans (`"false"` not `false`) — `agbom.py:73-76` **[SOURCE]**. |
| Is `metadata.component` allowed to be `application`? | **Yes.** `metadata.component` is a full `component`, and `application` is in the enum. |
| Is the `type: "tool"` claim true? | **Yes — verified, not taken.** See the positive control above. `tool` is genuinely absent from the CycloneDX 1.6 `component.type` enum, and CycloneDX's `metadata.tools` is a different construct. **This is the one part of the commit message that is exactly right and was worth writing down.** |

### F-12 — LOW — What a validator accepts and a buyer still cannot use

`backend/src/app/platform/agbom.py:169-193` **[SOURCE]**

Missing, all optional under the schema but all load-bearing for the use case the module docstring argues for:

* **`serialNumber`** — absent. Two AgBOMs cannot be told apart as documents.
* **`metadata.tools`** — absent. Nothing records what produced the document. **The sibling dependency SBOM has it** **[MEASURED]**: `sbom metadata keys: ['timestamp', 'tools', 'component', 'properties']` vs `agbom metadata keys: ['timestamp', 'component']`.
* **No `version` on any component.** Every tool and every rail is versionless. The module docstring opens with the March 2026 litellm compromise — an event that is *entirely about a version*. An inventory with no versions cannot answer "did this change since last week", which is the only question a BOM exists to answer over time.

**Fix.** Add `serialNumber` (`urn:uuid:` + a stable digest of the content, so the same inventory keeps the same serial), a `metadata.tools` entry naming Aegis + `PRODUCT_VERSION`, and a `version` on each tool component — the natural one is the prompt/registry revision, or `PRODUCT_VERSION` if nothing finer exists.

### F-10 — LOW — Wrong media type, and the plan said so

**[MEASURED]**

```
GET /v1/platform/agbom      →  ctype=application/json
GET /v1/stack/sbom?...      →  ctype=application/vnd.cyclonedx+json
```

`05-sbom-agbom.md:352` **[DOC]** states the acceptance criterion verbatim: *"and the response `Content-Type: application/vnd.cyclonedx+json`."* The sibling endpoint already does this. The entire stated reason for choosing CycloneDX is "a buyer's existing scanner already reads it"; a scanner that dispatches on media type will not recognise this one.

**Fix.** `routes.py:2023` — return a `Response(content=..., media_type="application/vnd.cyclonedx+json")` as `/stack/sbom` does.

### F-4 — HIGH — The validation I just did is not in the test suite

**[MEASURED]** `grep -rln "agbom" backend/tests aegis/tests` → **0 files**, and the same across the web test files → **0 files.** There is no test of any kind for `agbom.py` or for `/platform/agbom`. (The only non-source hits repo-wide are `PatchCheck.tsx`, `client.ts`, the generated `schema.d.ts`, and the plan doc.)

`05-sbom-agbom.md:321-323` **[DOC]**:

> **S4 — Schema validation against the published CycloneDX 1.6 JSON Schema**, in the test suite, offline, from a vendored copy of the schema. **This is the task that settles the `type: "tool"` question**, and it must run in CI or the document will drift out of validity silently.

The plan named this task, said why it was the important one, and it was not built. The commit's central justification — "a document that fails validation defeats the point of using a standard format" — is currently protected by nothing. Anyone adding a component with a new `type`, or a non-string property value, breaks the document and no test notices.

**Fix.** Vendor `bom-1.6.schema.json` into `backend/tests/data/`, add `backend/tests/platform/test_agbom.py` that validates `build_agbom()` against it, **and** asserts the negative control (`type="tool"` fails) so the divergence recorded in the docstring is a tested fact rather than a comment.

### Also wrong, cosmetic

`agbom.py:157` **[SOURCE]** — the `build_agbom` docstring says *"A CycloneDX 1.6 document describing the agent as a ``service``"*. It does not. The agent is `metadata.component` with `type: "application"`; the `bom-ref` is `service/aegis` but no `services[]` array is emitted. CycloneDX has a real `services` array and it is arguably where an agent belongs. Either use it or fix the sentence.

---

# B. Is the AgBOM honest?

## F-1 — CRITICAL — The `requested` rationale is false, and the document is non-deterministic

This is the finding that decides the verdict.

`backend/src/app/platform/agbom.py:91-101` **[SOURCE]**, repeated near-verbatim in the commit message:

> **"requested", not "in use", and the distinction is not pedantry.** Measured on this deployment: the router asks for `genailab-maas-gpt-4o` while the usage ledger records every answer as coming from `DeepSeek-V4-Flash` — **because the router reads `MODEL_<ROLE>` from the process environment, `.env` is loaded into pydantic settings rather than into `os.environ`**, and the gateway endpoint answers with whatever it actually serves.

The bolded premise is false. **[MEASURED]**, fresh process, `backend/` as cwd:

```
before: None
after litellm: DeepSeek-V4-Flash
```

```python
import os
print('before:', os.environ.get('MODEL_GENERATION'))
import litellm
print('after litellm:', os.environ.get('MODEL_GENERATION'))
```

`import litellm` calls `load_dotenv()`. `backend/.env:40-43` **[SOURCE]** contains:

```
MODEL_GENERATION=DeepSeek-V4-Flash
MODEL_CHEAP=DeepSeek-V4-Flash
MODEL_REASONING=DeepSeek-V4-Flash
MODEL_EMBEDDING=text-embedding-3-large
```

So `.env` **is** loaded into `os.environ`, `_routed_default` at `aegis/src/aegis/gateway/routing.py:328` **[SOURCE]** (`os.environ.get(f"MODEL_{role.name}", ...)`) **does** find it, and the router requests `DeepSeek-V4-Flash` — the very same id the usage ledger records. There is no divergence between "requested" and "observed" to be honest about. The observation the commit calls *"something worth more than the endpoint"* is an artefact of *when* the AgBOM is built relative to the first `import litellm`.

### The consequence: one process, two different inventories

**[MEASURED]** — same process, same code, one import in between:

```
AgBOM models BEFORE litellm import: ['genailab-maas-Llama-3.2-90B-Vision-Instruct',
  'genailab-maas-Phi-4-reasoning', 'genailab-maas-gpt-4o', 'genailab-maas-gpt-4o-mini',
  'genailab-maas-text-embedding-3-large', 'genailab-maas-whisper']
AgBOM models AFTER  litellm import: ['DeepSeek-V4-Flash',
  'genailab-maas-Llama-3.2-90B-Vision-Instruct', 'genailab-maas-whisper',
  'text-embedding-3-large']
SAME PROCESS, SAME CODE, DIFFERENT INVENTORY: True
```

This is not a laboratory result. It happened to me against the live server, unprompted:

* **[MEASURED]** `curl` at 10:20 against pid 11323 → 14 components, 6 models, `genailab-maas-gpt-4o` present.
* **[MEASURED]** the file the *browser* downloaded at 10:35 against **the same pid 11323** → 12 components, 4 models, `DeepSeek-V4-Flash` present, `gpt-4o` gone.

Between the two, some request path imported `litellm` and mutated `os.environ` under the endpoint. Two AgBOMs pulled from one running deployment fifteen minutes apart disagree about what models the agent uses. A buyer who diffs them sees a fleet change that never happened.

`agbom.py:106-109` **[SOURCE]** makes this worse by asserting the opposite:

> Asked of the router rather than read off settings, **so this reports the deployment a call would ACTUALLY reach.** … an inventory that disagrees with the running system is worse than none.

It does not report what a call would reach — it reports what a call would reach *given the import state of the process at the instant of the request*.

**Fix.** Three things, and the first is not optional:

1. Delete the false explanation from `agbom.py:91-101` and from any doc that repeats it. Re-derive the real relationship between `.env`, `os.environ` and the ledger before writing a replacement.
2. Make the document deterministic: resolve the routing from a single explicit source at import (or force `load_dotenv()` at app startup before anything reads `MODEL_*`) so the answer cannot change mid-process.
3. Report the **whole fleet** (see F-3), not the currently-selected slice — a fleet declaration does not move under you.

## F-3 — HIGH — The model inventory is both over- and under-inclusive

`agbom.py:110-114` **[SOURCE]** iterates `ModelRole` and calls `model_for(role)`, which returns *one* deployment per role.

**Under-inclusive.** `_FLEET_DECLARATION` declares **12** deployments (`aegis/src/aegis/gateway/routing.py:138-176` **[SOURCE]**), four of them `tenant_selectable=True`. The AgBOM reports at most 6, and as served right now, 4. The plan's acceptance criterion 2 **[DOC `05-sbom-agbom.md:356-370`]** says *"Expect **12**"* and *"Expect exactly **four** with `aegis:model:tenant-selectable`"*. There is no `tenant-selectable` property in the document at all. A tenant can select `genailab-maas-DeepSeek-V3-0324` today and it will never appear in the inventory.

The ledger settles it. **[MEASURED]**, `select model, count(*) from usage_ledger group by 1`:

```
 genailab-maas-text-embedding-3-large                 |  3430
 genailab-maas-gpt-4o-mini                            |  3278
 DeepSeek-V4-Flash                                    |  2412
 genailab-maas-gpt-4o                                 |  2225
 genailab-maas-DeepSeek-V3-0324                       |  1750
 genailab-maas-gpt-35-turbo                           |  1356
 genailab-maas-Llama-3.3-70B-Instruct                 |  1349
 genailab-maas-Llama-4-Maverick-17B-128E-Instruct-FP8 |  1346
 genailab-maas-Phi-4-reasoning                        |   862
```

**Five deployments with thousands of recorded answers each are absent from the AgBOM.** The document's own `aegis:observedIn` property points the reader at `usage_ledger` — a table that contradicts it.

**Over-inclusive.** **[MEASURED]** `emitted ids not in _FLEET_DECLARATION: ['DeepSeek-V4-Flash', 'text-embedding-3-large']`. The served document emits `machine-learning-model` components for two ids the platform's own fleet does not declare and the pricing table cannot look up (`_FLEET[id]` would `KeyError`). An inventory that lists models the platform says it does not run is not a smaller problem than one that omits models it does.

**Fix.** Enumerate `_FLEET_DECLARATION` — all 12 — and carry `aegis:model:role`, `aegis:model:tenant-selectable`, and a separate `aegis:routing:default` marker for whichever one the role currently routes to. That is deterministic, complete, and still honest about the difference between "declared" and "in force".

## F-7 — MEDIUM — What a buyer would expect and not find

The module docstring at `agbom.py:11-14` **[SOURCE]** promises *"which tools exist and at what risk tier, which model deployments answer which role, which rails run, **what the agent can read from**"*, and `:34-36` **[SOURCE]** goes further: *"The knowledge sources are the collections this deployment holds, not a promise about their contents."*

**[MEASURED]** `grep -n "knowledge\|collection" backend/src/app/platform/agbom.py` → one hit, and it is line 35, the sentence above. **There are no knowledge components in the document.** The docstring describes a section of the AgBOM that does not exist.

The full list of what is missing from an agent inventory:

| Missing | Where it lives | Why it matters |
|---|---|---|
| **MCP peer tools** | `backend/src/app/mcp/client.py:819-844` — `ExternalToolRegistry` holds `_servers`, `_tools`, `_grants` at runtime | These are, in the module's own words, *"code we did not write, reached over a network, returning content into an agent's context"*, **HIGH risk by default**, with no `InverseAction`. They are exactly the class of component **ASI04** exists for, and they are the only tools in the system whose supply chain the operator does not control. An agent inventory that lists four in-process tools and silently omits every federated one is incomplete in precisely the direction that matters. |
| **Knowledge sources / vector collections** | `aegis/src/aegis/memory/vector_ops.py:77-90` (`aegis_mem` prefix), the Qdrant collections, LightRAG's stores | Promised by the docstring. Absent. |
| **Memory configuration** | the memory subsystem the commit itself praises | What the agent retains across turns, and under what budget, is a blast-radius fact. |
| **The local ML spine** | `aegis/src/aegis/ml/model.py:1-35` — an XGBoost + HistGB soft-voting ensemble, MAPIE conformal, SHAP | This is a `machine-learning-model` in the literal CycloneDX sense, in-process, and the agent surfaces its predictions as evidence. Not listed. |
| **Guardrail posture** | `aegis/src/aegis/settings/spec.py:591-686` — `guardrails.topical.block`, `guardrails.grounding.block`, `guardrails.pii.block`, denylist terms/patterns | `_rail_components()` (`agbom.py:135-150` **[SOURCE]**) emits four names and a stage, nothing else. A rail set to advisory renders identically to one that hard-blocks. `05-sbom-agbom.md:319` **[DOC]** scoped S3 as *"guardrails (with live posture)"*. Delivered: names. |

The tool list itself is **correct** — I checked it. **[MEASURED]** `TOOL_REGISTRY` = `add_case_note(low, rw)`, `assign_request(medium, rw)`, `find_requests(low, ro)`, `update_request_status(high, rw)`; `ALLOWLIST` = `operations_lead`→all four, `client`→`add_case_note`. Every tier, every read-only flag and every persona list in the document matches the registry exactly. Credit where due: this half is derived, not hand-maintained, and it is right.

**Fix.** At minimum, add the MCP peer tools (they are enumerable via `ExternalToolRegistry.tools(include_disabled=True)`) with `aegis:scheme: "mcp"` beside the existing `"local"` — the `scheme` property was clearly put there for this and is currently a constant. Then either build the knowledge/memory sections or **delete the two docstring sentences that claim they are there.**

---

# C. Is `max_tool_result_tokens` enforced?

## F-5 — RESOLVED during the audit — At `763fe2b`: no. Fixed by `d3aa5c6`.

> **Timeline note.** When I started this audit, `HEAD` was `763fe2b` and the fix existed only as uncommitted working-tree changes. Partway through, commit **`d3aa5c6` — *"fix(agent): the per-result token ceiling was a field nothing read"*** (2026-08-27 10:29) landed and committed exactly that work. The analysis below stands as the record of what the audited commit shipped; the remedy is now in the tree. **Everything in F-1, F-2, F-3, F-4, F-6 … F-13 was re-checked against `d3aa5c6` and still holds.**

**[MEASURED]** `git show 763fe2b -- aegis/src/aegis/agent/subagent.py` adds 27 lines: the `CEILING` enum member and the trajectory check. **It does not touch `_tool_message`.** At `HEAD`, `max_tool_result_tokens` appears in exactly three places — `deps.py:414` (declaration), `deps.py:462` (`as_dict`), `harness.py:86` (knob spec) — and is **read by nothing**.

That is the third occurrence of this defect class in this build, after Phase 2's `read_back_for` and Phase 3's memory screen. The field is not merely unused: it is *published* as a tunable on the harness screen, where an operator can set it and watch nothing happen.

It is worse than a normal dead field because of what the docstring says about it. `deps.py:409-414` **[SOURCE]**:

> **This is the bound that actually bites first in practice: a run's real exposure is one unbounded tool result, not a long conversation.**

And `07-long-horizon-ceiling.md:387-397` **[DOC]** agrees: *"**Which of the two is load-bearing?** The tool-result cap."* The commit shipped the backstop and left the load-bearing bound unbound, while stating in its own docstring which one mattered.

**`d3aa5c6` fixes it.** It adds `max_tokens` to `_tool_message` (`subagent.py:739-772`), threads it through the two append sites at `:614` and `:670`, and adds two tests — including `test_the_per_result_ceiling_is_read_by_something`, which greps the package for a reader and whose own docstring names the two prior occurrences of the defect. That is the right instinct and the right test, and it is the sort of test that should have existed before the field shipped.

I verified the implementation on its merits and it is **correct**:

* Truncation is marked, never silent — `[truncated: N tokens exceeded the M-token ceiling for one tool result; the full text is on the run record]`.
* The "full text is on the run record" claim is **true**: `_execute` at `subagent.py:727-733` **[SOURCE]** appends the complete (rail-screened) `summary` to `result.tool_calls` *before* `_tool_message` ever truncates. The record/prompt asymmetry the plan asked for is real.
* **[MEASURED]** `aegis/tests/agent/test_trajectory_ceiling.py` — 6 passed.

**Fix.** None needed — `d3aa5c6` is the fix. The lesson is the process one: `763fe2b` shipped a declared-but-unbound seam for the third time in this build, and it took an audit gate to surface it. The `test_the_per_result_ceiling_is_read_by_something` pattern should be applied to every new `AgentConfig` field at the moment it is added, not after.

---

# D. Attack the ceiling

## F-2 — CRITICAL — Every consequence the commit claims for `CEILING` is false

The commit message, `subagent.py:119-124` **[SOURCE]** and `07-long-horizon-ceiling.md` all state the same three things: **the lane stops** (true), **what it found is kept** (false), **the synthesis says it was cut short** (false).

**[MEASURED]** — a real `run_subagent` with `max_trajectory_tokens=1`, capturing every event the writer received:

```
WIRE agent_status beats: [('started', 'summarise anything'),
                          ('thinking', 'step 1/4'),
                          ('done', '1 step(s), 0 proposed')]
result.status: ceiling
result.contributed: False
_omission_phrase: returned nothing usable
synthesis_note: Synthesised from 0 of 1 agents; the analyst returned nothing usable.
```

### D-2a — The wire says `done`

`subagent.py:562-582` **[SOURCE]** sets `result.status = CEILING` and `break`s. `_loop` then returns normally, so `run_subagent` falls through to the success path at `subagent.py:503-512` **[SOURCE]** and emits `status="done", detail="1 step(s), 0 proposed"`.

Compare `TIMEOUT` at `subagent.py:460-471` **[SOURCE]**, which emits `status="timeout"`. `CEILING` does not sit beside `TIMEOUT`; it is invisible. A lane cut at its ceiling renders on the console as a lane that finished cleanly.

`07-long-horizon-ceiling.md:387` **[DOC]** specified the line that is missing, verbatim:

```python
writer(events.agent_status(..., status="ceiling", detail=result.error))
```

The existing test never catches this because it passes `writer=lambda _e: None` (`test_trajectory_ceiling.py:109` **[SOURCE]**) and never inspects a single event.

### D-2b — The findings are discarded

`subagent.py:243-245` **[SOURCE]**:

```python
@property
def contributed(self) -> bool:
    """Whether this lane produced findings the synthesis can use."""
    return self.status is SubAgentStatus.OK and bool(self.findings.strip())
```

`CEILING` is not `OK`, so `contributed` is `False` **regardless of findings**. `TeamOutcome.contributing` (`team.py:108-110` **[SOURCE]**) filters on it, and `graph.py:686-689` **[SOURCE]** builds the run's `context` from `contributing` only.

**[MEASURED]**, a `CEILING` result carrying real findings:

```
[a] findings present: True
[a] contributed: False
[a] contributing: []
[a] omitted: ['Analyst']
[a] context the graph builds from contributing: ''
```

The lane's own error string, written three lines above in the same function, reads *"…what it found before that is kept."* It is not kept. It is dropped on the floor.

### D-2c — The synthesis says the wrong thing

`team.py:517-526` **[SOURCE]** handles `TIMEOUT`, `CANCELLED` and `FAILED` by name and falls through for everything else:

```python
def _omission_phrase(result: SubAgentResult) -> str:
    if result.status is SubAgentStatus.TIMEOUT:
        return f"timed out ({result.error})"
    if result.status is SubAgentStatus.CANCELLED:
        return f"was cut short ({result.error})"
    if result.status is SubAgentStatus.FAILED:
        return f"failed ({result.error})"
    return "returned nothing usable"
```

A new `SubAgentStatus` member was added and this function — the one place the product turns a lane's terminal state into words a jury reads — was not updated. **[MEASURED]** output: *"Synthesised from 0 of 1 agents; the analyst returned nothing usable."*

The irony is exact. `synthesis_note`'s own docstring says naming the omission *"is what makes the degradation visible **and** graceful"*, and the new designed terminal state is the one it cannot name.

**Fix**, all three, and none is more than a few lines:
1. `subagent.py:503-512` — branch on `result.status is SubAgentStatus.CEILING` and emit `status="ceiling"` with `result.error` as detail.
2. `subagent.py:245` — `return self.status in (SubAgentStatus.OK, SubAgentStatus.CEILING) and bool(self.findings.strip())`, so a lane that found something before the cut still contributes. (If that is judged too permissive, then delete "what it found is kept" from the docstring, the enum comment and the commit narrative.)
3. `team.py:517` — add `if result.status is SubAgentStatus.CEILING: return f"was cut short at its trajectory ceiling ({result.error})"`.
4. Amend the existing test to assert on the emitted events, not just the returned object. The current test's `writer=lambda _e: None` is what let all of this through.

## F-9 (part) — The main graph is unbounded, and the plan said to say so

**[MEASURED]** `grep -rn "max_trajectory_tokens"` across the repo → `deps.py:407`, `deps.py:461`, `harness.py:78`, `subagent.py:568`, and tests. **`graph.py` never reads it.**

So the bound applies to sub-agent lanes only. A **single-lane / non-team run has no trajectory ceiling at all**, and the commit's title — *"a bound on a run's own history"* — is true only for fan-out runs.

**On the merits, the design decision is defensible and the plan made it explicitly.** `graph.py:1060-1069` **[SOURCE]** rebuilds `messages` fresh every planning round (system + one user turn), so there is no accumulating trajectory on that path; its size is driven by retrieval context (`final_top_k = 6`, `retrieval/pipeline.py:105` **[SOURCE]**) and working memory, both separately capped. `07-long-horizon-ceiling.md:413-419` **[DOC]** reached the same conclusion and added a requirement:

> **This plan's recommendation: enforce the ceiling on the lane path only, and say so in the docstring and the architecture paragraph.** Enforcing it on a path where it cannot fire is worse than not enforcing it, because it reads as coverage.

The "and say so" half was not done. **[MEASURED]** `grep -n "trajectory compaction\|ceiling" docs/architecture/system-architecture.md` → one unrelated hit about Qdrant. The A7 architecture paragraph, which the plan calls *"the deliverable, not a footnote"*, does not exist. A reader of `AgentConfig` sees a config-level field named `max_trajectory_tokens` with no hint that half the run paths ignore it.

**Fix.** Add the A7 paragraph as drafted in the plan, and add one sentence to `deps.py:395` saying the field binds sub-agent lanes only and why the main path does not need it.

---

# E. The measurement

## Is 36000 defensible? Not really — but it is under-defended in the opposite direction from the one the commit worries about.

The commit worries the sample is thin (two runs, peak 11,859) and says so honestly in the docstring — that part is good practice and I want it kept. The real problem is different: **with the currently shipped tool set the ceiling is structurally unreachable, and in Hindi it fires far too early.**

### Unreachable, by construction

Every term is already capped by something else:

| Term | Cap | Source |
|---|---|---|
| Steps per lane | **4** | `subagent_max_steps = 4`, `deps.py:437`; `team.py:223` takes the `min` **[SOURCE]** |
| Retrieval context | 6 chunks | `retrieval/pipeline.py:105` `final_top_k = 6` **[SOURCE]** |
| Largest tool result | **25 rows** | `FIND_REQUESTS_MAX_LIMIT = 25`, enforced in the args model *and* re-clamped in the body, `adapter/tools.py:200-210, 425` **[SOURCE]** |
| The other three tools | short confirmations | `TOOL_REGISTRY` |

**[MEASURED]** A deliberately generous synthetic English trajectory — 40 context rows, 4 steps, 25 rows per tool result — measures **11,757 tokens**. That is the structural ceiling of the shipped configuration, and it lands within 1% of the "peak 11,859" the commit measured. The measurement is not thin; it is *saturated*. 36,000 is roughly 3× a number that cannot go much higher.

I tried to construct a run that legitimately needs more than 36,000 and, **with the in-process tool set, could not.** The only route past it is a tool result that is not size-bounded — which means MCP peer tools (`app/mcp/client.py`; **[MEASURED]** no result-size cap anywhere in that module), i.e. exactly the components the AgBOM omits (F-7) and, at the audited commit, exactly the input `max_tool_result_tokens` was supposed to bound and does not (F-5).

**So the answer to "is it doing anything" is: at `763fe2b` it was the only bound on an MCP result, and a very loose one. With `d3aa5c6`'s per-result cap in place it becomes a backstop that will essentially never fire under the shipped tool set.** That is an acceptable place for a backstop to be — but the docstring should then say the tool-result cap is the operative bound, rather than presenting 36,000 as if it were the interesting number. And the number that would make it fire — an unbounded MCP peer result — is bounded now by a cap of 4,000 tokens per result, so 36,000 is reachable only via nine oversized results inside a four-step lane.

## F-6 — MEDIUM — The estimator is wrong for non-ASCII, and this product ships to India

`subagent.py:576` **[SOURCE]**:

```python
size = count_tokens(json.dumps(messages, default=str))
```

`json.dumps` defaults to `ensure_ascii=True`, so every Devanagari character becomes a six-byte `\uXXXX` escape before it is tokenised.

**[MEASURED]** — the *same* service-desk trajectory shape, English vs Hindi:

```
english: true_content_tokens=11332  ceiling_measures=11757  ratio=1.04  breaches_36000=False
hindi:   true_content_tokens=28272  ceiling_measures=60057  ratio=2.12  breaches_36000=True
```

The Hindi lane is measured at **2.12× its real cost** and is killed at the ceiling while carrying about **17,000 real tokens** — less than half the stated bound, and well inside any model's context window. The failure is invisible: the operator sees a bound of 36,000 and a lane dying, with no indication the number being compared is not the number in the config.

For a product whose Phase 6 is `06-compliance-asi-india.md`, a bound that silently halves itself in Hindi is not a rounding error.

Two smaller inaccuracies in the same expression:

* **The proxy is not the prompt.** `json.dumps` adds keys, braces and quotes that are not sent; conversely the tool schemas *are* sent and are not counted. **[MEASURED]** the four tool definitions for `operations_lead` are **1,487 tokens** on every single call, invisible to the ceiling.
* `default=str` will stringify any non-JSON value rather than fail, which is right, but it means the measured size can diverge from the serialised payload for structured content.

**Fix.** `json.dumps(messages, ensure_ascii=False, default=str)` at minimum. Better: `sum(count_tokens(str(m.get("content", ""))) for m in messages)` plus a small per-message constant, and add the tool-definition size once. Add a test with non-ASCII content — this is a one-line bug that a one-line test would have caught, and the absence of that test is the same gap as F-4.

---

# F. Regressions

## Test suites

| Suite | Commit claims | **[MEASURED]** |
|---|---|---|
| `aegis/tests` | 2405 | **2406 passed, 14 skipped** in 280.82s (2406 includes the 2 uncommitted tests) |
| `backend/tests` | 2196 | **run 1: 2195 passed, 1 FAILED, 1 skipped** (389.24s) · **run 2: 2196 passed, 1 skipped** (481.16s) — intermittent, see F-11 |
| `web` | 406 | **406 pass, 0 fail** |
| `tsc --noEmit` | clean | **clean** (no output) |
| `scripts/build_openapi.py --check` | gate green | **`openapi.json is current.`** |
| `test_route_coverage.py` | green, AgBOM reachable | **5 passed** — `/platform/agbom` is genuinely traced from a portal root, not allowlisted |

### F-11 — LOW — An intermittent tenant-isolation failure

I ran the full backend suite twice, both with `-p no:randomly` (so collection order was identical). **[MEASURED]**

```
run 1:  FAILED tests/mcp/test_streamable_http.py::test_a_gate_filed_over_mcp_is_not_visible_to_another_tenant
        1 failed, 2195 passed, 1 skipped, 4099 warnings in 389.24s
run 2:  2196 passed, 1 skipped, 4097 warnings in 481.16s
```

Also **[MEASURED]**: passes in isolation (`1 passed in 6.96s`), and `tests/mcp/` alone passes (`76 passed in 9.71s`).

Same order, different outcome — so this is **genuinely intermittent** (a race or leaked shared state), not deterministic ordering. The commit's "backend 2196" is reproducible; it is just not reliable.

I am reporting it because of *which* test it is. A cross-tenant visibility assertion that fails one run in two is either a real intermittent isolation leak or a flaky fixture, and the two are not distinguishable from the outside. It is not caused by Phase 7 and should not block it, but it should not be left unexplained either.

**Fix.** Run the suite in a loop against that test with `-p no:randomly` to characterise the rate, then look for shared state between the MCP tests and whatever precedes them — a module-scoped registry, a `ContextVar` not reset, or a connection reused across tenant contexts.

## The devops screen — verified in a real browser

The Chrome extension was not connected, so I drove headless Chromium (Playwright, already vendored in `web/`) through the real login and the real screens.

**[MEASURED]** end-to-end success:

```
NET 200 http://localhost:3001/v1/stack/advisories application/json
NET 200 http://localhost:3001/v1/stack/advisories application/json
AgBOM button appeared
NET 200 http://localhost:3001/v1/platform/agbom application/json
DOWNLOAD OK: aegis-agbom.cyclonedx.json
```

The downloaded file validates against CycloneDX 1.6 (see section A). **The feature works.** But:

### F-8 — MEDIUM — It is on the wrong screen, and it is gated behind a network call

**It is not on the stack screen.** The commit says *"route coverage green with the AgBOM reachable from the **devops stack screen**"*, and the task brief repeats it. **[MEASURED]** I loaded `/app/devops/stack` ("Tech Stack & Versions"), waited 20 s, and captured a full-page screenshot: it contains the SBOM component table, the pipeline health panel, the provenance block — **and no export buttons of any kind.** `AgBOM button count: 0`.

The buttons live in `web/src/components/devops/PatchCheck.tsx:518-548` **[SOURCE]**, inside the `AdvisoryAudit` component, which mounts on **`/app/devops/patch` ("Patch Check")**. `05-sbom-agbom.md:299` **[DOC]** described it accurately (*"The SBOM export block gains an AgBOM download"*); the commit message did not. An operator following the commit message goes to the wrong screen and concludes the feature was not built.

**It is gated behind an unrelated network dependency.** `PatchCheck.tsx:549-559` **[SOURCE]** returns a bare `LoadingState` while `state.status` is `idle | loading`, and the `exports` block — the AgBOM button included — is rendered **only** in the `error` and `ready` branches. The gate is `POST /stack/advisories`, which queries a live advisory database.

**[MEASURED]** `POST /v1/stack/advisories: http=200 time=10.775s` — and on my first cold pass the button had still not appeared after 25 s (screenshot shows the skeleton). The AgBOM endpoint itself is purely local and answers in milliseconds. On a firewalled venue network the advisory call hangs, `state` never leaves `loading`, and the AgBOM is undownloadable — a jury-demo failure mode with no relationship to the AgBOM at all.

**Fix.** Hoist `exports` out of `AdvisoryAudit` so it renders in all four states (or at least render it above the loading skeleton), **and** correct the commit-message/handoff claim to name the Patch Check screen. Consider putting it on the stack screen too, which is where a reader looking for a bill of materials will go first.

### F-13 — LOW — Silent failure on the download

`PatchCheck.tsx:523-532` **[SOURCE]**:

```tsx
const downloadAgbom = useCallback(() => {
  void getAgbom(token).then((text) => { ... })
}, [token])
```

No `.catch`. A 401/403/500 produces an unhandled promise rejection and a button that does nothing visible. The sibling `download` for the SBOM has the same shape, so this is a consistency issue rather than a new one — but the AgBOM is the one an operator will click while being watched.

## F-9 — MEDIUM — Plan deliverables not built

Checked against `07-long-horizon-ceiling.md`'s own task list:

| Task | Status |
|---|---|
| A1 — two `AgentConfig` fields + `as_dict()` | **Done** |
| A2 — two `_KNOB_SPECS` entries | **Done**, bounded, defaults inside bounds |
| **A3 — tenant-tightenable `_Binding`** | **NOT DONE.** **[MEASURED]** `grep -n "max_trajectory_tokens" aegis/src/aegis/settings/agent.py` → nothing. `AGENT_SETTING_BINDINGS` (`:97-108`) still has four entries. A tenant cannot tighten either ceiling. |
| A4 — per-lane check | **Done** (before the call, correctly — verified by the existing test) |
| A4 — per-result truncation | **Missing at `763fe2b`; landed in `d3aa5c6`** — see F-5 |
| A5 — `CEILING` enum + visible refusal | **Half done** — enum added, the `status="ceiling"` beat is missing (F-2) |
| **A6 — `'ceiling'` in the web lane union + `TERMINAL`** | **NOT DONE.** **[MEASURED]** `agentLanes.ts:36-48` **[SOURCE]** — `LaneStatus` is still `queued\|started\|thinking\|acting\|done\|failed\|timeout\|waiting\|blocked`, and `TERMINAL` is still `['done','failed','timeout','blocked']`. (Moot only because the backend never sends `'ceiling'` — F-2. Fixing F-2 without this will send an unmodelled status to the console.) |
| **A7 — architecture paragraph** | **NOT DONE** (the plan calls it *"the deliverable, not a footnote"*) |
| **A8 — ASI08 gap sentence** | **NOT DONE.** **[SOURCE]** `backend/src/app/platform/compliance.py:3006-3011` still reads *"…nothing yet detects a failure cascading across a fan-out"* with no mention of the ceiling that just shipped. `06-compliance-asi-india.md:1051` **[DOC]** required this rewrite in the same change. |

## What is verifiably right

Recorded so the fixes do not undo it:

* **The `application`-not-`tool` divergence is correct and correctly explained.** **[MEASURED]** both directions against the published schema.
* **RBAC is right.** **[MEASURED]** `client → 403`, `admin → 200`, `devops → 200`, unauthenticated → `401`.
* **The tool half of the inventory is exact.** Every tier, read-only flag and persona list matches `TOOL_REGISTRY`/`ALLOWLIST`.
* **The `dependencies` graph is well-formed** — zero dangling refs, empty elements declared as the spec asks.
* **The ceiling is checked before the model call**, not after, and a test proves the model is never reached. **[MEASURED]** `6 passed`.
* **The deferred `tiktoken` import is real.** **[MEASURED]** `import aegis.agent.subagent` → `tiktoken imported: False`, `aegis.memory.tokens imported: False`; `test_isolation.py` — `1 passed`.
* **The uncommitted truncation's "full text is on the run record" is true**, verified at `subagent.py:727-733`.
* **The route is genuinely reachable** — `test_route_coverage.py` traces it from a portal root rather than allowlisting it, and the browser download proves the path end to end.
* **The docstring records its sample size.** Two samples is thin and the code says so. Keep that habit.

---

## Fix order

1. **F-1** — delete the false `.env`/`os.environ` explanation from `agbom.py:91-101` and the commit narrative; make the model list deterministic.
2. **F-2** — the three-line `CEILING` fix (`status="ceiling"` beat, `contributed`, `_omission_phrase`) and a test that asserts on emitted events.
3. ~~**F-5**~~ — done in `d3aa5c6` during this audit.
4. **F-3** — enumerate `_FLEET_DECLARATION`, all 12, with `tenant-selectable`.
5. **F-4** — the vendored-schema validation test, with the `type="tool"` negative control.
6. **F-6** — `ensure_ascii=False`, plus a non-ASCII test.
7. **F-7** — MCP peer tools in the AgBOM, or delete the docstring sentences that promise knowledge sources.
8. **F-8** — hoist `exports` out of the loading branch; correct "stack screen" to "Patch Check".
9. **F-9** — A3, A6, A7, A8.
10. **F-10 / F-11 / F-12 / F-13** — media type, the MCP order-dependent failure, `serialNumber`/`metadata.tools`/versions, the missing `.catch`.
