"""A deliberately mis-wired adapter — the fixture the conformance suite is proved against.

Every break in this package is a *plausible first attempt*, not a caricature: a third
specialist added to the roster without a graph node, a tool registered before anyone
decided its risk tier, an allowlist entry with a typo, an ML feature list spelled
``FEATURE_COLUMNS``, a playbook that the selector's table never names. None of them
raises anything at import, at startup or on a query — which is the whole argument for an
executable conformance suite, and why this fixture exists.

**It is self-contained, and that is load-bearing.** It used to reuse ``app.adapter`` for
everything that was not broken, which read as economical and was not: the memory break
was that the *shipped* hints named ``de_escalation`` while this directory held
``closing_cases.md``, so any correct retarget of the production adapter re-pointed those
literals and the break simply vanished — the meta-test went from ``12 failed, 1 passed``
to ``11 failed, 2 passed`` on a change that had nothing to do with it. A fixture whose
job is to prove a suite can fail must not be coupled to the code the suite exists to let
you rewrite. Nothing here imports the domain; the only imports are from ``aegis`` itself,
which is the core every adapter builds on.

``generator`` is absent on purpose: it is on disk nowhere and imported nowhere, which is
the ``missing_members`` scar itself.
"""

from __future__ import annotations

from . import corpus, memory_spec, ml_spec, personas, prompts, roster, schema, tools

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
