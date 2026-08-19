"""The record types, stamped with a version — the one piece this fixture gets right."""

from __future__ import annotations

from dataclasses import dataclass

SCHEMA_VERSION = "0.1.0"


@dataclass(frozen=True)
class WorkItem:
    """One record of the fixture's invented world."""

    id: str
    state: str
    urgency: str
