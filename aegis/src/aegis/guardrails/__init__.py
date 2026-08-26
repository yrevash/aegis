"""Aegis guardrails — SOTA, LLM-agnostic input/output rails.

Standalone usage::

    from aegis.guardrails import check_input
    result = await check_input("... user text ...", completer=my_completer)

``completer`` is any :class:`aegis.core.interfaces.ChatCompleter`; omit it to run
deterministic-only injection screening (the model layer logs that it is off).
"""

from __future__ import annotations

from aegis.core.interfaces import ChatCompleter
from aegis.core.types import GuardResult
from aegis.guardrails import content_safety, grounding, pii, schema, topical
from aegis.guardrails.content_safety import (
    HAZARD_CATEGORIES,
    ContentSafetyVerdict,
    screen_content,
)
from aegis.guardrails.grounding import GroundingVerdict, check_grounding
from aegis.guardrails.media import MediaGuardResult, MediaScreen, media_rail, screen_image
from aegis.guardrails.memory_write import MemoryWriteCandidate
from aegis.guardrails.pipeline import (
    AnyRail,
    Guardrails,
    LegacyTextRail,
    Rail,
    RailDescription,
)
from aegis.guardrails.topical import TopicVerdict, screen_topic
from aegis.media import MediaPayload


async def check_input(
    text: str | MediaPayload,
    *,
    completer: ChatCompleter | None = None,
    vision_completer: ChatCompleter | None = None,
) -> GuardResult:
    """Screen inbound ``text`` with a fresh :class:`Guardrails` pipeline.

    Args:
        text: The inbound user input — text, or a :class:`~aegis.media.MediaPayload`.
        completer: Optional chat completer for model-based injection detection.
            If None, only deterministic injection signatures are checked.
        vision_completer: Optional vision-capable completer for the image-injection
            screen. Without it, image payloads fail **closed** (blocked): unlike
            text, pixels have no offline signature backstop to degrade to.

    Returns:
        A GuardResult with the verdict and potentially redacted text.
    """
    return await Guardrails(
        completer=completer, vision_completer=vision_completer
    ).check_input(text)


async def check_output(
    text: str | MediaPayload,
    *,
    completer: ChatCompleter | None = None,
    vision_completer: ChatCompleter | None = None,
) -> GuardResult:
    """Screen outbound ``text`` with a fresh :class:`Guardrails` pipeline.

    Args:
        text: The outbound model response — text, or a media payload.
        completer: Optional chat completer for model-based injection detection.
            If None, only deterministic injection signatures are checked.
        vision_completer: Optional vision-capable completer for the image-injection
            screen (image payloads fail closed without one).

    Returns:
        A GuardResult with the verdict and potentially redacted text.
    """
    return await Guardrails(
        completer=completer, vision_completer=vision_completer
    ).check_output(text)


async def check_memory_write(
    text: str,
    *,
    subject: str = "",
    predicate: str = "",
    object: str = "",  # noqa: A002 - the triple's own field name
    origin: str = "consolidation",
    completer: ChatCompleter | None = None,
) -> GuardResult:
    """Screen a candidate memory fact with a fresh :class:`Guardrails` pipeline.

    The fourth rail stage (:attr:`~aegis.core.types.GuardStage.MEMORY_WRITE`). Run this
    over anything on its way into the durable store. It exists because the other three
    stages structurally cannot see this attack: the poisoning message is ordinary
    conversation the INPUT rail rightly passes, and the fact it becomes is read back on
    a *later* turn as this platform's own belief. OWASP ASI06.

    This returns the bare :class:`GuardResult` for callers — like the red-team runner —
    that only need the verdict. The write path uses
    :meth:`Guardrails.check_memory_write`, which hands back the **rewritten fields**,
    because a caller that stores the strings it passed in has not redacted anything.

    Args:
        text: The rendered sentence a retriever will later put in front of a model.
        subject: The triple's subject, screened alongside the text.
        predicate: The triple's predicate.
        object: The triple's object.
        origin: ``"consolidation"`` or ``"operator:<username>"``, named in the rationale
            so a refusal is attributable to the same actor a successful write is.
        completer: Optional chat completer for model-based injection detection. Without
            one only deterministic signatures run — which is a real limit, not a
            formality: a policy override phrased as an ordinary business sentence
            carries no signature at all.

    Returns:
        A GuardResult; BLOCK means the fact must not be stored.
    """
    verdict = await Guardrails(completer=completer).check_memory_write(
        MemoryWriteCandidate(
            subject=subject, predicate=predicate, object=object, text=text, origin=origin
        )
    )
    return verdict.result


async def check_tool_result(
    text: str | MediaPayload,
    *,
    tool_name: str | None = None,
    completer: ChatCompleter | None = None,
    vision_completer: ChatCompleter | None = None,
) -> GuardResult:
    """Screen a tool's output with a fresh :class:`Guardrails` pipeline.

    The third rail stage (:attr:`~aegis.core.types.GuardStage.TOOL_RESULT`): run this
    over anything a tool returns *before* it is put into an agent's context. Web
    search content is the case that matters — it is arbitrary third-party text that
    the model reads as context, and nothing else in the turn screens it.

    Args:
        text: The tool's output — text, or a :class:`~aegis.media.MediaPayload`.
        tool_name: Optional name of the tool that produced ``text``, recorded in the
            rationale.
        completer: Optional chat completer for model-based injection detection.
            If None, only deterministic injection signatures are checked.
        vision_completer: Optional vision-capable completer for the image-injection
            screen (image payloads fail closed without one).

    Returns:
        A GuardResult; BLOCK means the content must not reach the agent's context.
    """
    return await Guardrails(
        completer=completer, vision_completer=vision_completer
    ).check_tool_result(text, tool_name=tool_name)


async def run_guards(
    input_text: str, output_text: str, *, completer: ChatCompleter | None = None
) -> tuple[GuardResult, GuardResult]:
    """Run both rails and return ``(input_verdict, output_verdict)``.

    Args:
        input_text: The inbound user input text to screen.
        output_text: The outbound model response text to screen.
        completer: Optional chat completer for model-based injection detection.
            If None, only deterministic injection signatures are checked.

    Returns:
        A tuple of (input_guard_result, output_guard_result).
    """
    g = Guardrails(completer=completer)
    return await g.check_input(input_text), await g.check_output(output_text)


__all__ = [
    "AnyRail",
    "ContentSafetyVerdict",
    "GroundingVerdict",
    "Guardrails",
    "HAZARD_CATEGORIES",
    "LegacyTextRail",
    "MediaGuardResult",
    "MediaScreen",
    "Rail",
    "RailDescription",
    "TopicVerdict",
    "check_grounding",
    "check_input",
    "check_output",
    "check_memory_write",
    "check_tool_result",
    "content_safety",
    "grounding",
    "media_rail",
    "pii",
    "run_guards",
    "schema",
    "screen_content",
    "screen_image",
    "screen_topic",
    "topical",
]
