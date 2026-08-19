"""Audit A / §5.6-5.7: what a tenant-less cache is allowed to hold, and to decide.

The cache is deliberately shared across tenants and that stays: a public web search
is public web content, and one provider call serving every tenant who asks the same
question is the whole point (``test_two_tenants_share_one_entry_by_design`` pins it
down). Two things were riding along with the sharing that had no business being
shared.

**The query text.** ``cache_key``'s docstring claimed the query "is hashed, never
stored". It was false: the cached value was ``WebSearchResponse.model_dump_json()``
and ``WebSearchResponse.query`` is the raw query. The sorted-set index added for the
entry cap finished the argument off — anyone with read access to Redis could
``zrange`` the index and ``get`` every tenant's question without knowing a single
one of them in advance.

**The screening verdict.** The cached value was the *screened* response, so a warm
query cost zero classifier calls. But ``guardrails.denylist.terms`` and
``guardrails.pii.entities`` are tenant-scoped, UNION-merged settings: tenant A and
tenant B do not have the same rail. Caching the verdict meant whichever tenant
searched first decided what the other was allowed to see — a strict tenant's
denylist silently skipped because a lax tenant primed the entry, or a lax tenant
served a BLOCK their own rails never asked for.

The fix separates the two: the cache holds the **provider's raw hits and nothing
else**, and every read — warm or cold — is screened by the rails of the tenant
doing the reading.
"""

from __future__ import annotations

from aegis.websearch.cache import (
    INDEX_KEY,
    CachedWebResults,
    InMemoryWebSearchCache,
    RedisWebSearchCache,
    cache_key,
)
from aegis.websearch.service import WebSearch
from aegis.websearch.types import WebSearchResult

SECRET = "acme-holdings merger with globex, board memo ref BM-7741"
TENANT_B_SECRET = "tenant b's own confidential question"


class _Client:
    name = "tavily"

    def __init__(self) -> None:
        self.queries: list[str] = []

    async def search(self, query, *, max_results=5):  # noqa: ANN001, ARG002
        self.queries.append(query)
        return [WebSearchResult(title="t", url="https://x/1", content="public web text")]


class _FakeRedis:
    """The subset RedisWebSearchCache uses, so the stored bytes are inspectable."""

    def __init__(self) -> None:
        self.kv: dict[str, str] = {}
        self.zset: dict[str, float] = {}

    def get(self, key):  # noqa: ANN001
        return self.kv.get(key)

    def setex(self, key, ttl, value):  # noqa: ANN001, ARG002
        self.kv[key] = value

    def zadd(self, key, mapping):  # noqa: ANN001, ARG002
        self.zset.update(mapping)

    def zcard(self, key):  # noqa: ANN001, ARG002
        return len(self.zset)

    def zrange(self, key, start, end):  # noqa: ANN001, ARG002
        return sorted(self.zset, key=lambda k: self.zset[k])[start : end + 1]

    def delete(self, *keys):  # noqa: ANN001
        for k in keys:
            self.kv.pop(k, None)

    def zrem(self, key, *keys):  # noqa: ANN001, ARG002
        for k in keys:
            self.zset.pop(k, None)


# ── The raw query never lands in the shared cache ────────────────────────────


async def test_the_raw_query_text_is_never_stored_in_the_tenant_less_cache():
    """cache_key's docstring says the query is never stored. Now it is true."""
    redis = _FakeRedis()
    search = WebSearch(client=_Client(), cache=RedisWebSearchCache(redis))
    await search.search(SECRET)

    stored = " ".join(redis.kv.values())
    assert SECRET not in stored, (
        "one tenant's raw query text is stored verbatim in the shared, tenant-less "
        f"cache: {list(redis.kv.values())!r}"
    )
    assert "acme-holdings" not in stored and "BM-7741" not in stored


