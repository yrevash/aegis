"""Custom NeMo Guardrails actions — the bridge from Colang to our Python rails.

Each ``@action`` here is referenced by an ``execute`` in ``rails/*.co`` and
delegates to the same pure/API checks that back
:func:`aegis.guardrails.pipeline.check_input` and
:func:`aegis.guardrails.pipeline.check_output`. Keeping the logic in one place
means the declarative Colang policy and the fast programmatic API can never
drift apart.

Actions are declared ``is_system_action=True`` so NeMo runs them locally and
injects the conversation ``context`` (from which we read ``user_message`` /
``bot_message``). They return either a boolean (allow/deny, used by the Colang
``if not $x`` branches) or the redacted string (assigned back to the message
variable).

``nemoguardrails`` is accessed via :func:`aegis.core.lazy.require`: this module is only
imported at live-integration time (via :mod:`aegis.guardrails.nemo`), never by the
offline unit tests. Verified against NeMo Guardrails 0.23, Colang 1.0.
"""

from __future__ import annotations

from aegis.core.lazy import require
from aegis.guardrails import classifier, content_safety, grounding, pii, schema, topical

nemoguardrails_actions = require("aegis[nemo]", "nemoguardrails.actions")
action = nemoguardrails_actions.action


def _completer():  # noqa: ANN202 - returns aegis.core.interfaces.ChatCompleter | None
    """Return the host-wired ``ChatCompleter`` for the model-based rails, if any.

    Read lazily (per call) from :mod:`aegis.guardrails.nemo` so the actions pick up
    whatever completer the host wired via ``nemo.set_completer`` — exactly how the
    programmatic pipeline (:class:`aegis.guardrails.Guardrails`) receives its
    completer. ``None`` (the offline default) runs the deterministic backstop only.
    """
    from aegis.guardrails import nemo

    return nemo.get_completer()


@action(is_system_action=True)
async def validate_input_schema(context: dict | None = None) -> bool:
    """Return ``True`` if the inbound user message is structurally valid.

    Args:
        context: The NeMo conversation context (``user_message`` is read).

    Returns:
        Whether the message passes schema/format validation.
    """
    text = (context or {}).get("user_message", "")
    return schema.validate_input_format(text).ok


@action(is_system_action=True)
async def redact_pii_input(context: dict | None = None) -> str:
    """Return the inbound user message with any PII masked.

    Args:
        context: The NeMo conversation context (``user_message`` is read).

    Returns:
        The redacted message text.
    """
    text = (context or {}).get("user_message", "")
    redacted, _ = pii.redact(text)
    return redacted


@action(is_system_action=True)
async def self_check_injection(context: dict | None = None) -> bool:
    """Return ``True`` if the inbound message is *safe* (not prompt injection).

    PII is redacted before the injection check so no secret would leak to a
    model-based classifier call.

    The full model-based layer runs when the host has wired a ``ChatCompleter``
    via :func:`aegis.guardrails.nemo.set_completer` (the backend shim does this
    with its cost-routed cheap-model gateway); with no completer wired the
    deterministic signature backstop runs on its own (logged, not silent — see
    :func:`aegis.guardrails.classifier.detect_injection`). This is the same
    completer the programmatic pipeline
    (:func:`aegis.guardrails.pipeline.Guardrails.check_input`) uses, so the two
    front doors enforce identically.

    Args:
        context: The NeMo conversation context (``user_message`` is read).

    Returns:
        ``True`` when safe to proceed, ``False`` to block. Fails closed.
    """
    text = (context or {}).get("user_message", "")
    redacted, _ = pii.redact(text)
    verdict = await classifier.detect_injection(redacted, completer=_completer())
    return not verdict.injection


