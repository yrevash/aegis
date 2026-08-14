"""Typed results for :mod:`aegis.vision` — the shape a vision analysis returns.

Everything here is a *record of what happened*, not a summary of it. The module's
whole reason to exist is an ordering claim ("the injection screen cleared this
image before the model ever saw it"), and a claim like that is only worth
anything if the caller can read, per control, whether it ran and what it decided.
So :class:`ControlReport` is a list on every result — including the blocked ones,
including the controls that did **not** run — rather than a boolean the UI has to
take on trust.

Pydantic + stdlib only. No codecs, no model client, no ``app.*``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class VisionStage(StrEnum):
    """The ordered stages of one analysis. The order **is** the security control.

    An image must clear :attr:`INJECTION_SCREEN` before :attr:`MODEL` runs; text
    rendered into pixels is read by a vision model exactly as if the user had
    typed it, and until this module existed nothing in the codebase would have
    looked at it. Every other ordering choice in this module is negotiable; that
    one is not.
    """

    HYGIENE = "hygiene"
    INJECTION_SCREEN = "injection_screen"
    IMAGE_PII = "image_pii"
    MODEL = "vision_model"
    OUTPUT_RAILS = "output_rails"


class ControlOutcome(StrEnum):
    """What one control decided — or why it decided nothing.

    ``NOT_RUN`` and ``FAILED_CLOSED`` are deliberately distinct. "The operator did
    not enable the image-PII rail" and "the injection screen had no completer, so
    the image was blocked rather than passed" are different statements about
    coverage, and collapsing them into one would be the exact dishonesty this
    codebase bans.
    """

    PASSED = "passed"
    BLOCKED = "blocked"
    REDACTED = "redacted"
    NOT_RUN = "not_run"
    FAILED_CLOSED = "failed_closed"


class VisionOutcome(StrEnum):
    """The terminal outcome of an analysis."""

    ANSWERED = "answered"
    BLOCKED = "blocked"


class ControlReport(BaseModel):
    """One control's line in the audit record.

    Attributes:
        stage: Which control this is.
        outcome: What it decided (see :class:`ControlOutcome`).
        detail: A short, PII-free sentence a human can read in the console.
    """

    model_config = ConfigDict(frozen=True)

    stage: VisionStage
    outcome: ControlOutcome
    detail: str = ""

    @property
    def ran(self) -> bool:
        """Whether the control actually executed."""
        return self.outcome not in {ControlOutcome.NOT_RUN, ControlOutcome.FAILED_CLOSED}


class PIIRegion(BaseModel):
    """One rectangle of personal data found burned into the pixels.

    Coordinates are in the *source image's* pixel space with the origin at the
    top-left, which is what Presidio's image analyzer reports and what a browser
    needs to overlay a box on the rendered image. Only the entity **kind** is
    carried — never the recognised value, which is the PII itself.
    """

    model_config = ConfigDict(frozen=True)

    entity_type: str = Field(description="Presidio entity kind, e.g. 'EMAIL_ADDRESS'.")
    left: int
    top: int
    width: int
    height: int
    score: float | None = Field(
        default=None, description="Presidio's confidence for this detection, when reported."
    )


class ImageFacts(BaseModel):
    """What payload hygiene measured about the image — facts, not claims.

    ``declared_mime`` is attacker-controlled and kept only so a mismatch is
    visible; ``sniffed_mime`` is the one derived from magic bytes and the only one
    anything downstream should believe.
    """

    model_config = ConfigDict(frozen=True)

    declared_mime: str
    sniffed_mime: str | None = None
    byte_size: int | None = None
    width: int | None = None
    height: int | None = None
    provenance: str = Field(
        default="unknown", description="The MediaSource the payload was tagged with."
    )


class VisionUsage(BaseModel):
    """Billable accounting for the analysis call, carried to the console.

    Deliberately a local type rather than an import of ``aegis.gateway.Usage``:
    this module is a leaf and must not depend on the gateway to state what a call
    cost. The host maps its gateway's usage onto this on the way in.
    """

    model_config = ConfigDict(frozen=True)

    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    images: int = 0
    cost_usd: float = 0.0
    cost_source: str = Field(
        default="provider",
        description="Provenance of cost_usd — 'provider' | 'estimated' | 'unpriced'. "
        "A $0 with source 'unpriced' means billable work nobody could price, which is "
        "a different statement from a genuine $0.",
    )


class ScreenVerdict(BaseModel):
    """The image-injection screen's verdict, flattened for the wire.

    Mirrors :class:`aegis.guardrails.media.ImageScreenVerdict` field for field. It
    is restated here (rather than embedded) so the analysis result serialises to
    one flat, versionable JSON contract the console can render without reaching
    into another package's types.
    """

    model_config = ConfigDict(frozen=True)

    injection: bool = Field(description="True when rendered text addressed an AI system.")
    contains_text: bool = False
    reason: str = ""
    screened: bool = Field(
        default=True,
        description="Whether a vision model actually looked at the image. False means "
        "the control did not run and the block is a fail-closed one.",
    )


class OutputRailVerdict(BaseModel):
    """What the existing text output rails decided about the model's answer."""

    model_config = ConfigDict(frozen=True)

    verdict: str = Field(description="'pass' | 'block' | 'redact' | 'flag'.")
    reason: str = ""
    layer: str | None = None
    redactions: list[str] = Field(default_factory=list)


class VisionAnalysis(BaseModel):
    """The full, itemised result of one image analysis.

    Attributes:
        outcome: Answered, or blocked at some stage.
        question: The question that was asked of the image.
        answer: The model's analysis. **Empty unless** ``outcome`` is
            ``ANSWERED`` — a blocked run never carries model text, because on a
            blocked run there is no model text.
        blocked_stage: Which control refused, when one did.
        blocked_reason: Why, in a sentence a human can act on.
        screen: The injection screen's verdict. Present on every run that got past
            hygiene — including passes, because "we looked and found nothing" is
            the claim the console exists to make.
        pii_entities: Presidio entity kinds found burned into the image.
        pii_regions: Where they were found, for the console overlay.
        image: What hygiene measured about the bytes.
        controls: One line per control, in execution order.
        usage: What the analysis call cost.
        output: The text output rails' verdict on the answer.
    """

    outcome: VisionOutcome
    question: str = ""
    answer: str = ""
    blocked_stage: VisionStage | None = None
    blocked_reason: str = ""
    screen: ScreenVerdict | None = None
    pii_entities: list[str] = Field(default_factory=list)
    pii_regions: list[PIIRegion] = Field(default_factory=list)
    image: ImageFacts | None = None
    controls: list[ControlReport] = Field(default_factory=list)
    usage: VisionUsage = Field(default_factory=VisionUsage)
    output: OutputRailVerdict | None = None

    @property
    def blocked(self) -> bool:
        """Whether the run was refused."""
        return self.outcome is VisionOutcome.BLOCKED

    def coverage(self) -> str:
        """A one-line, honest statement of which controls ran and which did not.

        Written once, here, so no caller has to reassemble it — and so a surface
        that shows a green verdict cannot omit the controls that never ran.
        """
        ran = [c.stage.value for c in self.controls if c.ran]
        missing = [c.stage.value for c in self.controls if not c.ran]
        parts = [f"Controls run: {', '.join(ran) if ran else 'none'}."]
        if missing:
            parts.append(f"Did NOT run: {', '.join(missing)}.")
        return " ".join(parts)
