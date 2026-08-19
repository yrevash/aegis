"""Aegis web search — a provider-neutral seam, cached, and guarded on the way in.

The platform depends on :class:`WebSearchClient` and :class:`WebSearch`, never on a
vendor. Tavily is one implementation (:mod:`aegis.websearch.tavily`, an optional
extra: ``pip install aegis[websearch]``); a fake in a test is another.

Standalone usage::

    from aegis.guardrails import Guardrails
    from aegis.websearch import WebSearch

    search = WebSearch.from_env(guard=Guardrails())
    answer = await search.search("latest guidance")
    if answer.degraded:
        ...  # no web evidence — NOT "the web had nothing"

Three properties are the whole point of this package:

* **Cached.** A repeated query costs zero provider calls. The cache is shared across
  tenants deliberately — a public web page is the same page for everybody — so it holds
  the provider's raw hits and nothing else: not the query that found them
  (:class:`CachedWebResults`), and not one tenant's guardrail verdict.
* **Guarded.** Every hit passes the ``TOOL_RESULT`` rail before it is returned — on a
  cache hit exactly as on a cold call, because the rails are tenant-scoped — so a
  prompt injection planted in a web page is blocked and reported rather than read by
  the model (OWASP LLM01).
* **Loud when degraded.** No key, or a failed provider, produces an ERROR log and a
  ``web_search`` event — never an empty result list that looks like a clean search.
"""

from __future__ import annotations

from aegis.websearch.cache import (
    DEFAULT_MAX_ENTRIES,
    DEFAULT_TTL_SECONDS,
    CachedWebResults,
    InMemoryWebSearchCache,
    RedisWebSearchCache,
    WebSearchCache,
    cache_key,
    make_web_search_cache,
    normalise_query,
)
from aegis.websearch.service import (
    DEFAULT_MAX_RESULTS,
    NO_KEY_REASON,
    TOOL_NAME,
    WebSearch,
)
from aegis.websearch.tavily import (
    API_KEY_ENV,
    LEGACY_MISSPELLED_ENV,
    TavilyWebSearchClient,
)
from aegis.websearch.types import (
    BlockedResult,
    ToolResultScreen,
    WebSearchClient,
    WebSearchResponse,
    WebSearchResult,
    WebSearchStatus,
)

__all__ = [
    "API_KEY_ENV",
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_MAX_RESULTS",
    "DEFAULT_TTL_SECONDS",
    "LEGACY_MISSPELLED_ENV",
    "NO_KEY_REASON",
    "TOOL_NAME",
    "BlockedResult",
    "CachedWebResults",
    "InMemoryWebSearchCache",
    "RedisWebSearchCache",
    "TavilyWebSearchClient",
    "ToolResultScreen",
    "WebSearch",
    "WebSearchCache",
    "WebSearchClient",
    "WebSearchResponse",
    "WebSearchResult",
    "WebSearchStatus",
    "cache_key",
    "make_web_search_cache",
    "normalise_query",
]
