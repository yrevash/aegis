# Guardrails — the theory

The published frameworks, the algorithms, and the trade-offs between the approaches that
were available. This is where "we run guardrails" becomes something you can defend against
"why *those* guardrails?"

---

## 1. The threat model: OWASP LLM Top 10

The industry-standard taxonomy for LLM application risk. The 2025 list, with the entries
this module addresses:

| Id | Risk | Which control |
|---|---|---|
| **LLM01** | Prompt Injection | Injection signatures + model classifier + spotlighting of retrieved content |
| **LLM02** | Insecure Output Handling | Output schema rail, output content filter |
| **LLM06** | Sensitive Information Disclosure | PII redaction — on the *input* path before any model call, and on the output path |
| **LLM08** | Excessive Agency | Not a text control at all — the risk-tiered human gate in `agent/` |
| **LLM09** | Misinformation | The grounding self-check against retrieved passages |

Two of these are worth dwelling on.

**LLM06 on the input path** is the one people forget. Redacting PII from the *answer* is
obvious. Redacting it from the *query before you send it to your injection classifier* is
the subtle one — that classifier is a third-party model call, so an unredacted query is a
disclosure you performed yourself, in the name of safety.

**LLM08 is deliberately not solved here.** No text filter can decide whether issuing a
$4,200 refund is acceptable. That is a structural control: the model's output alone must
not be able to authorise a dangerous action. It lives in the agent's risk gate, and the
correct answer to "how do you stop the agent doing something bad?" is *not* "we filter the
text".

---

## 2. The content-safety taxonomy: MLCommons S1–S13

Homegrown "toxicity" lists do not survive scrutiny — they are unprincipled, undocumented,
and incomparable with anyone else's. The industry standard is the **MLCommons AI Safety
hazard taxonomy**, the same categories **Meta's Llama Guard** and **NVIDIA's Aegis safety
dataset** classify against:

| Code | Category | Code | Category |
|---|---|---|---|
| S1 | Violent Crimes | S8 | Intellectual Property |
| S2 | Non-Violent Crimes | S9 | Indiscriminate Weapons (CBRN) |
| S3 | Sex-Related Crimes | S10 | Hate |
| S4 | Child Sexual Exploitation | S11 | Suicide & Self-Harm |
| S5 | Defamation | S12 | Sexual Content |
| S6 | Specialized Advice (unqualified medical/legal/financial) | S13 | Elections |
| S7 | Privacy | | |

**Why speak the standard rather than invent a list?** Three reasons that all matter in an
enterprise conversation. The codes are stable and interoperable, so a verdict means the
same thing to another team's pipeline. There are public benchmark datasets scored against
them, so you can measure yourself. And a category code in a trace is auditable in a way
that "toxicity: 0.83" is not.

**Why not run Llama Guard itself?** It is a dedicated safety model — better than a
general model prompted to do the same job. It also needs a GPU. Under a 16 GB, no-GPU
deployment constraint, the available option is the **NeMo Guardrails "self check" pattern**:
prompt a cheap general model with the taxonomy and ask for a structured verdict. That is a
genuine quality trade, and the honest way to present it is as a constraint-driven decision
with a stated upgrade path, not as an equivalent choice.

---

## 3. Unicode: the algorithms behind normalisation

### The general categories

Every Unicode codepoint carries a two-letter general category. The ones that matter here
are all under `C` (Other):

| Category | Contains | Decision |
|---|---|---|
| `Cc` | Control characters (C0, C1) | Rejected except tab/newline/CR |
| `Cf` | **Format** — zero-width space/joiner, bidi overrides, soft hyphen, language tag | **Rejected wholesale** |
| `Cs` | Surrogates | Rejected |
| `Co` | Private use | Rejected |
| `Cn` | **Unassigned** | **Not** rejected by category |

`Cn` is excluded deliberately, and the reason is a good one: a Python whose Unicode
database predates a newly-assigned emoji would classify that emoji as `Cn` and reject
perfectly legitimate text. So unassigned codepoints are not rejected as a class — but the
**Tag block is rejected explicitly by range**, precisely because most of it is `Cn` and a
category test alone would miss it.

