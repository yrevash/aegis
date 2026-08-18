"""Every gold span is genuinely in its source document — the anchor test.

A gold set whose spans are not actually in the documents measures nothing, and it fails
*silently*: every arm scores zero on the broken case, the ablation still produces a table,
and the table is wrong by the same amount everywhere. So this is asserted against the
**PDF's own text layer**, read with PDFium — deliberately *not* against the Docling parse
the pipeline uses, because checking a parse against itself is not a check.

Reading the text layer of all four fixtures costs about a second, so this runs in the
default suite. The straddle check below needs a real parse (minutes) and is gated.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from aegis.evals.goldset import load_gold_set
from aegis.retrieval.citations import matched_fraction, span_present

pytest.importorskip("pypdfium2", reason="the 'ingestion' extra is not installed")

from aegis.ingestion import probe_page_text  # noqa: E402 - after the import guard

PDF_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "pdfs"

GOLD = load_gold_set()
DOCUMENTS = sorted({case.doc_id for case in GOLD if case.doc_id})


@pytest.fixture(scope="module")
def text_layers() -> dict[str, str]:
    """The raw text layer of every fixture the gold set anchors to."""
    missing = [name for name in DOCUMENTS if not (PDF_DIR / name).exists()]
    if missing:
        pytest.skip(f"fixtures not downloaded ({missing}) — run {PDF_DIR}/fetch.sh")
    return {
        name: "\n".join(probe_page_text(PDF_DIR / name)) for name in DOCUMENTS
    }


@pytest.mark.parametrize("case", GOLD, ids=[case.id for case in GOLD])
def test_every_gold_span_is_verbatim_in_its_source_document(case, text_layers):
    if not case.gradeable:
        pytest.skip("unanswerable cases carry no span by construction")
    document = text_layers[case.doc_id]
    for span in case.required_spans:
        assert span_present(span, document), (
            f"{case.id}: span is not in {case.doc_id} "
            f"({matched_fraction(span, document):.0%} of it matches) — {span!r}"
        )


def test_unanswerable_questions_have_no_answer_in_any_fixture(text_layers):
    """The refusal cases are only honest if the corpus really cannot answer them.

    Checked by their distinctive terms rather than by a span (there is nothing to quote):
    if a term a refusal case is built around turns up in the corpus, the case is
    answerable and the refusal metric is measuring the wrong thing.
    """
    forbidden = {
        "corporation tax": "United Kingdom corporation tax",
        "World Cup": "the 2022 World Cup final",
        "amoxicillin": "an antibiotic dosage",
        "Kubernetes": "the Gateway API",
        "Llama 3": "the Llama 3 paper",
    }
    corpus = " ".join(text_layers.values())
    for term, why in forbidden.items():
        assert not span_present(term, corpus), f"{term!r} is in the corpus — {why}"


@pytest.mark.skipif(
    not os.environ.get("AEGIS_DOCLING_SLOW_FIXTURES"),
    reason="set AEGIS_DOCLING_SLOW_FIXTURES=1 to parse all four fixtures (~18 min)",
)
def test_no_gold_span_straddles_a_chunk_boundary_in_the_shipped_chunking():
    """Every span lands **inside** one structural chunk, so no case is ungradeable.

    A span that sits across a boundary would score its arm zero for a chunking decision
    rather than for a retrieval failure. ``eval-design`` §2.1 says such a case must be
    dropped and counted; this asserts there are none to drop.
    """
    from aegis.ingestion import parse_pdf  # noqa: PLC0415 - only under the slow gate
    from aegis.retrieval.chunker import chunk_sections  # noqa: PLC0415

    for name in DOCUMENTS:
        if not (PDF_DIR / name).exists():
            pytest.skip(f"{name} is not downloaded — run {PDF_DIR}/fetch.sh")
        chunks = chunk_sections(parse_pdf(PDF_DIR / name), chunk_size=400, overlap=60)
        texts = [chunk.contextualized() for chunk in chunks]
        for case in GOLD:
            if case.doc_id != name:
                continue
            for span in case.required_spans:
                assert any(span_present(span, text) for text in texts), (
                    f"{case.id}: span straddles every chunk boundary in {name} — {span!r}"
                )
