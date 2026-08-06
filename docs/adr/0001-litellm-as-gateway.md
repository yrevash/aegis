# ADR 0001 — LiteLLM as the single model gateway

- **Status:** Accepted
- **Date:** 2026-08-03
- **Deciders:** Team

## Context

Every model call in the system (generation, cheap extraction, reasoning,
embeddings, the LLM-as-judge eval, the guardrail classifier) targets the TCS
GenAI Lab fleet, exposed as an **OpenAI-compatible** endpoint at
`https://genailab.tcs.in` (self-signed cert). We need cost tracking, model
routing, and fallback — and the rubric explicitly scores token/cost efficiency
("tokens are visible to the jury").

The spike confirmed the gateway works via a plain OpenAI-compatible client
(`langchain_openai.ChatOpenAI` with a custom `base_url`) and that
**tool/function-calling passes cleanly** — so the LangGraph agent design holds.

## Decision

Route **all** model calls through **LiteLLM**, configured as a *custom
OpenAI-compatible provider* pointed at `genailab.tcs.in` (not the `azure/`
provider strings originally assumed). We do **not** call the endpoint directly
from feature code.

## Consequences

- **+** One choke point for cost/token accounting → feeds the live dashboard.
- **+** Built-in retry/fallback and a uniform interface across the fleet.
- **+** Heterogeneous routing (cheap vs reasoning vs generation) lives in one
  config-driven registry (`core/models.py`), swappable per role via env.
- **−** One extra dependency in the call path; mitigated by it being the *only*
  model dependency (no per-provider SDKs).
- **Note:** the gateway's self-signed cert requires scoped TLS-verification
  disabling (`GENAILAB_SSL_VERIFY=false`) — documented as a known exception, not
  a blanket setting.

## Alternatives considered

- **Direct OpenAI-compatible client per call** — simpler, but loses centralised
  cost tracking, routing, and fallback; scatters the model dependency.
- **`azure/` LiteLLM provider strings** — the docs' original assumption, but the
  real endpoint is a generic OpenAI-compatible gateway, not Azure OpenAI.
