# Shared research context — read this first

You are evaluating TWO candidate problem statements for a national-finals AI hackathon.
Your job is to produce evidence-backed research that helps a team decide **which one to
build**, and — whichever wins — to have a ready plan for it.

## The two candidates

- `docs/hackathon/two-ideas/_briefs/ps17-brief.md` — Contract Obligation, SLA & Commercial Leakage Monitor
- `docs/hackathon/two-ideas/_briefs/ps04-brief.md` — AI-Powered Dynamic Covenant Monitoring & Early Warning

Read BOTH briefs in full before you research anything.

## Rules of engagement (non-negotiable)

1. **Do not read any other file in this repository.** Read only the two briefs above and
   this file. Do not `ls`, `grep`, or explore the working directory. Do not infer anything
   from the repository's name, path, or directory structure. This evaluation must be a
   clean-room judgement on the merits of the two problem statements alone. Any pre-existing
   codebase is irrelevant to your analysis and must not appear in your output.
2. **Do not spawn subagents.** Do all research yourself.
3. **Every non-obvious claim needs a citable source** — a URL to a research paper, an
   analyst report, a regulator publication, a standards body, a patent record, or a vendor's
   published technical documentation. Use WebSearch / WebFetch. If you cannot find a source
   for a claim, either drop the claim or label it explicitly `[UNVERIFIED — no source found]`.
   Never invent a statistic, a paper title, a patent number, or a URL. A fabricated citation
   in front of a CTO jury is worse than no citation.
4. **Cover BOTH problem statements** in your lane, and end with an explicit comparative
   verdict for your lane.
5. **Make no code changes.** You write exactly one Markdown file, at the path given in your
   task. Nothing else.

## Hard constraints on any solution proposed

- **Windows demo machine, no Docker / no container runtime.** Anything that requires
  containers, Kubernetes, or a Linux-only daemon is out. Prefer embeddable, pip/npm-installable,
  or single-binary components. If you propose something heavyweight, say how it runs on bare
  Windows or mark it as a stretch goal.
- **7-day build window: 29 Aug → 4 Sep 2026.** A small team. Plans must be shippable in that
  window, not aspirational roadmaps.
- **Synthetic data only.** No live enterprise systems. Both briefs explicitly permit mock
  interfaces and synthetic corpora.
- **Deliverable is a live working demo plus a pitch**, judged by a panel of CTOs.

## What the jury scores (the metrics that decide this)

Weight your analysis toward these. They are the actual winning factors:

| Metric | What it means here |
| --- | --- |
| **Backend depth** | Genuine engineering substance — data modelling, state, concurrency, correctness under change. Must be *explainable with a visual*. |
| **Visuals / frontend** | Can the solution be *seen*? Does the UI make the hard backend legible to a CTO in a 5-minute demo? |
| **Patentability** | Is there a defensible, novel, claimable mechanism? Prior art matters. |
| **Uniqueness / originality** | How different is this from what every other team and every incumbent vendor will show? |
| **Innovation** | Genuine technical novelty, not feature count. |
| **Business impact** | Quantified value: leakage recovered, losses avoided, FTE hours, cycle time. Real numbers with sources. |
| **Production readiness** | The "how would you deploy and scale this?" answer. Workers, schedulers, queues, failure recovery, security, multi-tenancy — and the *problem-specific* scaling maths. |
| **Explainability** | Every decision traceable. Per-loop observability (OpenTelemetry spans over agent/reasoning loops), and a clear **human–AI autonomy model** stating which of the 5 autonomy levels applies to which action. |
| **Out-of-the-box factor** | Optional but valued: a blockchain / smart-contract / cryptographic-provenance angle, implemented *as a concept* (embedded hash chain, Merkle audit log, signed attestations) if a real chain cannot be installed on Windows. Never force it; only propose it where it is genuinely load-bearing. |

## Structural requirement the team has asked for

The pitch must **decompose each problem statement into distinct sub-problems** — the way a
team would split the work — and present a named solution for each. "We found N hard
sub-problems inside this; here is what we built for each" is the narrative spine. Where your
lane touches this, express your findings in those terms.

## Output format

Write ONE Markdown file to the exact path given in your task. Structure it as:

```
# <Lane name> — comparative research

## Executive answer            <- 5–10 bullets, the verdict up front
## PS-17: <lane> analysis
## PS-04: <lane> analysis
## Head-to-head verdict for this lane
   - Winner, and by how much (score each 1–10 on your lane's criterion, justify)
   - What would change the verdict
## Risks and open questions
## Sources
   - Numbered list. Every URL you actually opened. Mark ones you could not verify.
```

Be concrete and specific. Name technologies, name papers, name numbers. Avoid generic
consulting prose. A CTO reading this should learn something they did not already know.
