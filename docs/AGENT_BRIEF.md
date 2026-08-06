# AGENT_BRIEF — shared context for every module-builder subagent

You are building **one module** of a pre-built, domain-agnostic agentic platform
("the weapon") for the **TAIF S2 blind hackathon**. The work is graded by **a
human jury AND an AI reader** that parses code + docs — so structure, types,
docstrings, and clarity are *literally scored*.

## Step 1 — read the spec (these files ARE the requirements)

- `docs/hackathon.md` — mission, rubric, the "money-shot" demo, the winning sentence.
- `docs/how_to_approach.md` — operating manual (research-before-implement, quality bars).
- `docs/backend.md` — backend architecture.
- `docs/security.md` — security design.
- `backend/src/app/api/schemas.py` — **LOCKED** SSE `StreamEvent` union + endpoint
  request/response contracts. **Reuse these types; never redefine them.**
- `backend/src/app/core/models.py` — **LOCKED** `ModelRole` registry. Request models
  by **role**, never a hard-coded id.
- `backend/src/app/config.py` — `Settings` (pydantic-settings).
- `backend/pyproject.toml` — deps (your group is listed) + the Ruff config you must pass.

## Locked decisions (do not relitigate)

- **Gateway = LiteLLM** as a *custom OpenAI-compatible provider* → base_url
  `https://genailab.tcs.in`, self-signed cert (`settings.genailab_ssl_verify=False`
  → disable TLS verify for it). Model strings are **bare deployment names** (no
  `azure/` prefix); via LiteLLM use the `openai/<id>` custom-provider form with
  `api_base` + `api_key`.
- **Heterogeneous routing by `ModelRole`**. Embedding model fixed.
- **Reranker = API-based only** (NO local reranker model — RAM).
- **Guardrails = NeMo Guardrails** (Colang policy file = readable security artifact).
- **Constraints:** 16 GB **Windows** machine on the day, **no Docker, no GPU**, no
  local model weights. Dev on macOS, deploy to Windows. Everything **portable** —
  no absolute paths, no OS-specific code.

## Shared internal contracts (other agents build these in parallel)

Build to these **exact** signatures. If a symbol isn't importable yet, define a
`typing.Protocol` or thin stub and import the real module — do not block.

```python
# app.core.llm
from app.core.models import ModelRole
class ToolCallResult(BaseModel): id: str; name: str; args: dict
class Usage(BaseModel): prompt_tokens: int; completion_tokens: int; cost_usd: float
class LLMResult(BaseModel): content: str; tool_calls: list[ToolCallResult]; usage: Usage; model: str
async def complete(role: ModelRole, messages: list[dict], *, tools: list[dict] | None = None,
                   temperature: float = 0.0, response_format: dict | None = None) -> LLMResult: ...
async def embed(texts: list[str]) -> list[list[float]]: ...

# app.data
async def get_session() -> AsyncSession: ...          # FastAPI-style async dependency
async def record_audit(*, action: str, actor: str | None, model: str | None,
                       trace_id: str | None, payload: dict, approved_by: str | None = None) -> None: ...

# app.observability
def init_observability(app) -> None: ...
def get_tracer(): ...                                  # OTel tracer emitting gen_ai.* spans

# app.retrieval
async def retrieve(query: str, *, persona: str | None = None) -> RetrievalResult: ...
async def ingest(docs: list) -> IngestReport: ...

# app.ml
def predict_explain(features: dict) -> "app.api.schemas.MLExplainResponse": ...

# app.guardrails
class GuardResult(BaseModel): verdict: "app.api.schemas.GuardVerdict"; reason: str; text: str
async def check_input(text: str) -> GuardResult: ...
async def check_output(text: str) -> GuardResult: ...

# app.adapter — exposes: schema, personas, a tool registry (typed tool callables +
# per-persona allowlist), system prompts, ml_spec (features+target), generate_synthetic(...)
```

## Quality bars (SCORED — treat as features)

- Full type hints / Pydantic everywhere. Google-style docstrings on every public
  module/class/function.
- Must pass the repo Ruff config (`E,F,I,UP,B,SIM,ANN,D`). Run `ruff check` on your
  files and fix every finding before finishing.
- Small, single-responsibility files; **no god-files**; names consistent with the
  repo. Prefer pure functions + dependency injection over globals.
- No domain logic in the core; config-driven where it matters.
- Tests under `backend/tests/<yourmodule>/` that **run without live infra or API
  keys** — mock the LLM gateway, DB, Neo4j, Redis, and all network. Unit tests must
  not require Postgres/Neo4j/Redis/internet.

## Research mandate

Before writing against any fast-moving library, **WebFetch/WebSearch its official
current docs and verify the real API** — do NOT trust training memory (LangGraph,
LightRAG, NeMo Guardrails, MAPIE, LiteLLM, OpenTelemetry/Phoenix, react-force-graph
all changed recently). Record the version/API you targeted in a top-of-file
docstring or a short `NOTES.md`.

## File ownership (STRICT — prevents parallel conflicts)

- Create/edit files **only** under your assigned directory(ies).
- Do NOT edit `pyproject.toml`, `.env.example`, `config.py`, `api/schemas.py`,
  `core/models.py`, `main.py`, `api/routes.py`, or any other module's directory. If
  you need a new dependency, env var, or a change outside your dir, **list it in your
  final report** — the orchestrator merges it.
- Do NOT create a git worktree, do NOT `git commit`. Work in place.

## Final report (your output)

Files created; the exact public interface you exposed; library versions/APIs you
targeted; deps/env to merge; any contract mismatch; what remains for live
integration; how to run your tests.
