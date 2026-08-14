# Teaching Aegis — zero to mastery

A complete course on this system. It assumes **no prior knowledge** — not of agents,
not of RAG, not of vector databases — and takes you to the point where you can defend
every design decision in an interview.

It is written for someone who has to *explain* Aegis, not just run it.

---

## How this is organised

One folder per module. Every folder has the same six files, so once you learn the
shape you can navigate any module the same way.

| File | What it gives you | Read it when |
|---|---|---|
| `00-concepts.md` | The idea from zero. No code. Analogies, plain language, why the problem exists at all. | You've never heard of this |
| `10-theory.md` | The real computer science. Algorithms, formulas, trade-offs, the published research the design comes from. | You want to answer "why this approach?" |
| `20-in-aegis.md` | Our exact implementation — file paths, line numbers, every public function, how you import it, what the module composes. | You want to read the code |
| `30-deep-dive.md` | Consistency, concurrency, failure modes, tenant isolation, and the real bugs we found and fixed. | You want to sound like you built it |
| `40-diagrams.md` | Mermaid flowcharts of every path through the module. | You think visually, or you're whiteboarding |
| `50-interview.md` | The questions you will actually be asked, with answers. | The night before |

---

## The reading order

**Do not start with a module.** Start with foundations — the modules assume that
vocabulary and will feel arbitrary without it.

### 1. Foundations (start here)

[`00-foundations/`](00-foundations/) — what a token is, what an embedding is, why
vector search exists, what "RAG" actually means, what makes something an *agent*
rather than a chatbot, and why every one of those choices creates a problem the rest
of this system exists to solve.

### 2. The spine — how a request flows

Read these four in order. Together they are one complete request, end to end.

| Order | Module | What it does in the request |
|---|---|---|
| 1 | [`guardrails/`](guardrails/) | Decides whether the input is allowed in at all |
| 2 | [`retrieval/`](retrieval/) | Finds the evidence to answer with |
| 3 | [`agent/`](agent/) | Plans, decides, acts, and repairs itself |
| 4 | [`gateway/`](gateway/) | Every model call in the system funnels through here |

### 3. The knowledge layer

| Module | What it holds |
|---|---|
| [`memory/`](memory/) | What the system remembers between turns and across sessions |
| [`data/`](data/) | The portable ORM foundation everything persists through |

### 4. The trust layer — the part that makes it enterprise

| Module | The claim it backs |
|---|---|
| [`ml/`](ml/) | "The prediction is calibrated" |
| [`governance/`](governance/) | "One tenant cannot see another's data or spend their budget" |
| [`observability/`](observability/) | "Every step is traced" |
| [`evals-ops/`](evals-ops/) | "Quality is measured, and improvements are gated" |

### 5. The modalities

| Module | Input |
|---|---|
| [`media/`](media/) | The shared payload + rail seam all three sit on |
| [`voice/`](voice/) | Speech |
| [`vision/`](vision/) | Images |
| [`forecast/`](forecast/) | Time series |

### 6. The foundation everything imports

[`core/`](core/) — types, the streaming spine, the lazy-dependency mechanism, and the
Module Contract that makes `aegis` an importable package rather than an app you fork.

---

## The five ideas that explain every design decision here

If you internalise nothing else, internalise these. Almost every question you get can
be answered by reaching for one of them.

**1. Branding, never hiding.** Every capability has a product name *and* its honest
underlying tech, stated together. "Aegis Retrieval (Neo4j/LightRAG + Qdrant)". The
moment you hide the tech, you are selling something you cannot defend in a technical
interview.

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
real data. Where nothing was measured, the UI says "not yet measured" rather than
printing a zero. The forecast module will tell you its 90% intervals actually achieved
76% coverage — because the alternative is a number that quietly lies.

**5. The core is imported, not forked.** `aegis/` is a package with no domain logic.
`backend/` composes it. To point the whole platform at a new problem you write one
adapter and change nothing else.

---

## How to use this before an interview

1. **Read `00-foundations/` fully.** Everything else assumes it.
2. **Pick the three modules you'd most like to be asked about** and read all six files
   for each. For most people that is `agent/`, `retrieval/` and `governance/` — they
   carry the hardest questions.
3. **Read every `50-interview.md`.** They are short and they are the highest-yield
   thing here.
4. **Be able to draw two diagrams from memory:** the request flow end to end, and the
   human-approval interrupt/resume path. If you can draw those on a whiteboard and
   talk through them, you can hold a forty-minute conversation about this system.
5. **Learn one bug per module well enough to tell the story.** "We found that X, and
   here's why it mattered, and here's what we changed" is the single most convincing
   thing you can say. `30-deep-dive.md` has them.
