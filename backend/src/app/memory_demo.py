"""The demo memory corpus — durable facts, written through the platform's own write path.

Every other demo surface had rows and the memory screens had none, so three portals
rendered an honest-but-empty record. This module fills them, and it fills them the way
a person would: by calling ``POST /v1/memory/facts`` as each principal, over HTTP, with
that principal's own token.

**Why HTTP and not SQL, or even ``aegis.memory.crud``.** The interesting part of a
memory write is not the INSERT. It is everything the route does before the INSERT:
``_resolve_subject`` refuses any subject the caller does not manage, ``_screened`` runs
the input rail so a written fact cannot smuggle an instruction into a future prompt,
``_embed`` asks the live gateway for a vector, and ``_safe_audit`` records who wrote
what about whom. A seeder that goes around all of that proves nothing about the
subsystem it is seeding — and this corpus exists precisely to make the scoping
demonstrable. So the seeder is a *client*: it logs in, it is refused where a person
would be refused, and it writes only where a person could.

**Tagging follows :mod:`app.demo`.** That module tags a row by prefixing an identifier
column with ``demo-`` and removes the corpus with one ``LIKE 'demo-%'`` per table. The
memory row's identifier-shaped column reachable from the write path is ``predicate``
(``_LABEL_PATTERN`` in :mod:`app.api.routes_memory` already constrains it to a label
rather than prose), so that is where the prefix goes. ``--wipe`` then removes exactly
the tagged rows, through ``DELETE /v1/memory/facts/{id}`` — the same authorised path,
never a bulk delete that would also take a real person's real memory with it.

**What this corpus does *not* claim.** There is no platform- or tenant-wide memory
bucket in this subsystem, and this module does not pretend otherwise. ``recall()``
filters ``subject_id`` **and** ``tenant_id`` on every arm, and ``memory_subject_for``
composes exactly one subject shape — ``user:<id>``. A memory is therefore private to
one person by construction. What *is* scoped wider is the **reach over those
records**: a plain principal manages one subject, a tenant admin manages every subject
inside its own tenant, and a platform admin every tenant's. That is the contrast this
corpus is arranged to show, and ``scripts/prove_memory_scope.sh`` shows it against a
live server rather than asserting it here.

Run it::

    AEGIS_DEMO_DATA=1 python -m app.memory_demo          # write
    python -m app.memory_demo --wipe                     # remove, no flag needed
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

#: Environment gate, shared with :mod:`app.demo`. Fabricated data is written only when
#: a human asks for it by name.
DEMO_ENV = "AEGIS_DEMO_DATA"

#: The tag. Prefixed onto every seeded fact's ``predicate``, exactly as
#: :data:`app.demo.DEMO_PREFIX` is prefixed onto every seeded run id.
DEMO_PREFIX = "demo-"

#: Where the API lives when nothing says otherwise.
DEFAULT_BASE_URL = "http://localhost:8110"

#: The seed password. Same default as :func:`app.seed.seed_password`.
DEFAULT_PASSWORD = "demo"


# ─────────────────────────────────────────────────────────────────────────────
# The corpus
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class FactSpec:
    """One durable fact, in the shape ``POST /v1/memory/facts`` accepts.

    Attributes:
        predicate: The label, **without** the ``demo-`` prefix — :meth:`tag` adds it.
        object: The value the predicate takes, as a short label.
        text: The sentence the assembler will inject into a future prompt.
        fact_type: One of ``preference`` / ``entity_attr`` / ``commitment`` /
            ``constraint``; anything else is silently coerced by the route.
        importance: 1–10, the recall ranker's weight.
    """

    predicate: str
    object: str
    text: str
    fact_type: str = "entity_attr"
    importance: int = 5

    def tag(self) -> str:
        """Return the tagged predicate — the thing ``--wipe`` matches on."""
        return f"{DEMO_PREFIX}{self.predicate}"

    def payload(self) -> dict[str, object]:
        """Return the request body for this fact, written to the caller's own record."""
        return {
            "text": self.text,
            "fact_type": self.fact_type,
            "predicate": self.tag(),
            "object": self.object,
            "importance": self.importance,
        }


@dataclass(frozen=True, slots=True)
class SubjectCorpus:
    """The facts one principal holds about itself.

    Attributes:
        username: The seeded login this corpus is written **as**, so every row is
            authored by the person it is about and no write needs another's reach.
        facts: The facts, in write order.
    """

    username: str
    facts: tuple[FactSpec, ...]


