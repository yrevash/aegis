# SOTA-09 — The real `ragas` and the real `deepeval`, running through the Aegis gateway

> **Source of every claim here.** Each fact is marked `[MEASURED]` (a command run on this
> machine, on 2026-08-27), `[SOURCE]` `file:line` (read in this repo), `[SOURCE-ragas-0.4.3]`
> / `[SOURCE-deepeval-4.2.0]` (read in the released wheel, downloaded from PyPI and unpacked),
> `[DOC]` (vendor documentation) or `[ESTIMATE]` (arithmetic over measured inputs, not yet
> observed). Where this document says something it did not establish, it says so in the same
> sentence.
>
> **Two things the briefing for this work asserted are wrong, and both are corrected below**
> — see *"The click conflict is real"* and *"`ToolCorrectnessMetric` is no longer free"*.
> Everything else in the briefing held up under verification.

---

## What this is, in one paragraph

Aegis today ships a hand-rolled imitation of `ragas` and `deepeval` and says so in its own
docstrings. This plan replaces the imitation with the actual libraries — `ragas==0.4.3` and
`deepeval==4.2.0`, installed, imported, and executing their real metric code — while keeping
every model call those libraries make inside `aegis.gateway.complete` / `.embed`, so eval
spend is budgeted, ledgered, rate-limited, traced and circuit-broken exactly like production
spend. The mechanism is a pair of adapter classes over the libraries' own abstract bases; the
libraries never learn a `base_url`, never hold a key, and never open a socket of their own.

---

## Part 1 — What is actually there today

Not an interpretation. The code says it about itself.

`aegis/src/aegis/evals/__init__.py:3` **[SOURCE]**:

> *"RAGAS-style deterministic lexical proxies (context-precision/recall/groundedness), an
> optional **injected** LLM-as-judge, and a DeepEval-pattern per-metric regression gate — all
> hand-rolled, with **no heavy deps** (no ``ragas``/``deepeval``) and **no ORM**."*

`aegis/src/aegis/evals/metrics.py:3` **[SOURCE]**:

> *"These are **RAGAS-style deterministic proxies** — inspired by RAGAS metric *ideas* but
> computed here with transparent token/substring overlap, with **no external LLM and no
> `ragas` library** … They are proxies, not the RAGAS-the-library metrics they are named
> after."*

`aegis/src/aegis/evals/regression.py:1` and `:22` **[SOURCE]**:

> *"The **DeepEval-pattern** CI regression gate … This is a native implementation of the
> *DeepEval pattern* … **Why native, not the ``deepeval`` package.** ``deepeval`` is heavy
> and, for most of its metrics, calls an external LLM judge."*

`aegis/src/aegis/evals/harness.py:1` **[SOURCE]** — the runner is honest too: *"It is fully
offline: the embedding is a local deterministic hash and the reranker is a pass-through."*

And the honesty extends to the one metric it refuses to fake. `harness.py:175-182`
**[SOURCE]** emits `MetricConfig(name="answer_relevancy", value=None, computed=False)`, which
`metrics.py:140` **[SOURCE]** documents as *"an honestly-not-computed metric such as RAGAS
answer relevancy, whose ``value`` is then ``None``."* That empty cell is the thing this plan
finally fills with a real number.

**What stays.** `aegis/src/aegis/evals/ir_metrics.py` **[SOURCE]** — nDCG@k, MRR, recall@k,
precision@k, Wilson intervals, paired bootstrap, exact McNemar — is **not** an imitation of
anything. Neither `ragas` nor `deepeval` ships significance testing; searched both wheels,
neither has a McNemar, a bootstrap or a Wilson interval **[SOURCE-ragas-0.4.3]**
**[SOURCE-deepeval-4.2.0]**. Its module docstring carries the sentence that wins arguments
with judges — *"n=50 defends a difference of roughly 15 points and cannot defend one of 5"*
**[SOURCE]** `ir_metrics.py:46`. Keep it, keep it named `ir_metrics`, and do not let anyone
"consolidate" it into the library path.

---

## Part 2 — The constraint that turns into the feature

`aegis/tests/evals/test_isolation.py:29-31` **[SOURCE]** runs a subprocess that imports
`aegis.evals`, `aegis.evals.harness`, `aegis.evals.regression`, `aegis.evals.judge` and
`aegis.evals.stream`, and asserts:

```python
banned = {'litellm', 'fastapi', 'sqlalchemy', 'torch', 'langgraph',
          'xgboost', 'nemoguardrails', 'ragas', 'deepeval'}
hit = banned & set(sys.modules); assert not hit, hit
```

**This test does not get weakened, edited, or exempted. It stays green, byte for byte.**

