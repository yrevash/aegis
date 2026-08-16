# Foundations — interview questions and answers

The general questions — the ones that come before anyone asks about a specific module.
Module-specific questions live in each module's own `50-interview.md`.

Answers are written the way you should *say* them: claim first, then the reason, then a concrete
detail from this system. The concrete detail is what makes it convincing — anyone can recite a
definition, few can say "and here's what broke when we got it wrong."

If a term in an answer is unfamiliar, it is defined in [`10-guide.md`](10-guide.md).

---

### "Walk me through what happens when a user asks your system a question."

Input rails first — injection, PII, schema, content. A block ends the run right there; nothing
downstream runs at all.

Then a supervisor routes the turn to a specialist. The default path recalls long-term memory,
retrieves evidence with three retrievers fused by reciprocal rank fusion and then reranked, runs
an ML prediction as *evidence only*, and plans.

If the plan proposes tools, a gate checks the **risk tier of each tool**. Anything at or above
the threshold interrupts the graph, checkpoints the whole state to Postgres, and waits for a
human — which means the process can die there and the run still finishes.

After acting, a reflect step decides whether to re-plan. It is bounded, so it terminates. Then
generation, the output rail, and only then does the answer reach the user. The turn is written
back to memory.

*Have diagram 2 from [`40-diagrams.md`](40-diagrams.md) in your head while you say this.*

---

### "Why RAG instead of fine-tuning?"

Different tools. Fine-tuning teaches *style and format*; it teaches *facts* poorly and
expensively, cannot be updated without retraining, and cannot cite a source. RAG
updates the moment a document changes and can point at the exact passage used — which
is what makes an answer auditable. For a compliance-adjacent product, "here is the
paragraph I used" matters more than a slightly smoother tone.

---

### "What's the hardest problem in RAG?"

Retrieval, not generation. If the right passage never reaches the prompt, no model
saves you. Concretely: semantic search is excellent at paraphrase and **bad at exact
identifiers** — search for invoice `INV-2291` and every invoice number looks alike in
embedding space. That's why we run keyword search alongside vector and graph.

And fusing them is its own problem, because their scores aren't comparable — a cosine
of 0.82 and a BM25 of 14.3 mean nothing to each other. RRF fixes that by discarding
scores and using only ranks.

*If you want to go further:* we found our keyword arm was only re-scoring the pool the
other retrievers already returned, so it could never add recall while still reporting
itself as a firing retrieval arm. That's an honest-provenance bug, not just a quality
one.

---

### "How do you stop an agent doing something dangerous?"

Structurally, not by asking it nicely. The model never executes anything — it emits a
*request* to call a tool, and our code decides. Every tool carries a **risk tier**, and
anything at or above the threshold stops for a human.

Crucially, the gate fires on the **tool's risk**, not the model's confidence. A
confidence gate fails exactly when the model is confidently wrong, which is its most
dangerous state. A 99%-confident $4,200 refund still stops, because refunds are
high-risk.

---

### "What is prompt injection, and how do you defend against it?"

The model can't distinguish your instructions from text it merely read — it's all
tokens, with no privilege boundary. Indirect injection is the dangerous form: the
payload arrives inside a retrieved document or **rendered into an image**.

Layers, because none is sufficient: signature matching (fast, evadable), a classifier
model (better, costs a call), and structural limits so model output alone never
authorises a dangerous action.

*The detail worth telling:* our signature matcher caught only exact ASCII. A filler
word, a zero-width space, a Cyrillic homoglyph, base64, or German all walked through —
I tested all six. The fix normalises a *comparison-only* view (NFKC, strip format
characters, fold confusables) while never mutating the text passed downstream. And the
invisible-character rail missed the **Unicode Tag block**, which models decode as ASCII
but renders as nothing at all — a genuinely invisible instruction channel.

---

### "Your model says it's 90% confident. What does that actually mean?"

By default, nothing. Raw model probabilities aren't calibrated.

To make it mean something you need **conformal prediction**: hold out a calibration
set, measure how wrong the model actually was, and build intervals from that measured
error. Then 90% coverage is a property demonstrated from data.