#: The corpus.
#:
#: Content is drawn from the four documents in ``docs/corpus`` — 16 CFR 435, 16 CFR
#: 703, 12 CFR 1026.13 and the CFPB complaint breakdown — and split the same way they
#: are: Northwind holds the FTC merchandise and dispute rules, Vertex holds Regulation
#: Z and the CFPB material. A Northwind principal therefore remembers deadlines that
#: only its own tenant's documents can support, which is what makes the isolation
#: legible on the screen rather than only in a test.
MEMORY_CORPUS: tuple[SubjectCorpus, ...] = (
    SubjectCorpus(
        username="northwind.admin",
        facts=(
            FactSpec(
                predicate="sla-dispute",
                object="40 days",
                text=(
                    "Northwind's dispute desk runs under 16 CFR 703: intake, "
                    "investigation, and a decision inside 40 days of the notice."
                ),
                fact_type="constraint",
                importance=9,
            ),
            FactSpec(
                predicate="sla-refund",
                object="7 working days",
                text=(
                    "A refund on a cancelled mail, internet or telephone order goes "
                    "out within seven working days, per 16 CFR 435."
                ),
                fact_type="constraint",
                importance=9,
            ),
            FactSpec(
                predicate="approval-threshold",
                object="USD 500",
                text=(
                    "Any refund above USD 500 stops at the human approval gate for "
                    "the whole of the pilot."
                ),
                fact_type="constraint",
                importance=8,
            ),
            FactSpec(
                # No colon in `object`: `_LABEL_PATTERN` admits letters, digits and a
                # little punctuation and nothing else, which is what keeps the two
                # label fields shaped like labels rather than like prose.
                predicate="desk-hours",
                object="0900 to 1800 IST",
                # "The Northwind case desk is staffed…" was written first, and the PII
                # rail read the proper noun in that position as a person and redacted
                # it. The rail is not wrong to be cautious; the sentence is what
                # changed, because a seeded corpus that reads as [REDACTED_PERSON] on
                # screen teaches the wrong thing about the subsystem.
                text=(
                    "The case desk is staffed 09:00 to 18:00 IST; the 40-day decision "
                    "clock is counted in calendar days regardless."
                ),
                importance=4,
            ),
        ),
    ),
    SubjectCorpus(
        username="northwind.analyst",
        facts=(
            FactSpec(
                predicate="eval-scope",
                object="16 CFR 435 and 703",
                text=(
                    "Retrieval is scored against the two Northwind regulations only. "
                    "A hit from another tenant's corpus is a failure, not recall."
                ),
                fact_type="constraint",
                importance=8,
            ),
            FactSpec(
                predicate="rail-policy",
                object="grounding on",
                text=(
                    "The grounding rail stays on for every refund answer: an "
                    "ungrounded deadline is worse than a refusal."
                ),
                fact_type="preference",
                importance=7,
            ),
            FactSpec(
                predicate="report-shape",
                object="per-metric delta",
                text=(
                    "Eval regressions are wanted as per-metric deltas against the "
                    "last release, never as one blended score."
                ),
                fact_type="preference",
                importance=5,
            ),
        ),
    ),
    SubjectCorpus(
        username="northwind.client",
        facts=(
            FactSpec(
                predicate="answer-shape",
                object="deadline first",
                text=(
                    "Refund cases are wanted with the deadline stated first: the ship "
                    "date, then the seven-working-day refund clock."
                ),
                fact_type="preference",
                importance=7,
            ),
            FactSpec(
                predicate="goodwill-limit",
                object="USD 50",
                text=(
                    "No goodwill credit above USD 50 is issued from this desk without "
                    "the tenant administrator."
                ),
                fact_type="constraint",
                importance=8,
            ),
            FactSpec(
                predicate="open-commitment",
                object="40-day backlog",
                text=(
                    "Owes a backlog review of every dispute case still open past its "
                    "40-day decision deadline."
                ),
                fact_type="commitment",
                importance=6,
            ),
            FactSpec(
                predicate="desk",
                object="returns",
                text=(
                    "Works the Northwind returns desk, so every case opened here is a "
                    "merchandise-order case under 16 CFR 435."
                ),
                importance=5,
            ),
        ),
    ),
    SubjectCorpus(
        username="vertex.admin",
        facts=(
            FactSpec(
                predicate="sla-billing-error",
                object="2 cycles, 90 days",
                text=(
                    "Vertex resolves billing errors under 12 CFR 1026.13: acknowledge "
                    "within 30 days, resolve within two complete billing cycles and "
                    "never later than 90 days."
                ),
                fact_type="constraint",
                importance=9,
            ),
            FactSpec(
                predicate="approval-threshold",
                object="USD 1,000",
                text=(
                    "A dispute touching more than USD 1,000 is escalated to a named "
                    "reviewer before anything is credited."
                ),
                fact_type="constraint",
                importance=8,
            ),
            FactSpec(
                predicate="review-cadence",
                object="monthly",
                text=(
                    "Complaint volume is reviewed monthly against the CFPB product "
                    "and issue breakdown."
                ),
                fact_type="commitment",
                importance=5,
            ),
        ),
    ),
    SubjectCorpus(
        username="vertex.analyst",
        facts=(
            FactSpec(
                predicate="eval-scope",
                object="Reg Z and CFPB",
                text=(
                    "Evaluation runs against Regulation Z and the CFPB complaint "
                    "breakdown only; Vertex holds no FTC merchandise rule."
                ),
                fact_type="constraint",
                importance=8,
            ),
            FactSpec(
                predicate="known-weakness",
                object="table chunks",
                text=(
                    "The CFPB fact sheet is mostly tables, so the table-chunk path is "
                    "where recall breaks first and is watched on every ingest."
                ),
                importance=6,
            ),
        ),
    ),
    SubjectCorpus(
        username="vertex.client",
        facts=(
            FactSpec(
                predicate="sla-notice",
                object="60 days",
                text=(
                    "A consumer has 60 days after the statement was sent to deliver a "
                    "billing-error notice; a case opened later is out of window."
                ),
                fact_type="constraint",
                importance=9,
            ),
            FactSpec(
                predicate="answer-shape",
                object="cite Reg Z",
                text=(
                    "Disputes are wanted with the Regulation Z citation attached, not "
                    "paraphrased away."
                ),
                fact_type="preference",
                importance=7,
            ),
            FactSpec(
                predicate="close-rule",
                object="ack on file",
                text=(
                    "No billing-error case is closed before the acknowledgement is on "
                    "file for it."
                ),
                fact_type="constraint",
                importance=7,
            ),
        ),
    ),
    SubjectCorpus(
        username="admin",
        facts=(
            FactSpec(
                predicate="tenant-map",
                object="two pilots",
                text=(
                    "Two pilots are live: Northwind on the FTC merchandise and "
                    "dispute rules, Vertex on Regulation Z and the CFPB breakdown."
                ),
                importance=7,
            ),
            FactSpec(
                predicate="receipt-policy",
                object="cite the source",
                text=(
                    "Every service-level figure shown on a screen carries the "
                    "regulation or the table it was read from."
                ),
                fact_type="preference",
                importance=6,
            ),
        ),
    ),
)


