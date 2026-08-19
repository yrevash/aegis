"""The Tavily adapter — normalisation and the optional-extra discipline.

Never a real network call: the SDK client is injected. That is the point of the seam.
"""

from __future__ import annotations

import pytest

from aegis.websearch import TavilyWebSearchClient
from aegis.websearch.tavily import API_KEY_ENV, LEGACY_MISSPELLED_ENV


class FakeSdk:
    """Stands in for ``tavily.AsyncTavilyClient`` — no network, no package needed."""

    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def search(self, query, *, max_results, search_depth):
        self.calls.append((query, max_results, search_depth))
        return self.payload


async def test_hits_are_normalised_to_the_provider_neutral_shape():
    sdk = FakeSdk(
        {
            "results": [
                {
                    "title": "A",
                    "url": "https://example.com/a",
                    "content": "body",
                    "score": 0.91,
                    "published_date": "2026-08-01",
                }
            ]
        }
    )
    hits = await TavilyWebSearchClient("k", client=sdk).search("q", max_results=3)

    assert len(hits) == 1
    assert hits[0].url == "https://example.com/a"
    assert hits[0].score == pytest.approx(0.91)
    assert hits[0].published_date == "2026-08-01"
    assert sdk.calls == [("q", 3, "basic")]


async def test_basic_search_depth_is_the_default_because_credits_are_real():
    sdk = FakeSdk({"results": []})
    await TavilyWebSearchClient("k", client=sdk).search("q", max_results=1)
    assert sdk.calls[0][2] == "basic"
    sdk2 = FakeSdk({"results": []})
    await TavilyWebSearchClient("k", client=sdk2, search_depth="advanced").search(
        "q", max_results=1
    )
    assert sdk2.calls[0][2] == "advanced"


async def test_a_missing_or_odd_payload_is_survived_not_crashed_on():
    assert await TavilyWebSearchClient("k", client=FakeSdk({})).search("q") == []
    assert await TavilyWebSearchClient("k", client=FakeSdk(None)).search("q") == []
    sdk = FakeSdk({"results": ["not a dict", {"url": "https://e/x"}]})
    hits = await TavilyWebSearchClient("k", client=sdk).search("q")
    assert [h.url for h in hits] == ["https://e/x"]


def test_an_empty_key_raises_here_rather_than_pretending():
    """Deciding how to degrade belongs to WebSearch, which reports it. Not to this."""
    with pytest.raises(ValueError, match=API_KEY_ENV):
        TavilyWebSearchClient("")


def test_the_error_names_the_misspelling_that_caused_the_outage():
    with pytest.raises(ValueError, match=LEGACY_MISSPELLED_ENV):
        TavilyWebSearchClient("")


def test_the_env_var_names_are_what_the_dotenv_files_say():
    assert API_KEY_ENV == "TAVILY_API_KEY"
    assert LEGACY_MISSPELLED_ENV == "TRAVILY_API_KEY"


def test_the_provider_name_is_stable_because_cache_keys_depend_on_it():
    assert TavilyWebSearchClient("k", client=FakeSdk({})).name == "tavily"


def test_the_vendor_sdk_is_not_imported_just_by_importing_the_adapter():
    """`tavily-python` is an optional extra; the import graph must not need it."""
    import sys

    assert "tavily" not in sys.modules
