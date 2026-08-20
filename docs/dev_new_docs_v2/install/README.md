# Installing Aegis v2 — from `git clone` to a demo that works

`docs/install/` is the v1 runbook. It predates **Qdrant**, **Superset**, the demo
seeders and the document corpus, so following it alone leaves you with a platform
that boots and shows nothing.

This folder is the v2 path. Read in order:

| | |
|---|---|
| [`01-prerequisites.md`](01-prerequisites.md) | what to install, and the versions this was built against |
| [`02-bootstrap.md`](02-bootstrap.md) | the ordered runbook, with the check that proves each step |
| [`03-demo-data.md`](03-demo-data.md) | what each seeder writes, and **how to remove it before the hackathon** |
| [`AGENT-CONTEXT.md`](AGENT-CONTEXT.md) | hand this to a coding agent doing the install |

---

## The thing to understand first

**Almost none of the demo lives in git.** A clone gives you the code, the four
corpus PDFs and the Superset asset bundle. It does not give you a working demo.

| In git | Only on the machine that built it |
|---|---|
| all code · 46 illustrations | Postgres — ~57,000 demo rows, 11 users, 6 documents |
| `docs/corpus/` — 4 real PDFs + `SOURCES.md` | Qdrant — 113 vectors |
| `docs/operations/superset/` — the asset bundle | `.superset/` — venv + metadata DB (gitignored, 630 MB) |
| `scripts/superset.sh` · `app/seed.py` · `app/demo.py` · `app/memory_demo.py` | `backend/.env` — **your API keys** |

Everything on the right is rebuildable from something on the left. That is the
whole design, and it has been tested the hard way: the first Superset instance was
installed into a temp directory and a restart destroyed it — every dataset, chart
and dashboard. It was rebuilt from the committed bundle in about ten minutes. That
is why the bundle is committed and why the assembly is a script.

## What it costs

One step spends real money: **re-ingesting the four corpus PDFs** calls the
embedding provider. It is ~76 chunks, so cents rather than dollars, but it is not
free and it needs a working `AZURE_API_KEY`.

Everything else — seeding, demo data, memory, re-indexing from stored embeddings —
costs nothing and calls no provider.

## If you are short of time

The platform is usable without Superset (analytics has a native path) and without
Temporal (retrieval works; only new ingestion needs it). Do steps 1–5 of
[`02-bootstrap.md`](02-bootstrap.md) and stop. Steps 6–7 add the embedded
dashboards and the ability to ingest new documents.
