"""A deliberately mis-wired adapter — the fixture the conformance suite is proved against.

Every break in this package is a *plausible first attempt*, not a caricature: a third
specialist added to the roster without a graph node, a tool registered before anyone
decided its risk tier, an allowlist entry with a typo, an ML feature list spelled
``FEATURE_COLUMNS``, a playbook renamed without touching the selector that names it.
None of them raises anything at import, at startup or on a query — which is the whole
argument for an executable conformance suite, and why this fixture exists.

It reuses ``app.adapter`` for everything that is *not* broken, so the diff between a
correct adapter and this one is exactly the list of defects being demonstrated. Twelve
of the thirteen checks fail against it; ``skills/`` is intact, so that check passes, and
a run against this fixture therefore also shows that the suite is capable of passing a
check in the middle of a wall of failures.

``generator`` is absent on purpose: it is on disk nowhere and imported nowhere, which is
the ``missing_members`` scar itself.
"""

from __future__ import annotations

from app.adapter import schema

from . import corpus, memory_spec, ml_spec, personas, prompts, roster, tools

DOMAIN_ID = "broken_demo"
DOMAIN_DESCRIPTION = "Support stuff."
"""Too vague to be a topical rail — which is what it is wired up as."""

__all__ = [
    "DOMAIN_DESCRIPTION",
    "DOMAIN_ID",
    "corpus",
    "memory_spec",
    "ml_spec",
    "personas",
    "prompts",
    "roster",
    "schema",
    "tools",
]
