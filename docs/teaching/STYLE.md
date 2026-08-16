# How these docs are written

This is the writing contract for `docs/teaching/`. Read it before adding or changing a
module. The reference implementation is [`agent/10-guide.md`](agent/10-guide.md).

The goal is simple: **someone should be able to read a module guide in ten minutes and
know what the module is, how it works, how to use it, and why it exists.**

---

## The shape

Three files per module.

| File | What it is |
|---|---|
| `10-guide.md` | The module, in four sections. **1,200–2,000 words.** |
| `40-diagrams.md` | 4–6 diagrams. |
| `50-interview.md` | Questions and answers. |

### The guide has exactly four sections

```
## 1. What it is
## 2. How it works in Aegis
## 3. How you use it in code
## 4. Why it helps us
```

Nothing else. No extra sections, no appendices, no "further reading".

**1. What it is** — the problem in plain words, and what this module does about it. One
small concrete example. Three or four short paragraphs.

**2. How it works in Aegis** — the real design. The pieces, how they fit, the main flow.
Use a table to list parts. Keep explanations to a few sentences each.

**3. How you use it in code** — the import, and a short real snippet. The functions a
caller actually touches, and the settings that matter. This is the section people come
back to, so make it easy to scan.

**4. Why it helps us** — what we get out of it, in a few lines. What breaks without it.

---

## Write simply

The point is speed of understanding, not completeness.

**Short sentences.** If a sentence has two commas and a dash, split it.

**Plain words.** Say "runs again" not "re-executes". Say "the database makes sure only
one wins" not "an optimistic concurrency-controlled state transition". If a technical
term is genuinely the name of the thing, use it — but define it in one plain sentence the
first time.

**Show, then name.** A short concrete example before the term. One example is enough;
don't stack three.

**Cut every sentence that is not doing work.** No throat-clearing ("In this section we
will explore…"). No restating what the last paragraph said.

**One idea per paragraph.** Two to four sentences each.

**Tables for lists of things.** Prose for reasoning. Never put an argument in a table.

---

## What NOT to put in a guide

These were in the first version and made it unreadable. Leave them out.

**Bug stories.** No "what it was / why it mattered / how we fixed it" write-ups. If a bug
produced a rule worth knowing, state the rule in one line and move on. A reader learning
the module does not need the history of the module.

**Failure-mode catalogues.** Don't list every way each part can break.

**Research citations and papers.** Name a technique if it has a real name. Don't cite
authors and years.

**Line-by-line code references.** Point at a file when it helps someone find the code.
Don't cite `file.py:1234` for every claim — it dates fast and clutters the page.

**Exhaustive API tables.** List the handful of fields a caller uses. Link to the source
for the rest.

**Long quoted docstrings.** Say the thing yourself, shorter.

If depth is genuinely valuable, it belongs in `50-interview.md`, where the question
"tell me about a hard bug" actually gets asked.

---

## Diagrams

**Four to six per module. Not one per code path.**

Keep a diagram only if all three hold:

1. It is genuinely spatial or temporal — a flow, a sequence, a state machine, a tree.
   Arithmetic, field lists and comparisons are tables.
2. It is readable without a paragraph explaining how to read it.
3. It is not a redraw of another diagram with one box highlighted.

Give each one a single-line caption saying what to look at. Then stop.

Run `node scripts/check-teaching-mermaid.mjs` before committing — a diagram that fails to
parse renders as an error box. Avoid `;` and `()` inside sequence-diagram messages;
mermaid treats them as syntax.

---

## Never make things up

No invented numbers, benchmarks, or citations. If you state a figure, it must come from
real code, a real test, or a run you actually did. If something is illustrative, say so.

Don't overclaim. Prompt injection is not solved. RAG does not remove hallucination. A
guide that oversells is worth less than one that is short and honest.