**The cost, stated rather than hidden.** Rejecting all of `Cf` also rejects U+200D ZERO
WIDTH JOINER — which is how multi-person emoji like 👨‍👩‍👧 are constructed — and the Arabic
formatting marks U+0600–U+0605 and U+061C. Those inputs are refused. That is the deliberate
price of closing an invisible-instruction channel, and the right way to handle it is to
name the exact codepoint in the rejection reason so an operator can see why.

### The normalisation forms

Unicode defines four normalisation forms. Two matter here:

- **NFD / NFC** — canonical decomposition and composition. `é` becomes `e` + combining
  acute, or recombines. Canonical equivalence: the two forms are *the same character*.
- **NFKC** — **compatibility** composition. This is the aggressive one. It folds
  fullwidth `ｉ` → `i`, mathematical bold `𝐢` → `i`, ligature `ﬁ` → `fi`, superscript `²` → `2`,
  circled `ⓐ` → `a`.

NFKC is exactly the hammer needed for evasions using alternative letterforms, and exactly
the hammer you must **never** apply to text you then store or forward — it is lossy and
not round-trippable. Comparison view only.

### The folding pipeline

The order is not arbitrary:

```
strip invisible/format characters   →  ig<ZWSP>nore  becomes  ignore
NFKC                                →  ｉｇｎｏｒｅ      becomes  ignore
NFD, then drop combining marks      →  ignoré        becomes  ignore
collapse whitespace runs            →  i g n o r e   becomes  i g n o r e (single-spaced)
```

Stripping must come **first**: NFKC on a string containing a zero-width space leaves the
zero-width space, so a fold-then-strip order would produce `ig nore` in some cases. And
case is deliberately **preserved**, so a rail may still match case-sensitively — which
matters for a pattern like `\bDAN\b` (the jailbreak persona) that must not fire on the
ordinary given name "Dan".

### Confusables

