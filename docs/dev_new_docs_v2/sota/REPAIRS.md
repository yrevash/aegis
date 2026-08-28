# Phase 5 & 7 audit repairs — what was fixed, and what was verified after

Both audits returned **FAIL**. Every finding below was reproduced independently before
being fixed, and re-verified against the live stack afterwards. Findings I did **not**
fix are listed at the end with the reason, rather than left out.

Commits: `d3aa5c6`, `92b7aa3`, `abfd862`, `c58a5d4`, `dee91d1`, `a60847e`, `72d2c9b`.

---

## Phase 7 — the two criticals

### F-1 — the `requested` rationale was false, and the AgBOM changed shape mid-process

Reproduced in six lines:

```
before: None
after litellm: DeepSeek-V4-Flash
```

`import litellm` calls `load_dotenv()`. The module docstring claimed the opposite — that
`.env` reaches pydantic settings but never `os.environ` — and built a whole "requested vs
observed" argument on it. There was no divergence to be honest about.

What the false explanation hid: the environment mutates the first time any request path
imports litellm, so an inventory built by asking the router *"what would you pick right
now"* was **non-deterministic within one process**. Reproduced here:

```
BEFORE: 6 models   AFTER: 4 different models   SAME PROCESS: True
```

**Fixed** at the root — `app.config` calls `load_dotenv(override=False)` at import, so the
environment is settled before anything reads it — and at the source, by enumerating
`_FLEET_DECLARATION` instead of the currently-routed slice.

**Verified live:** two pulls a minute apart return byte-identical components; 25
components (was 14); `serialNumber` stable across builds; `application/vnd.cyclonedx+json`;
0 schema errors against the vendored CycloneDX 1.6 schema; RBAC devops/admin 200,
unauthenticated 401.

### F-2 — `CEILING` was invisible, discarded its findings, and was described backwards

Three claims, all false:

| Claim | Was | Now |
|---|---|---|
| the lane stops | true | true |
| what it found is kept | `contributed` tested `status is OK`, so every finding was dropped | CEILING contributes when it has findings |
| the synthesis says it was cut short | fell through to *"returned nothing usable"* | *"was cut short at its trajectory ceiling"* |
| the wire | `status="done"` — a truncated lane rendered as a clean one | `status="ceiling"`, beside `timeout` |

Fixing the second created a new silence — a truncated lane that now contributes would be
counted among the healthy ones, and *"1 of 1"* would hide the truncation. `synthesis_note`
now names truncated contributors too.

`ceiling` is carried end to end: `LaneStatus`, `TERMINAL`, the trace view, `stream.ts` and
the OpenAPI schema. Without that the backend fix alone would have left the card spinning
forever on a finished run.

The existing test is why this shipped: it passed `writer=lambda _e: None` and never
inspected an event. It now asserts on the wire.

---

## Phase 5 — the critical

### A — the metering badge was over an unmetered route

The audit measured seven invocations, ~108 model calls, ~$0.088 spent, **0 rows in
`usage_ledger`**. The adapters call the gateway correctly; the route bound no
`GovernanceContext`, so every judge call ran with `ctx=None`.

The claim was rendered to the reader in three places — the route docstring, the API
response `source` field, and the evals screen's `Receipt`.

**Fixed** with the binding the voice route already uses. The new test is proven
non-vacuous: removing the binding fails it with the sentence above.

**Verified live:** `usage_ledger` 19502 → 19511 across one `limit=1` run — 5 completions +
4 embeddings, exactly the audit's measured per-case cost.

---

## Everything else fixed

