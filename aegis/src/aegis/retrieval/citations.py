"""Verbatim span verification — the one primitive the gold set and citations share.

## What this is for

Two questions in this platform are the *same* question:

* **Grading retrieval** (task 4.11). Gold truth is anchored to a verbatim answer span in
  the source document, never to a chunk id, because a naive baseline chunks in fixed
  windows and we chunk structurally — so chunk ids are not comparable across arms, which
  is how most published RAG ablations quietly stop measuring anything. A retrieved chunk
  is a hit iff the gold span appears in it.
* **Checking a citation** (task 4.14, D10). A citation is a claim that a quoted span came
  from a particular chunk. Checking it is the same containment test, run over the chunk
  the answer named.

So the containment test is written once, here, and used twice. Changing how it
normalises changes both, deliberately — a normalisation that is too generous inflates the
eval and waves through a fabricated quote in exactly the same motion.

## What "verbatim" has to mean over a PDF

Strictly byte-identical containment is the wrong test and would fail honestly-correct
citations. The same sentence reaches us through three different transports — the PDF text
layer (PDFium), the parser's block text (Docling), and the spotlighted answer context the
model actually read (:mod:`aegis.retrieval.spotlight`, which replaces every space with a
datamark token) — and they disagree about whitespace, ligatures, quotation marks, dashes
and line-wrap hyphens without disagreeing about a single word. :func:`normalise_span`
folds exactly those differences and nothing else:

* Unicode NFKC, which maps the ligatures a PDF font emits (``ﬁ`` → ``fi``) onto their
  letters;
* line-wrap hyphens (a ``trans-`` at the end of one line and ``former`` at the start of
  the next), soft hyphens, and the noncharacter ``U+FFFE`` that PDFium emits in their
  place on three of the four fixtures — joined rather than turned into a word boundary,
  because ``WordPiece to\ufffekens`` and ``WordPiece tokens`` are the same two words;
* case;
* every run of non-alphanumeric characters — punctuation, newlines, column gutters,
  spotlight datamarks — collapsed to a single space.

What it deliberately does **not** do is stem, drop stop-words, or fuzzy-match. A quote
that says a different word from the document is a failed citation, and this test says so.

## What a failed check does, and why it is not a drop

A citation that fails verification is **kept and marked**, never silently dropped and
never rendered as though it had been checked. Dropping it is the tempting option because
it makes the output look clean, and it is the wrong one: an answer quoting a sentence that
is in no retrieved chunk is the single loudest hallucination signal this system can
produce, and deleting it destroys the evidence while leaving the surrounding prose — which
was generated from the same reasoning — in place and unlabelled. Rendering it unmarked is
worse still, because it launders a fabrication as a checked quote. So
:func:`verify_citations` returns one :class:`CitationCheck` per citation with an explicit
:class:`CitationStatus`, and the surface that renders citations is expected to render an
``UNVERIFIED`` one visibly as unverified. ``matched_fraction`` is carried with it so the
difference between a near-miss (a paraphrase, ~0.8) and an invention (~0.1) is visible
rather than collapsed into one boolean.

**What this catches and what it does not** (stated plainly rather than oversold): it
catches fabricated *quotes*. It does not catch fabricated *reasoning over real quotes* —
an answer can cite three true sentences and draw a conclusion none of them support, and
every citation here will verify.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from enum import StrEnum

#: A hyphen (any of the Unicode dashes a PDF may use) immediately before a line break —
#: a *soft wrap*, not part of the word. ``trans-\nformer`` is one word in the document and
#: has to normalise to one word here, or every gold span that happens to straddle a line
#: break in the source PDF would fail against the chunk that genuinely contains it.
_WRAP_HYPHEN = re.compile(r"[-‐‑­]\s*\n\s*")

#: Characters that are not content and are deleted outright rather than collapsed to a
#: space. The soft hyphen is a hyphenation hint by definition; ``U+FFFE``/``U+FFFF`` are
#: Unicode **noncharacters** and can never legitimately appear in text — PDFium emits
#: ``U+FFFE`` where it cannot map a glyph, and on these fixtures the unmappable glyph is
#: the wrap hyphen (332 of them in ``bert-two-column.pdf`` alone, e.g. ``to\ufffekens``).
#: Collapsing them to a space instead would split one word into two and fail a span that
#: the document does contain.
_NOT_A_CHARACTER = str.maketrans({"\u00ad": None, "\ufffe": None, "\uffff": None})

#: Any run of characters that is not a letter or a digit. Collapsed to a single space, so
#: punctuation, newlines, the two-column gutter's stray spaces and the spotlight datamark
#: token (``▁``) all stop being differences.
_NON_ALNUM = re.compile(r"[^a-z0-9]+", re.ASCII)


def normalise_span(text: str) -> str:
    """Fold a span onto the form containment is tested in.

    See the module docstring for the four differences this folds and why each one is a
    transport artefact rather than a difference in what the document says. The result is
    space-padded at both ends so that :func:`span_present` matches on whole words: without
    the padding, ``"rate"`` would be "found" inside ``"corporate"``.

    Args:
        text: Any span of document, chunk or answer text.

    Returns:
        The normalised form: lowercase, alphanumeric runs separated by single spaces,
        with one leading and one trailing space. An input with no alphanumeric content
        normalises to a single space, which :func:`span_present` treats as no span at all.
    """
    joined = _WRAP_HYPHEN.sub("", text.translate(_NOT_A_CHARACTER))
    folded = unicodedata.normalize("NFKC", joined).lower()
    return " " + _NON_ALNUM.sub(" ", folded).strip() + " "


def span_present(span: str, text: str) -> bool:
    """Whether ``span`` appears verbatim in ``text``, up to :func:`normalise_span`.

    This is the hit test for the gold set and the verification test for a citation — one
    function, so the two can never drift apart.

    Args:
        span: The quoted / gold answer span.
        text: The chunk (or document) it is claimed to come from.

    Returns:
        ``True`` if the normalised span is a normalised substring of the text. An empty
        or punctuation-only span is never present: a citation that quotes nothing has not
        been verified, it has been skipped, and returning ``True`` there would make the
        empty quote the easiest way to pass this check.
    """
    needle = normalise_span(span)
    if needle.strip() == "":
        return False
    return needle in normalise_span(text)


def matched_fraction(span: str, text: str) -> float:
    """How much of ``span`` is actually present in ``text``, in ``[0, 1]``.

    The diagnostic that goes beside a failed check. It is the length of the longest
    common run of normalised characters divided by the length of the normalised span, so
    ``1.0`` means the whole span is there (i.e. :func:`span_present` is ``True``), ~0.8
    means a paraphrase or a dropped clause, and ~0.1 means the quote was invented. Two
    citations that both fail are not equally wrong, and a single boolean cannot say so.

    Args:
        span: The quoted / gold answer span.
        text: The chunk it is claimed to come from.

    Returns:
        The matched fraction; ``0.0`` for an empty span or empty text.
    """
    needle = normalise_span(span).strip()
    haystack = normalise_span(text).strip()
    if not needle or not haystack:
        return 0.0
    match = SequenceMatcher(None, needle, haystack, autojunk=False).find_longest_match(
        0, len(needle), 0, len(haystack)
    )
    return match.size / len(needle)


class CitationStatus(StrEnum):
    """The three outcomes of checking one citation, none of which is "silently gone"."""

    #: The quoted span is verbatim in the chunk the citation names.
    VERIFIED = "verified"
    #: The chunk exists and was retrieved, but does not contain the quoted span.
    UNVERIFIED = "unverified"
    #: The citation names a chunk that is not among the sources the answer was given.
    UNKNOWN_SOURCE = "unknown-source"


@dataclass(frozen=True)
class Citation:
    """One claim of the form "this quote came from that chunk".

    Attributes:
        source_id: The id of the chunk / :class:`~aegis.retrieval.models.Source` the
            quote is attributed to.
        quote: The span, as the answer presented it.
    """

    source_id: str
    quote: str


@dataclass(frozen=True)
class CitationCheck:
    """The verdict on one citation, plus enough detail to act on it.

    Attributes:
        citation: The citation as it was claimed.
        status: What checking it found.
        matched_fraction: How much of the quote was found in the cited chunk (see
            :func:`matched_fraction`). ``0.0`` when the cited source does not exist,
            because nothing was compared — not because nothing matched.
        reason: A human-readable sentence for the UI and the logs.
    """

    citation: Citation
    status: CitationStatus
    matched_fraction: float
    reason: str

    @property
    def verified(self) -> bool:
        """Whether this citation may be rendered as a checked quote."""
        return self.status is CitationStatus.VERIFIED


def verify_citations(
    citations: Sequence[Citation], sources: Sequence[object]
) -> list[CitationCheck]:
    """Check every citation against the sources the answer was actually given.

    One check per citation, in the order they were claimed. Nothing is dropped and
    nothing is reordered — see the module docstring for why a failing citation is marked
    rather than deleted.

    Args:
        citations: What the answer claimed.
        sources: The retrieved sources backing that answer — anything with ``.id`` and
            ``.text``, which is what :class:`~aegis.retrieval.models.Source` and
            :class:`~aegis.retrieval.models.Candidate` both are. Duplicate ids resolve to
            the first occurrence, matching the order the answer saw them in.

    Returns:
        One :class:`CitationCheck` per citation.
    """
    by_id: dict[str, str] = {}
    for source in sources:
        source_id = str(getattr(source, "id", ""))
        if source_id and source_id not in by_id:
            by_id[source_id] = str(getattr(source, "text", ""))

    checks: list[CitationCheck] = []
    for citation in citations:
        text = by_id.get(citation.source_id)
        if text is None:
            checks.append(
                CitationCheck(
                    citation=citation,
                    status=CitationStatus.UNKNOWN_SOURCE,
                    matched_fraction=0.0,
                    reason=(
                        f"cites {citation.source_id!r}, which is not one of the "
                        f"{len(by_id)} sources this answer was given"
                    ),
                )
            )
            continue
        if span_present(citation.quote, text):
            checks.append(
                CitationCheck(
                    citation=citation,
                    status=CitationStatus.VERIFIED,
                    matched_fraction=1.0,
                    reason=f"quoted span is verbatim in {citation.source_id}",
                )
            )
            continue
        fraction = matched_fraction(citation.quote, text)
        checks.append(
            CitationCheck(
                citation=citation,
                status=CitationStatus.UNVERIFIED,
                matched_fraction=fraction,
                reason=(
                    f"quoted span is not in {citation.source_id} "
                    f"({fraction:.0%} of it matches)"
                ),
            )
        )
    return checks


def citation_validity(checks: Sequence[CitationCheck]) -> float | None:
    """The fraction of citations that verified — the free answer-quality metric.

    Args:
        checks: The output of :func:`verify_citations`.

    Returns:
        The verified fraction, or ``None`` when there were no citations at all. ``None``
        rather than ``1.0`` on purpose, following the same rule as the rest of the eval
        surface: an answer that cited nothing has not achieved perfect citation validity,
        it has not been measured.
    """
    if not checks:
        return None
    return sum(check.verified for check in checks) / len(checks)