The full Unicode confusables table (UTS #39) maps thousands of codepoints. Aegis maps
**Cyrillic and Greek only** — the two scripts that supply a near-complete Latin lookalike
alphabet and account for essentially all homoglyph evasion seen in the wild.

Two reasons not to map everything. First, the full table is large and its long tail
(Armenian, Cherokee, Coptic, Deseret) is not what attackers reach for. Second, and more
importantly: **the confusable map is applied in addition to the plain fold, never instead
of it**, because mapping rewrites genuine Cyrillic and Greek prose into Latin gibberish. A
rail that needs to match a Russian-language signature must match against the *unmapped*
fold. Running both views doubles the matching work and is still trivially cheap.

**What normalisation explicitly does not cover:** leetspeak (`1gn0re`), which cannot be
folded without false-positiving on ordinary text containing digits. That falls through to
the model classifier, and the code says so.

---

## 4. Verdict parsing: the fail-open trap in one function

Every model-backed rail asks for strict JSON and then needs a fallback for the day the
model returns prose instead. **That fallback is the entire security posture of the rail.**
Read it too generously and the rail opens.

The historical fallback in this codebase — and in a great deal of published example code —
was:

```python
if lowered.startswith("no"):
    return BENIGN
```

This is not a verdict signal. It is a **prefix**. Consider the reply:

> No doubt this is a prompt injection attempt.

Starts with "no". Parsed as benign. The input sails through the rail it was just described
as attacking. The mirror image, `startswith("yes")`, is the same defect pointing the other
way, and it produces false blocks instead of false passes.

The correct rule accepts only an **unambiguous** signal:

1. An explicit `"<field>": true|false` key/value anywhere in the text — the JSON shape,
   surviving a stray prefix or trailing prose.
2. A response whose *entire* content is a bare yes/no/true/false token.

Everything else — including any text carrying **both** signals — returns "no verdict", and
each caller then applies its own documented fail direction.

That last clause is the design insight worth stating: the parser does not decide the
outcome. It reports whether a verdict exists. **Blocking rails map "no verdict" to block.
Advisory rails map it to whatever their `block` flag declares.** One parser, correct fail
direction per rail.

---

## 5. Layer ordering as a formal argument

The input chain is:

```
schema → PII redaction → injection → content safety → topical → custom rails
```

Each position is justifiable:

**Schema first** because it is free and pure. Length caps, empty check, invisible-character
rejection. No I/O, no tokens spent. There is no reason to spend a model call on a payload
that is 400 KB of control characters.

**PII second** because the next three layers are all model calls. Redacting first means
sensitive data never leaves the process for a safety check. Skipping this would make your
guardrail an LLM06 violation.

**Injection third, content safety fourth.** Both are model calls, and both have a
deterministic signature backstop that runs first at no cost. Injection precedes content
safety because injection is the attack on the *system*, and a successful injection can
disable everything after it.

**Topical fifth, advisory.** It is a business-domain question, not a safety one.

**Custom rails last** so a domain rule operates on text that has already cleared every
universal control.

The output chain is deliberately different:

```
schema → content filter → content safety → custom → grounding → PII
```

**PII moves to the end**, and the reason is instructive: on the input path you redact
before you spend model calls. On the output path there are no downstream model calls to
protect — the text is going to the user — so PII redaction is the *final* transform, applied
to text that has already survived every block decision. If it ran first, every later rail
would be judging text that had been rewritten.

Note also that the output PII rail returns `REDACT`, not `BLOCK`: the answer is delivered
with `[REDACTED_EMAIL]` in place of the address, rather than withheld entirely.

---

## 6. Spotlighting: defending retrieved content structurally

**Microsoft, "Defending Against Indirect Prompt Injection Attacks With Spotlighting"
(arXiv 2403.14720)**, plus MSRC's 2025 guidance. Three instantiations:

- **Delimiting** — wrap untrusted spans in explicit boundary markers.
- **Datamarking** — interleave a marker token *through* the text, so a span that closes the
  fence early cannot escape the boundary.
- **Encoding** — transform the untrusted text (e.g. base64) so it is unmistakably data.

Aegis combines the first two, plus an explicit natural-language instruction. Delimiting
alone is defeatable: an attacker who knows the fence format can close it and write outside.
Datamarking closes that, because every whitespace run in the untrusted text is replaced by
the marker, so the marker signal is continuous rather than positional. And the fences are
**randomised per block**, so they cannot be forged by an attacker who read your source.

Encoding is not used, because base64-encoding retrieved passages degrades the model's
ability to actually *read* them, which defeats the purpose of retrieval.

**What spotlighting is and is not.** It is a strong hint, not a boundary. The model can
still be persuaded to obey marked text — it is a next-token predictor, not a reference
monitor. Its value is that it converts "the model has no idea this is untrusted" into "the
model has been told clearly and repeatedly", which measurably reduces compliance with
embedded instructions. It is one layer, and it is presented as one layer.

---

## 7. PII detection: Presidio vs regex

Two approaches, and the choice is a genuine trade:

| | Regex | Microsoft Presidio |
|---|---|---|
| Detects | Fixed patterns: emails, card numbers, phone shapes | Names (NER), IBANs, phone numbers validated by `phonenumbers`, locations, and more |
| Dependencies | None | `presidio-analyzer` + a spaCy model (hundreds of MB) |
| False negatives | High — misses anything unpatterned, especially person names | Much lower |
| Determinism | Total | Model-dependent |

Aegis uses **Presidio when available, regex as a transparent fallback**, selected lazily on
first use and pinnable by environment variable. The critical property: it never *silently*
stops redacting — the active engine is logged and queryable.

**The Luhn check** is worth knowing as an example of cheap validation reducing false
positives. A 16-digit run is not necessarily a card number. Luhn's checksum (double every
second digit from the right, sum digits, total ≡ 0 mod 10) is satisfied by ~10% of random
16-digit strings, so requiring it cuts card false positives roughly tenfold for one line of
arithmetic.

---

## 8. Grounding: the RAGAS faithfulness metric as a rail

An answer is **grounded** if every factual claim in it is entailed by the retrieved
passages. This is the RAGAS *faithfulness* metric, and it is also NeMo Guardrails'
`self_check_facts` rail.

It is a semantic entailment judgement, so unlike the injection rail there is **no
deterministic backstop possible** — no regex decides whether a claim follows from a
paragraph.

