# ADR 0009 — A server-free, embedded vector store (no Qdrant, no pgvector)

- **Status:** Accepted
- **Date:** 2026-08-15
- **Deciders:** Team
- **Supersedes:** the vector-store half of ADR 0003 (LightRAG over GraphRAG)
- **Related:** ADR 0006 (RRF hybrid retrieval), `aegis/src/aegis/retrieval/lightrag_backend.py`,
  `aegis/src/aegis/retrieval/vector_store.py`, `backend/src/app/retrieval/NOTES.md`,
  `INSTALL.md`.

## Context

The vector tier has now been chosen three times, and the constraint that decided it
changed each time.

1. **ADR 0003 (2026-08-03)** chose **pgvector**, on the reasoning that Postgres was
   already a dependency so the vector tier was free.
2. That was replaced by **Qdrant**, a purpose-built ANN server, because pgvector needs a
   privileged, server-side `CREATE EXTENSION vector` and its index tuning is a Postgres
   administration problem rather than a retrieval one.
3. This ADR replaces Qdrant, because of a **deployment** fact neither earlier decision
   knew: Aegis has to run on a locked-down enterprise Windows machine where **no
   additional server binary may be installed** — no service registration, no listening
   port, nothing that arrives outside the Python package set. Qdrant is a server. So is
   a pgvector-enabled Postgres extension, in the sense that matters here: it needs
   privileged installation into a server we do not control.

The requirement, then, is a *real* ANN engine that ships as an ordinary pip dependency
and runs inside the backend process.

Two independent vector tiers need this, and they resolve differently:

- **Aegis's own store** (`aegis.retrieval.vector_store`, used by memory recall and the
  isolation-scoped index) — we control this code, so any embedded library will do.
- **LightRAG's internal vectors** — we do *not* control this; we can only select from
  the storage backends LightRAG ships.

## Decision

**Aegis's own store: Chroma, run embedded.** `chromadb.PersistentClient` over a local
directory, HNSW with cosine distance. It is a genuine ANN index, not a RAM dictionary,
and it is configured by a path (`VECTOR_STORE_PATH`) rather than a URL.

**LightRAG's internal vectors: `NanoVectorDBStorage`.** This was chosen by enumerating
what LightRAG 1.5.6 can *actually* load, verified by importing each implementation
module against the installed package rather than trusting its documentation:

| LightRAG backend | Result | Verdict |
|---|---|---|
| `NanoVectorDBStorage` | imports cleanly | **chosen** — pure Python, file-backed under `working_dir`, LightRAG's own default |
| `ChromaVectorDBStorage` | declared in `lightrag.kg.STORAGES`, but `lightrag.kg.chroma_impl` **does not ship** in 1.5.6 | unusable — cannot be selected at all |
| `FaissVectorDBStorage` | `ModuleNotFoundError: faiss` | rejected — an extra native wheel |
| `QdrantVectorDBStorage` | imports, but needs the server we are removing | rejected by the constraint |
| `PGVectorStorage` | imports, but needs the `pgvector` extension installed into the Postgres server | rejected — the same privileged install that started this |

Note the asymmetry this creates and do not paper over it: Aegis's own store is Chroma,
LightRAG's is NanoVectorDB. They are two different libraries because only one of the two
choices is ours to make.

## Consequences

- **+** The entire stack installs from pip and native packages with **zero vector server**:
  no binary download, no Windows service, no port. This is the whole point — it is what
  makes the platform deployable on the target machine at all.
- **+** One fewer process to start, supervise, and explain in the run scripts;
  `scripts/dev-native.sh`, `preflight.*` and `install-windows.ps1` all lose a step.
- **+** The failure mode gets simpler and more honest: there is no network partition
  between the app and its vectors, only a directory that is or is not writable.
- **−** **NanoVectorDB is a brute-force cosine scan**, not an HNSW index: it holds the
  matrix in memory and persists it as JSON, so LightRAG-side query cost grows linearly
  with the corpus. Accepted at Aegis's corpus scale; the remedy at large scale is to
  point `vector_storage` back at a service, which is a one-line change in
  `lightrag_backend.py`.
- **−** Both embedded stores assume a **single writing process**. A multi-worker
  deployment would need a shared, external store again. Accepted: the target deployment
  is a single machine running a single backend process.
- **−** Two vector libraries instead of one, for the reason given above.

## What did *not* change

**"Embedded" is not "optional", and it is not a fallback.** In full stores mode the
vector store remains a hard dependency, exactly like Postgres and Redis. `main.py`'s
lifespan opens the store at `VECTOR_STORE_PATH` and lets any failure propagate, so an
unwritable or corrupt directory **fails the boot** rather than degrading to a
non-durable in-RAM index. `aegis.core.config` requires `AEGIS_VECTOR_STORE_PATH` in
`full` mode and `aegis.core.health.probe_vector_store` reports its real state to
`/readyz`. The no-silent-fallback posture that ADR 0003 and the `AegisMode` design
established is unchanged; only the engine behind it moved.

## Alternatives considered

- **Keep Qdrant, run it embedded** (`QdrantClient(path=...)`). Qdrant's own local mode
  would have satisfied the constraint for *our* store — but not for LightRAG, whose
  `QdrantVectorDBStorage` speaks HTTP to a node and has no embedded path. Keeping Qdrant
  would therefore have kept the server requirement anyway, for LightRAG alone.
- **pgvector.** Postgres is already installed, so it looks free. It is not: the
  extension needs a privileged `CREATE EXTENSION` on a server the enterprise controls,
  which is the same class of blocker as installing a vector server. Also already
  rejected once, in the move that produced the Qdrant decision.
- **FAISS.** A first-rate ANN library and genuinely embedded, but it arrives as a native
  wheel with its own build/BLAS surface, and LightRAG's `faiss_impl` is not importable in
  this environment. More installation risk, on the exact axis we are trying to reduce.
- **A hand-rolled in-memory index.** Rejected on the no-fakes bar: brute-force cosine in
  a dict is not a vector database, and claiming one would be the kind of thing this
  project refuses to do.