@action(is_system_action=True)
async def self_check_content_safety(context: dict | None = None) -> bool:
    """Return ``True`` if the message clears the MLCommons content-safety screen.

    Screens against the MLCommons / Llama Guard hazard taxonomy (S1–S13) via
    :func:`aegis.guardrails.content_safety.screen_content`, delegating to the same
    primitive the programmatic pipeline uses on both the inbound and outbound path
    — so the declarative NeMo policy covers the same layers. Reads ``bot_message``
    on the output rail, falling back to ``user_message`` on the input rail. PII is
    redacted first so no secret reaches a model-based safety call. Fails closed.

    Args:
        context: The NeMo conversation context (``bot_message``/``user_message``).

    Returns:
        ``True`` when safe to proceed, ``False`` to block.
    """
    ctx = context or {}
    text = ctx.get("bot_message") or ctx.get("user_message", "")
    redacted, _ = pii.redact(text)
    verdict = await content_safety.screen_content(redacted, completer=_completer())
    return not verdict.unsafe


@action(is_system_action=True)
async def self_check_topic(context: dict | None = None) -> bool:
    """Return ``True`` if the inbound message is within the configured domain.

    Advisory dialog rail (OWASP LLM01-adjacent). Delegates to
    :func:`aegis.guardrails.topical.screen_topic`, reading the host-wired business
    domain from :func:`aegis.guardrails.nemo.get_allowed_topics` (mirroring how the
    completer is wired). When no ``allowed_topics`` are configured the rail is a
    no-op and returns ``True``. Advisory by default (``block=False``): an off-topic
    query returns ``False`` for the flow to *note* — the bundled flow does not
    ``stop`` — so a legitimate blind-domain demo is never broken. PII is redacted
    first so nothing sensitive reaches the self-check.

    Args:
        context: The NeMo conversation context (``user_message`` is read).

    Returns:
        ``True`` when on-topic or the rail is disabled, ``False`` when off-topic.
    """
    from aegis.guardrails import nemo

    text = (context or {}).get("user_message", "")
    redacted, _ = pii.redact(text)
    verdict = await topical.screen_topic(
        redacted, allowed_topics=nemo.get_allowed_topics(), completer=_completer()
    )
    return verdict.on_topic


@action(is_system_action=True)
async def self_check_grounding(context: dict | None = None) -> bool:
    """Return ``True`` if the answer is grounded in the retrieved context passages.

    Advisory output rail (OWASP LLM09 Misinformation). Delegates to
    :func:`aegis.guardrails.grounding.check_grounding`, the SOTA self-check-facts
    pattern. Reads the answer from ``bot_message`` and the retrieved passages from
    the NeMo ``relevant_chunks`` context variable (a string or list). When no
    contexts are present the rail is a no-op and returns ``True``. Advisory by
    default (``block=False``): an ungrounded answer returns ``False`` for the flow
    to *note* — the bundled flow does not ``stop``.

    Args:
        context: The NeMo conversation context (``bot_message`` + ``relevant_chunks``).

    Returns:
        ``True`` when grounded or the rail is disabled, ``False`` when ungrounded.
    """
    ctx = context or {}
    answer = ctx.get("bot_message", "")
    chunks = ctx.get("relevant_chunks")
    if isinstance(chunks, str):
        contexts = [chunks] if chunks.strip() else []
    elif isinstance(chunks, list):
        contexts = [c for c in chunks if isinstance(c, str)]
    else:
        contexts = []
    verdict = await grounding.check_grounding(answer, contexts, completer=_completer())
    return verdict.grounded


@action(is_system_action=True)
async def validate_output_schema(context: dict | None = None) -> bool:
    """Return ``True`` if the model's answer passes schema + content filter.

    Args:
        context: The NeMo conversation context (``bot_message`` is read).

    Returns:
        Whether the output is structurally valid and clears the content filter.
    """
    text = (context or {}).get("bot_message", "")
    return schema.validate_output_format(text).ok and schema.content_filter(text).ok


@action(is_system_action=True)
async def redact_pii_output(context: dict | None = None) -> str:
    """Return the model's answer with any PII masked.

    Args:
        context: The NeMo conversation context (``bot_message`` is read).

    Returns:
        The redacted answer text.
    """
    text = (context or {}).get("bot_message", "")
    redacted, _ = pii.redact(text)
    return redacted