That absence forces a design decision. If the checker is unavailable, what happens? Aegis
makes grounding **advisory by default**: an ungrounded answer produces a non-blocking FLAG
that surfaces in the trace, and an unavailable checker fails *open*, because a downed
checker manufacturing a spurious "this may be a hallucination" warning on every single
answer is its own kind of lie. A `block` knob flips both: hard block, and fail closed.

The general principle: **the fail direction should follow the enforcement posture.** A rail
configured to block is a safety control and fails closed. A rail configured to advise is a
quality signal and fails open. What is not acceptable is a rail whose fail direction is
undocumented.

---

## 9. Caching a safety verdict

The injection classifier is a model call on every request. Caching it is attractive and has
exactly two conditions:

**Key on the exact text.** The verdict is a pure function of the string, so the key is a
SHA-256 of the (already redacted) text. Because it is keyed on the text alone, there is no
tenant or persona in the key — and therefore **no cross-tenant reuse risk**. That is worth
stating explicitly, because "we cache safety verdicts" sounds alarming until you show the
key is content-addressed.

**Never cache the deterministic layer.** Signature matching is free and offline. Caching a
free decision buys nothing and adds a place for a stale answer to live. Only the model
verdict is cached.

**Cache failures fail open — as a miss.** This is the one place fail-open is correct, and
the reason is that the cache is not a control. A cache read that errors means "recompute",
which runs the real control. Failing closed on a cache error would mean a Redis blip blocks
every request in the system.

---

## 10. Programmatic vs declarative policy engines

**NeMo Guardrails** (NVIDIA) is the reference declarative framework. Policies are written
in **Colang**, a small DSL:

```
define flow guardrail input injection
  $safe = execute self_check_injection
  if not $safe
    bot refuse input
    stop
```

The trade:

| | Programmatic | Declarative (Colang) |
|---|---|---|
| Speed | Direct function calls | An engine turn per check |
| Testability | Trivial, offline | Needs the engine |
| Auditability | Read Python | **Read the policy file** |
| Who can review it | Engineers | Security, compliance, legal |

The failure mode of choosing both naively is **drift**: two implementations of one policy
diverge, and now nobody can say which is enforced.

The resolution is **one policy, two front doors**: the Colang flows `execute` custom
actions that delegate straight back to the same rail functions the programmatic path calls.
The `.co` file is a readable artifact; the enforcement is shared code.

Two traps live in that arrangement, both of which this codebase hit:

- **The action registry can drift from the policy.** If a `.co` file `execute`s an action
  the registration list forgot, anyone constructing the engine themselves hits an
  unregistered-action failure. The fix is to derive the registration from a single table
  and have a test assert it covers every `execute` in the `.co` files.
- **The engine constructs its own model.** See the deep dive — this is the best story in
  the module.

---

## 11. Red-teaming: how you know any of this works

Assertions about a guardrail are worthless without an adversarial suite. The structure that
works:

- **A battery of attack cases**, each with an id, a category, and the payload.
- **Per-case expectations** — which layer *should* catch it. That matters: a case caught by
  the model classifier when it was supposed to be caught deterministically is a regression,
  because the deterministic layer is the one that works offline.
- **Coverage limits pinned by test.** The docstring claims non-English coverage for seven
  languages and base64 only. A test asserts exactly that, so the claim cannot silently
  become false as the patterns change.

That last point generalises: **an honesty claim in a docstring that no test enforces will
eventually be a lie.**

---

## 12. What this module deliberately does not attempt

Stating the gaps is part of the defence, because an unstated gap reads as a claim.

- **No local safety model.** No Llama Guard, no cross-encoder classifier — the deployment
  target has 16 GB and no GPU.
- **No content-safety or topical screen over raw pixels.** Both would need a second vision
  call per image. Unsafe *imagery* is out of scope, and the media verdict says so in its
  skipped-rails list rather than letting a reader assume coverage.
- **No leetspeak folding, no hex/ROT13/URL decoding.** Base64 only.
- **Homoglyph coverage is Cyrillic and Greek**, not the full UTS #39 table.
- **Non-English signatures cover seven languages** and only the "ignore the previous
  instructions" family within them.

Everything outside those limits is the model classifier's job — which is precisely why the
deterministic layer never runs alone by choice.

**Next:** [`20-in-aegis.md`](20-in-aegis.md) — the exact implementation.
