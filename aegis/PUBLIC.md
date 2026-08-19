# The public API of `aegis`

`aegis` is meant to be **imported, not forked**. That sentence is only true if
there is a stated boundary, because without one every internal becomes
load-bearing by accident: someone imports it, it works, and now it can never
change.

This file is that boundary. It is the *only* place a stability promise is made.

---

## The rule

> A name is **public** if and only if it appears in the `__all__` of a top-level
> `aegis` package or module **and** is listed in the Stable table below.
>
> Everything else is internal — including every submodule, and including names
> with no leading underscore.

**Why a second list rather than just `__all__`.** `__all__` has accumulated to
**over seven hundred names** across the package tree. It is a re-export
convenience, and it cannot be both "everything importable" and "everything
supported". So `__all__` stays exactly as it is — nothing breaks — and this table
is the supported subset.

Count it yourself rather than trusting a number in a document that a new package
will silently invalidate:

```bash
cd aegis && python - <<'EOF'
import ast, pathlib
total = 0
for f in pathlib.Path("src/aegis").rglob("__init__.py"):
    for node in ast.walk(ast.parse(f.read_text())):
        is_all = isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == "__all__" for t in node.targets
        )
        if is_all and isinstance(node.value, ast.List | ast.Tuple):
            total += len(node.value.elts)
print(total)
EOF
```

**44 of them are Stable** — roughly one name in seventeen. That ratio is the
point. A surface that is 94% unpromised is a surface that can still be changed.

---

## The three tiers

| Tier | Promise | Where it is |
|---|---|---|
| **Stable** | Will not be removed or have its signature narrowed without a deprecation cycle: one minor release warned by `aegis.core.deprecated`, then removal. | The table below. |
| **Provisional** | Public, works, documented — but may change in a minor with only a `CHANGELOG.md` entry. Use it; pin your version. | Everything else in a package `__all__`. |
| **Internal** | Exported so the reference host can reach it. **No compatibility promise of any kind.** May move or vanish in a patch. | Every submodule, and every name not in a package `__all__`. |

Nothing is deleted to make this true. Names are *marked*, not removed —
`aegis.governance`'s ~79 exports and `aegis.memory`'s ~52 stay exactly where
they are, they are simply not promised.

---

## Stable — the 44

Each of these resolves, and is in its package's `__all__`. That is not a claim,
it is asserted by `tests/core/test_public_surface.py`, which reads this file.

### `aegis.core` — the shared vocabulary

Every other module speaks in these types, so they change last.

| Name | What it is |
|---|---|
| `aegis.core.RiskLevel` | LOW / MEDIUM / HIGH. The **only** signal that drives the human gate. |
| `aegis.core.GuardVerdict` | ALLOW / REDACT / BLOCK — a rail's decision. |
| `aegis.core.GuardResult` | A rail's decision plus its reason and redactions. |
| `aegis.core.GuardStage` | Which rail stage produced a result. |
| `aegis.core.RunStatus` | The lifecycle of one agent run. |
| `aegis.core.ApprovalDecision` | What a human did at the gate. |
| `aegis.core.AegisEvent` | Base of the streamed event union — the product's primary interface. |
| `aegis.core.StepStarted` | Stage opened. |
| `aegis.core.StepFinished` | Stage closed, with its timing. |
| `aegis.core.GuardrailEvent` | A rail fired, with its verdict. |
| `aegis.core.SpanKind` | OpenInference span kind stamped on every event. |
| `aegis.core.require` | The sanctioned optional import. Raises naming the exact `pip install`. It is **not** `aegis.require`. |
| `aegis.core.deprecated` | Decorator that announces a removal. See below. |
| `aegis.core.warn_deprecated` | The same announcement where a decorator cannot reach. |
| `aegis.core.AegisDeprecationWarning` | So you can turn *our* removals into build failures without failing on a dependency's. |

### `aegis.adapter` — the one thing an integrator implements