def corpus_usernames() -> tuple[str, ...]:
    """Return the logins this corpus writes as, in order."""
    return tuple(entry.username for entry in MEMORY_CORPUS)


def tagged_predicates() -> tuple[str, ...]:
    """Return every tagged predicate the corpus writes, in write order."""
    return tuple(fact.tag() for entry in MEMORY_CORPUS for fact in entry.facts)


# ─────────────────────────────────────────────────────────────────────────────
# Summaries
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class SeedSummary:
    """What a seed run wrote, skipped and could not do."""

    written: dict[str, int] = field(default_factory=dict)
    skipped: dict[str, int] = field(default_factory=dict)
    refused: list[str] = field(default_factory=list)
    redacted: list[str] = field(default_factory=list)
    unembedded: int = 0

    @property
    def total_written(self) -> int:
        """Total facts written by this run."""
        return sum(self.written.values())

    def lines(self) -> list[str]:
        """Return one human line per principal, plus the honest caveats."""
        out: list[str] = []
        for username in corpus_usernames():
            wrote = self.written.get(username, 0)
            skip = self.skipped.get(username, 0)
            if wrote or skip:
                out.append(f"  {username:<20} {wrote} written, {skip} already present")
        for note in self.refused:
            out.append(f"  REFUSED  {note}")
        for note in self.redacted:
            out.append(f"  REDACTED {note}")
        if self.unembedded:
            out.append(
                f"  {self.unembedded} fact(s) stored without a vector — the embedding "
                "gateway refused; recall reaches them by recency only"
            )
        return out


