# Backend — agentic core

FastAPI + LiteLLM + LangGraph + LightRAG + ML spine + guardrails + OTel. Clean,
typed, modular, fully local-or-API (no Docker, no GPU, 16 GB laptop).

## Layout

```
src/app/
  config.py         # typed settings (pydantic-settings), single source of env
  main.py           # FastAPI app + router wiring
  api/              # routes + Pydantic contracts + SSE event schema
  core/             # config-driven model registry + LiteLLM gateway
  agent/            # LangGraph orchestration (plan-and-execute + tool loop)
  retrieval/        # LightRAG + Neo4j + embedded vectors + Redis semantic cache
  ml/               # XGBoost + MAPIE (conformal) + SHAP
  guardrails/       # input/output rails
  data/             # DB models, audit log
  observability/    # OTel gen_ai.* spans → Phoenix
  adapter/          # the five domain-specific pieces (see adapter/README.md)
```

## Setup

```bash
# from backend/
uv venv && source .venv/bin/activate      # (Windows: .venv\Scripts\activate)
uv pip install -e ".[dev]"
cp .env.example .env                       # fill in GENAILAB_API_KEY etc.
uvicorn app.main:app --reload --app-dir src
```

OpenAPI docs at `/docs` once running. Lint/format: `ruff check src` /
`ruff format src`. Tests: `pytest`.

## Model routing

Code requests a model by **role** (`core/models.py`), never by hard-coded id.
Override any role via `MODEL_<ROLE>` env vars. See `docs/architecture/backend.md` §2.

## De-risk spikes

`spikes/tool_calling_spike.py` (agent design depends on this passing — ✅ confirmed)
and `spikes/list_models.py` (ground-truth model fleet).
