"""The web-search entry point: cache in front, guardrail behind, degradation out loud.

One class, :class:`WebSearch`, composes the three things a search has to do before its
text is fit to put in front of a model:

1. **Ask the cache first.** A rehearsed query on a warm cache costs zero provider
   calls. The phase-05 budget assumes this; it is also what makes the demo survive
   conference wifi and a rate limit. The cache is shared across tenants on purpose —
   a public web page is the same page for everybody — so it holds the provider's raw
   hits and nothing else (see :class:`~aegis.websearch.cache.CachedWebResults`).
2. **Screen every hit through the ``TOOL_RESULT`` rail** before it is returned to
   anyone — on a cache hit exactly as on a cold call. Search results are arbitrary
   third-party text that a model reads as context — OWASP LLM01 — and this is the only
   place in the turn that looks at them. A blocked hit is *reported*, not quietly
   dropped. Screening happens **after** the cache and never inside it: the rails are
   tenant-scoped (``guardrails.denylist.terms`` and ``guardrails.pii.entities`` are
   UNION-merged per tenant), so a cached verdict would mean whichever tenant searched
   first decided what every later tenant was allowed to see.
3. **Degrade loudly.** No API key means the run continues on internal evidence only,
   and says so: an ERROR log **and** a ``web_search`` event carrying
   ``status="degraded_no_key"``. It must never be possible to mistake "we could not
   search" for "the web had nothing" — that is the standing defect this codebase
   keeps re-learning, and a silent control is worse than no control.
"""

from __future__ import annotations

import logging
import os