The domain seam as an executable contract, so "have I implemented the adapter?"
is answered by a type checker and one function call rather than by counting
files. Ten pieces, **nine members** — `skills/` has no member of its own because
it is already named by `memory_spec.SKILLS_DIR`.

| Name | What it is |
|---|---|
| `aegis.adapter.DomainAdapter` | The Protocol a domain adapter satisfies. Structural — no inheritance, no registration. |
| `aegis.adapter.adapter_members` | Every member name the Protocol requires, read off the Protocol itself. |
| `aegis.adapter.missing_members` | What a candidate adapter does not have yet. Necessary, not sufficient — it checks presence, not shape. |

The eight per-piece Protocols (`SchemaModule`, `MLSpecModule`, `GeneratorModule`,
`ToolsModule`, `PersonasModule`, `PromptsModule`, `MemorySpecModule`,
`RosterModule`, `CorpusModule`) are the members of `DomainAdapter` and are
exported alongside it; they are Provisional while the shapes settle. Bind to
`DomainAdapter` and let the type checker reach the rest.

### `aegis.agent` — the engine contract

`AgentDeps` is the seam. A host binds real functions to it; a test binds fakes
and drives the whole vertical slice with no infrastructure and no network.

| Name | What it is |
|---|---|
| `aegis.agent.AgentDeps` | Every capability the graph reaches through. The integration contract. |
| `aegis.agent.AgentConfig` | `gate_min_risk` and the streaming knobs. The gate threshold lives here and nowhere else. |
| `aegis.agent.MemoryDeps` | The memory half of the same seam. |
| `aegis.agent.ToolOutcome` | Structural view of an action-tool result (`ok`, `summary`). |
| `aegis.agent.run_agent` | Run one turn, streaming events. |
| `aegis.agent.build_agent` | Compile the graph. |
| `aegis.agent.risk_at_least` | The gate comparison, so a host cannot reimplement it differently. |
| `aegis.agent.graph_topology` | The compiled graph's real shape — what `/agent/topology` serves. |

### `aegis.guardrails` — the rails

| Name | What it is |
|---|---|
| `aegis.guardrails.check_input` | Input rail: injection, PII, schema, topic. Fail-closed. |
| `aegis.guardrails.check_output` | Output rail, including grounding against the retrieved passages. |
| `aegis.guardrails.check_tool_result` | The rail on what a tool returned — the injection vector people forget. |
| `aegis.guardrails.Guardrails` | The composed rail stack. |

### `aegis.retrieval` — evidence and its provenance

| Name | What it is |
|---|---|
| `aegis.retrieval.Retriever` | The retrieval Protocol a host injects. |
| `aegis.retrieval.RetrievalResult` | Passages, citations, per-arm provenance. |
| `aegis.retrieval.RetrievalScope` | **A security boundary.** Keyword-only, no default, on every call. |
| `aegis.retrieval.RetrievalConfig` | Which arms run, and how they fuse. |
| `aegis.retrieval.Citation` | One claim traced to one span of one source. |
| `aegis.retrieval.build_default_retriever` | The shipped hybrid retriever. |

### `aegis.gateway` — the single model chokepoint

| Name | What it is |
|---|---|
| `aegis.gateway.complete` | Every model call goes through here. Budgets are enforced *before* spend. |
| `aegis.gateway.embed` | Embeddings, through the same ledger. |
| `aegis.gateway.configure` | Wire the gateway once, at the composition root. |
| `aegis.gateway.LLMResult` | Text, usage, cost, trace id. |
| `aegis.gateway.BudgetExceededError` | Raised *before* the spend, not after. |

### `aegis.memory` — the durable-fact contract

| Name | What it is |
|---|---|
| `aegis.memory.MemorySpec` | What counts as a durable fact in *your* domain. The only memory seam. |
| `aegis.memory.MemoryFact` | One durable fact, bitemporally scoped. |
| `aegis.memory.RecallBundle` | What recall assembled, and from where. |