@dataclass(slots=True)
class WipeSummary:
    """What a wipe run removed."""

    deleted: dict[str, int] = field(default_factory=dict)
    refused: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        """Total facts deleted."""
        return sum(self.deleted.values())

    def lines(self) -> list[str]:
        """Return one human line per principal."""
        out = [
            f"  {username:<20} {count} removed"
            for username, count in self.deleted.items()
            if count
        ]
        out.extend(f"  REFUSED  {note}" for note in self.refused)
        return out


# ─────────────────────────────────────────────────────────────────────────────
# The client
# ─────────────────────────────────────────────────────────────────────────────


def demo_enabled() -> bool:
    """Whether the environment has asked for fabricated data by name."""
    return os.environ.get(DEMO_ENV) == "1"


class MemoryDemoError(RuntimeError):
    """The API refused something the seeder cannot proceed without."""


async def _login(client: httpx.AsyncClient, username: str, password: str) -> str:
    """Return a bearer token for ``username``.

    Raises:
        MemoryDemoError: If the credentials are refused.
    """
    response = await client.post(
        "/v1/auth/login", json={"username": username, "password": password}
    )
    if response.status_code != 200:
        raise MemoryDemoError(
            f"login refused for {username!r}: {response.status_code} {response.text[:200]}"
        )
    return str(response.json()["token"])


async def _self_subject(client: httpx.AsyncClient, token: str) -> str:
    """Return the caller's own subject key, as the server composes it.

    Read from ``GET /v1/memory/subjects`` rather than composed here. The subject shape
    is the isolation key, and a seeder that builds its own would be asserting the very
    thing the subsystem is supposed to decide.

    Raises:
        MemoryDemoError: If this sign-in is backed by no memory record at all.
    """
    response = await client.get(
        "/v1/memory/subjects", headers={"authorization": f"Bearer {token}"}
    )
    response.raise_for_status()
    subject = response.json().get("self_subject")
    if not subject:
        raise MemoryDemoError("this sign-in has no memory record of its own")
    return str(subject)


async def _own_facts(
    client: httpx.AsyncClient, token: str, subject: str, *, include_invalid: bool = False
) -> list[dict[str, object]]:
    """Return the caller's own facts — the idempotency check and the wipe's worklist."""
    response = await client.get(
        "/v1/memory/facts",
        params={"subject": subject, "include_invalid": str(include_invalid).lower()},
        headers={"authorization": f"Bearer {token}"},
    )
    response.raise_for_status()
    rows = response.json().get("rows", [])
    return [row for row in rows if isinstance(row, dict)]


async def seed_memory(
    *, base_url: str = DEFAULT_BASE_URL, password: str = DEFAULT_PASSWORD
) -> SeedSummary:
    """Write the corpus, one principal at a time, through ``POST /v1/memory/facts``.

    Idempotent: a fact whose tagged predicate is already on the principal's record is
    skipped rather than duplicated, so a second run writes nothing.

    Args:
        base_url: Where the API is listening.
        password: The seed password every principal was created with.

    Returns:
        What was written, skipped and refused.
    """
    summary = SeedSummary()
    async with httpx.AsyncClient(base_url=base_url, timeout=180.0) as client:
        for entry in MEMORY_CORPUS:
            try:
                token = await _login(client, entry.username, password)
            except MemoryDemoError as exc:
                summary.refused.append(str(exc))
                continue

            headers = {"authorization": f"Bearer {token}"}
            try:
                subject = await _self_subject(client, token)
                existing = {
                    str(row.get("predicate", ""))
                    for row in await _own_facts(client, token, subject)
                }
            except (httpx.HTTPStatusError, MemoryDemoError) as exc:
                summary.refused.append(
                    f"{entry.username}: cannot read its own record ({exc}) — nothing written"
                )
                continue

            for fact in entry.facts:
                if fact.tag() in existing:
                    summary.skipped[entry.username] = summary.skipped.get(entry.username, 0) + 1
                    continue
                response = await client.post(
                    "/v1/memory/facts", json=fact.payload(), headers=headers
                )
                if response.status_code != 200:
                    summary.refused.append(
                        f"{entry.username} · {fact.tag()}: "
                        f"{response.status_code} {response.text[:160]}"
                    )
                    continue
                body = response.json()
                summary.written[entry.username] = summary.written.get(entry.username, 0) + 1
                if body.get("verdict") == "redact":
                    summary.redacted.append(f"{entry.username} · {fact.tag()}")
                if not body.get("embedded", True):
                    summary.unembedded += 1
    return summary