from aegis.websearch.cache import (
    DEFAULT_TTL_SECONDS,
    CachedWebResults,
    InMemoryWebSearchCache,
    WebSearchCache,
    cache_key,
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

logger = logging.getLogger(__name__)

#: The tool name stamped on every guardrail verdict raised over search content.
TOOL_NAME = "web_search"

#: Default number of hits requested from the provider.
DEFAULT_MAX_RESULTS = 5

#: The message an operator sees when no key is configured. It names the correct
#: variable, because the reason this never worked was that the wrong one was set.
NO_KEY_REASON = (
    f"No web-search provider is configured ({API_KEY_ENV} is unset), so this run has "
    "NO external web evidence — it is answering from the internal corpus only. This is "
    "not an empty result set: nothing was searched."
)


class WebSearch:
    """Cached, guarded web search over any :class:`WebSearchClient`."""

    def __init__(
        self,
        *,
        client: WebSearchClient | None = None,
        cache: WebSearchCache | None = None,
        guard: ToolResultScreen | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_results: int = DEFAULT_MAX_RESULTS,
        unavailable_reason: str = "",
    ) -> None:
        """Compose a search seam.

        Args:
            client: The provider. ``None`` means no provider is configured: every
                search degrades to internal-only and says so. This is a first-class
                state, not an error.
            cache: The result cache. Defaults to a process-local in-memory cache —
                which is honest for a library default, and a host in ``full`` mode
                should pass :func:`~aegis.websearch.cache.make_web_search_cache`'s
                Redis backend instead.
            guard: The ``TOOL_RESULT`` guardrail (a
                :class:`aegis.guardrails.Guardrails` satisfies it structurally).
                ``None`` means content is returned unscreened, which is logged at
                ERROR on every call — an unscreened tool result is a live LLM01
                surface and must never be a quiet default.
            ttl_seconds: Cache time-to-live.
            max_results: Default provider result cap.
            unavailable_reason: Why there is no client, when there is none. Defaults
                to :data:`NO_KEY_REASON`.
        """
        self._client = client
        self._cache = cache if cache is not None else InMemoryWebSearchCache()
        self._guard = guard
        self._ttl = ttl_seconds
        self._max_results = max_results
        self._reason = unavailable_reason or NO_KEY_REASON

    @property
    def available(self) -> bool:
        """Whether a provider is configured at all."""
        return self._client is not None

    @property
    def provider(self) -> str:
        """The configured provider's name, or ``"none"``."""
        return getattr(self._client, "name", "none") if self._client else "none"

    @classmethod
    def from_env(
        cls,
        *,
        cache: WebSearchCache | None = None,
        guard: ToolResultScreen | None = None,
        env: dict[str, str] | None = None,
        **kwargs: object,
    ) -> WebSearch:
        """Build from the environment, reading ``TAVILY_API_KEY`` and nothing else.

        The misspelled ``TRAVILY_API_KEY`` is **not** honoured. It is detected, and
        its presence is logged at ERROR naming the rename, because reading it would
        make two spellings both "work" and leave the ambiguity in place forever.

        Args:
            cache: Optional cache backend.
            guard: Optional ``TOOL_RESULT`` guardrail.
            env: Environment mapping to read (defaults to ``os.environ``).
            **kwargs: Forwarded to :class:`WebSearch`.

        Returns:
            A configured :class:`WebSearch` — with a client if a key was found, and
            an explicitly degraded one if not.
        """
        source = os.environ if env is None else env
        api_key = (source.get(API_KEY_ENV) or "").strip()
        if not api_key and (source.get(LEGACY_MISSPELLED_ENV) or "").strip():
            logger.error(
                "%s is set but %s is not. %s is the historical MISSPELLING and is read "
                "by nothing — this is why web search never worked. Rename it in .env.",
                LEGACY_MISSPELLED_ENV,
                API_KEY_ENV,
                LEGACY_MISSPELLED_ENV,
            )
        client = TavilyWebSearchClient(api_key) if api_key else None
        return cls(client=client, cache=cache, guard=guard, **kwargs)  # type: ignore[arg-type]

    async def search(
        self,
        query: str,
        *,
        max_results: int | None = None,
        emitter: object | None = None,
    ) -> WebSearchResponse:
        """Search the web, cached and screened, reporting exactly what happened.

        Never raises for a provider failure: a search that cannot run is a *reported*
        degradation, because a research agent that crashes the run over a flaky web
        call is worse than one that says it had no web evidence.

        Args:
            query: The search query.
            max_results: Override the default result cap.
            emitter: Optional AG-UI emitter (anything with an async
                ``custom(name, value)``); receives the ``web_search`` event.

        Returns:
            A :class:`WebSearchResponse`. Check ``.degraded`` before treating an empty
            ``.results`` as evidence of absence.
        """
        want = max_results if max_results is not None else self._max_results
        if self._client is None:
            return await self._degraded(query, WebSearchStatus.DEGRADED_NO_KEY,
                                        self._reason, emitter)

        key = cache_key(self._client.name, query, want)
        hits = self._cache_get(key)
        cached = hits is not None
        if hits is None:
            try:
                hits = await self._client.search(query, max_results=want)
            except Exception as exc:  # noqa: BLE001 - a failed search degrades, loudly
                reason = (
                    f"Web search provider '{self._client.name}' failed ({type(exc).__name__}: "
                    f"{exc}); this run has NO external web evidence."
                )
                return await self._degraded(query, WebSearchStatus.DEGRADED_ERROR, reason,
                                            emitter, exc_info=exc)
            self._cache_set(key, hits)

        # Screened here, warm or cold, and never before the cache: the rail is this
        # tenant's, and the cache belongs to all of them.
        response = await self._screen(query, hits, cached=cached)
        await self._emit(emitter, response)
        return response

    async def _screen(
        self, query: str, hits: list[WebSearchResult], *, cached: bool = False
    ) -> WebSearchResponse:
        """Run every hit through **this caller's** ``TOOL_RESULT`` rail.

        Args:
            query: The query being answered (returned on the response, never stored).
            hits: The provider's raw hits, fresh or rehydrated from the shared cache.
            cached: Whether ``hits`` came from the cache — reported on the response.

        Returns:
            The screened :class:`WebSearchResponse`.
        """
        provider = self.provider
        if self._guard is None:
            logger.error(
                "Web search returned %d result(s) with NO tool-result guardrail wired. "
                "Third-party web content is reaching an agent's context unscreened "
                "(OWASP LLM01). Pass guard= to WebSearch.",
                len(hits),
            )
            return WebSearchResponse(
                query=query, results=tuple(hits), provider=provider, cached=cached
            )

        kept: list[WebSearchResult] = []
        blocked: list[BlockedResult] = []
        redacted = 0
        for hit in hits:
            verdict = await self._guard.check_tool_result(
                f"{hit.title}\n{hit.content}", tool_name=TOOL_NAME
            )
            outcome = str(getattr(verdict, "verdict", "pass"))
            if outcome == "block":
                logger.error(
                    "TOOL_RESULT rail BLOCKED a web-search hit from %s (layer=%s): %s",
                    hit.url or "<no url>",
                    getattr(verdict, "layer", None),
                    getattr(verdict, "reason", ""),
                )
                blocked.append(
                    BlockedResult(
                        url=hit.url,
                        title=hit.title,
                        layer=getattr(verdict, "layer", None),
                        reason=str(getattr(verdict, "reason", "")),
                    )
                )
                continue
            if outcome == "redact":
                redacted += 1
                title, _, content = str(getattr(verdict, "text", "")).partition("\n")
                hit = hit.model_copy(update={"title": title, "content": content})
            kept.append(hit)
        return WebSearchResponse(
            query=query,
            results=tuple(kept),
            provider=provider,
            cached=cached,
            blocked=tuple(blocked),
            redacted=redacted,
        )

    async def _degraded(
        self,
        query: str,
        status: WebSearchStatus,
        reason: str,
        emitter: object | None,
        *,
        exc_info: BaseException | None = None,
    ) -> WebSearchResponse:
        """Log at ERROR, emit the event, and return an explicitly degraded response."""
        logger.error("Web search degraded to internal-only: %s", reason, exc_info=exc_info)
        response = WebSearchResponse(
            query=query, provider=self.provider, status=status, reason=reason
        )
        await self._emit(emitter, response)
        return response

    def _cache_get(self, key: str) -> list[WebSearchResult] | None:
        """Read the cached raw hits, failing open (a miss) on any error or corruption."""
        try:
            raw = self._cache.get(key)
        except Exception:  # noqa: BLE001 - a cache miss is safe; never fail a search on it
            logger.warning(
                "Web-search cache read failed; treating as a miss (fail-open).",
                exc_info=True,
            )
            return None
        if raw is None:
            return None
        try:
            return list(CachedWebResults.model_validate_json(raw).results)
        except Exception:  # noqa: BLE001 - a bad entry recomputes rather than breaking
            logger.warning("Discarding a corrupt web-search cache entry.", exc_info=True)
            return None

    def _cache_set(self, key: str, hits: list[WebSearchResult]) -> None:
        """Store the provider's **raw** hits, swallowing any error (fail-open).

        Raw, not screened, and without the query. The cache is tenant-less by design
        — the same public page for everybody who asks — so the value may only contain
        things that are the same for everybody. The query is one tenant's, and so is
        the guardrail verdict: ``guardrails.denylist.terms`` and
        ``guardrails.pii.entities`` are UNION-merged per tenant, so a cached verdict
        let whichever tenant searched first decide what the next one could see, in
        both directions.

        **What this costs.** A warm query still costs zero provider calls — the
        expensive, rate-limited, wifi-dependent part, and the one the phase-05 budget
        arithmetic is about. It no longer costs zero *classifier* calls: the rail runs
        over every hit on every read. That is the price of the tenant's own rails
        actually being the ones applied to their own results, and it is not
        negotiable at the price of a rail that does not run.
        """
        try:
            self._cache.set(
                key, CachedWebResults(results=tuple(hits)).model_dump_json(), ttl=self._ttl
            )
        except Exception:  # noqa: BLE001 - a failed write just means no hit later
            logger.warning(
                "Web-search cache write failed; continuing uncached (fail-open).",
                exc_info=True,
            )

    @staticmethod
    async def _emit(emitter: object | None, response: WebSearchResponse) -> None:
        """Emit the ``web_search`` CustomEvent (no-op without an emitter).

        Carries the outcome and the counts, never the page text and never the query's
        results — enough for the console to show a degraded search as degraded.
        """
        if emitter is None:
            return
        from aegis.core import stream_names

        custom = getattr(emitter, "custom", None)
        if custom is None:
            return
        await custom(
            stream_names.WEB_SEARCH,
            {
                "provider": response.provider,
                "status": response.status.value,
                "degraded": response.degraded,
                "cached": response.cached,
                "results": len(response.results),
                "blocked": len(response.blocked),
                "redacted": response.redacted,
                "reason": response.reason,
            },
        )
