"""Unit tests for the aegis.vision pipeline — the happy path and every refusal."""

from __future__ import annotations

import pytest

from aegis.core.types import GuardResult, GuardVerdict
from aegis.media import ImagePayload
from aegis.vision import (
    STAGE_ORDER,
    ControlOutcome,
    VisionAnalyser,
    VisionOutcome,
    VisionStage,
    analyse_image,
)

from .conftest import (
    FakeAnalyzer,
    FakeFinding,
    FakeRedactor,
    FakeScreen,
    RecordingAnalyst,
    bomb_payload,
    png_payload,
)


def _by_stage(analysis, stage: VisionStage):
    """The single control report for ``stage``."""
    matches = [c for c in analysis.controls if c.stage is stage]
    assert len(matches) == 1, f"expected exactly one {stage} report, got {matches}"
    return matches[0]


async def test_clean_image_is_answered_with_every_control_reported(clean_png, analyst):
    """A clean image reaches the model and returns an itemised, answered result."""
    screen = FakeScreen(injection=False, contains_text=False)
    result = await analyse_image(
        clean_png,
        "What is in this image?",
        screen_completer=screen,
        analyst=analyst,
        output_check=lambda t: _pass(t),
    )

    assert result.outcome is VisionOutcome.ANSWERED
    assert result.answer == "A single white pixel."
    assert result.blocked_stage is None
    assert [c.stage for c in result.controls] == list(STAGE_ORDER)
    assert all(c.outcome is not ControlOutcome.FAILED_CLOSED for c in result.controls)
    assert result.usage.cost_usd == pytest.approx(0.00243)
    assert result.usage.images == 1
    assert result.usage.model == "genailab-maas-Llama-3.2-90B-Vision-Instruct"


async def test_image_facts_record_sniffed_mime_and_dimensions(clean_png, analyst):
    """Hygiene's measured facts land on the result, declared MIME kept beside them."""
    result = await analyse_image(
        clean_png,
        "",
        screen_completer=FakeScreen(injection=False),
        analyst=analyst,
        output_check=_pass,
    )
    assert result.image is not None
    assert result.image.declared_mime == "image/png"
    assert result.image.sniffed_mime == "image/png"
    assert (result.image.width, result.image.height) == (1, 1)
    assert result.image.provenance == "user_upload"


async def test_blank_question_falls_back_to_the_stated_default(clean_png, analyst):
    """An empty question becomes the documented default, not an empty prompt."""
    result = await analyse_image(
        clean_png, "   ", screen_completer=FakeScreen(injection=False), analyst=analyst
    )
    assert result.question == "Describe this image."


async def test_hygiene_bomb_is_refused_before_any_model_call():
    """A decompression bomb never costs a screen call, let alone an analysis call."""
    screen = FakeScreen(injection=False)
    analyst = RecordingAnalyst()
    result = await analyse_image(
        bomb_payload(), "What is this?", screen_completer=screen, analyst=analyst
    )

    assert result.outcome is VisionOutcome.BLOCKED
    assert result.blocked_stage is VisionStage.HYGIENE
    assert screen.calls == [] and analyst.calls == []
    assert _by_stage(result, VisionStage.HYGIENE).outcome is ControlOutcome.BLOCKED
    assert _by_stage(result, VisionStage.INJECTION_SCREEN).outcome is ControlOutcome.NOT_RUN
    assert result.answer == ""


async def test_uri_only_image_is_refused_by_hygiene():
    """Bytes this process never held cannot be screened, so they are not analysed."""
    analyst = RecordingAnalyst()
    result = await analyse_image(
        ImagePayload(uri="https://example.invalid/x.png", mime_type="image/png"),
        "What is this?",
        screen_completer=FakeScreen(injection=False),
        analyst=analyst,
    )
    assert result.outcome is VisionOutcome.BLOCKED
    assert result.blocked_stage is VisionStage.HYGIENE
    assert analyst.calls == []


