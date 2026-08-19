# adapter/ — the ten swappable domain pieces

Everything else in `app/` is the **stable core**, built ahead and untouched on
the day. Only the pieces below are domain-specific. On the day, once the problem
is revealed, *only this directory changes*. **Domain logic must never leak into
the core** — the core imports from `adapter/` through clean interfaces, never the
reverse.

Ten pieces: **eight Python modules** plus the two content directories `corpus/`
and `skills/`. `__init__.py` is not one of them — it is the registry that
re-exports the pieces to the core, and its `__all__` is the contract to keep
stable while the pieces underneath are rewritten.

The numbering is the recommended **edit order**; it is the same numbering used in
each module's own docstring and in the authoritative retargeting procedure,
[`SKILL.md`](../../../../SKILL.md) at the repository root (the `retarget-aegis`
skill). This table is the local *map* of the ten pieces, not a second procedure.

| # | Piece | What it defines | Lands in |
|---|-------|-----------------|----------|
| 1 | **Data schema** | The entities and enums of the new world — the vocabulary everything else shares | `schema.py` |
| 2 | **ML features + target** | What the ML spine predicts, on which features, and the latent ground-truth signal | `ml_spec.py` |
| 3 | **Synthetic generator** | The LLM-driven generator that seeds realistic data (with a templated offline fallback) | `generator.py` |
| 4 | **Tool definitions** | The real actions the agent can take (typed, MCP-shaped, allowlisted per persona) | `tools.py` |
| 5 | **Personas** | Who the agent serves; persona → data scope + tool allowlist | `personas.py` |
| 6 | **Prompts** | Who the agent is — the system prompt per persona (paired with piece 5) | `prompts.py` |
| 7 | **Memory contract** | What counts as a durable fact, how it is extracted, who it is scoped to, how the profile reads | `memory_spec.py` |
| 8 | **Agent roster** | Which specialists the multi-agent supervisor may route to, and how each is recognised | `roster.py` |
| 9 | **Domain corpus** | The seed Markdown documents ingested into the graph/vector store | `corpus/` |
| 10 | **Procedural skills** | The how-to-act playbooks selected per query by `memory_spec.select_skills` | `skills/` |

> Keep each piece behind the interface the core expects. Swapping a domain =
> editing these files only, with zero changes to `agent/`, `retrieval/`, `ml/`,
> `memory/`, `guardrails/`, `api/`, or `observability/`.
