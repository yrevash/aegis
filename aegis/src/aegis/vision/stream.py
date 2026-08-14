"""AG-UI streaming for a vision analysis — shows the work, in the order it happened.

Two custom events, deliberately separate:

* ``VISION_SCREEN`` is emitted **the moment the injection screen decides**, before
  the analysis call is even attempted. A console that only ever learns the
  verdict alongside the answer cannot show that the screen came first; emitting
  it early is what makes the ordering claim visible rather than asserted.
* ``VISION_ANALYSIS`` carries the finished, itemised result — controls and all.

The bracket is ``SpanKind.GUARDRAIL`` for the screen and ``SpanKind.LLM`` for the
analysis, so the existing trace panel renders both without new render rails.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aegis.core import stream_names
from aegis.core.events import SpanKind
from aegis.vision.types import ScreenVerdict, VisionAnalysis

if TYPE_CHECKING:  # pragma: no cover - typing only
    from aegis.core.stream import AegisEmitter

_SCREEN_STEP = "vision_screen"
_ANALYSE_STEP = "vision_analyse"


def screen_payload(verdict: ScreenVerdict) -> dict:
    """Project the screen's verdict onto its wire payload.

    ``screened`` is carried explicitly so a UI can distinguish "we looked and it
    was clean" from "we could not look, so we blocked" — the two states this
    module refuses to collapse.
    """
    return {
        "injection": verdict.injection,
        "containsText": verdict.contains_text,
        "screened": verdict.screened,
        "reason": verdict.reason,
    }


def analysis_payload(analysis: VisionAnalysis) -> dict:
    """Project a finished analysis onto its wire payload."""
    return {
        "outcome": analysis.outcome.value,
        "question": analysis.question,
        "answer": analysis.answer,
        "blockedStage": analysis.blocked_stage.value if analysis.blocked_stage else None,
        "blockedReason": analysis.blocked_reason,
        "screen": screen_payload(analysis.screen) if analysis.screen else None,
        "piiEntities": list(analysis.pii_entities),
        "piiRegions": [r.model_dump(mode="json") for r in analysis.pii_regions],
        "image": analysis.image.model_dump(mode="json") if analysis.image else None,
        "controls": [c.model_dump(mode="json") for c in analysis.controls],
        "usage": analysis.usage.model_dump(mode="json"),
        "output": analysis.output.model_dump(mode="json") if analysis.output else None,
        "coverage": analysis.coverage(),
    }


async def emit_screen_verdict(emitter: AegisEmitter, verdict: ScreenVerdict) -> None:
    """Emit one ``STEP(vision_screen, GUARDRAIL)`` bracket with the screen verdict.

    Args:
        emitter: The AG-UI emitter for streaming events.
        verdict: What the image-injection screen decided.
    """
    async with emitter.step(_SCREEN_STEP, SpanKind.GUARDRAIL):
        await emitter.custom(stream_names.VISION_SCREEN, screen_payload(verdict))


async def emit_analysis(emitter: AegisEmitter, analysis: VisionAnalysis) -> VisionAnalysis:
    """Emit one ``STEP(vision_analyse, LLM)`` bracket with the finished analysis.

    Args:
        emitter: The AG-UI emitter for streaming events.
        analysis: The completed (or refused) analysis.

    Returns:
        The same ``analysis``, so callers can stream-and-forward in one expression.
    """
    async with emitter.step(_ANALYSE_STEP, SpanKind.LLM):
        await emitter.custom(stream_names.VISION_ANALYSIS, analysis_payload(analysis))
    return analysis