That is not a compromise — it is the shape of the answer. The real integrations live in a new
subpackage `aegis/src/aegis/evals/libs/`, which the five modules named in that test never
import, and which imports `ragas` / `deepeval` **lazily inside the function that needs
them**, through the repo's sanctioned optional-import helper `aegis.core.lazy.require`
**[SOURCE]** `aegis/src/aegis/core/lazy.py:14` (*"A missing module raises an `ImportError`
naming the exact `pip install` command — never a silent `except ImportError: pass`"*).

The claim this buys is better than the one it replaces:

> *"The offline gate is dependency-free, deterministic and runs with no network — and the
> same corpus can be scored by the real `ragas` and the real `deepeval`, opt-in, with every
> call metered. Two gates, one corpus, and the cheap one never depends on the expensive one."*

---

## Part 3 — The dependency resolution, re-measured (and the correction)

All of the following was run against the live `backend/.venv` (Python 3.11.11) with
`uv pip install --dry-run`, which resolves and installs nothing **[MEASURED]**.

### The resolution does succeed

```
$ uv pip install --dry-run --python .venv/bin/python "ragas==0.4.3" "deepeval==4.2.0"
Resolved 114 packages in 80ms
```

with these version changes **[MEASURED]**:

| Package | From | To | Direction |
|---|---|---|---|
| `huggingface-hub` | 1.27.0 | **1.16.1** | down |
| `click` | 8.4.2 | **8.3.3** | down |
| `fsspec` | 2026.7.0 | 2026.6.0 | down |
| `jiter` | 0.16.0 | 0.14.0 | down |
| `rich` | 15.0.0 | 14.3.4 | down |
| `tabulate` | 0.10.0 | 0.9.0 | down |
| `langchain-core` | 1.5.4 | 1.6.0 | up |

plus 27 new packages: `datasets`, `instructor`, `langchain` / `-classic` / `-community` /
`-text-splitters`, `scikit-network`, `posthog`, `backoff`, `appdirs`, `diskcache`,
`pyfiglet`, `questionary`, `wheel`, `execnet`, and three pytest plugins
(`pytest-xdist`, `pytest-repeat`, `pytest-rerunfailures`).

### The click conflict is real — the briefing's diagnosis was wrong

The briefing stated *"There is NO click conflict — huggingface-hub declares no click
dependency; only presidio_analyzer (>=8.1.0,<9.0.0) constrains it."* **That is false for the
version installed here.** Measured against the live venv's metadata **[MEASURED]**:

```
huggingface_hub 1.27.0 -> click<9.0.0,>=8.4.2
deepeval 4.2.0          -> click<8.4.0,>=8.0.0
```

And the resolver says so directly when asked to keep the hub **[MEASURED]**:

```
$ uv pip install --dry-run ... "ragas==0.4.3" "deepeval==4.2.0" "huggingface-hub>=1.27"
  × No solution found when resolving dependencies:
  ╰─▶ Because deepeval==4.2.0 depends on click>=8.0.0,<8.4.0 and
      huggingface-hub>=1.27.0 depends on click>=8.4.2,<9.0.0, we can conclude
      that deepeval==4.2.0 and huggingface-hub>=1.27.0 are incompatible.
```

**The huggingface-hub downgrade is not incidental drift. It is `deepeval`'s click cap,
transmitted through `click`, and made binding because `ragas → datasets → huggingface-hub`
drags the hub into the resolution set at all.** No published `deepeval` relaxes it: 4.1.0 and
4.0.0 also cap `click<8.4.0`; 3.7.0 caps `click<8.3.0` **[MEASURED]**.

### Which is why the two libraries are installed differently

Resolved separately **[MEASURED]**:

| | huggingface-hub after | Other changes |
|---|---|---|
| `ragas==0.4.3` **alone** | **1.27.0 — untouched** | fsspec↓, jiter↓, rich↓, langchain-core↑, +14 new |
| `deepeval==4.2.0` **alone** | **1.27.0 — untouched** | click↓, rich↓, tabulate↓, +13 new |
| both together | **1.16.1 — downgraded** | all of the above |

So:

- **`ragas` goes into the backend venv.** It never touches `huggingface-hub`, therefore never
  touches the ingestion/ML stack's actual risk surface. It is the library that must run
  in-process anyway, because it is the one whose calls have to carry a bound
  `GovernanceContext` (Part 8, Part 9).
- **`deepeval` goes into its own environment.** Its job here is a pytest-native CI gate and
  agentic-behaviour metrics — a *command*, not a request handler. It runs under
  `uv run --isolated --with 'deepeval==4.2.0' --with-editable ./aegis …`, so `click 8.3.3`
  and `huggingface-hub 1.16.1` exist only inside that ephemeral environment and the backend
  venv never sees them.

That isolated environment resolves cleanly and is small: `deepeval` + `litellm` + the `aegis`
core deps compiles to **88 packages with no `torch`, no `transformers`, no `docling`**
**[MEASURED]** (`uv pip compile --python-version 3.11`). Adding `ragas` to it takes it to
126, still with none of those three **[MEASURED]**. `huggingface-hub 1.16.1` in an
environment that contains no transformers, no docling and no fastembed is a version number
with nothing downstream of it.

### If a single environment is chosen anyway — how to validate the hub downgrade

Prefer the split. If someone insists on one venv, the downgrade is **declared-compatible** —
every constraint on `huggingface-hub` in the live venv is satisfied by 1.16.1 **[MEASURED]**:

```
transformers 5.8.1     -> huggingface-hub<2.0,>=1.5.0     ✓
fastembed 0.8.0        -> huggingface-hub>=0.20,<2.0      ✓
docling-ibm-models     -> huggingface_hub<2,>=0.23        ✓
tokenizers 0.22.2      -> huggingface-hub>=0.16.4,<2.0    ✓
```

Declared-compatible is not the same as working: 1.16.1 was published 2026-05-21 and 1.27.0 on
2026-08-07 **[MEASURED]**, so `transformers 5.8.1` was released against a hub eleven minor
versions newer than the one it would be pinned to. The undeclared risk is API drift in the
download/cache layer. **T-DEP-1** is therefore: take the downgrade in a throwaway venv and
run, in order, (a) `python -c "import transformers, docling, fastembed"`, (b) the ONNX
reranker's real load path in `aegis/src/aegis/retrieval/local_reranker.py`, (c) one PDF
through `aegis.ingestion.convert` end to end, and (d) `pytest aegis/tests/ingestion
aegis/tests/retrieval`. Any failure retires the single-venv option permanently rather than
being patched around.

### Two things that must not be "fixed"

Forcing `rich>=15` makes the resolver drag `openai` **2.54.0 → 1.109.1** and `typer` to 0.9.4
**[MEASURED]**. Forcing `jiter>=0.16` does the same to `openai` **[MEASURED]**. A downgraded
`openai` SDK under `litellm 1.96` is a far worse trade than a cosmetic console library.
**Accept `rich 14.3.4`, `tabulate 0.9.0` and `jiter 0.14.0`. Do not pin around them.**

### The pins

```toml
# aegis/pyproject.toml — new optional extras
evals-ragas = ["ragas==0.4.3"]
evals-deepeval = ["deepeval==4.2.0"]
```

Pinned **exactly**, both of them. `ragas` deprecated its primary entry point inside the 0.4
line (Part 5) and `deepeval` moved its custom-model schema contract inside the 4.x line
(Part 5); a floating pin on either is a demo that resolves differently on the morning it
matters. Vendor the wheels before rehearsal, as Phase 11 §L0 requires of Langflow.

---

## Part 4 — The central design point: everything goes through the gateway

`aegis/src/aegis/gateway/llm.py:1582` `complete()` and `:1792` `embed()` **[SOURCE]** are the
one chokepoint. Reading the bodies, a single call through `complete` performs:

| Control | Where **[SOURCE]** |
|---|---|
| Budget/rate check **before** spend | `llm.py:1624-1627` — `_governance.get_context()` then `await _governance.enforce(ctx)` |
| Tenant-tier model bound + circuit breaker | `llm.py:1633` — `_resolve_chain(role)`; *"fails loudly rather than succeeding on a model outside the tier"* |
| Fleet-wide slot limiter | `llm.py:1822` (`embed`), the same `_limiter.slot()` on `complete` |
| Usage ledger row + tally | `record_call(...)` per call, `llm.py:1841-1846` on the embed path |
| `gen_ai.*` OTel span | `_observability.span(GenAIOperation…)` |
| Bounded output + per-call timeout + one corrective JSON re-ask | `llm.py:1593-1595` docstring |

`embed()` in particular takes `texts: list[str]` and returns one vector per input
**[SOURCE]** `llm.py:1792-1800` — one call, one ledger row, one limiter slot, for a whole
batch. That fact is worth a design decision on its own (Part 5).

**Pointing `ragas`/`deepeval` at a `base_url` bypasses every row of that table.** It is the
identical failure Phase 11 documents for Langflow — *"A flow that hits Run today instantiates
a LangChain chat model **inside the Langflow process** and calls the provider directly. It
never enters `aegis.gateway.complete`"* **[SOURCE]** `phase-11-langflow.md:30-33` — and the
identical consequence: budgets not checked, no `usage_ledger` row, tenant spend invisible.
Eval runs are *expensive*: Part 7 counts 72 completions for one six-case suite. Seventy-two
invisible model calls on the one screen Aegis pitches as proof that no model call is
invisible is not a compromise, it is a contradiction.

**Therefore: adapters, not base URLs. No `OPENAI_API_KEY` is set for either library, no
`OPENAI_BASE_URL`, no `DEEPEVAL_*` provider variable. Neither library is ever given a way to
reach a network.** A test asserts this (Part 13, T-3).

---

## Part 5 — The adapters, against the real base classes

All signatures below were read from the released wheels, not from documentation.

### 5.1 `ragas` — `aegis/evals/libs/ragas_adapter.py`

`ragas` 0.4 abandoned its old surface. Both facts verified in the wheel:

- `ragas/evaluation.py:447-452` **[SOURCE-ragas-0.4.3]** — *"evaluate() is deprecated and
  will be removed in a future version. Use the @experiment decorator instead."* Same for
  `aevaluate()` at `:106`.
- `ragas/llms/base.py:159` **[SOURCE-ragas-0.4.3]** — *"LangchainLLMWrapper is deprecated and
  will be removed in a future version."*

The live surface is the **collections** metric namespace, and it validates its components by
`isinstance` — `ragas/metrics/collections/base.py:113-131` **[SOURCE-ragas-0.4.3]**:

```python
def _validate_llm(self):
    if not isinstance(llm, InstructorBaseRagasLLM):
        raise ValueError("Collections metrics only support modern InstructorLLM. …")
def _validate_embeddings(self):
    if not isinstance(embeddings, BaseRagasEmbedding):
        raise ValueError("Collections metrics only support modern embeddings. …")
```

So subclassing is not a stylistic choice; nothing else is accepted.

**`AegisRagasLLM(InstructorBaseRagasLLM)`** — the base declares exactly two abstract methods,
`ragas/llms/base.py:730-748` **[SOURCE-ragas-0.4.3]**:

```python
class InstructorBaseRagasLLM(ABC):
    @abstractmethod
    def generate(self, prompt: str, response_model: t.Type[InstructorTypeVar]) -> InstructorTypeVar: ...
    @abstractmethod
    async def agenerate(self, prompt: str, response_model: t.Type[InstructorTypeVar]) -> InstructorTypeVar: ...
```

`agenerate` is the one that runs. Its body:

1. Derive `response_model.model_json_schema()` and append it to the prompt as an explicit
   output contract — the model is not psychic about the Pydantic class.
2. `result = await aegis.gateway.complete(role, [{"role": "user", "content": prompt}],
   temperature=0.0, response_format={"type": "json_object"}, max_tokens=…)`. The gateway
   already does one corrective re-ask on invalid JSON **[SOURCE]** `llm.py:1593-1595`, so
   that retry is free and is not reimplemented here.
3. **Strip the reasoning preamble with the repo's existing stripper.** `judge.py:109`
   `_json_candidates` **[SOURCE]** already handles a `<think>…</think>` block
   (`_THINK_BLOCK`, `judge.py:31`), an unterminated `<think>` ramble, a ```` ```json ````
   fence (`_FENCE`, `judge.py:34`), and prose either side of the object, by walking to the
   first balanced `{…}`. Neither library does any of this: `ragas` hands the string to
   `instructor`, and `deepeval`'s `trimAndLoadJson`
   (`deepeval/metrics/utils.py:455-488` **[SOURCE-deepeval-4.2.0]**) does only
   `find("{")` / `rfind("}")` plus a trailing-comma retry — which mangles any `<think>`
   block that happens to contain a brace. The fleet's REASONING role is
   `genailab-maas-Phi-4-reasoning` with `genailab-maas-DeepSeek-R1` in the same tier
   **[SOURCE]** `aegis/src/aegis/gateway/routing.py:141-142`, both of which emit that
   preamble routinely. **Promote `_json_candidates` from private to a shared internal helper
   (`aegis/evals/_jsonish.py`) and have `judge.py` and both adapters import the one copy.**
   Do not fork it.
4. `return response_model.model_validate_json(candidate)` over the candidates in order.
5. On exhaustion, raise `JudgeUnavailableError` **[SOURCE]** `judge.py:49` — never return a
   zero-valued instance of the schema. See 5.3.

`generate` (sync) is abstract and must exist. Implement it to raise a `RuntimeError` naming
`agenerate` / `ascore`. It is never reached on our path: `BaseMetric.score()` wraps
`asyncio.run(self.ascore(...))` **[SOURCE-ragas-0.4.3]**
`ragas/metrics/collections/base.py:63-80`, and `ascore` uses `agenerate`. A silent
`asyncio.run` inside a live event loop would be a deadlock; a loud error is the honest
implementation of a method we do not support.

Note that `instructor` ships as a `ragas` dependency and **is installed but unused** on this
path — we bypass it by validating against the Pydantic model ourselves. Say that in the
module docstring rather than letting a reader assume it is in the loop.

**`AegisRagasEmbeddings(BaseRagasEmbedding)`** — `ragas/embeddings/base.py:29-109`
**[SOURCE-ragas-0.4.3]** declares two abstract methods and two concrete batch methods:

```python
@abstractmethod def  embed_text(self, text: str, **kwargs) -> List[float]        # :51
@abstractmethod async def aembed_text(self, text: str, **kwargs) -> List[float]  # :64
             def  embed_texts(self, texts, **kwargs)                             # :76 default: loop
       async def aembed_texts(self, texts, **kwargs)                             # :92 default: asyncio.gather
```

The default `aembed_texts` is `asyncio.gather` over one `aembed_text` per text — **N
concurrent gateway calls, N limiter slots, N ledger rows for one logical batch**
`ragas/embeddings/base.py:107-109` **[SOURCE-ragas-0.4.3]**.

**Override `aembed_texts` to a single `await aegis.gateway.embed(texts)`.** The gateway's
`embed` already takes a list **[SOURCE]** `llm.py:1792`. One call, one slot, one row. This is
the clearest example of why an adapter beats a base URL: the adapter can be *better* than the
library's own transport, not merely equivalent to it.

### 5.2 `deepeval` — `aegis/evals/libs/deepeval_adapter.py`

`deepeval/models/base_model.py:61-153` **[SOURCE-deepeval-4.2.0]**:

```python
class DeepEvalBaseLLM(ABC):
    def __init__(self, model: Optional[str] = None, *args, **kwargs):
        self.name = parse_model_name(model)
        self.model = self.load_model()          # :64-65  — called from __init__
    def __init_subclass__(cls, **kwargs): ...   # :67-80  — wraps generate/a_generate in deepeval tracing
    @abstractmethod def load_model(...)         # :82
    @abstractmethod def generate(...) -> str    # :92
    @abstractmethod async def a_generate(...) -> str  # :101
    @abstractmethod def get_model_name(...) -> str    # :110
```

**The briefing said deepeval "requires schema support in its custom-model `generate()`". In
4.2.0 the schema arrives through a different door, and getting this wrong is a silent
correctness bug, not a crash.** The metrics call
`metric.model.a_generate_with_schema(prompt, schema=schema_cls)` — `deepeval/metrics/utils.py:563-594`
**[SOURCE-deepeval-4.2.0]** — and the base implements that concretely at `base_model.py:148-153`:

```python
async def a_generate_with_schema(self, *args, schema=None, **kwargs):
    if schema is not None:
        try:
            return await self.a_generate(*args, schema=schema, **kwargs)
        except TypeError:
            pass                      # ← "this means provider doesn't accept schema kwarg"
    return await self.a_generate(*args, **kwargs)
```

**That bare `except TypeError` is a trap.** Any `TypeError` raised anywhere inside our
`a_generate` body — a bad kwarg to `complete`, a `None` where a string was expected — is
swallowed and silently retried *without the schema*, producing an unstructured string that
then goes through `trimAndLoadJson`. The metric still returns a number. Nobody sees a failure.

Two rules follow, and both get a test (T-4):

1. `async def a_generate(self, prompt: str, *, schema: type[BaseModel] | None = None) -> str | BaseModel`
   — `schema` must be an **explicitly named parameter**, so the `TypeError` branch is
   structurally unreachable for the reason the library intends it.
2. **The body raises no `TypeError`, ever.** Wrap the whole body so that an internal
   `TypeError` is re-raised as `JudgeUnavailableError`. A schema-less silent fallback is
   exactly the class of dishonest degradation this repo forbids.

Implement `load_model()` to return `self` (there is no model object to load; the gateway is a
module function) and `get_model_name()` to return the resolved deployment id from
`aegis.gateway.routing.model_for(role)`, so the deepeval report names the model that actually
answered rather than a label we invented.

Because our class is a `DeepEvalBaseLLM` but not one of deepeval's own provider classes,
`initialize_model` returns `using_native_model=False` — `deepeval/metrics/utils.py:698-711`
**[SOURCE-deepeval-4.2.0]** — so deepeval accrues no cost of its own. Correct: our ledger is
the record, and two cost numbers for one call is the disagreement Aegis exists to prevent.

**Always pass `model=AegisDeepEvalLLM(...)` to every metric, including the ones that make no
LLM call.** `initialize_model(None)` falls through to `return OpenAIModel(model=model), True`
— `deepeval/metrics/utils.py:735-737` **[SOURCE-deepeval-4.2.0]** — which constructs an
OpenAI client in the metric's `__init__`. A metric that never calls a model would still
demand a key.

### 5.3 `BudgetExceededError` propagates — NOT RUN, never 0.0

`aegis/src/aegis/gateway/types.py:25` **[SOURCE]** defines `BudgetExceededError`; `complete`
raises it when the injected governance hook refuses **[SOURCE]** `llm.py:1616`.

Neither adapter catches it. Neither adapter catches `SlotUnavailableError` or
`ModelUnavailableError` either. This follows the contract `judge.py:49` **[SOURCE]** already
states, and states better than this document could:

> *"This exists so a judge outage is *distinguishable from a genuine ``0.0``*. Any caller that
> gates a release on the judge MUST let this propagate (fail closed): substituting ``0.0``
> makes a draft and its baseline score identically ``0.0``, which silently PASSES a
> ``margin=0.0`` eval gate and auto-promotes every candidate prompt. A control that cannot
> run must stop the release, not wave it through."*

The runner catches these **one level up, per metric**, and records
`MetricReading(computed=False, value=None, not_run_reason="budget_exceeded")` — reusing the
`MetricConfig(computed=False, value=None)` shape the dashboard already renders **[SOURCE]**
`metrics.py:140`, `harness.py:175-182`. A budget-stopped metric shows as *not run*, with the
reason, in the same visual slot that today reads *"One cell left empty."* The release gate
(Part 9) does not catch it at all — there, it aborts the release.

---

## Part 6 — Which metric from which library, and why never both

**The rule: one question, one number, one library.** Running `ragas` faithfulness and
`deepeval` faithfulness over the same corpus doubles the spend and returns two different
answers to one question, with nothing to arbitrate between them. Every metric below has
exactly one owner.

### Ragas owns "is the RAG answer good"

| Metric (`ragas.metrics.collections`) | Question | Replaces |
|---|---|---|
| `Faithfulness` | Is every claim in the answer supported by the retrieved context? | the `groundedness` **lexical** proxy, `metrics.py` |
| `AnswerRelevancy` | Does the answer address the question? | **nothing — this is the empty cell**, `harness.py:175-182` |
| `ContextPrecisionWithReference` | Are the retrieved passages relevant, and well-ranked? | the `context_precision` proxy |
| `ContextRecall` | Was the context the answer needs actually retrieved? | the `context_recall` proxy |

`AnswerRelevancy` is the headline. Everything else has a defensible proxy today; this one has
a blank.

### DeepEval owns agentic behaviour and the CI gate

| Metric | Question | Replaces |
|---|---|---|
| `ToolCorrectnessMetric` | Did the agent call the tools it should have? | the `tool_selection_accuracy` metric, `regression.py:241-265` |
| `assert_test` / `@pytest.mark.parametrize` gate | Did quality regress in CI? | the hand-written `run_regression_gate` **assertion style** (the report object stays) |

**Correction: `ToolCorrectnessMetric` is no longer free.** The briefing described it as
"deterministic and free". In 4.2.0 that is conditional. Read at
`deepeval/metrics/tool_correctness/tool_correctness.py` **[SOURCE-deepeval-4.2.0]**:

- `__init__` (`:37-62`) now takes `available_tools` and `model`, and imports
  `a_generate_with_schema_and_extract`; the package ships
  `templates/get_tool_selection_score.txt` and a `ToolSelectionScore` schema.
- `a_measure` (`:195-206`): **if and only if** `self.available_tools` is truthy does it call
  `await self._a_get_tool_selection_score(...)` — an LLM call. Otherwise it substitutes
  `ToolSelectionScore(score=1, reason="No available tools were provided…")`.
- `_calculate_score` and `_generate_reason` (`:275-295`) are pure set/ordering comparison and
  string formatting. No model.

**So: construct it with `available_tools=None` and it makes zero LLM calls and is exactly the
free deterministic gate we want — but pass `model=` anyway (5.2) so no OpenAI client is
built.** If someone later wants the LLM-graded tool-*selection* dimension, that is a separate,
budgeted decision, not a default. Assert the zero-call property in a test (T-5) rather than
trusting a default that has already moved once inside a minor line.

### What is not adopted, and why

- **`ragas` testset generation** — synthesises a corpus with an LLM. Aegis's corpus is a
  frozen constant *because* the gate is deterministic (`corpus.py:12` **[SOURCE]**:
  *"Everything here is a constant: the gate is deterministic because its inputs are frozen"*).
  Generating it would destroy that.
- **`deepeval`'s RAG metrics** (`AnswerRelevancyMetric`, `FaithfulnessMetric`) — duplicates of
  ragas's, by the one-owner rule.
- **Confident AI / `deepeval login`** — the hosted platform. Aegis's record is `run_events`
  and `usage_ledger`. No eval data leaves the box.
- **`ragas.evaluate()`** — deprecated **[SOURCE-ragas-0.4.3]** `evaluation.py:447`, and it
  also fires analytics (Part 10). Call `await metric.ascore(...)` per case.

---

## Part 7 — What one run costs, counted from the library sources

Call counts are **[SOURCE-ragas-0.4.3]**, counted by reading each metric's `ascore`. Token
sizes and USD are **[ESTIMATE]** until T-9 measures them.

The corpus is 6 cases (`SEED_CASES`, `corpus.py:100-131` **[SOURCE]**) over 5 documents →
5 chunks (`corpus.py:47-88`, `corpus_chunks()`), so a retrieval returns at most 5 sources
even though `final_top_k=6` **[SOURCE]** `aegis/src/aegis/retrieval/pipeline.py:105`.

| Metric | Completions per case | Where counted **[SOURCE-ragas-0.4.3]** |
|---|---|---|
| the answer under test | 1 | `harness.py:334-338` **[SOURCE]** — generate, *then* grade |
| `Faithfulness` | **2** (statement extraction, then NLI verdict) | `collections/faithfulness/metric.py:136` and `:145` |
| `AnswerRelevancy` | **3** (`strictness=3`, default) | `collections/answer_relevancy/metric.py:116-120` — `for _ in range(self.strictness)` |
| `ContextPrecisionWithReference` | **≤5** — one per retrieved context | `collections/context_precision/metric.py:105-111` — `for context in retrieved_contexts` |
| `ContextRecall` | **1** | `collections/context_recall/metric.py:119` |
| `ToolCorrectnessMetric` | **0** (with `available_tools=None`) | Part 6 |
| **Total** | **12 per case → 72 per run** | |

Embeddings: `AnswerRelevancy` makes `aembed_text(user_input)` plus `aembed_texts(3 questions)`
— `collections/answer_relevancy/metric.py:134` and `:139`. With `aembed_texts` overridden
(5.1) that is **2 gateway calls per case → 12 per run**, not 24.

**Cost [ESTIMATE].** Rates are USD per 1k tokens **[SOURCE]** `routing.py:96`. At roughly 900
input / 200 output tokens per eval call (ragas prompts carry the context inline), 72 calls is
~65k input and ~14k output tokens:

| Judge role | Deployment **[SOURCE]** `routing.py:139-142` | Per run **[ESTIMATE]** |
|---|---|---|
| `CHEAP` | `genailab-maas-gpt-4o-mini` @ $0.00015 / $0.0006 | **≈ $0.02** |
| `REASONING` | `genailab-maas-DeepSeek-R1` @ $0.00135 / $0.0054 | **≈ $0.30** — R1's `<think>` preamble roughly triples the output token count |

**Latency [ESTIMATE].** 72 sequential calls at 2-4 s is 150-300 s. With bounded concurrency
of 4 (see below), 40-75 s. Both are **two orders of magnitude** above the current
`/evals/report`.

**Concurrency is bounded at 4 by `asyncio.Semaphore` in the runner, not left to
`asyncio.gather`.** The gateway's fleet-wide limiter would otherwise refuse calls with
`SlotUnavailableError` **[SOURCE]** `llm.py:1617-1619`, and — per 5.3 — that would correctly
mark metrics NOT RUN. A suite that fails because it DDoS'd its own limiter is a bad suite.

**Judge role: `CHEAP` by default, `REASONING` opt-in via `AEGIS_EVAL_JUDGE_ROLE`.** Fifteen
times the cost for a structured-extraction task is not obviously worth it, and it is not this
plan's job to assert which scores better. The role is a knob and the report records which one
ran, so the question is answerable by measurement later.

---

## Part 8 — The route: never on a dashboard poll

`backend/src/app/api/routes.py:3882` **[SOURCE]** is `GET /evals/report`, and `:3843-3846`
**[SOURCE]** explains the memoisation:

> *"The regression gate is a deterministic, network-free computation (~1s) over the seed
> corpus, so its result is stable for the process lifetime — memoised once so repeated
> dashboard polls do not re-run it."*

Both halves of that sentence stop being true for a real suite: it is neither deterministic nor
~1s, and `EvalsView.tsx:284` **[SOURCE]** fetches it on every mount and on every token change.
**Wiring the real suite behind that path turns a page refresh into ~$0.30 and 60 s.**

So:

- **`GET /evals/report` is untouched.** Same handler, same memo, same `run_regression_gate`,
  same ~1s. It remains the free deterministic view. Only its `source` string and prose change
  (Part 11).
- **`POST /evals/run` is new** — `require_admin_or_ai_team` (matching the existing guard
  **[SOURCE]** `routes.py:3883`), a **POST** because it spends money and POST is what a
  browser will not do on a poll, and body `{"suite": "ragas" | "tool_correctness", "limit": int}`.
  It binds the caller's `GovernanceContext` exactly as every other governed route does, so
  `_GovernanceHook.get_context()` **[SOURCE]** `backend/src/app/core/llm.py:163-168` returns
  a real context and `enforce` runs before spend.
- **`GET /evals/runs` and `GET /evals/runs/{id}`** read persisted results. A real suite run is
  a durable artifact with a timestamp, not a value cached in a module global — which also
  retires the `EvalsView` disclaimer *"The report carries no timestamps, so a curve would be
  invented rather than measured"* **[SOURCE]** `EvalsView.tsx:398`, honestly, by producing
  timestamps.
- **Admission-capped.** One in-flight real suite per tenant. Two concurrent runs is 144
  completions.
- **Response shape** carries `metrics[]` in the existing `MetricConfig` shape (so the
  dashboard needs no new renderer), plus `library` (`"ragas 0.4.3"` / `"deepeval 4.2.0"`),
  `judge_role`, `llm_calls`, `embed_calls`, `cost_usd`, `trace_id`, and per-metric
  `not_run_reason`.

---

## Part 9 — The release gate scores on a real Ragas metric

`aegis/src/aegis/ops/gate.py:67` `make_eval_fn` **[SOURCE]** today returns a scorer that, per
case: builds the offline retriever, generates an answer under the candidate `system_prompt`,
then grades it with the hand-written judge — `gate.py:131` calls `judge_answer(...)` and
`:132` averages `(verdict.groundedness + verdict.relevance) / 2.0` **[SOURCE]**. That average
of two hand-rolled numbers is the value `aegis/src/aegis/ops/release.py:337-338` **[SOURCE]**
compares against `baseline + margin` to decide whether a prompt is promoted.

**Replace the score, keep the contract.** New `make_eval_fn(complete, *, limit=3,
scorer="ragas")`:

- steps 1 and 2 unchanged — same `build_eval_retriever()`, same generation under the candidate
  prompt (this is what makes the score prompt-*dependent*, and `gate.py:81-84` **[SOURCE]**
  is emphatic about why that matters);
- step 3 becomes `await Faithfulness(llm=AegisRagasLLM(...)).ascore(...)` and
  `await AnswerRelevancy(llm=…, embeddings=AegisRagasEmbeddings()).ascore(...)`, averaged —
  the same two dimensions the hand-written judge produced, now from the real library;
- `limit` stays 3 **[SOURCE]** `gate.py:54` (`DEFAULT_EVAL_SUBSET`). Note the arithmetic: at 5
  ragas calls per case per candidate, and `release()` scoring **both** draft and baseline
  (`release.py:337-338`), one release is 3 × 5 × 2 = 30 eval completions plus 6 generations.
  Do not raise `limit` without re-reading that line.

**Fail-closed is preserved literally.** `gate.py:91-96` **[SOURCE]** — *"the scorer never
invents a number … It is deliberately *not* caught-and-zeroed: zeroing scores the draft and
its baseline identically, which passes a `margin=0.0` gate and auto-promotes every candidate
on a judge outage."* The ragas path raises the same `JudgeUnavailableError` on an unusable
reply (5.1) and lets `BudgetExceededError` through (5.3), so
`aegis/tests/ops/test_release_fail_closed.py` **[SOURCE]** passes unchanged. **If that file
needs editing, the implementation is wrong.**

Keep `scorer="judge"` as a parameter selecting the old path. The release gate must still run
on a machine with no `ragas` installed — that is what `aegis.core.lazy.require` is for, and a
release gate that cannot run because an optional extra is missing is a worse failure than the
one being fixed.

---

## Part 10 — Telemetry: two libraries phone home, not one

**`deepeval`.** `deepeval/telemetry/client.py:25-30` **[SOURCE-deepeval-4.2.0]** hardcodes a
PostHog project key and `https://us.i.posthog.com`, gated on
`bool(get_settings().DEEPEVAL_TELEMETRY_OPT_OUT)`. `deepeval/telemetry/__init__.py:8`: *"Opt
out with `DEEPEVAL_TELEMETRY_OPT_OUT=1`."* The setting is a pydantic `Optional[bool]`
(`deepeval/config/settings.py:788`) parsed by a validator at `:1256-1300` whose documented
precedence is *"Any OFF signal wins"* — so `1` works, and the deprecated
`DEEPEVAL_TELEMETRY_ENABLED` cannot re-enable it.

**`ragas` also ships telemetry, and the briefing did not mention it.**
`ragas/_analytics.py:36-49` **[SOURCE-ragas-0.4.3]**:

```python
USAGE_TRACKING_URL = "https://t.explodinggradients.com"
RAGAS_DO_NOT_TRACK = "RAGAS_DO_NOT_TRACK"

def do_not_track() -> bool:
    return os.environ.get(RAGAS_DO_NOT_TRACK, str(False)).lower() == "true"
```

**Read that comparison carefully. It matches the literal string `"true"` only.**
`RAGAS_DO_NOT_TRACK=1` does **not** opt out — it is falsy to this function, and tracking stays
on. The value must be exactly `true` (case-insensitive). `ragas/metrics/base.py:450` and
`:494` **[SOURCE-ragas-0.4.3]** show `_analytics_batcher.add_evaluation(EvaluationEvent(...))`
firing inside `single_turn_ascore`, and `ragas/_analytics.py:287-289` registers an `atexit`
flush — so events accumulate in-process and go out at interpreter shutdown.

**Both variables go into `backend/.env` and `backend/.env.example`**, in a commented block
that says why, in the register that file already uses (its existing entries run to twenty
lines of reasoning apiece — see `AEGIS_STORAGE_ENCRYPTION` and `AEGIS_MCP_ALLOW_PRIVATE_PEERS`
**[SOURCE]**):

```bash
# ── Eval libraries: telemetry off, both of them ───────────────────────────────
# `deepeval` ships a PostHog client (deepeval/telemetry/client.py) and `ragas` posts
# to t.explodinggradients.com (ragas/_analytics.py). Aegis's claim is that every model
# call and every eval is accounted for on this box; a background POST to a vendor,
# carrying which metrics ran and how many rows, is that claim's opposite.
#
# RAGAS_DO_NOT_TRACK must be the literal string "true". ragas compares
# `os.environ.get(...).lower() == "true"`, so RAGAS_DO_NOT_TRACK=1 leaves tracking ON.
DEEPEVAL_TELEMETRY_OPT_OUT=1
RAGAS_DO_NOT_TRACK=true
```

Both are also set in the isolated `uv run` invocation (Part 13) — a CI runner does not read
`backend/.env`. A test asserts them (T-6).

**Filesystem side effects.** `deepeval/constants.py:5-6` **[SOURCE-deepeval-4.2.0]** —
`KEY_FILE = ".deepeval"`, `HIDDEN_DIR = os.getenv("DEEPEVAL_CACHE_FOLDER", ".deepeval")`; it
also honours `DEEPEVAL_HOME` (default `~/.deepeval`) for *"the anonymous telemetry id"*
(`config/settings.py:796`). `ragas` uses `appdirs` with `USER_DATA_DIR_NAME = "ragas"`
(`_analytics.py:39`). Add `.deepeval/` to `.gitignore` — it is absent today **[MEASURED]** —
and point `DEEPEVAL_CACHE_FOLDER` at the repo's existing `runs/` area so an eval run leaves
its droppings somewhere the repo already accounts for.

---

## Part 11 — Retiring the claims

Shipping the real libraries while the site still reads *"no ragas dependency"* is precisely
the dishonesty this repo forbids. **This part is not optional cleanup; it lands in the same
commit as the extras.**

Every file that asserts an imitation, found by grepping `ragas-style`, `RAGAS-style`,
`no ragas dependency`, `DeepEval-pattern`, `DeepEval-shaped`, `RAGAS-the-library` across the
repo **[MEASURED]**:

### The user-visible claims — these are the ones that become false

| File:line **[SOURCE]** | Current text | Becomes |
|---|---|---|
| `web/src/components/landing/stackClaims.ts:110` | `'RAGAS-style deterministic proxies — no LLM call, no ragas dependency'` | the real library, named and versioned |
| `README.md:210` | `RAGAS-style deterministic proxies — no LLM call, no \`ragas\` dependency` | same |
| `README.md:93` | `\| **Aegis Evals** \| RAGAS-style proxies + LLM judge \|` | `ragas 0.4.3 + deepeval 4.2.0, through the gateway` |
| `backend/src/app/capabilities.py:151` | `tech="RAGAS-style proxies + LLM judge"` | same — **this one is served live** at `GET /platform/capabilities` and `GET /about` |
| `backend/src/app/main.py:157` | the same line inside the OpenAPI description | same |
| `web/src/components/evals/EvalsView.tsx:272` | *"the deterministic RAGAS/DeepEval-pattern gate scored with **no LLM**"* | must distinguish the two gates |
| `web/src/components/evals/EvalsView.tsx:94` | `return 'Deterministic overlap metric — no LLM.'` — the **default** `metricGloss` | a per-metric gloss that does not assert "no LLM" for metrics that use one |
| `docs/architecture/eval-strategy.md:10-13` | *"`ragas`, `deepeval`, `langfuse`, and `patronus` are **not** dependencies of this repo"* | **the load-bearing false sentence.** Rewrite: which two are now dependencies, in which environment, and which two still are not |
| `docs/architecture/eval-strategy.md:31,53-54` | the Layer-1 "proxies" framing | four layers: proxies (free), ragas (real), deepeval (real), Phoenix (live) |
| `docs/architecture/backend.md:40,167` | `RAGAS-style proxies + LLM judge`; *"**not** the `ragas` library (which is not a dependency…)"* | same |
| `docs/architecture/system-architecture.md:196` | `RAGAS-style metrics + an LLM-judge harness` | same |
| `docs/module/MODULE_REFERENCE.md:125` | mermaid node `aegis.evals<br/>RAGAS-style + LLM-judge` | same |
| `docs/teaching/evals.md:5,36,101` | *"are described as 'RAGAS-style,' a deterministic approximation"* | same |

### The internal descriptions — accurate about the free gate, but must stop implying it is the whole story

`aegis/src/aegis/evals/__init__.py:3-4`, `metrics.py:3-7,140`, `harness.py:133`,
`regression.py:1,3-7,22-24,308,394`, `backend/src/app/eval/__init__.py:3-4`,
`backend/src/app/eval/metrics.py:3`, `backend/src/app/eval/regression.py:3,84`,
`backend/tests/eval/test_regression_gate.py:1,4`,
`aegis/tests/evals/test_regression_gate.py:1`,
`aegis/tests/evals/test_data_consistency.py:183`,
`scripts/preflight.sh:52,57`, `scripts/preflight.ps1:82,92`,
`backend/src/app/platform/compliance.py:583,1333,1453,1943`.

These stay **substantially true** — the free gate really is a hand-rolled proxy and really
does run with no LLM. Each gets one added clause: *"…and `aegis.evals.libs` runs the real
`ragas` / `deepeval` over the same corpus when the extra is installed."* Do not delete the
honest description of the free gate; the free gate still exists and its honesty is an asset.

`backend/openapi.json:13136,14797` and `web/src/lib/api/generated/schema.d.ts:1021` are
**generated**. Do not hand-edit; regenerate after the docstrings change, and verify the
regeneration in T-8.

### The new one-line claim

> **Aegis Evals** — `ragas 0.4.3` + `deepeval 4.2.0`, plus a dependency-free offline gate.
> Every eval call goes through the Aegis gateway: budgeted, ledgered, traced.

That is a stronger sentence than the one it replaces, and — unlike the current one — it stays
true after the extras are installed.

---

## Part 12 — The empty cell

`web/src/components/evals/EvalsView.tsx:429-445` **[SOURCE]**. The comment above the card is
worth reading before touching it:

> *"This card used to be a dashed box holding one badge — the weakest thing on the screen, and
> it was carrying the most interesting claim: that the platform leaves a cell of the score
> matrix empty rather than fill it with a number it cannot defend."*

```tsx
<CardHeader eyebrow="ragas · answer relevancy" title="One cell left empty" />
<CardBody>
  <SceneState name="matrix" size="sm">
    <Absence
      figure="Answer relevancy"
      why="Scoring it needs a model to judge a model; every figure here is deterministic."
      needed="An LLM judge wired into the gate."
    />
```

**It says `needed="An LLM judge wired into the gate."` This work is that.**

The card becomes stateful, and — this is the whole point — **it keeps the `Absence` state**:

- **No real run yet** → the `Absence` stands, `needed` rewritten to *"A run of the real ragas
  suite — POST /evals/run"*, with the trigger button beside it.
- **A run exists** → the real `AnswerRelevancy` score, the same `MetricBullets` bar every
  other metric uses, and a `Receipt` carrying `ragas 0.4.3 · answer_relevancy · N cases ·
  <judge model> · $X.XX · trace <id>`.
- **Budget refused, or the judge produced nothing usable** → `Absence` again, with
  `why="Not run: the tenant budget refused the call"` and the `trace_id`. **Not `0.0`.** This
  is 5.3 rendered on a screen, and it is the most persuasive thirty seconds available: *the
  cell went from empty, to a real number from the real library, to empty-with-a-reason when
  the budget stopped it — and it never once showed a zero it could not defend.*

`MetricConfig` already carries `computed`/`value: None` **[SOURCE]** `metrics.py:140`, so the
shape needs no invention. `metricGloss` (`EvalsView.tsx:89-95` **[SOURCE]**) gains an
`answer_relevancy` branch and loses its "no LLM" default (Part 11).

---

## Part 13 — Verification

> Nothing below is satisfied by reading code. Every item is a command with an expected
> observation.

### Environments

```bash
# Ragas — into the backend venv. Expect NO huggingface-hub line in the output.
cd /Users/yrevash/aegis/backend && uv pip install --dry-run --python .venv/bin/python "ragas==0.4.3"
uv pip install --python .venv/bin/python "ragas==0.4.3"
.venv/bin/python -c "import importlib.metadata as m; print('hub', m.version('huggingface-hub'))"
# EXPECT: hub 1.27.0   ← if this prints 1.16.1, deepeval leaked into this venv. Stop.

# DeepEval — never installed into that venv. Ephemeral, per invocation:
export DEEPEVAL_TELEMETRY_OPT_OUT=1 RAGAS_DO_NOT_TRACK=true
uv run --isolated --with "deepeval==4.2.0" --with-editable ./aegis \
  pytest aegis/tests/evals/libs/test_deepeval_gate.py -q
```

### The suites

```bash
# 1. The free gate — unchanged, still ~1s, still offline. Run it FIRST and LAST.
cd backend && PYTHONPATH=src .venv/bin/python -m app.eval.regression   # exit 0
./scripts/preflight.sh                                                # exit 0 (gate is its exit code)

# 2. The isolation test — the one that must not move.
cd aegis && .venv/bin/pytest tests/evals/test_isolation.py -q          # PASSES with ragas installed

# 3. The adapter unit tests (offline, fake gateway — no keys, no network).
cd aegis && .venv/bin/pytest tests/evals/libs -q

# 4. The real ragas suite against a live gateway (SPENDS MONEY — see Part 7).
cd backend && PYTHONPATH="../aegis/src:src" .venv/bin/python -m app.eval.real_suite --limit 6
```

### The endpoints

```bash
TOKEN=...   # an admin or ai_team bearer

# The free report — unchanged shape, ~1s, no spend.
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/evals/report | jq '.source, .passed'
# EXPECT: "offline_regression_gate"  and  true

# The real suite — POST, because it spends.
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"suite":"ragas","limit":6}' localhost:8000/evals/run | jq '{
    library, judge_role, llm_calls, embed_calls, cost_usd, trace_id,
    metrics: [.metrics[] | {name, value, computed, not_run_reason}] }'
```

**Expected shape** — the numbers are what Part 7 predicted, and a mismatch is a finding:

```json
{ "library": "ragas 0.4.3", "judge_role": "cheap",
  "llm_calls": 72, "embed_calls": 12, "cost_usd": 0.0x, "trace_id": "…",
  "metrics": [ {"name":"faithfulness","value":0.…,"computed":true,"not_run_reason":null},
               {"name":"answer_relevancy","value":0.…,"computed":true,"not_run_reason":null},
               {"name":"context_precision","value":0.…,"computed":true,"not_run_reason":null},
               {"name":"context_recall","value":0.…,"computed":true,"not_run_reason":null} ] }
```

```bash
curl -s -H "Authorization: Bearer $TOKEN" localhost:8000/evals/runs | jq '.[0] | {id, started_at, cost_usd}'
```

### Proving the calls went through the gateway — the load-bearing check

**This is the one that decides whether the plan was actually implemented.** The gateway writes
a `usage_ledger` row per call via `record_call` **[SOURCE]** `llm.py:1841-1846`.

```sql
-- Baseline
SELECT count(*), coalesce(sum(cost_usd),0) FROM usage_ledger;
-- …POST /evals/run…  then:
SELECT model, count(*) AS calls, round(sum(cost_usd)::numeric, 4) AS usd
  FROM usage_ledger WHERE created_at > :t0 GROUP BY model ORDER BY calls DESC;
```

**EXPECT: 84 new rows** — 72 completions on the judge deployment + 12 on
`genailab-maas-text-embedding-3-large` — and `sum(cost_usd)` equal to the `cost_usd` the
endpoint returned. **Zero new rows means the libraries reached a provider directly and the
whole design was bypassed.** Cross-check the same run in Phoenix: 84 `gen_ai.*` spans under
one `trace_id`, since the gateway's observability sink emits them on the same path.

### Budget refusal → NOT RUN, never 0.0

```sql
UPDATE budgets SET usd_cap = 0.0 WHERE tenant_id = <t>;
```

```bash
curl -s -X POST ... /evals/run | jq '[.metrics[] | {name, value, computed, not_run_reason}]'
```

**EXPECT** every metric `{"value": null, "computed": false, "not_run_reason": "budget_exceeded"}`.
**A single `0.0` here is a bug of exactly the class `judge.py:49` was written to prevent.**
And: `SELECT count(*) FROM usage_ledger WHERE created_at > :t0` must be **0** — no row for
work that did not happen.

### Tests to write

| id | File | Asserts |
|---|---|---|
| **T-1** | `aegis/tests/evals/test_isolation.py` — **unchanged** | `ragas`/`deepeval` absent from `sys.modules` after importing the five core modules, *with `ragas` installed* |
| **T-2** | `aegis/tests/evals/libs/test_lazy_import.py` | importing `aegis.evals.libs` alone does not import `ragas`; it appears in `sys.modules` only after the first adapter call. Missing extra → `ImportError` naming `pip install aegis[evals-ragas]` |
| **T-3** | `aegis/tests/evals/libs/test_no_direct_provider.py` | with `OPENAI_API_KEY` / `OPENAI_BASE_URL` **unset** and `socket.socket` monkeypatched to raise, a full ragas metric run against a fake `complete`/`embed` **succeeds** — proving nothing opens a socket of its own |
| **T-4** | `aegis/tests/evals/libs/test_deepeval_schema.py` | `a_generate(prompt, schema=Model)` returns a `Model`; a `TypeError` raised inside the body surfaces as `JudgeUnavailableError` and is **not** silently retried schema-less by `a_generate_with_schema` (Part 5.2) |
| **T-5** | `aegis/tests/evals/libs/test_tool_correctness_free.py` | `ToolCorrectnessMetric(available_tools=None, model=<adapter>)` over `ROUTER_EVAL_CASES` makes **zero** calls to the injected `complete` — pinning the Part-6 correction against a future deepeval release |
| **T-6** | `aegis/tests/evals/libs/test_telemetry_off.py` | `RAGAS_DO_NOT_TRACK` is the literal `"true"` (asserted via `ragas._analytics.do_not_track() is True`) and `DEEPEVAL_TELEMETRY_OPT_OUT` is truthy; both present in `backend/.env.example` |
| **T-7** | `aegis/tests/evals/libs/test_thinkblock.py` | a DeepSeek-shaped reply — `<think>…{"a":1}…</think>\n\`\`\`json\n{...}\n\`\`\`` — parses through both adapters, and the **same** `_json_candidates` is imported by `judge.py` and both adapters (one copy, not three) |
| **T-8** | `backend/tests/test_claims_are_true.py` | grep-style: no file under `web/`, `README.md`, `backend/src/app/capabilities.py`, `backend/src/app/main.py` or `docs/architecture/` contains `no ragas dependency`; and `capabilities.py`'s `tech` for `evals` names both libraries with versions matching the pins in `aegis/pyproject.toml` |
| **T-9** | `backend/tests/eval/test_real_suite_call_count.py` | with a counting fake `complete`/`embed`, a 6-case ragas suite makes **exactly 72** completions and **12** embed calls — turning Part 7's arithmetic into a regression test, so a `strictness` default change is caught by CI rather than by an invoice |
| **T-10** | `aegis/tests/ops/test_release_fail_closed.py` — **unchanged** | must pass with `scorer="ragas"` parametrised in. If this file needs editing, the implementation is wrong |
| **T-DEP-1** | manual, Part 3 | the hub-downgrade validation, only if the single-venv option is pursued |

### In the browser

`http://localhost:3000/evals`, signed in as admin or ai_team:

1. **On load**, before any run: the page is unchanged from today — same metric bullets, same
   metric × case matrix, and the *"One cell left empty"* card still reads as an `Absence`,
   with `needed` now naming the run button. **Nothing spent.** Confirm in devtools that the
   only request is `GET /evals/report`, and confirm in `usage_ledger` that a page reload adds
   zero rows.
2. **Click "Run the real suite"**: loading state, then the ragas card shows a real
   `AnswerRelevancy` bar with a `Receipt` naming `ragas 0.4.3`, the case count, the judge
   deployment, the USD, and a `trace_id` that links to Phoenix.
3. **Set the tenant cap to 0 and re-run**: the card returns to `Absence` reading *"Not run:
   the tenant budget refused the call"* with the trace id. **Screenshot this.** It is the
   whole argument in one frame.
4. **Landing page** `/`: the stack-claims row for the eval gate names `ragas` and `deepeval`
   as dependencies, not as things avoided.
5. `curl -s localhost:8000/about | jq '.modules[] | select(.key=="evals")'` — the live
   manifest agrees with the landing page. These two disagreeing is the exact drift Part 11
   exists to close.

---

## Definition of done

- [ ] `ragas==0.4.3` in the backend venv; `huggingface-hub` still **1.27.0**, verified by
      command, not by assumption.
- [ ] `deepeval==4.2.0` runs in an isolated `uv run` environment and is **not** in
      `backend/.venv`.
- [ ] `aegis/tests/evals/test_isolation.py` passes **unedited**, with `ragas` installed.
- [ ] `python -m app.eval.regression` still exits 0 in ~1s with no network; `preflight.sh`
      exits 0.
- [ ] `GET /evals/report` is byte-identical in shape and still memoised.
- [ ] `POST /evals/run` returns real `ragas` scores, and **84 new `usage_ledger` rows** appear
      for one 6-case run, summing to the `cost_usd` the endpoint reported.
- [ ] The same run produces 84 `gen_ai.*` spans under one trace in Phoenix.
- [ ] A tenant at its cap gets `computed:false` + `not_run_reason` on every metric and
      **zero** ledger rows.
- [ ] `ToolCorrectnessMetric` runs with **zero** LLM calls, asserted by test.
- [ ] `aegis/src/aegis/evals/ir_metrics.py` is untouched and still exported.
- [ ] `aegis/tests/ops/test_release_fail_closed.py` passes with the ragas scorer, unedited.
- [ ] `DEEPEVAL_TELEMETRY_OPT_OUT=1` and `RAGAS_DO_NOT_TRACK=true` (literal `true`) in
      `backend/.env`, `backend/.env.example` and the CI invocation.
- [ ] `.deepeval/` in `.gitignore`.
- [ ] Every file in Part 11 changed; `backend/openapi.json` and `schema.d.ts` regenerated, not
      hand-edited; **no string `no ragas dependency` survives anywhere in the repo.**
- [ ] The evals screen shows a real answer-relevancy number, and shows an `Absence` with a
      reason when the budget refuses — never a `0.0`.

---

## Risks, stated plainly

1. **The click conflict is real and the briefing had it backwards.** `deepeval 4.2.0` caps
   `click<8.4.0`; `huggingface-hub>=1.27.0` requires `click>=8.4.2`; `uv` calls them
   *incompatible* **[MEASURED]**. No published `deepeval` (4.2.0, 4.1.0, 4.0.0, 3.7.0) relaxes
   it **[MEASURED]**. The environment split in Part 3 is the mitigation, and it is not
   optional. **Anyone who "simplifies" this by installing both into `backend/.venv` silently
   downgrades `huggingface-hub` eleven minor versions under `transformers 5.8.1`, `docling`
   and `fastembed`.**
2. **The single most likely implementation failure is a `base_url`.** It is easier, it works
   on the first try, and it produces identical-looking scores with zero ledger rows. The
   detection is the ledger count in Part 13 and nothing else — the numbers on the screen look
   right either way.
3. **`deepeval`'s `a_generate_with_schema` swallows `TypeError`**
   (`base_model.py:148-153` **[SOURCE-deepeval-4.2.0]**) and retries without the schema. An
   unrelated `TypeError` in our adapter therefore degrades silently into unstructured output
   that still yields a number. T-4 exists for exactly this and must not be skipped.
4. **`RAGAS_DO_NOT_TRACK=1` does not disable ragas telemetry.** The comparison is
   `== "true"` **[SOURCE-ragas-0.4.3]** `_analytics.py:49`. The intuitive value is the wrong
   one, and it fails silently in the direction of sending data.
5. **`ToolCorrectnessMetric` changed inside a minor line.** It gained an LLM path in 4.x. Pin
   exactly and let T-5 fail loudly if a future version makes the LLM path unconditional.
6. **Cost and latency are two orders of magnitude above the current report.** ~72 completions
   and ~$0.02-$0.30 per run **[ESTIMATE]**. The separate POST route, the admission cap and the
   untouched memoised `GET /evals/report` are the three things keeping a dashboard poll from
   becoming a budget incident. Do not merge them "for consistency".
7. **Deprecation churn.** `ragas` deprecated `evaluate()` and `LangchainLLMWrapper` inside
   0.4 **[SOURCE-ragas-0.4.3]**; `deepeval` moved its schema entry point inside 4.x. Both pins
   are exact, and the wheels are vendored before rehearsal. A tutorial found online will show
   the deprecated API — the wheel is the authority, not the blog post.
8. **The claims are load-bearing and public.** `capabilities.py:151` is *served*, at
   `/platform/capabilities` and `/about`, and mirrored on the landing page. Part 11 lands in
   the same commit as the extras, or the repo ships the exact dishonesty it forbids —
   inverted, which is worse: claiming to avoid a dependency it now has.
