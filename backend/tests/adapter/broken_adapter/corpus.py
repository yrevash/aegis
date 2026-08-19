"""Two seed documents that share one id."""

from __future__ import annotations

from dataclasses import dataclass

_BODY = (
    "Work items are raised by a caller, triaged by the desk and closed once the caller "
    "confirms the outcome. An item that has had no reply for thirty days is closed with "
    "a note recording why. Every state change is a write action and goes to the "
    "approval gate before it is asserted as done."
)


@dataclass(frozen=True)
class SeedDoc:
    """One seed knowledge document."""

    id: str
    title: str
    body: str


def load_seed_corpus() -> list[SeedDoc]:
    """THE BREAK: two records sharing one id, so every citation on one resolves wrongly."""
    return [
        SeedDoc(id="kb-1", title="Lifecycle of a work item", body=_BODY),
        SeedDoc(id="kb-1", title="Closing an item", body=_BODY),
    ]
