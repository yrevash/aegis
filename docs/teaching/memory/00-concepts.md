# Memory — the concept, from zero

No code in this file. Just the idea, and why it is harder than it sounds.

---

## The problem

A language model has **no memory**. Every call is stateless — it reads the tokens you
send and predicts the next ones. It does not remember your last message, your name, or
anything you told it yesterday.

So when ChatGPT appears to remember what you said three turns ago, nothing was
remembered. The application **re-sent the whole conversation** in the prompt. The
illusion of memory is someone else's storage and someone else's decision about what to
include.

That decision is the entire subject of this module.

---

## Why "just send everything" fails

The naive fix is to keep appending every turn and re-send the lot. It breaks three
ways, and they compound:

**It stops fitting.** A model can only read a fixed number of tokens — its context
window. A long-running support conversation blows past it, and then you must drop
something anyway. The only question is whether you drop it *thoughtfully* or the API
drops it for you.

**It gets expensive fast.** You pay per token on every call. Re-sending a 50-turn
history on turn 51 means paying for those 50 turns again. And again on turn 52. Cost
grows quadratically with conversation length.

**Quality actually gets worse.** This is the unintuitive one. Models reliably lose
information buried in the middle of a long context — the "lost in the middle" effect.
Padding the prompt with everything you have makes the model *less* likely to use the
one line that mattered. More context is not more better.

So memory is not a storage problem. **It is a selection problem under a budget.**

---

## What humans do, and why it is the right model

Human memory is not one thing. Psychologists split long-term memory into kinds, and
that split turns out to be exactly the right engineering decomposition:

**Episodic memory** — specific events you experienced. *"On Tuesday the customer said
their card was declined twice."* Tied to a time and a context.

**Semantic memory** — facts you know, detached from when you learned them. *"This
customer is on the Premium tier."* You do not remember being told; you just know it.

**Procedural memory** — how to do things. *"When a refund exceeds $2,000, escalate."*
Skills and policies rather than facts.

There is also **working memory** — the small, fast scratchpad of what you are thinking
about *right now*. It has a hard capacity limit, which is precisely the constraint a
context window imposes.

An agent needs all four, and they behave so differently that storing them the same way
guarantees you serve the wrong one.

---

## The four kinds, as an engineering problem

| Kind | Holds | Written | Retrieved by | Grows |
|---|---|---|---|---|
| **Working** | What is in this prompt, right now | Assembled per turn | — | No, it is a budget |
| **Episodic** | Actual turns that happened | Every turn | Similarity + recency | Fast |
| **Semantic** | Distilled facts about a subject | By consolidation | Similarity | Slowly |
| **Procedural** | Skills and policies | Authored by humans | Task match | Rarely |

Read that table again with the cost lens: episodic grows fast and is mostly noise;
semantic is small and dense with signal. That asymmetry is why a good memory system
**distils** rather than accumulating.

---

## Consolidation — the idea that makes it work

If you only store raw turns, memory is a transcript. Searching a transcript for "what
tier is this customer" means hoping the words appear near each other somewhere.

**Consolidation** is a background pass that reads recent turns and extracts durable
facts from them. The conversation contains *"...well I'm on Premium so I should get
next-day..."*, and consolidation writes a fact: `tier = premium`. Now the answer is one
small row, not a paragraph the model must infer from.

This is not a database trick. It is deliberately modelled on how human memory
consolidates episodes into semantic knowledge during sleep — the transcript fades, the
fact remains.

Consolidation immediately raises the hard question of this whole module:

---

## The hard part: what happens when facts conflict

The customer said they were on Premium in March. In July they say they downgraded to
Standard.

You now hold two facts that contradict. The naive options are all wrong:

- **Keep both** → the model sees both and picks arbitrarily.
- **Overwrite the old one** → you have destroyed history. "What tier were they on when
  they filed that complaint?" is now unanswerable, and for anything audited that is
  disqualifying.
- **Trust the newest blindly** → a model mis-extraction silently corrupts a real fact.

