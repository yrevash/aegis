"""Audio guarding — transcribe first, then run the **full** text rail stack.

**Why this order.** Every attack that works in text works when spoken, and the
text rails are the mature ones: signatures, the injection classifier, the
MLCommons content-safety screen, PII, schema, topical, and any custom rail the
host added. Building a parallel "audio policy" would mean re-implementing all of
that against a weaker signal, and the two would drift. Transcribe-then-guard
reuses the whole stack unchanged, and everything the operator already configured
applies to voice on the day voice is switched on.

**Why transcription is not implemented here.** Speech-to-text is a model
concern, not a policy concern — it belongs to whichever module owns the ASR
backend. This module defines the *contract* (:data:`Transcriber`, an injected
callable) and nothing else, exactly as the text rails take an injected
:class:`~aegis.core.interfaces.ChatCompleter` instead of hard-wiring a provider.

**Fail closed.** With no transcriber wired there is no transcript, and with no
transcript the rails have nothing to judge. Audio is then **blocked** with a
verdict that says the control did not run — never passed through on the
assumption that someone downstream will handle it.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable

from aegis.core.types import GuardResult, GuardVerdict
from aegis.media import AudioPayload

logger = logging.getLogger(__name__)

#: Turns an audio payload into text. Sync or async. Supplied by the host (or by
#: whichever module owns ASR); this package never implements or imports one.
Transcriber = Callable[[AudioPayload], "str | Awaitable[str]"]

#: The rail label that appears in verdicts and the trace panel.
LAYER = "media_audio"


async def transcribe(payload: AudioPayload, transcriber: Transcriber) -> str:
    """Run ``transcriber`` over ``payload``, awaiting it when it is async.

    Args:
        payload: The audio payload.
        transcriber: The injected transcription callable.

    Returns:
        The transcript text.

    Raises:
        Exception: Whatever the transcriber raises — the caller decides the fail
            direction (:func:`guard_audio` fails closed).
    """
    result = transcriber(payload)
    if inspect.isawaitable(result):
        result = await result
    return str(result)


async def guard_audio(
    payload: AudioPayload,
    *,
    transcriber: Transcriber | None,
    text_check: Callable[[str], Awaitable[GuardResult]],
) -> GuardResult:
    """Transcribe ``payload`` and screen the transcript with the full text stack.

    Args:
        payload: The audio payload to guard.
        transcriber: The injected transcriber, or ``None`` (blocks, fail-closed).
        text_check: The complete text rail stack — in practice
            :meth:`aegis.guardrails.Guardrails.check_input`, so every configured
            rail (including custom ones) applies to the transcript unchanged.

    Returns:
        The text stack's :class:`~aegis.core.types.GuardResult`, with ``layer``
        prefixed so a reader can see the verdict came via the transcript rather
        than from typed input. A missing or failing transcriber is a BLOCK.
    """
    if transcriber is None:
        logger.warning(
            "Audio payload received with no transcriber configured; blocking (fail-closed)."
        )
        return GuardResult(
            verdict=GuardVerdict.BLOCK,
            reason="Audio cannot be guarded without transcription, and no transcriber is "
            "configured. The text rails never saw this payload; blocked (fail-closed).",
            text="",
            layer=LAYER,
        )
    try:
        transcript = await transcribe(payload, transcriber)
    except Exception as exc:  # noqa: BLE001 - a failed transcription must fail closed
        logger.warning("Audio transcription failed; blocking (fail-closed).", exc_info=True)
        return GuardResult(
            verdict=GuardVerdict.BLOCK,
            reason=f"Audio transcription failed, so the text rails could not run: {exc}. "
            "Blocked (fail-closed).",
            text="",
            layer=LAYER,
        )

    result = await text_check(transcript)
    return result.model_copy(
        update={
            "layer": f"{LAYER}:{result.layer}" if result.layer else LAYER,
            "reason": f"[transcript] {result.reason}",
        }
    )
