"""The span-anchored gold set: schema, loader, content hash, and the hit rule.

## Why the anchor is a span and not a chunk id

A naive baseline chunks a corpus into fixed word windows. This pipeline chunks it on
structural boundaries, and a table is its own chunk. **Their chunk ids are not
comparable**, so a gold set keyed on ``chunk_id`` cannot grade the two against the same
ground truth — which is the single most common way a published RAG ablation quietly stops
measuring anything.

So gold truth is a **verbatim answer span in the source document**. A retrieved chunk is a
hit iff that span appears in it, under
:func:`~aegis.retrieval.citations.span_present`. Three things fall out for free:

1. Every arm is graded against identical ground truth however it chunks or parses.
2. The set survives a re-ingest, a chunker change and a parser swap. It does not need
   rebuilding when Docling moves.
3. The same containment check *is* citation verification — one primitive, two uses.

## The straddle case, which is the one real weakness

A span can sit across a chunk boundary in one arm and inside a chunk in another, which
would score the first arm 0 for a chunking decision rather than for a retrieval failure.
Two mitigations, both load-bearing: spans are **a single sentence and at most
:data:`MAX_SPAN_WORDS` words**, taken verbatim; and a case whose span is in *no* chunk of
*any* arm is reported as **ungradeable and dropped**, counted in the report, never scored
zero against one arm. :func:`gradeable` is that filter.

## What is in the file, and why ``kind`` matters

``kind`` is what lets the report say "31 known-item, 12 table, 5 multi-hop, 5
unanswerable" instead of "53 cases", and it is what makes the stratified subsets honest —
the table subset is where structure-aware ingestion should show up, the multi-hop subset
is the only place the graph arm can earn its keep, and the unanswerable subset is not
graded for retrieval at all (it measures refusal, which is an answer-side property).

The shipped set is :data:`FIXTURE_GOLD_SET_PATH`, built over the four PDFs in
``tests/fixtures/pdfs``. It is JSONL, one case per line, hashed as a whole by
:func:`gold_set_hash` so a run artifact can pin which gold set produced it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from aegis.retrieval.citations import span_present

#: Longest a gold span may be. A span is one sentence: long enough to be unambiguous,
#: short enough that it rarely straddles a chunk boundary in any arm.
MAX_SPAN_WORDS = 30

#: The gold set shipped with the package, anchored to the four fixture PDFs.
FIXTURE_GOLD_SET_PATH = Path(__file__).resolve().parent / "data" / "fixture_gold_set.jsonl"


class GoldKind(StrEnum):
    """What kind of question a case is — the axis every subset table is cut on."""

    #: A locator question written from the passage's own distinctive terms. Deterministic
    #: to build and to re-derive; lexically close to its target, which flatters the
    #: keyword arm and is stated rather than hidden.
    KNOWN_ITEM = "known_item"
    #: A hand-written question in a user's words rather than the document's.
    HANDWRITTEN = "handwritten"
    #: The answer is a cell of a table. Where structure-aware ingestion should show up.
    TABLE = "table"
    #: Needs two passages, so it carries more than one required span. The only cases
    #: where the graph arm can earn its keep and the only ones where nDCG is not
    #: degenerate.
    MULTI_HOP = "multi_hop"
    #: A plausible question the corpus provably does not answer. Not graded for
    #: retrieval — it exists to measure refusal.
    UNANSWERABLE = "unanswerable"


@dataclass(frozen=True)
class GoldCase:
    """One labelled question, anchored to verbatim text in a source document.

    Attributes:
        id: Stable case id (``g-001``).
        query: The question, as a user would ask it.
        doc_id: The fixture file the answer lives in (``""`` for an unanswerable case).
        answer_span: The verbatim sentence that answers it. Empty for an unanswerable
            case, which is the marker that it is not retrieval-gradeable.
        also_requires: Further verbatim spans a complete answer needs — non-empty only
            for multi-hop cases, and the reason they are multi-gold.
        page_no: The printed page the span is on. Roll-up reporting only; never graded,
            because a page number that disagreed with the parse would fail a case for
            the parser's opinion rather than for the retrieval.
        section_path: The heading path the span sits under. Reporting only, same reason.
        kind: See :class:`GoldKind`.
        provenance: How this case was made and by whom, so the set's biases can be read
            off the file rather than remembered.
    """

    id: str
    query: str
    doc_id: str
    answer_span: str
    kind: GoldKind
    provenance: str
    also_requires: tuple[str, ...] = field(default_factory=tuple)
    page_no: int | None = None
    section_path: str = ""

    @property
    def required_spans(self) -> tuple[str, ...]:
        """Every span a complete retrieval must surface (empty when unanswerable)."""
        if not self.answer_span:
            return ()
        return (self.answer_span, *self.also_requires)

    @property
    def gradeable(self) -> bool:
        """Whether this case can be scored by retrieval metrics at all."""
        return bool(self.required_spans)

    def as_dict(self) -> dict[str, object]:
        """Return the case as the plain dict one JSONL line holds."""
        record: dict[str, object] = {
            "id": self.id,
            "query": self.query,
            "doc_id": self.doc_id,
            "answer_span": self.answer_span,
            "kind": str(self.kind),
            "provenance": self.provenance,
        }
        if self.also_requires:
            record["also_requires"] = list(self.also_requires)
        if self.page_no is not None:
            record["page_no"] = self.page_no
        if self.section_path:
            record["section_path"] = self.section_path
        return record


def _case_from_dict(record: dict[str, object]) -> GoldCase:
    """Build a :class:`GoldCase` from one decoded JSONL record.

    Args:
        record: The decoded object.

    Returns:
        The case.

    Raises:
        ValueError: If a required field is missing, if a gradeable case's span is longer
            than :data:`MAX_SPAN_WORDS`, or if ``kind`` is not a :class:`GoldKind`. All
            three fail loudly at load: a malformed gold set that loads anyway measures
            something nobody chose.
    """
    try:
        case = GoldCase(
            id=str(record["id"]),
            query=str(record["query"]),
            doc_id=str(record["doc_id"]),
            answer_span=str(record["answer_span"]),
            kind=GoldKind(str(record["kind"])),
            provenance=str(record["provenance"]),
            also_requires=tuple(str(s) for s in record.get("also_requires", ())),
            page_no=(
                int(record["page_no"]) if record.get("page_no") is not None else None
            ),
            section_path=str(record.get("section_path", "")),
        )
    except KeyError as exc:
        raise ValueError(f"gold case is missing field {exc.args[0]!r}: {record}") from exc
    for span in case.required_spans:
        if len(span.split()) > MAX_SPAN_WORDS:
            raise ValueError(
                f"{case.id}: span is {len(span.split())} words, over the "
                f"{MAX_SPAN_WORDS}-word limit that keeps it inside one chunk"
            )
    if case.kind is GoldKind.UNANSWERABLE and case.answer_span:
        raise ValueError(f"{case.id}: an unanswerable case cannot carry an answer span")
    if case.kind is not GoldKind.UNANSWERABLE and not case.answer_span:
        raise ValueError(f"{case.id}: a {case.kind} case must carry an answer span")
    return case


def load_gold_set(path: Path | str = FIXTURE_GOLD_SET_PATH) -> tuple[GoldCase, ...]:
    """Load a JSONL gold set, validating every case on the way in.

    Args:
        path: The JSONL file. Defaults to the shipped fixture set.

    Returns:
        The cases in file order.

    Raises:
        ValueError: On a malformed case (see :func:`_case_from_dict`) or a duplicate id.
    """
    cases: list[GoldCase] = []
    seen: set[str] = set()
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        case = _case_from_dict(json.loads(line))
        if case.id in seen:
            raise ValueError(f"duplicate gold case id {case.id!r}")
        seen.add(case.id)
        cases.append(case)
    return tuple(cases)


def dump_gold_set(cases: Iterable[GoldCase], path: Path | str) -> None:
    """Write ``cases`` to ``path`` as JSONL, one case per line.

    Args:
        cases: The cases to write.
        path: Destination file.
    """
    lines = [
        json.dumps(case.as_dict(), ensure_ascii=False, sort_keys=True) for case in cases
    ]
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def gold_set_hash(cases: Sequence[GoldCase]) -> str:
    """Content hash of a gold set, for pinning a run artifact to the set that produced it.

    Args:
        cases: The cases, in order.

    Returns:
        A hex SHA-256 over the canonical JSON of every case. Order-sensitive on purpose:
        a reordered file is a different run input, and pretending otherwise would let two
        artifacts claim the same provenance for different case orders.
    """
    digest = hashlib.sha256()
    for case in cases:
        digest.update(json.dumps(case.as_dict(), sort_keys=True).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def is_hit(chunk_text: str, span: str) -> bool:
    """Whether ``chunk_text`` contains ``span`` verbatim (the grading rule).

    A thin, named alias for :func:`~aegis.retrieval.citations.span_present` — named
    because "is this chunk a hit" is the question the eval asks, and having one name for
    it here makes it impossible to grade with a second, subtly different normaliser.

    Args:
        chunk_text: The retrieved chunk's text.
        span: The gold answer span.

    Returns:
        ``True`` if the chunk contains the span.
    """
    return span_present(span, chunk_text)


def hit_ranks(case: GoldCase, ranked_texts: Sequence[str]) -> tuple[tuple[int, ...], ...]:
    """The 1-based ranks at which each of a case's required spans was retrieved.

    Args:
        case: The gold case.
        ranked_texts: The retrieved chunk texts, best first.

    Returns:
        One tuple of ranks per required span, in ``required_spans`` order (empty tuple
        for a span that never appeared). This is the shape every metric in
        :mod:`aegis.evals.ir_metrics` consumes, so a metric can never be computed from a
        different notion of "hit" than the one above.
    """
    return tuple(
        tuple(
            rank
            for rank, text in enumerate(ranked_texts, start=1)
            if is_hit(text, span)
        )
        for span in case.required_spans
    )
