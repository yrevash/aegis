# adapter/ — the five swappable domain pieces

Everything else in `app/` is the **stable core**, built ahead and untouched on
the day. Only the five pieces below are domain-specific. On the day, once the
problem is revealed, *only this directory changes*. **Domain logic must never
leak into the core** — the core imports from `adapter/` through clean interfaces,
never the reverse.

| # | Piece | What it defines | Lands in |
|---|-------|-----------------|----------|
| 1 | **Data schema + synthetic generator** | The shape of the world + an LLM-driven generator that seeds realistic data | `schema.py`, `generator.py` |
| 2 | **Tool definitions** | The real actions the agent can take (typed, MCP-shaped, allowlisted per persona) | `tools.py` |
| 3 | **Prompts + personas** | Who the agent is, who it serves; persona → data/tool scope | `prompts.py`, `personas.py` |
| 4 | **ML features + target** | What the ML spine predicts and on which features | `ml_spec.py` |
| 5 | **Domain corpus** | What gets ingested into the graph/vector store | `corpus/` |

> Keep each piece behind the interface the core expects. Swapping a domain =
> editing these files only, with zero changes to `agent/`, `retrieval/`, `ml/`,
> `guardrails/`, `api/`, or `observability/`.