---

## What is deliberately internal, and why

Being honest about this is more useful than a longer Stable list.

| Left internal | Why |
|---|---|
| **`aegis.governance`** (~79 exports) | Almost entirely ORM models, RLS policy helpers and JWT plumbing that the reference host reaches directly. It is a *host* concern, not an integrator one — a new domain never touches it. Promising 79 names would make every schema change a breaking change. |
| **`aegis.memory`'s other ~49** | Stores, scoring, consolidation, retention, token budgeting. The mechanism is ours; only the *spec* is yours. Promising the mechanism would freeze the ability to improve recall. |
| **`aegis.jobs`, `aegis.runs`, `aegis.settings`, `aegis.dbadmin`, `aegis.analytics`** | Operator surfaces, consumed by the console through HTTP. The HTTP route is the contract there; the Python names behind it are not. |
| **Every submodule** (`aegis.retrieval.types`, `aegis.jobs.stages`, …) | A submodule path is an implementation detail. Import from the package. |
| **`aegis.ingestion`, `aegis.ml`, `aegis.forecast`, `aegis.evals`, `aegis.ops`, `aegis.vision`, `aegis.voice`, `aegis.media`, `aegis.websearch`, `aegis.redteam`, `aegis.reports`, `aegis.security`, `aegis.observability`, `aegis.data`** | Provisional, not internal — they work and are documented, but their shapes are still moving. Pin your version and read `CHANGELOG.md`. |

### The reference host is not evidence

`backend/src/app` imports from **121 distinct `aegis.*` module paths**, most of
them submodules. That is deliberate and it is *not* a claim about the public API:
the backend is the reference composition root, developed in the same repo and
moved in lockstep. Do not read its import list as a supported surface. An
integrator's import list should look like the Stable table above.

---

## Versioning

`aegis` follows **SemVer**, tracked in the repo-root [`CHANGELOG.md`](../CHANGELOG.md).

Pre-1.0 semantics apply, and they are stated rather than assumed:

- **Breaking changes may land in a minor** (`0.x.y` → `0.(x+1).0`), and the
  changelog says so explicitly under a `Changed` or `Removed` heading.
- **A Stable name is warned for one full minor before it is removed.** Never
  removed in the same release it was deprecated in.
- **Provisional names may change in a minor with only a changelog entry** — no
  deprecation cycle is owed.
- **Internal names may change in a patch.** No notice at all.

### Deprecation

One mechanism, one function:

```python
from aegis.core import deprecated

@deprecated(since="0.2.0", removed_in="0.3.0", use="aegis.retrieval.build_default_retriever")
def build_legacy_retriever(): ...
```

Calling it emits an `AegisDeprecationWarning` naming the replacement, pointed at
*your* line, not at ours.

All three arguments are required, and `use=""` raises `ValueError` at **decoration
time** — import time. That is the rule made executable rather than written down:
a deprecation that does not say what to use instead is a threat, not a warning,
and this package cannot ship one.

Python hides `DeprecationWarning` by default outside `__main__`, which is correct
for a library. To surface every one on the path your integration actually takes:

```bash
pytest -W error::aegis.core.AegisDeprecationWarning   # ours only
python -W default::DeprecationWarning -m yourapp      # everyone's
```

---

## Keeping this file true

Prose about an API goes stale between the commit that changes a signature and the
commit that remembers the document. So this file is read by a test:

```bash
cd aegis && PYTHONPATH=src ../backend/.venv/bin/python -m pytest tests/core/test_public_surface.py -q
```

It parses the Stable tables above, and for every name asserts that it imports and
that it is in its package's `__all__` — the two halves of the rule at the top.
Rename a Stable name and that test fails until this file is updated with it.

The generated signature reference is separate and is **not** a promise:

```bash
backend/.venv/bin/python scripts/build_api_docs.py   # -> docs/api/, git-ignored
```
