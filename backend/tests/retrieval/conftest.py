"""Shared fakes for retrieval unit tests (no live Neo4j/Redis/network/gateway)."""

from __future__ import annotations

from types import SimpleNamespace

from app.api.schemas import GraphEdge, GraphNode
from app.retrieval.models import Candidate, Chunk, Recall


class FakeRedis:
    """Minimal async in-memory stand-in for `redis.asyncio.Redis`."""

    def __init__(self):
        self.kv: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value, *, ex=None):
        self.kv[key] = value
        return True

    async def sadd(self, key, *values):
        self.sets.setdefault(key, set()).update(values)
        return len(values)

    async def smembers(self, key):
        return set(self.sets.get(key, set()))


class RecordingComplete:
    """Async `complete` fake that records calls and returns a canned `.content`.

    Each response also carries a `.usage` (mirroring `LLMResult`) so callers that accrue
    per-call token/cost — e.g. query rewrite — can be exercised. Consumers that read only
    `.content` are unaffected.
    """

    def __init__(self, content: str, *, usage: SimpleNamespace | None = None):
        self.content = content
        self.usage = usage or SimpleNamespace(
            prompt_tokens=6, completion_tokens=4, cost_usd=0.0002
        )
        self.calls: list[dict] = []

    async def __call__(self, role, messages, *, temperature=0.0, response_format=None):
        self.calls.append(
            {
                "role": role,
                "messages": messages,
                "temperature": temperature,
                "response_format": response_format,
            }
        )
        return SimpleNamespace(content=self.content, usage=self.usage)


class SequenceEmbed:
    """Async `embed` fake returning a fixed vector (or one vector per call).

    ``SequenceEmbed(vec)`` returns ``vec`` for every call; ``SequenceEmbed.sequence([...])``
    returns each supplied vector on successive calls (the last is reused if exhausted),
    so tests can drive distinct query embeddings across retrieve() calls.
    """

    def __init__(self, vector, *, per_call=None):
        self.vector = vector
        self._per_call = list(per_call) if per_call is not None else None
        self.calls: list[list[str]] = []

    @classmethod
    def sequence(cls, vectors):
        """Build an embedder that yields ``vectors[i]`` on the i-th call."""
        vectors = list(vectors)
        return cls(vectors[0], per_call=vectors)

    async def __call__(self, texts):
        self.calls.append(list(texts))
        if self._per_call is not None:
            idx = min(len(self.calls) - 1, len(self._per_call) - 1)
            vector = self._per_call[idx]
        else:
            vector = self.vector
        return [list(vector) for _ in texts]


class FakeBackend:
    """Async `KnowledgeBackend` fake with a canned recall and recorded ingests."""

    def __init__(self, recall: Recall):
        self._recall = recall
        self.ingested: list[Chunk] = []
        self.recall_calls: int = 0

    async def ingest_chunks(self, chunks):
        self.ingested.extend(chunks)
        return (len(chunks), max(0, len(chunks) - 1))

    async def recall(self, query, *, top_k, scope):
        self.recall_calls += 1
        return self._recall


def make_recall() -> Recall:
    """Return a small canned `Recall` for pipeline tests."""
    return Recall(
        candidates=[
            Candidate(id="c0", text="the sky is blue during the day"),
            Candidate(id="c1", text="water boils at one hundred celsius"),
        ],
        nodes=[GraphNode(id="sky", label="sky", kind="entity")],
        edges=[GraphEdge(source="sky", target="day", relation="observed_during")],
    )