async def test_missing_analyst_is_a_refusal_not_an_empty_answer(clean_png):
    """No analyst wired ⇒ BLOCKED at the model stage, never a blank ANSWERED."""
    result = await analyse_image(
        clean_png, "What is this?", screen_completer=FakeScreen(injection=False), analyst=None
    )
    assert result.outcome is VisionOutcome.BLOCKED
    assert result.blocked_stage is VisionStage.MODEL
    assert _by_stage(result, VisionStage.MODEL).outcome is ControlOutcome.FAILED_CLOSED


async def test_analyst_failure_is_reported_as_a_refusal(clean_png):
    """A failed vision call produces a refusal carrying the reason, not a fake answer."""

    async def exploding(messages):
        raise RuntimeError("vision deployment 503")

    result = await analyse_image(
        clean_png, "What is this?", screen_completer=FakeScreen(injection=False), analyst=exploding
    )
    assert result.outcome is VisionOutcome.BLOCKED
    assert result.blocked_stage is VisionStage.MODEL
    assert "503" in result.blocked_reason


async def test_output_rails_block_withholds_the_answer(clean_png, analyst):
    """A BLOCK from the existing text rails withholds the model's text entirely."""

    async def blocking(text: str) -> GuardResult:
        return GuardResult(
            verdict=GuardVerdict.BLOCK, reason="Unsafe output blocked.", text=text, layer="content"
        )

    result = await analyse_image(
        clean_png,
        "What is this?",
        screen_completer=FakeScreen(injection=False),
        analyst=analyst,
        output_check=blocking,
    )
    assert result.outcome is VisionOutcome.BLOCKED
    assert result.blocked_stage is VisionStage.OUTPUT_RAILS
    assert result.answer == ""
    assert result.output is not None and result.output.verdict == "block"
    # The call still happened and still cost money — that must not be hidden.
    assert result.usage.cost_usd > 0


async def test_output_rails_redaction_is_what_leaves_the_module(clean_png, analyst):
    """On REDACT the masked text is returned, never the raw answer."""

    async def redacting(text: str) -> GuardResult:
        return GuardResult(
            verdict=GuardVerdict.REDACT,
            reason="Redacted PII on the outbound path: EMAIL_ADDRESS.",
            text="[REDACTED]",
            layer="pii",
            redactions=["EMAIL_ADDRESS"],
        )

    result = await analyse_image(
        clean_png,
        "What is this?",
        screen_completer=FakeScreen(injection=False),
        analyst=analyst,
        output_check=redacting,
    )
    assert result.outcome is VisionOutcome.ANSWERED
    assert result.answer == "[REDACTED]"
    assert _by_stage(result, VisionStage.OUTPUT_RAILS).outcome is ControlOutcome.REDACTED
    assert result.output is not None and result.output.redactions == ["EMAIL_ADDRESS"]


async def test_output_rails_error_withholds_the_answer(clean_png, analyst):
    """Rails that error must not let unscreened model text through."""

    async def exploding(text: str) -> GuardResult:
        raise RuntimeError("rail engine down")

    result = await analyse_image(
        clean_png,
        "What is this?",
        screen_completer=FakeScreen(injection=False),
        analyst=analyst,
        output_check=exploding,
    )
    assert result.outcome is VisionOutcome.BLOCKED
    assert result.blocked_stage is VisionStage.OUTPUT_RAILS
    assert result.answer == ""
    assert _by_stage(result, VisionStage.OUTPUT_RAILS).outcome is ControlOutcome.FAILED_CLOSED


async def test_unwired_output_rails_are_reported_as_not_run(clean_png, analyst):
    """No rails wired ⇒ the answer is returned but the gap is stated, not implied."""
    result = await analyse_image(
        clean_png, "What is this?", screen_completer=FakeScreen(injection=False), analyst=analyst
    )
    assert result.outcome is VisionOutcome.ANSWERED
    report = _by_stage(result, VisionStage.OUTPUT_RAILS)
    assert report.outcome is ControlOutcome.NOT_RUN
    assert not report.ran
    assert "NOT screened" in report.detail
    assert "output_rails" in result.coverage()
    assert "Did NOT run" in result.coverage()


