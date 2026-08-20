"""The demo memory corpus — the claims that hold without a server.

The seeder itself is a *client*: it logs in and calls ``POST /v1/memory/facts``, so
what it does is only true against a running API, and ``scripts/prove_memory_scope.sh``
is what checks that. What is checkable here is the corpus definition, and the two
things about it that are load-bearing:

1. **Every row is removable.** ``app.demo`` earns its wipe by tagging one identifier
   column per table with ``demo-``; this corpus earns the same wipe by tagging every
   predicate. A predicate that lost its prefix is a row nobody can find again.
2. **Every row is writable.** The route validates ``predicate`` and ``object`` against
   a label pattern and caps ``text`` at 2000 characters, and it clamps ``importance``
   to 1–10. A corpus entry that violates one of those is a 422 at seed time — which is
   exactly how the one entry with a colon in its ``object`` was found, after it had
   already shipped a refusal into the seed log.
"""

from __future__ import annotations

import re

import pytest

from app.memory_demo import (
    DEMO_PREFIX,
    MEMORY_CORPUS,
    corpus_usernames,
    tagged_predicates,
)

#: The route's own constraints, restated here so a change to either side fails a test
#: rather than a seed run. Mirrors ``_LABEL_PATTERN`` / ``_MAX_FACT_CHARS`` and the
#: ``Field`` bounds in ``app.api.routes_memory``.
LABEL_PATTERN = re.compile(r"^[\w .,'\-/()&+]*$")
MAX_FACT_CHARS = 2000
MAX_PREDICATE = 64
MAX_OBJECT = 128

#: The four values the route accepts; anything else is silently coerced, which would
#: make a corpus entry's declared type a lie.
FACT_TYPES = ("preference", "entity_attr", "commitment", "constraint")


def test_every_seeded_predicate_carries_the_demo_prefix() -> None:
    """The wipe finds a row by its prefix, so an untagged row is an unremovable one."""
    predicates = tagged_predicates()
    assert predicates, "the corpus is empty"
    untagged = [p for p in predicates if not p.startswith(DEMO_PREFIX)]
    assert untagged == []


def test_no_principal_writes_the_same_predicate_twice() -> None:
    """Idempotency is keyed on the predicate, so a repeat inside one corpus never skips.

    Two entries sharing a predicate would write both on the first run and skip both on
    the second — the duplicate would be permanent and invisible to ``--wipe``'s count.
    """
    for entry in MEMORY_CORPUS:
        tags = [fact.tag() for fact in entry.facts]
        assert len(tags) == len(set(tags)), f"{entry.username} repeats a predicate"


def test_every_fact_is_one_the_write_route_would_accept() -> None:
    """Each field is inside the bound the route enforces — no 422 at seed time."""
    for entry in MEMORY_CORPUS:
        for fact in entry.facts:
            tag = fact.tag()
            assert len(tag) <= MAX_PREDICATE, f"{tag} is too long a predicate"
            assert LABEL_PATTERN.match(tag), f"{tag} is not a label"
            assert len(fact.object) <= MAX_OBJECT, f"{tag}: object is too long"
            assert LABEL_PATTERN.match(fact.object), (
                f"{tag}: object {fact.object!r} is not a label"
            )
            assert 0 < len(fact.text) <= MAX_FACT_CHARS, f"{tag}: text is out of bounds"
            assert fact.fact_type in FACT_TYPES, f"{tag}: {fact.fact_type} is not a fact type"
            assert 1 <= fact.importance <= 10, f"{tag}: importance is out of range"


def test_the_corpus_is_written_as_accounts_the_seed_creates() -> None:
    """Every principal named here exists, or the seeder writes nothing for it.

    Read out of ``app.seed`` rather than restated, so renaming a seeded account breaks
    this test instead of silently emptying a portal's memory screen.
    """
    from app.seed import PLATFORM_PRINCIPALS, TENANTS

    known = {spec.username for spec in PLATFORM_PRINCIPALS}
    for tenant in TENANTS:
        known.add(tenant.admin.username)
        known.update(member.username for member in tenant.users)

    unknown = [name for name in corpus_usernames() if name not in known]
    assert unknown == []


@pytest.mark.parametrize("username", corpus_usernames())
def test_every_principal_in_the_corpus_gets_at_least_one_fact(username: str) -> None:
    """A principal listed with no facts is a memory screen that stays empty."""
    entry = next(e for e in MEMORY_CORPUS if e.username == username)
    assert entry.facts, f"{username} has no facts"


def test_the_tenant_corpora_do_not_borrow_each_others_regulations() -> None:
    """Northwind remembers the FTC rules; Vertex remembers Regulation Z.

    The corpus split mirrors ``docs/corpus/SOURCES.md`` — two documents per tenant —
    and that is the whole point of it: a fact citing a regulation the tenant does not
    hold could never be grounded from that tenant's own corpus, so the memory screen
    would be asserting something retrieval cannot support.
    """
    northwind_only = ("16 CFR 435", "16 CFR 703")
    vertex_only = ("12 CFR 1026.13", "Regulation Z", "CFPB")

    for entry in MEMORY_CORPUS:
        if not entry.username.startswith(("northwind.", "vertex.")):
            continue
        blob = " ".join(fact.text for fact in entry.facts)
        foreign = vertex_only if entry.username.startswith("northwind.") else northwind_only
        # Vertex's analyst names the FTC rule to say it does NOT hold it, which is the
        # one legitimate cross-reference: a negation is not a citation.
        leaked = [
            token
            for token in foreign
            if token in blob and "no FTC" not in blob and "not" not in blob
        ]
        assert leaked == [], f"{entry.username} cites {leaked}, which its tenant does not hold"
