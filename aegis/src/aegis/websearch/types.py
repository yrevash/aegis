"""The web-search contract: what a result is, and what a search run reports about itself.

The rest of the system depends on these types and on :class:`WebSearchClient`, never
on Tavily. Tavily is one implementation of the protocol
(:mod:`aegis.websearch.tavily`); a fake in a test is another, and neither is special.

Every field on :class:`WebSearchResponse` beyond ``results`` exists so a caller can
tell the three outcomes apart that used to look identical from the outside: a search
that ran and found nothing, a search that never ran because there was no API key, and
a search whose content was blocked by the tool-result rail. Reporting only ``[]``
collapses all three into "no evidence", which is the silent-control defect this
codebase keeps paying for.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field


class WebSearchStatus(StrEnum):
    """How a search run actually ended."""

    #: The provider was called (or its cached answer reused) and returned results.
    OK = "ok"
    #: No API key was configured. Nothing external was called; the caller must treat
    #: this as "we have no web evidence", never as "the web had nothing".
    DEGRADED_NO_KEY = "degraded_no_key"
    #: The provider was configured but the call failed (network, auth, rate limit).
    DEGRADED_ERROR = "degraded_error"

    @property
    def degraded(self) -> bool:
        """Whether this status means no live external evidence was obtained."""
        return self is not WebSearchStatus.OK


class WebSearchResult(BaseModel):
    """One search hit, provider-neutral."""

    title: str = ""
    url: str = ""
    content: str = ""
    score: float | None = None
    published_date: str | None = None


class BlockedResult(BaseModel):
    """A hit the ``TOOL_RESULT`` rail refused to let into an agent's context.

    Carries the verdict and the rail that fired, and deliberately **not** the
    content: the whole point is that the text does not travel. The URL is kept
    because an operator needs to know which page attacked them.
    """

    url: str = ""
    title: str = ""
    layer: str | None = None
    reason: str = ""


class WebSearchResponse(BaseModel):
    """The result of one search, with its own provenance attached."""

    query: str
    results: tuple[WebSearchResult, ...] = ()
    provider: str = "none"
    status: WebSearchStatus = WebSearchStatus.OK
    cached: bool = False
    #: Human-readable reason the run degraded. Non-empty iff ``status.degraded``.
    reason: str = ""
    #: Hits the tool-result rail blocked. Non-empty means an injection (or other
    #: rail hit) was caught in third-party content and never reached the model.
    blocked: tuple[BlockedResult, ...] = ()
    #: Hits whose text the rail rewrote (PII redaction). Counted, not itemised.
    redacted: int = Field(default=0, ge=0)

    @property
    def degraded(self) -> bool:
        """Whether this response carries no live external evidence."""
        return self.status.degraded


@runtime_checkable
class WebSearchClient(Protocol):
    """A web search provider. The seam the platform actually depends on."""

    #: Stable provider id, stamped onto every response and every cache key.
    name: str

    async def search(self, query: str, *, max_results: int) -> list[WebSearchResult]:
        """Return up to ``max_results`` hits for ``query``, or raise on failure."""
        ...


@runtime_checkable
class ToolResultScreen(Protocol):
    """The tool-result guardrail, as this module needs it.

    Structural, so :class:`aegis.guardrails.Guardrails` satisfies it without
    ``aegis.websearch`` importing the guardrails package (and without a test having
    to build a full pipeline to exercise the search path).
    """

    async def check_tool_result(
        self, text: str, *, tool_name: str | None = None
    ) -> object:
        """Screen ``text``; return a ``GuardResult``-shaped verdict."""
        ...
