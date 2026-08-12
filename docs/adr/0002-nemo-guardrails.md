# ADR 0002 — NeMo Guardrails (Colang policy) over Guardrails AI

- **Status:** Accepted
- **Date:** 2026-08-03
- **Deciders:** Team
- **Related:** `docs/security/overview.md` §3 (layered guardrail architecture),
  `docs/security/threat-model.md`, ADR 0001 (LiteLLM gateway).

## Context

`docs/security/overview.md` mandates a **layered, RAM-friendly, defense-in-depth** rail
system wrapping *every* model interaction: an **input rail** (PII redaction +
API injection classifier + schema validation) and an **output rail** (schema
validation + PII redaction/content filter). Two constraints shape the choice:

1. **No local guardrail model.** The day-one machine is a 16 GB Windows laptop,
   no Docker, no GPU. Llama Guard, Presidio-with-spaCy, and any locally-served
   classifier are out. Detection must be **pure code or a cheap API call**.
2. **Security must be *legible*.** The work is graded by a human jury *and* an AI
   reader. `docs/security/overview.md` §6 calls out "the Colang / guardrail policy
   artifact — a human-readable policy file = *our security is codified, not
   vibes*" as a place points are won.

The two orchestration options named in `docs/security/overview.md` §3 are **Guardrails AI**
(composable Python validators) and **NeMo Guardrails** (a **Colang** policy DSL).
Both can wrap input/output rails; both can call custom Python for the actual
checks; neither *requires* a local model for our chosen rails.

## Decision

Use **NeMo Guardrails** as the orchestration layer, with the **Colang 1.0** policy
under `app/guardrails/config/` as a first-class, human-readable security artifact.

The policy is the *declarative twin* of a fast programmatic engine
(`app/guardrails/rails.py`, exposing `check_input` / `check_output`). Both front
doors call the **same** custom actions — the self-built PII redactor
(`pii.py`), the cheap API injection classifier (`classifier.py`), and the schema
validators (`schema.py`) — so the readable policy and the enforced behaviour
cannot drift apart. The agent graph calls the programmatic API for speed and
offline testability; live integration loads the Colang policy via
`app/guardrails/nemo.py`.

We select **Colang 1.0** (not 2.x): it is the current default in NeMo Guardrails
0.23, the entire built-in rail catalogue is authored in it, and its
`define flow … / execute … / bot refuse` grammar reads like a security policy —
exactly the "codified, not vibes" artifact the jury and AI reader reward. Colang
2.x is still beta and materially different (see Alternatives).

We take **no local model** from NeMo: content-safety / Llama-Guard / Presidio
rails are deliberately unused. Injection detection is the `ModelRole.CHEAP` API
call; PII detection is stdlib regex.

## Consequences

- **+** The `.co` policy files are self-documenting security evidence — they map
  1:1 onto the threat model and demo directly to the jury.
- **+** One policy, two front doors: the Colang artifact stays honest because its
  actions delegate to the same functions the app enforces.
- **+** Config-driven rails: adding/re-ordering a rail is a `config.yml` + `.co`
  edit, not a code change; NeMo's `rails.output.streaming` gives a ready path for
  the streaming caveat.
- **+** `nemoguardrails` is imported **lazily**, so the module and its unit tests
  run with the package absent (it is an optional dependency).
- **−** NeMo carries more machinery than we strictly use (dialog management,
  Colang runtime). Mitigated by treating it as *policy + action registry* and
  keeping enforcement in typed Python that is unit-tested offline.
- **−** Colang is a bespoke DSL with a learning curve vs plain Python validators.
  Accepted: the readability payoff is the whole point.
- **Version note:** targeted **NeMo Guardrails 0.23, Colang 1.0** (verified
  against current docs, Aug 2026; `pyproject.toml` pins `>=0.11`, which the
  orchestrator should bump to `>=0.23`).

## Alternatives considered

- **Guardrails AI (composable validators).** Excellent, Pythonic, strong
  validator hub. But its artifact is Python/RAIL-XML config, which reads as code,
  not as a *policy* — it loses the "human-readable security artifact" scoring
  angle that `docs/security/overview.md` §6 explicitly calls out. Our checks are custom
  and self-built anyway, so its validator hub is not decisive.
- **Colang 2.x.** More powerful (imports, standard library, explicit `flow`
  activation), but still beta in 0.23, and the built-in guardrail catalogue "is
  not yet fully usable" from it. Materially different syntax with less
  documentation — wrong bet for a policy that must be legible and stable on demo
  day. Revisit when 2.x exits beta.
- **Hand-rolled rails, no orchestrator.** Simplest to run, but forfeits the
  policy artifact and the config-driven rail composition — the exact things that
  score. We keep a hand-rolled *engine* for speed, but wrap it in NeMo for the
  artifact.
- **A local guard model (Llama Guard / Presidio+spaCy).** Rejected outright by
  the 16 GB / no-GPU constraint (see ADR 0001's fleet-is-remote stance).
