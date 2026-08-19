"""The Tavily adapter — one implementation of :class:`WebSearchClient`, nothing more.

Everything above this file talks to the protocol. Swapping Tavily for Brave, SerpAPI
or an internal search service is a new class in this package and one line in the
composition root; nothing in the agent, the guardrails or the cache changes. That is
the whole reason the seam exists.

``tavily-python`` is an **optional extra** (``pip install aegis[websearch]``) and is
imported lazily through :func:`aegis.core.require`, so importing this module on a box
that has never installed it is free. A missing package raises an ImportError naming
the exact install command — it does not degrade silently, because the layer that
decides how to degrade is :class:`~aegis.websearch.service.WebSearch`, and it says so
out loud when it does.

**The API key was spelled ``TRAVILY_API_KEY`` in ``backend/.env``** — which is why web
search never worked: nothing was ever going to read that name. It is
``TAVILY_API_KEY`` now, in ``.env``, in ``.env.example`` and here.
"""

from __future__ import annotations

import logging
from typing import Any

from aegis.core.lazy import require
from aegis.websearch.types import WebSearchResult

logger = logging.getLogger(__name__)

#: The install target named in the ImportError when ``tavily`` is absent.
EXTRA = "aegis[websearch]"

#: The environment variable holding the Tavily key. Spelled correctly, once, here.
API_KEY_ENV = "TAVILY_API_KEY"

#: The historical misspelling that shipped in ``backend/.env``. Named so the
#: degradation message can tell an operator exactly what to rename.
LEGACY_MISSPELLED_ENV = "TRAVILY_API_KEY"


class TavilyWebSearchClient:
    """Tavily, behind :class:`~aegis.websearch.types.WebSearchClient`."""

    name = "tavily"

    def __init__(
        self,
        api_key: str,
        *,
        search_depth: str = "basic",
        client: Any = None,  # noqa: ANN401 - a test double or a pre-built SDK client
    ) -> None:
        """Create the adapter.

        Args:
            api_key: The Tavily API key. Non-empty — deciding what to do about an
                absent key belongs to :class:`~aegis.websearch.service.WebSearch`,
                which reports the degradation; this class never pretends.
            search_depth: Tavily's ``basic`` (one credit) or ``advanced`` (more
                credits, deeper crawl). ``basic`` is the default because the phase-05
                budget is real money.
            client: Advanced/test seam — a pre-built async Tavily client. When given,
                ``tavily`` is never imported.

        Raises:
            ValueError: if ``api_key`` is empty and no ``client`` was supplied.
        """
        if not client and not api_key:
            raise ValueError(
                f"TavilyWebSearchClient needs an API key. Set {API_KEY_ENV} "
                f"(note: NOT {LEGACY_MISSPELLED_ENV}, which is the historical "
                "misspelling and is read by nothing)."
            )
        self._api_key = api_key
        self._search_depth = search_depth
        self._client = client

    def _sdk(self) -> Any:  # noqa: ANN401 - the vendor SDK's own type
        """Return the async Tavily client, importing the optional extra on first use."""
        if self._client is None:
            tavily = require(EXTRA, "tavily")
            self._client = tavily.AsyncTavilyClient(api_key=self._api_key)
        return self._client

    async def search(self, query: str, *, max_results: int = 5) -> list[WebSearchResult]:
        """Return up to ``max_results`` hits for ``query``.

        Args:
            query: The search query.
            max_results: Provider result cap.

        Returns:
            The provider's hits, normalised to :class:`WebSearchResult`.

        Raises:
            Exception: whatever the SDK raises. The caller
                (:class:`~aegis.websearch.service.WebSearch`) turns a failure into a
                *reported* degradation; swallowing it here would hide it.
        """
        raw = await self._sdk().search(
            query, max_results=max_results, search_depth=self._search_depth
        )
        return [_to_result(hit) for hit in _hits(raw)]


def _hits(raw: Any) -> list[dict]:  # noqa: ANN401 - the SDK returns a plain dict
    """Pull the result list out of a Tavily payload, tolerating an empty answer."""
    if not isinstance(raw, dict):
        logger.warning("Tavily returned %s, not a dict; treating as no results.", type(raw))
        return []
    results = raw.get("results") or []
    return [hit for hit in results if isinstance(hit, dict)]


def _to_result(hit: dict) -> WebSearchResult:
    """Normalise one Tavily hit into the provider-neutral shape."""
    score = hit.get("score")
    return WebSearchResult(
        title=str(hit.get("title") or ""),
        url=str(hit.get("url") or ""),
        content=str(hit.get("content") or ""),
        score=float(score) if isinstance(score, int | float) else None,
        published_date=(
            str(hit["published_date"]) if hit.get("published_date") is not None else None
        ),
    )