| ID | Was | Now |
|---|---|---|
| P7 F-3 | inventory omitted 5 models with thousands of ledger rows each, and emitted 2 the fleet does not declare | all 12 declared, `tenant-selectable` marked, undeclared routed ids labelled rather than hidden |
| P7 F-4 | no schema test — the task settling the `tool` question | vendored schema, offline, with a `type="tool"` negative control |
| P7 F-6 | ceiling measured `\uXXXX` escapes: Hindi inflated **2.63×**, cut at half the stated bound | `ensure_ascii=False` → **1.00×**, with a test asserting both directions |
| P7 F-7 | docstring promised knowledge sources; document had none | 3 `data` components, configured-not-verified |
| P7 F-8 | all three BOM downloads gated behind a 10.8s advisory call that hangs on a firewalled network | exports render above the skeleton |
| P7 F-9 | A3/A6/A7/A8 not built | ceilings tenant-tightenable; `ceiling` in the web union; the §10 architecture paragraph; the ASI08 gap sentence rewritten |
| P7 F-10 | `application/json` | `application/vnd.cyclonedx+json` |
| P7 F-12 | no `serialNumber` — the document could not be diffed, which is the whole litellm story | content-derived serial: unchanged deployment ⇒ same serial |
| P7 F-13 | download had no `.catch` — silent no-op plus unhandled rejection | failure stated in an `aria-live` region |
| P5 C-3 | ragas NaN entered the sample; **one NaN turned the whole metric NaN** while `cases` still claimed 2 | not-run, and a partial sample says so |
| P5 C-4 | ragas `0.0` from a failed judge recorded as a measured score | not-run |
| P5 G-2 | faithfulness **1.000 by construction** (answer = context), unstated | stated above the figures |
| P5 G-3 | button 500'd at the proxy's 30s ceiling on the first cold press — the demo — while the backend kept spending | `proxyTimeout` 180s |
| P5 G-4 | 14–134s of silence | cost stated before the press, elapsed counter during |
| P5 G-5 | a failed re-score wiped a good result back to copy that reads as policy | previous result kept |
| P5 D-3 | `limit` unconstrained, silently clamped | `ge=1, le=6`, documented as a spend bound |
| P5 F-1..F-4 | every doc and manifest still said ragas is **not** a dependency | corrected, including the live `/platform/capabilities` manifest and the landing grid |

---

## Not fixed, with reasons

- **P7 F-11 — intermittent MCP tenant-isolation failure.** Could not reproduce. 6/6 in
  isolation, 8/8 in-module, and four full backend runs this session all passed. Guessing
  at a fix for a cross-tenant assertion would be worse than leaving it flagged. **Still
  open.**
- **P7 F-8, second half — the AgBOM button is on Patch Check, not the stack screen.** The
  commit message was wrong, not the code. The blocking half (the network gate) is fixed;
  adding a second copy to the stack screen is a design call worth making deliberately.
- **DeepEval.** Not installed, and the reason is exact: `deepeval` requires
  `click>=8.0.0,<8.4.0`, `huggingface_hub` requires `click>=8.4.2`. Disjoint —
  unsatisfiable in one interpreter. `uv pip install deepeval` *appears* to succeed and
  silently downgrades click, leaving `huggingface_hub` violating its own declared pin.
  Verified separately that the real library **does** run offline against a custom
  `DeepEvalBaseLLM`, so this is an environment constraint, not a capability one.
- **P5 G-3, the better fix.** A job id + polling is the right shape for a
  money-spending request. Raising the proxy ceiling clears the measured worst case and is
  the change that fits before demo day.

---

## Verified against the live stack

- AgBOM: deterministic, 25 components, schema-clean, correct media type, RBAC correct.
- Live eval: metered, 9 ledger rows for one case.
- A2A: card served (unsigned — `a2a_public_origin` unset, which is the Phase 6 fix
  working), `SendMessage` → `TASK_STATE_COMPLETED` with a real answer.
- RAG: 45 candidates, 6 sources, a grounded answer for a tenant-bound principal.
- **Tenant isolation: northwind 9 docs, vertex 5 docs, intersection empty.**

### One operational finding for demo day

The seeded `client`, `admin`, `ai`, `aiteam` and `devops` accounts are **platform-scoped**
(`tenant_id: null`) and therefore own no documents — a query as `client` returns 0
candidates and the agent correctly says it cannot answer. That is tenant isolation
working, not a bug. **Demo with `northwind.client` or `vertex.client`.**
