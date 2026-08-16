# Teaching Aegis — zero to mastery

A complete course on this system. It assumes **no prior knowledge** — not of agents,
not of RAG, not of vector databases — and takes you to the point where you can defend
every design decision in an interview.

It is written for someone who has to *explain* Aegis, not just run it.

---

## How this is organised

One folder per module. Every folder has the same three files, so once you learn the
shape you can navigate any module the same way.

| File | What it gives you | Read it when |
|---|---|---|
| `10-guide.md` | **The module in about ten minutes.** What it is, how it works here, how you use it in code, and why it helps. | Always. Start here. |
| `40-diagrams.md` | Four to six diagrams — the flows and sequences a picture explains faster than words. | You think visually, or you're whiteboarding |
| `50-interview.md` | The questions you will actually be asked, with answers. The deeper detail lives here. | The night before |

Every guide has the same four sections, so you always know where to look:

```
1. What it is          2. How it works in Aegis
3. How you use it in code   4. Why it helps us
```

Guides are deliberately short. They teach the module, not its history — no bug
post-mortems, no research citations, no exhaustive API dumps. If you want the hard
detail, it is in `50-interview.md`. [`STYLE.md`](STYLE.md) is the writing contract.

---

## The reading order

**Do not start with a module.** Start with foundations — the modules assume that
vocabulary and will feel arbitrary without it.

### 1. Foundations (start here)

[`00-foundations/`](00-foundations/10-guide.md) — what a token is, what an embedding is, why
vector search exists, what "RAG" actually means, what makes something an *agent*
rather than a chatbot, and why every one of those choices creates a problem the rest
of this system exists to solve.

### 2. The spine — how a request flows

Read these four in order. Together they are one complete request, end to end.

| Order | Module | What it does in the request |
|---|---|---|
| 1 | [`guardrails/`](guardrails/10-guide.md) | Decides whether the input is allowed in at all |
| 2 | [`retrieval/`](retrieval/10-guide.md) | Finds the evidence to answer with |
| 3 | [`agent/`](agent/10-guide.md) | Plans, decides, acts, and repairs itself |
| 4 | [`gateway/`](gateway/10-guide.md) | Every model call in the system funnels through here |

### 3. The knowledge layer

| Module | What it holds |
|---|---|
| [`memory/`](memory/10-guide.md) | What the system remembers between turns and across sessions |
| [`data/`](data/10-guide.md) | The portable ORM foundation everything persists through |

### 4. The trust layer — the part that makes it enterprise

| Module | The claim it backs |
|---|---|
| [`ml/`](ml/10-guide.md) | "The prediction is calibrated" |
| [`governance/`](governance/10-guide.md) | "One tenant cannot see another's data or spend their budget" |
| [`observability/`](observability/10-guide.md) | "Every step is traced" |
| [`evals-ops/`](evals-ops/10-guide.md) | "Quality is measured, and improvements are gated" |

### 5. The modalities

| Module | Input |
|---|---|
| [`media/`](media/10-guide.md) | The shared payload + rail seam all three sit on |
| [`voice/`](voice/10-guide.md) | Speech |
| [`vision/`](vision/10-guide.md) | Images |
| [`forecast/`](forecast/10-guide.md) | Time series |

### 6. The foundation everything imports

[`core/`](core/10-guide.md) — types, the streaming spine, the lazy-dependency mechanism, and the
Module Contract that makes `aegis` an importable package rather than an app you fork.

---

## The five ideas that explain every design decision here

If you internalise nothing else, internalise these. Almost every question you get can
be answered by reaching for one of them.

**1. Branding, never hiding.** Every capability has a product name *and* its honest
underlying tech, named together and named precisely — "Aegis Retrieval (Neo4j/LightRAG,
with Chroma and NanoVectorDB for vectors)". Blur the tech and you are selling something
you cannot defend when someone asks a follow-up question.

**2. No silent fallbacks.** A control that cannot run must fail **closed** and say so.
The alternative — returning a plausible answer with a degraded path nobody can see —
is how systems lie. This rule is why several bugs in this codebase were *findable*:
they were violations of it.

**3. ML informs; risk gates.** The machine-learning signal is *evidence injected into
the plan*. It never decides whether to stop for a human. That decision is made by the
**risk tier of the tool being called**. A model that is 99% confident about issuing a
$4,200 refund still stops for a human, because refunds are high-risk — not because the
model was unsure.

**4. Measured, never claimed.** If a number is displayed, something computed it from
real data. Where nothing was measured, the UI says "not yet measured" instead of printing
a zero. Ask the forecast module for 90% intervals on its demo series and it reports that
they actually achieved **76%**, and flags `meets_request: false` — rather than quietly
showing you the number you asked for.

**5. The core is imported, not forked.** `aegis/` is a package with no domain logic.
`backend/` composes it. To point the whole platform at a new problem you write one
adapter and change nothing else.

---

## How to use this before an interview

1. **Read `00-foundations/` fully.** Everything else assumes it.
2. **Pick the three modules you'd most like to be asked about** and read all three files
   for each. For most people that is `agent/`, `retrieval/` and `governance/` — they
   carry the hardest questions.
3. **Read every `50-interview.md`.** They are short and they are the highest-yield
   thing here.
4. **Be able to draw two diagrams from memory:** the request flow end to end, and the
   human-approval interrupt/resume path. If you can draw those on a whiteboard and
   talk through them, you can hold a forty-minute conversation about this system.
5. **Learn one bug per module well enough to tell the story.** "We found that X, here's
   why it mattered, here's what we changed" is the most convincing thing you can say.
   Those live in `50-interview.md`, not in the guides.
