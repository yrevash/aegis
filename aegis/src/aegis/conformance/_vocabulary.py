"""The reference domain's vocabulary, quarantined — and the scanner that keeps it out.

The central promise of this platform is *"only the adapter changes"*. Every other
conformance check reads the adapter and asks whether the **domain** is wired correctly.
This one reads the **core** and asks the opposite question: does any module outside the
adapter still know the shipped domain's words?

It exists because a fresh agent, handed only this repository and a one-line problem
statement, retargeted the platform to a different domain and produced an integration
that looked correct and was broken in four places at once — a persona id decided by an
``if`` in the login path (so every sign-in raised ``KeyError``), a record collection read
by attribute name in the forecast module (``AttributeError``), a client-facing chart
title frozen as a core constant (wrong on screen forever), and an ML sanity probe built
from a literal feature row (which then reported ``distinct=False`` on a *correct*
integration). Not one of them was caught by the adapter suite, the agent suite, the
conformance suite or ruff, because all four are the same defect wearing four hats: **a
string of the shipped domain's, living in a core module.**

Why a hardcoded list is the right shape here. The core cannot derive "the old
vocabulary" from a retargeted adapter — the old adapter is gone. So the words are
frozen here, as *data the check is quarantining*, never as behaviour: nothing in the
platform reads this module, and a term listed here is a term the core is forbidden to
contain. That is the inverse of domain knowledge leaking into the core; it is the list
of exactly what may not.

**The check is unconditional.** It is not "fails only after a retarget": the reference
adapter itself must keep the core clean, which is the only way the promise is true
*before* anybody retargets. :data:`SHIPPED_DOMAIN_ID` is used for one thing — when the
reference adapter is the one loaded, every term below must still be found somewhere
inside it, so a stale entry that no longer means anything is a failure rather than a
line of decoration.
"""

from __future__ import annotations

from pathlib import Path

__all__ = [
    "MIN_CORE_FILES",
    "SHIPPED_DOMAIN_ID",
    "SHIPPED_VOCABULARY",
    "core_files",
    "scan_for_terms",
]

SHIPPED_DOMAIN_ID = "service_request_management"
"""``DOMAIN_ID`` of the reference adapter the vocabulary below belongs to."""

SHIPPED_VOCABULARY: tuple[str, ...] = (
    # piece 5 — the persona ids the login path used to decide between
    "operations_lead",
    # piece 3 — the demand series' record collection and its client-facing title
    "dataset.requests",
    "Service requests opened per day",
    "num_requests",
    # piece 2 — the ML feature and target names the sanity probe used to spell out
    "queue_depth_at_open",
    "agent_tenure_months",
    "reopened_count",
    "customer_tier",
    "description_length",
    "resolution_hours",
    # piece 1 — the record types
    "ServiceRequest",
    "SupportAgent",
    # piece 4 — the action tools, once keyed by name in the host's MCP annotations
    "update_request_status",
    "assign_request",
    "add_case_note",
    # piece 10 — the playbooks, and the domain's own id
    "closing_requests",
    "de_escalation",
    SHIPPED_DOMAIN_ID,
)
"""Words that belong to the reference domain and may not appear in any core module.

Each one is specific enough that an innocent occurrence is not plausible — that is the
selection rule. A generic word ("customer", "client", "request") is deliberately absent:
this check must never be the reason somebody deletes a true sentence from a core
docstring, or its first false positive is the last time anybody believes it.
"""

MIN_CORE_FILES = 20
"""Fewest source files a real core scan can plausibly see.

The anti-vacuity floor. A check that silently finds nothing must fail, not pass — that
is the exact way the playbook-reachability check went hollow when its literals moved out
of the function it was reading.
"""

#: Directory names never scanned: build noise and test trees.
_SKIP_DIRS = frozenset({"__pycache__", "tests", "node_modules", ".venv"})

#: This package, which necessarily contains every word above and is therefore never
#: scanned. Excluded by path, not by name, so a host package that happens to hold a
#: directory called ``conformance`` is still checked.
_SELF = Path(__file__).resolve().parent


def core_files(*roots: Path, exclude: Path | None = None) -> list[Path]:
    """Return every core Python source under ``roots``, minus the adapter and skips.

    Args:
        roots: Package directories to scan.
        exclude: The adapter's own directory — the one place the vocabulary belongs.

    Returns:
        Sorted, de-duplicated source paths.
    """
    excluded = [_SELF] + ([exclude.resolve()] if exclude is not None else [])
    found: set[Path] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            resolved = path.resolve()
            if any(d == resolved or d in resolved.parents for d in excluded):
                continue
            if _SKIP_DIRS & set(resolved.parts):
                continue
            found.add(resolved)
    return sorted(found)


def scan_for_terms(files: list[Path], terms: tuple[str, ...]) -> list[tuple[str, Path, int]]:
    """Return every ``(term, file, line number)`` occurrence of ``terms`` in ``files``.

    A plain, case-sensitive substring scan on purpose: it sees the word in a docstring,
    a comment, a dict key and a string literal alike, all of which are places the leaks
    this check descends from actually lived.

    Args:
        files: Source files to read.
        terms: The forbidden words.

    Returns:
        One tuple per occurrence, in file then line order.
    """
    hits: list[tuple[str, Path, int]] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # pragma: no cover - unreadable source
            continue
        if not any(term in text for term in terms):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            hits.extend((term, path, number) for term in terms if term in line)
    return hits
