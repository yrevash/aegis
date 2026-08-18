"""The text-layer probe and the per-document OCR decision it drives (D3)."""

from __future__ import annotations

import pytest

from aegis.ingestion import (
    MIN_CHARS_PER_PAGE,
    TextLayerProbe,
    TextLayerProbeError,
    decide_ocr,
    probe_text_layer,
)

from .conftest import fixture_pdf


def test_a_born_digital_document_turns_ocr_off_and_says_why():
    decision = decide_ocr(TextLayerProbe(page_chars=(2000,) * 20))

    assert decision.enabled is False
    assert "20/20" in decision.reason
    assert "born-digital" in decision.reason


def test_a_scanned_document_turns_ocr_on():
    decision = decide_ocr(TextLayerProbe(page_chars=(0, 3, 0, 12, 0)))

    assert decision.enabled is True
    assert "0/5" in decision.reason


def test_the_pages_we_give_up_on_are_named_not_just_counted():
    # 19 of 20 pages have text, so OCR stays off — and page 7 is lost. D3 states that
    # trade-off; this asserts it is visible in the log rather than silent.
    probe = TextLayerProbe(page_chars=tuple(0 if n == 7 else 2000 for n in range(1, 21)))

    decision = decide_ocr(probe)

    assert decision.enabled is False
    assert probe.pages_without_text() == (7,)
    assert "page 7" in decision.reason.replace("page(s) without one are not OCR'd: ", "page ")


def test_the_threshold_is_where_it_is_documented_to_be():
    mostly_text = TextLayerProbe(page_chars=(2000,) * 8 + (0,) * 2)
    half_text = TextLayerProbe(page_chars=(2000,) * 5 + (0,) * 5)

    assert decide_ocr(mostly_text).enabled is False
    assert decide_ocr(half_text).enabled is True


def test_a_page_with_a_stamp_but_no_text_layer_does_not_count_as_text():
    barely = TextLayerProbe(page_chars=(MIN_CHARS_PER_PAGE - 1,) * 10)

    assert barely.pages_with_text == 0
    assert decide_ocr(barely).enabled is True


def test_an_empty_document_asks_for_ocr_rather_than_reading_nothing():
    decision = decide_ocr(TextLayerProbe(page_chars=()))

    assert decision.enabled is True


def test_a_missing_file_raises_rather_than_guessing_the_ocr_answer(tmp_path):
    pytest.importorskip("pypdfium2", reason="the 'ingestion' extra is not installed")

    with pytest.raises(TextLayerProbeError):
        probe_text_layer(tmp_path / "not-a-file.pdf")


def test_the_probe_reads_a_real_born_digital_pdf():
    pytest.importorskip("pypdfium2", reason="the 'ingestion' extra is not installed")
    probe = probe_text_layer(fixture_pdf("bert-two-column.pdf"))

    assert probe.page_count == 16
    assert probe.pages_with_text == 16
    assert decide_ocr(probe).enabled is False
