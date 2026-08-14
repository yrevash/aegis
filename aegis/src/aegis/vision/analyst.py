"""The seam between this module and whatever actually calls a vision model.

:mod:`aegis.vision` decides *policy* — what must clear before pixels reach a
model, and in what order. It must not own a provider: the Module Contract makes
every leaf importable without the gateway, and this leaf is no exception. So the
main analysis call arrives as an injected callable, exactly as
:class:`~aegis.core.interfaces.ChatCompleter` arrives in the guardrails.

The reason this is a bespoke Protocol rather than a reuse of ``ChatCompleter`` is
cost. A ``ChatCompleter`` returns a bare ``str``, which would throw away the
usage the console is required to show — and a vision call is the most expensive
thing this platform does per byte. :class:`AnalystReply` therefore carries the
text *and* what it cost, and the host maps its gateway's ``Usage`` onto it.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from aegis.vision.types import VisionUsage


class AnalystReply(BaseModel):
    """One vision-model answer plus its billable accounting.

    Attributes:
        text: The model's analysis of the image.
        usage: What the call cost, in the call's own billable units.
    """

    model_config = ConfigDict(frozen=True)

    text: str = Field(default="", description="The model's analysis (may be empty).")
    usage: VisionUsage = Field(default_factory=VisionUsage)


@runtime_checkable
class VisionAnalyst(Protocol):
    """An async callable that runs one multimodal completion and reports its cost.

    ``messages`` is already in OpenAI multimodal content-block form (see
    :func:`aegis.vision.prompts.analysis_messages`), which is the shape
    ``aegis.gateway.complete`` forwards verbatim to litellm — so the host's
    adapter is a handful of lines and no gateway change is needed.
    """

    async def __call__(self, messages: list[dict]) -> AnalystReply:
        """Run the multimodal call and return the answer plus its usage."""
        ...
