"""Backend shim: image understanding lives in the standalone ``aegis.vision``.

``aegis.vision`` owns the ordering that makes vision safe — payload hygiene →
image-injection screen → image PII → the model → the output rails — and owns no
provider, because a leaf that imported this platform's gateway would stop being
importable on its own. This module is the **host adapter**, and it is the only
place under ``app`` that knows both halves:

* the **screen completer** and the **analyst** are both ``ModelRole.VISION``
  calls through ``app.core.llm.complete``. That role already routes to the fleet's
  hosted ``genailab-maas-Llama-3.2-90B-Vision-Instruct`` deployment and is already
  priced, and ``complete`` forwards ``messages`` verbatim to litellm — so the
  OpenAI-style multimodal content blocks ``aegis.vision`` builds need no gateway
  change at all. **Fleet models only**: nothing here downloads or runs a local
  vision model, and the isolation test in ``aegis/tests/vision`` pins that.
* the **output rails** are the platform's own configured stack
  (``app.guardrails.check_output``), so a vision answer is screened by exactly the
  same rails as every other model answer — not a parallel, weaker copy.

**The image-PII rail is opt-in on availability, and says so out loud.** It needs
``presidio-image-redactor`` (OCR + OpenCV, the ``aegis[media]`` extra). Rather
than pretend, this module enables the rail only when the package is importable
and otherwise lets ``aegis.vision`` report the stage as ``not_run`` with the
install command in the detail — which the console renders. A control that did not
run is shown as a control that did not run; it is never dressed up as a clean scan.
"""

from __future__ import annotations

import base64
import binascii
import logging
from importlib.util import find_spec

from aegis.media import ImagePayload, MediaSource, Provenance
from aegis.vision import AnalystReply, VisionAnalyser, VisionAnalysis, VisionUsage

logger = logging.getLogger(__name__)

#: Deployments this platform is allowed to call for vision — the hosted fleet
#: model behind ``ModelRole.VISION``. Documented here so the policy is readable at
#: the one place a vision call is actually issued.
FLEET_VISION_ROLE = "VISION"


def image_pii_available() -> bool:
    """Whether the image-PII rail's OCR stack is installed in this deployment.

    Checked rather than assumed. ``False`` does not silently disable a control:
    :mod:`aegis.vision` reports the stage as ``not_run`` with the install command,
    and the console shows that line beside the ones that did run.
    """
    return find_spec("presidio_image_redactor") is not None


def decode_image(
    image_base64: str, *, mime_type: str, filename: str | None = None
) -> ImagePayload:
    """Turn a base64 upload into an :class:`~aegis.media.ImagePayload`.

    The declared ``mime_type`` is carried, never trusted — ``aegis.media``'s
    hygiene rail sniffs the magic bytes and refuses a payload whose declaration
    disagrees with its content. Provenance is ``USER_UPLOAD`` because that is what
    this endpoint is; an image arriving from retrieval or a tool is tagged
    differently.

    Note the limit honestly: provenance is **carried and reported, not yet acted on**.
    :attr:`aegis.media.Provenance.untrusted` classifies RETRIEVAL/TOOL_OUTPUT/UNKNOWN as
    attacker-controlled, but no rail currently branches on it — every payload gets the
    same screening regardless of origin, and the value is only emitted on the
    ``guardrail_media`` event. Differential treatment by provenance is unbuilt.

    Args:
        image_base64: The image bytes, base64-encoded (a bare payload or a
            ``data:`` URL, which browsers produce from ``FileReader``).
        mime_type: The client's declared content type.
        filename: Optional original filename, kept for the audit trail only.

    Returns:
        The payload.

    Raises:
        ValueError: If the string is not valid base64 or decodes to nothing.
    """
    raw = image_base64.strip()
    if raw.startswith("data:"):
        _, _, raw = raw.partition(",")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"image_base64 is not valid base64: {exc}") from exc
    if not data:
        raise ValueError("image_base64 decoded to zero bytes")
    return ImagePayload(
        data=data,
        mime_type=mime_type,
        provenance=Provenance(source=MediaSource.USER_UPLOAD, origin=filename),
    )


async def _vision_completer(
    messages: list[dict], *, response_format: dict | None = None
) -> str:
    """Adapt ``app.core.llm.complete`` to the ``ChatCompleter`` protocol for the screen.

    Imports are deferred so importing this package never requires the gateway.
    """
    from app.core.llm import complete
    from app.core.models import ModelRole

    result = await complete(
        ModelRole.VISION, messages, temperature=0.0, response_format=response_format
    )
    return result.content


async def _analyst(messages: list[dict]) -> AnalystReply:
    """Run the main multimodal call and map the gateway's usage onto the module's.

    The mapping is deliberate rather than a passthrough: ``aegis.vision`` is a leaf
    and must not import ``aegis.gateway`` to state what a call cost. ``cost_source``
    travels with the number so a ``$0.00`` is never ambiguous — "unpriced" means
    billable work nobody could price, which is not the same claim as a real zero.
    """
    from app.core.llm import complete
    from app.core.models import ModelRole

    result = await complete(ModelRole.VISION, messages, temperature=0.0)
    usage = result.usage
    return AnalystReply(
        text=result.content,
        usage=VisionUsage(
            model=result.model,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            images=usage.images,
            cost_usd=usage.cost_usd,
            cost_source=str(usage.cost_source),
        ),
    )


async def _output_rails(text: str):  # noqa: ANN202 - aegis GuardResult, imported lazily
    """Screen the model's answer with the platform's own configured output rails."""
    from app.guardrails import check_output

    return await check_output(text)


def build_analyser() -> VisionAnalyser:
    """Wire the platform's gateway and rails into the ``aegis.vision`` pipeline.

    A fresh instance per call: the analyser holds no per-call state, and building
    it here (rather than at import) keeps this module importable with no gateway
    configured, which the offline test suite depends on.
    """
    return VisionAnalyser(
        screen_completer=_vision_completer,
        analyst=_analyst,
        output_check=_output_rails,
        image_pii=image_pii_available(),
    )


async def analyse(
    image_base64: str,
    question: str,
    *,
    mime_type: str = "image/png",
    filename: str | None = None,
) -> VisionAnalysis:
    """Decode an uploaded image and run it through the full vision pipeline.

    Args:
        image_base64: The image bytes, base64-encoded.
        question: What to ask about the image.
        mime_type: The client's declared content type (verified, not trusted).
        filename: Optional original filename, for the audit trail.

    Returns:
        The itemised :class:`~aegis.vision.VisionAnalysis`.

    Raises:
        ValueError: If the base64 payload is malformed.
    """
    payload = decode_image(image_base64, mime_type=mime_type, filename=filename)
    return await build_analyser().analyse(payload, question)


__all__ = [
    "FLEET_VISION_ROLE",
    "VisionAnalysis",
    "analyse",
    "build_analyser",
    "decode_image",
    "image_pii_available",
]