The guarantee depends on the calibration data being exchangeable with future data —
which is why splitting a **time series** randomly voids it, since you've leaked the
future into calibration. Our forecasting module uses chronological splits and reports
**measured** coverage: it will tell you a requested 90% achieved 76%. Reporting the
requested number as if it were achieved is the overclaim to avoid.

---

### "How do you know your changes made it better?"

Three layers. An offline deterministic gate over a fixed corpus — no model calls, runs
in CI. An LLM-as-judge for what heuristics can't see. And trace-level evaluation
scoring the *steps*, not just the final answer.

*The failure worth mentioning:* our judge returned a score of 0.0 whenever its output
failed to parse, which is indistinguishable from a genuinely bad answer. Since the gate
compared draft against baseline and both scored 0.0, `0.0 < 0.0` was false — the gate
**passed** and auto-promoted the prompt. A judge outage would have promoted every
candidate. An unparseable judge must fail closed, not score zero.

---

### "How do you keep one customer's data away from another's?"

Defence in depth. Application-level tenant filters, plus Postgres **row-level
security** so the database enforces it even if a query forgets its `WHERE`.

Two subtleties that are easy to get wrong, and we got both wrong first:

- Postgres **exempts the table owner** from RLS policies unless you also issue
  `FORCE ROW LEVEL SECURITY`. Apps usually connect as the owner — so RLS was enabled
  and enforced against nobody.
- The tenant GUC must be **transaction-scoped**. Set at session scope, a pooled
  connection carries one tenant's scope into the next request.

Also: `SET app.tenant_id = :tid` isn't executable — `SET` takes a literal, so a bind
parameter raises a syntax error. Our tests never caught it because they run SQLite,
where the code returns early. `set_config(name, value, true)` is the correct form and
gets transaction-locality in the same move.

---

### "What would you do differently?"

Pick something real. Good candidates from this system:

- **Test against the real database.** The RLS bug survived because the suite ran SQLite
  and the failing path was Postgres-only. A dialect-specific code path needs a
  dialect-specific test.
- **Treat silent exception handling as a design decision.** Several bugs hid inside a
  deliberate "never fail the run" swallow — including one where the entire usage ledger
  stopped writing and budgets stopped binding, with only a debug log to show for it.
- **Schema changes need a migration mechanism, not a docstring.** Adding columns to an
  ORM model does nothing to an existing table.

---

### "How do you decide which model to use?"

Route by job. Cheap models for classification, routing and extraction; reasoning models
for hard steps; a strong model for final generation. Every call goes through one
gateway, so routing, budgets, cost accounting and tracing happen in one place rather
than at seven call sites.

The measurable claim is **small-model share** — what fraction of calls went to a cheap
model without losing answer quality. *We found a bug in ours:* the marker list matched
`llama-3.2`, and our vision deployment is `Llama-3.2-90B-Vision`, so a 90-billion
parameter model was counted as "small" and inflated the savings story in our own
favour. Worth mentioning — it shows you check your own metrics.

---

### "How would you scale this?"

The bottleneck is API latency, not local compute. So: horizontal workers (already
possible — runs resume from a persisted row on any worker), managed store tiers, and
caching at two levels — a near-exact retrieval cache and a semantic answer cache that
short-circuits generation entirely.

The interesting constraint is that **budget enforcement is check-then-act**. Under
concurrency, N requests all read the same pre-spend total and all pass. Making a cap
truly binding needs a reservation or an atomic counter, not a read followed by a write.

---

### "What's the difference between this and just calling an API with LangChain?"

The instrumentation *is* the product. Anyone can chain a retriever to a model. What's
hard is being able to answer, for any individual run: what did it read, why did it act,
who approved it, what did it cost, and what would have stopped it. That's the gateway
chokepoint, the risk-tiered gate, the durable checkpointing, RLS, the trace tree and
the eval gates — and each one is a place where a naive implementation quietly lies.

---

## Two questions to ask *them*

Asking good questions is part of the interview.

- "When your agent takes a wrong action in production, what's the first thing you look
  at?" — tells you whether they actually have tracing or just logs.
- "How do you decide a prompt change is safe to ship?" — tells you whether they have an
  eval gate or a vibe check.

---

**Next:** [`../guardrails/10-guide.md`](../guardrails/10-guide.md) — the first thing a request
meets.