# ── Media actions (images + audio) ───────────────────────────────────────────
#
# The Colang policy could not previously see a non-text payload at all, so an
# image reached the model with no rail in front of it. These three actions give
# the declarative policy the same media coverage the programmatic pipeline has.
# The payload arrives as a JSON dict on the NeMo ``media`` context variable (see
# ``aegis.guardrails.nemo.nemo_check_media_input``), because a Colang flow can
# only be handed structured data through the conversation context.
#
# All three are no-ops (``True``) on a turn that carries no media, so the bundled
# text flows are unaffected by their presence in the rail list.


def _media_payload(context: dict | None):  # noqa: ANN202 - aegis.media.MediaPayload | None
    """Rebuild the media payload from the NeMo context, or ``None`` when absent.

    Raises:
        ValueError: If the context carries something that does not validate as a
            payload. The callers turn that into a *block*: a malformed payload is
            an unscreened payload.
    """
    from aegis.media import payload_from_context

    return payload_from_context((context or {}).get("media"))


@action(is_system_action=True)
async def check_media_hygiene(context: dict | None = None) -> bool:
    """Return ``True`` if the turn's media payload passes payload hygiene.

    Size cap, MIME truth (magic bytes vs the declared type) and the
    decompression-bomb guard — all pure, offline, and run before any model call.

    Args:
        context: The NeMo conversation context (``media`` is read).

    Returns:
        ``True`` when clean or there is no media; ``False`` to block. Fails closed
        on a malformed payload.
    """
    from aegis.media import inspect_payload

    try:
        payload = _media_payload(context)
    except Exception:  # noqa: BLE001 - an unparseable payload is an unscreened one
        return False
    if payload is None:
        return True
    return inspect_payload(payload).ok


@action(is_system_action=True)
async def self_check_media_injection(context: dict | None = None) -> bool:
    """Return ``True`` if the turn's image carries no instructions aimed at the model.

    Delegates to :func:`aegis.guardrails.media.screen_image` — the same cheap
    vision screen the programmatic pipeline runs — using the vision completer the
    host wired via :func:`aegis.guardrails.nemo.set_vision_completer`. With no
    vision completer the screen cannot run and this returns ``False`` (**fail
    closed**): there is no offline backstop for pixels, so an unscreened image is
    blocked rather than passed.

    Args:
        context: The NeMo conversation context (``media`` is read).

    Returns:
        ``True`` when safe to proceed or the turn has no image; ``False`` to block.
    """
    from aegis.guardrails import nemo
    from aegis.guardrails.media import screen_image
    from aegis.media import ImagePayload

    try:
        payload = _media_payload(context)
    except Exception:  # noqa: BLE001 - fail closed
        return False
    if not isinstance(payload, ImagePayload):
        return True
    verdict = await screen_image(payload, completer=nemo.get_vision_completer())
    return not verdict.injection


@action(is_system_action=True)
async def self_check_media_transcript(context: dict | None = None) -> bool:
    """Return ``True`` if the turn's audio transcribes to text the rails accept.

    Transcribe-then-guard: the injected transcriber (wired via
    :func:`aegis.guardrails.nemo.set_transcriber`) produces the transcript, and the
    **full** programmatic text stack screens it, so every rail the operator
    configured applies to speech unchanged. No transcriber means no transcript
    means no screening — which blocks (fail closed).

    Args:
        context: The NeMo conversation context (``media`` is read).

    Returns:
        ``True`` when the transcript clears the text rails or the turn has no
        audio; ``False`` to block.
    """
    from aegis.core.types import GuardVerdict
    from aegis.guardrails import nemo
    from aegis.guardrails.media import guard_audio
    from aegis.guardrails.pipeline import Guardrails
    from aegis.media import AudioPayload

    try:
        payload = _media_payload(context)
    except Exception:  # noqa: BLE001 - fail closed
        return False
    if not isinstance(payload, AudioPayload):
        return True
    guards = Guardrails(completer=nemo.get_completer())
    result = await guard_audio(
        payload, transcriber=nemo.get_transcriber(), text_check=guards.check_input
    )
    return result.verdict is not GuardVerdict.BLOCK