The correct answer is **bitemporal modelling**, and it is worth understanding properly
because it is a genuinely good interview answer.

### Two different times

Every fact carries **two independent time ranges**:

- **Valid time** — when the fact was true *in the world*. Premium was true from March
  to July.
- **Transaction time** — when the system *knew* it. We learned about the downgrade on
  15 July, even though it took effect on 1 July.

Keeping both lets you answer two genuinely different questions: *"what was true then?"*
and *"what did we believe then?"* The second is the one auditors ask, because it is the
question of whether a decision was reasonable given what was known at the time.

Nothing is ever deleted. A superseded fact is **closed** — stamped with the moment it
stopped being valid — and the new fact is inserted as its successor. History stays
intact and queryable.

---

## Retrieval: what makes a memory worth recalling

You have thousands of stored facts and turns, room for perhaps a dozen, and one
question. Which do you send?

Pure semantic similarity is not enough. Three signals matter, and the classic
formulation (from the *Generative Agents* paper) combines them:

**Relevance** — how semantically close is this memory to the current query? Cosine
similarity between embeddings.

**Recency** — how long ago was it? Usually with exponential decay, so last week fades
relative to yesterday. What someone said this morning generally matters more than the
same statement in March.

**Importance** — how significant is this memory intrinsically? *"Customer threatened to
cancel"* deserves to surface long after *"customer said thanks."*

The score is a weighted sum, and the weights are a real tuning decision.

### A trap worth knowing

If you normalise those three signals across the candidate set — scaling the best to 1.0
and the worst to 0 — you destroy *absolute* relevance. A candidate set whose best match
is a cosine of 0.15 still produces a top-ranked memory scored 1.0. Nothing was relevant,
and the ranking cheerfully says something was.

The fix is an absolute similarity floor **before** normalising: if nothing clears the
bar, recall nothing. Recalling nothing is a valid, honest answer. This is exactly the
class of bug the "no silent fallback" rule exists to catch.

---

## Forgetting is a feature

Memory that only grows gets slower, more expensive, and *worse* — more noise competing
with the signal.

Real systems need eviction. But eviction has a failure mode: if you rank by "how often
was this recalled", early memories entrench. They were recalled once, so they rank
higher, so they are recalled again. Rich-get-richer, and genuinely useful new memories
can never break in.

Decay of *access recency* behaves better than a raw access count for exactly this
reason — and note that a plain counter also means a memory recalled once can become
permanently immune to a forget sweep that only prunes never-accessed rows.

---

## Working memory: the assembly step

At query time all of this has to become **one block of text** inside a token budget.
That means choosing an order and an eviction policy.

Order matters because of "lost in the middle" — the beginning and end of a prompt get
attended to most. So the most valuable material goes at the edges, not buried.

Eviction matters because when the budget is tight, something must go. And the intuitive
choice is often wrong: shedding the verbatim recent conversation first is usually a
mistake, because those turns sit closest to the query and carry the most immediate
context.

---

## Multi-tenancy: the requirement that outranks everything

If several customers share the deployment, a memory recalled for the wrong tenant is
not a quality bug. It is a data breach, and it is *worse* than a normal one because the
leaked content gets pasted directly into a prompt and paraphrased back to a stranger.

Every read must be scoped. The subtle version of this failure is asymmetric scoping —
filtering by tenant *only when a tenant is supplied*, so an unscoped call quietly
matches everything. That reads as defensive coding and behaves as a leak.

---

## What you should now be able to explain

- Why models have no memory, and what "remembering" actually is mechanically
- Why re-sending everything fails on three axes at once, including quality
- Episodic vs semantic vs procedural vs working, and why the split is engineering, not trivia
- What consolidation is and why it mirrors human memory
- What bitemporal means, and the two different questions it answers
- Why relevance alone is not enough for recall, and what min-max normalisation destroys
- Why forgetting is required, and how naive frequency scoring ossifies
- Why an unscoped memory read is a breach, not a bug

**Next:** [`10-theory.md`](10-theory.md) — the algorithms and formulas behind all of it.
