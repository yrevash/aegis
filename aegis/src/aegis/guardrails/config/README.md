# Guardrails — NeMo Guardrails / Colang policy

This directory is a standard **NeMo Guardrails** config (verified against
**0.23**, **Colang 1.0**). It is the *declarative twin* of the fast programmatic
engine in `aegis/guardrails/pipeline.py` — **one policy, two front doors**:

| Front door | Used by | How |
|---|---|---|
| `check_input` / `check_output` | the agent graph, unit tests | direct Python calls (offline-testable, no gateway) |
| Colang flows here | live NeMo integration | `nemo.build_rails()` → `LLMRails` runs `rails/*.co` |

Both call the **same** actions (PII redactor, API injection classifier, schema
validator), so they cannot drift apart.

## Files

- `config.yml` — models + the input/output rail flow lists (defense-in-depth order).
- `rails/input.co` — schema → PII redaction → injection (Colang 1.0).
- `rails/output.co` — schema + content-filter → PII redaction (Colang 1.0).
- `actions.py` — custom `@action`s bridging Colang `execute` calls to our Python.
- `prompts.yml` — optional NeMo-native `self check input/output` prompts (off by default).

## No local guardrail model

Per `docs/security/overview.md`, all detection is **pure code or a cheap API call**. There
is no Llama Guard / Presidio-with-spaCy / local classifier — the 16 GB, no-GPU
constraint rules them out. The injection classifier is a `ModelRole.CHEAP`
(`gpt-4o-mini`) call through the LiteLLM gateway; PII detection is stdlib regex.

## Streaming caveat (important)

The **output rail assumes the complete answer**. When the final answer is
streamed to the frontend token-by-token, the output guard cannot scan-then-emit,
because the offending token may already be on the wire. Two supported strategies
(coordinate with `docs/learn/30-frontend.md`):

1. **Buffer briefly** — accumulate the answer (or fixed-size chunks) and run
   `check_output` on the buffered text *before* releasing it. Adds a little
   latency; guarantees nothing unredacted is ever emitted. **Preferred** for the
   final answer.
2. **Scan post-hoc and redact** — stream optimistically, run `check_output` on a
   trailing window, and retract/patch on a hit (the SSE `guardrail` event carries
   a `redact` verdict; the client masks the affected span). Lower latency; a
   redacted token may flash briefly.

NeMo Guardrails 0.23 also supports output-rail streaming via a
`rails.output.streaming` block (chunked checking) — the same buffer-vs-post-hoc
trade-off, expressed in config. Never stream raw tokens straight past the output
rail.