async def test_nothing_enumerable_from_the_cache_index_is_tenant_data():
    """The sorted-set index lists every key; what it leads to must be public content."""
    redis = _FakeRedis()
    search = WebSearch(client=_Client(), cache=RedisWebSearchCache(redis))
    await search.search(SECRET)
    await search.search(TENANT_B_SECRET)

    keys = redis.zrange(INDEX_KEY, 0, 99)
    recovered = [redis.get(k) for k in keys]
    assert len(keys) == 2, "non-vacuity: the index really does enumerate the entries"
    leaked = [r for r in recovered if SECRET in (r or "") or TENANT_B_SECRET in (r or "")]
    assert not leaked, f"enumerated {len(keys)} keys and recovered raw queries: {recovered!r}"
    # What an enumerator does get is exactly what the provider returned.
    assert all(CachedWebResults.model_validate_json(r).results for r in recovered)


async def test_two_tenants_share_one_entry_by_design():
    """Non-vacuity for the above: the sharing itself is real and deliberate.

    Public web results are not tenant data and cross-tenant sharing is the point of
    the cache — the phase-05 budget arithmetic assumes a rehearsed query is free the
    second time. The fixes above must not cost this.
    """
    client = _Client()
    cache = InMemoryWebSearchCache()
    a = WebSearch(client=client, cache=cache)
    b = WebSearch(client=client, cache=cache)
    await a.search("latest fda guidance")
    await b.search("Latest  FDA Guidance")
    assert client.queries == ["latest fda guidance"], (
        "the second tenant did not hit the shared entry; the test above proves nothing"
    )
    assert cache_key("tavily", "latest fda guidance", 5) == cache_key(
        "tavily", "Latest  FDA Guidance", 5
    )


# ── The screening verdict does not cross tenants with the entry ──────────────


class _Guard:
    """A TOOL_RESULT rail whose denylist is the TENANT's (guardrails.denylist.terms)."""

    def __init__(self, denied: str | None) -> None:
        self.denied = denied
        self.calls = 0

    async def check_tool_result(self, text, *, tool_name=None, emitter=None):  # noqa: ANN001, ARG002
        self.calls += 1

        class _V:
            layer = "denylist"
            reason = "tenant denylist"
            text = ""
            verdict = "pass"

        if self.denied and self.denied in text:
            _V.verdict = "block"
        return _V


async def test_each_tenants_own_rail_screens_the_shared_entry():
    """Tenant A's BLOCK is tenant A's; tenant B is screened by tenant B's rails."""
    client = _Client()
    cache = InMemoryWebSearchCache()

    strict = _Guard(denied="public")  # tenant A denies the word "public"
    lax = _Guard(denied=None)  # tenant B denies nothing

    a = WebSearch(client=client, cache=cache, guard=strict)
    b = WebSearch(client=client, cache=cache, guard=lax)

    first = await a.search("shared query")
    second = await b.search("shared query")

    assert first.results == () and len(first.blocked) == 1, first
    assert client.queries == ["shared query"], "the provider call is still shared"
    assert lax.calls == 1, "tenant B's own rail was never consulted"
    assert second.results, (
        "tenant B, whose rails deny nothing, was served tenant A's BLOCK verdict from "
        f"the tenant-less cache: results={second.results!r} blocked={second.blocked!r}"
    )
    assert second.cached is True


async def test_a_lax_tenant_priming_the_cache_does_not_disarm_a_strict_ones_denylist():
    """The same defect in the direction that actually loses data: order reversed."""
    client = _Client()
    cache = InMemoryWebSearchCache()

    lax = _Guard(denied=None)
    strict = _Guard(denied="public")

    await WebSearch(client=client, cache=cache, guard=lax).search("shared query")
    second = await WebSearch(client=client, cache=cache, guard=strict).search("shared query")

    assert strict.calls == 1, "the strict tenant's denylist never ran on cached content"
    assert second.results == () and len(second.blocked) == 1, (
        "a lax tenant primed the cache and the strict tenant's rail was skipped: "
        f"results={second.results!r}"
    )