async def test_image_pii_disabled_is_not_run_never_clean(clean_png, analyst):
    """An unenabled PII rail reports NOT_RUN — the one thing it must never imply is 'clean'."""
    result = await analyse_image(
        clean_png, "What is this?", screen_completer=FakeScreen(injection=False), analyst=analyst
    )
    report = _by_stage(result, VisionStage.IMAGE_PII)
    assert report.outcome is ControlOutcome.NOT_RUN
    assert "neither detected nor removed" in report.detail
    assert result.pii_entities == [] and result.pii_regions == []


async def test_image_pii_redacts_and_reports_regions(clean_png):
    """Found PII is painted out, the redacted image is what the model sees, boxes survive."""
    analyzer = FakeAnalyzer(
        [
            FakeFinding("EMAIL_ADDRESS", left=10, top=20, width=120, height=18, score=0.92),
            FakeFinding("PHONE_NUMBER", left=10, top=48, width=90, height=18, score=0.71),
        ]
    )
    redactor = FakeRedactor()
    analyst = RecordingAnalyst()

    result = await analyse_image(
        clean_png,
        "What is this?",
        screen_completer=FakeScreen(injection=False),
        analyst=analyst,
        output_check=_pass,
        image_analyzer=analyzer,
        image_redactor=redactor,
    )

    assert result.outcome is VisionOutcome.ANSWERED
    assert result.pii_entities == ["EMAIL_ADDRESS", "PHONE_NUMBER"]
    assert [r.entity_type for r in result.pii_regions] == ["EMAIL_ADDRESS", "PHONE_NUMBER"]
    assert (result.pii_regions[0].left, result.pii_regions[0].top) == (10, 20)
    assert result.pii_regions[0].score == pytest.approx(0.92)
    assert _by_stage(result, VisionStage.IMAGE_PII).outcome is ControlOutcome.REDACTED
    assert redactor.calls == 1
    # The model was sent the *redacted* rewrite, not the original bytes.
    sent = analyst.calls[0][1]["content"][1]["image_url"]["url"]
    assert "base64," in sent
    original = png_payload().data
    assert sent.split("base64,", 1)[1] != __import__("base64").b64encode(original).decode()


async def test_image_pii_clean_scan_passes_the_original_payload(clean_png):
    """A clean PII scan reports PASSED and forwards the original object untouched."""
    analyzer = FakeAnalyzer([])
    analyst = RecordingAnalyst()
    result = await analyse_image(
        clean_png,
        "",
        screen_completer=FakeScreen(injection=False),
        analyst=analyst,
        output_check=_pass,
        image_analyzer=analyzer,
    )
    assert _by_stage(result, VisionStage.IMAGE_PII).outcome is ControlOutcome.PASSED
    assert result.pii_entities == []
    assert analyzer.calls == 1


async def test_enabled_pii_rail_that_errors_fails_closed(clean_png):
    """An enabled rail that breaks blocks the run — it never quietly lets pixels past."""

    class Broken:
        def analyze(self, image):
            raise RuntimeError("tesseract missing")

    analyst = RecordingAnalyst()
    result = await analyse_image(
        clean_png,
        "What is this?",
        screen_completer=FakeScreen(injection=False),
        analyst=analyst,
        image_analyzer=Broken(),
    )
    assert result.outcome is VisionOutcome.BLOCKED
    assert result.blocked_stage is VisionStage.IMAGE_PII
    assert _by_stage(result, VisionStage.IMAGE_PII).outcome is ControlOutcome.FAILED_CLOSED
    assert analyst.calls == []


async def test_analyser_reports_whether_it_can_screen_at_all():
    """`can_screen` is the honest capability flag a host checks before promising anything."""
    assert VisionAnalyser(screen_completer=FakeScreen(injection=False)).can_screen is True
    assert VisionAnalyser().can_screen is False


async def _pass(text: str) -> GuardResult:
    """A PASS from the output rails."""
    return GuardResult(verdict=GuardVerdict.PASS, reason="Output passed every rail.", text=text)