async def wipe_memory(
    *, base_url: str = DEFAULT_BASE_URL, password: str = DEFAULT_PASSWORD
) -> WipeSummary:
    """Delete every ``demo-``-tagged fact, through the authorised delete route.

    Deliberately per-fact and per-principal rather than one bulk statement: the tag is
    what identifies a seeded row, and a principal deleting from its own record is the
    only reach this needs. A real person's real memory is never in the worklist.

    Args:
        base_url: Where the API is listening.
        password: The seed password every principal was created with.

    Returns:
        What was removed.
    """
    summary = WipeSummary()
    async with httpx.AsyncClient(base_url=base_url, timeout=180.0) as client:
        for entry in MEMORY_CORPUS:
            try:
                token = await _login(client, entry.username, password)
            except MemoryDemoError as exc:
                summary.refused.append(str(exc))
                continue
            headers = {"authorization": f"Bearer {token}"}
            try:
                subject = await _self_subject(client, token)
                rows = await _own_facts(client, token, subject, include_invalid=True)
            except (httpx.HTTPStatusError, MemoryDemoError) as exc:
                summary.refused.append(f"{entry.username}: cannot read its own record ({exc})")
                continue
            removed = 0
            for row in rows:
                predicate = str(row.get("predicate", ""))
                if not predicate.startswith(DEMO_PREFIX):
                    continue
                fact_id = row.get("id")
                response = await client.delete(
                    f"/v1/memory/facts/{fact_id}", headers=headers
                )
                if response.status_code == 200:
                    removed += 1
                else:
                    summary.refused.append(
                        f"{entry.username} · fact {fact_id}: {response.status_code}"
                    )
            summary.deleted[entry.username] = removed
    return summary


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def _parser() -> argparse.ArgumentParser:
    """Return the argument parser for ``python -m app.memory_demo``."""
    parser = argparse.ArgumentParser(
        prog="python -m app.memory_demo",
        description=(
            "Write (or remove) the demo memory corpus: durable service-request facts "
            "for every seeded principal, each written as that principal through "
            "POST /v1/memory/facts, every predicate tagged 'demo-'."
        ),
    )
    parser.add_argument(
        "--wipe",
        action="store_true",
        help=(
            "Delete every demo-tagged fact and report what was removed. Deliberately "
            f"does NOT require {DEMO_ENV}: the removal path must not fail because a "
            "variable was not exported."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("AEGIS_API_BASE", DEFAULT_BASE_URL),
        help=f"Where the API is listening (default {DEFAULT_BASE_URL}).",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("AEGIS_SEED_PASSWORD", DEFAULT_PASSWORD),
        help="The seed password every principal was created with.",
    )
    return parser


async def _run(argv: Sequence[str] | None = None) -> int:
    """Seed or wipe, print what happened, and return the exit code."""
    args = _parser().parse_args(argv)
    try:
        if args.wipe:
            wiped = await wipe_memory(base_url=args.base_url, password=args.password)
            verb = "removed" if wiped.total else "nothing to remove"
            print(f"Aegis demo memory {verb}")
            for line in wiped.lines():
                print(line)
            return 0

        if not demo_enabled():
            print(
                f"REFUSED  {DEMO_ENV} is not '1'.\n"
                "  The memory corpus is fabricated data. It is written only when a "
                "human asks for it by name:\n"
                f"    {DEMO_ENV}=1 python -m app.memory_demo\n"
                "  Removing it never needs the flag:  python -m app.memory_demo --wipe",
                file=sys.stderr,
            )
            return 2

        summary = await seed_memory(base_url=args.base_url, password=args.password)
    except httpx.HTTPError as exc:
        print(f"MEMORY DEMO FAILED  API unreachable at {args.base_url}: {exc}", file=sys.stderr)
        return 1
    except MemoryDemoError as exc:
        print(f"MEMORY DEMO FAILED  {exc}", file=sys.stderr)
        return 1

    verb = (
        f"seeded {summary.total_written} facts"
        if summary.total_written
        else "already seeded — nothing to do"
    )
    print(f"Aegis demo memory {verb}")
    for line in summary.lines():
        print(line)
    if summary.total_written:
        print(
            f"  every predicate carries the {DEMO_PREFIX!r} prefix; "
            "remove it all with `python -m app.memory_demo --wipe`"
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: run the memory seeder (or the wipe) and return its exit code."""
    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(_run(argv))


if __name__ == "__main__":  # pragma: no cover - CLI
    raise SystemExit(main())
