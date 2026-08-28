# The Aegis master guide

One book, read front to back. After it you should be able to explain every part of
Aegis out loud and answer the follow-up questions.

**`Aegis-master-guide.pdf`** — 80 pages, 12 diagrams, 40 cross-question blocks.

## The parts

| | Part | What it covers |
|---|---|---|
| 1 | Foundations | What Aegis is, the three fears it answers, the glass box, the big picture, the technology choices |
| 2 | The Agent | What an agent is, why a graph, the 18 nodes, act → verify → reflect, termination, tools and risk tiers, the human gate, fan-out |
| 3 | Knowledge | Ingestion, the three retrieval arms, RRF, cross-encoder rerank, the knowledge graph, caching, memory |
| 4 | Trust | The four guardrail stages, multi-tenancy and RLS, the human gate, the audit chain, budgets, compliance |
| 5 | Measurement | The two eval layers, LLM-as-judge and its biases, red-teaming, observability, XGBoost / SHAP / MAPIE |
| 6 | Interop | A2A, MCP, CycloneDX SBOM and AgBOM, OpenTelemetry |
| 7 | Surface | The five portals, SSE streaming, the design language |

## How it is written

Each part explains the **concept** before the code, names **what the alternatives were
and why this was chosen**, and ends every section with a `### Cross-questions` block —
the questions an examiner asks, with the answer to give.

Every number in the book was read from the source, not estimated.

## Rebuilding

```bash
node scripts/build-master-guide.mjs
```

Needs `marked`, `playwright` and `mermaid` from `web/node_modules` (`npm install` in
`web/` first). Mermaid is inlined into the print document, so the build works offline —
without it every diagram would print as a code listing instead.
