"""Composed input/output guardrail pipeline that emits its work as events.

Mirrors the legacy layered order (schema → PII redaction → injection on input;
schema → content filter → PII on output), but is LLM-agnostic (an injected
:class:`ChatCompleter`) and streams :class:`StepStarted` → :class:`GuardrailEvent`
→ :class:`StepFinished` so the frontend can render each step live.
"""

from __future__ import annotations

import copy
import hashlib
import inspect
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import TYPE_CHECKING

from aegis.core.config import AegisMode, CoreSettings
from aegis.core.events import AegisEvent, GuardrailEvent, SpanKind, StepFinished, StepStarted
from aegis.core.interfaces import ChatCompleter
from aegis.core.registry import register
from aegis.core.types import GuardResult, GuardVerdict, InjectionVerdict
from aegis.guardrails import pii, schema
from aegis.guardrails.cache import (
    InjectionCache,
    InMemoryInjectionCache,
    make_injection_cache,
)
from aegis.guardrails.classifier import (
    classify_injection,
    detect_injection,
    deterministic_injection,
)
from aegis.guardrails.content_safety import screen_content
from aegis.guardrails.grounding import check_grounding
from aegis.guardrails.media import MediaScreen, call_rail
from aegis.guardrails.media.audio import Transcriber
from aegis.guardrails.media.types import MediaGuardResult
from aegis.guardrails.policy import GuardrailPolicy
from aegis.guardrails.topical import screen_topic
from aegis.media import MediaKind, MediaLimits, MediaPayload, TextPayload, as_payload

if TYPE_CHECKING:
    from aegis.core.stream import AegisEmitter

logger = logging.getLogger(__name__)

_MODULE_ID = "guardrails"

#: The rail name a *finding* is filed under: a screen ran, looked at the text, and judged
#: it an injection attempt.
INJECTION_LAYER = "injection"

#: The rail name an **unchecked** refusal is filed under. A distinct value rather than a
#: flag inside the reason string, because the console keys its label off ``layer`` and the
#: two outcomes must not share one. See :func:`_injection_block`.
INJECTION_UNAVAILABLE_LAYER = "injection_unavailable"


def _injection_block(verdict: InjectionVerdict, redacted: str) -> GuardResult:
    """Render an ``injection=True`` verdict as the BLOCK the caller is shown.

    **The whole point of this function is that it has two branches.** A verdict with
    ``checked=True`` is a finding — a screen read the text and judged it an attack, and
    saying so is exactly right. A verdict with ``checked=False`` is *not* a finding: no
    screen could be completed, the rail fails closed, and the request was refused
    unexamined. Rendering the second as the first is what made a deployment with no model
    gateway tell every user that their question was a prompt-injection attempt — the
    worst sentence this system can produce, because it is an accusation and it is false.

    Both are still a :attr:`~aegis.core.types.GuardVerdict.BLOCK`: failing closed is not
    negotiable and is not what changes here. What changes is the sentence, and the
    ``layer`` the console groups and labels it by.

    Args:
        verdict: The injection verdict, whose ``checked`` flag selects the branch.
        redacted: The PII-redacted text the rail saw, carried onto the result.

    Returns:
        The BLOCK :class:`~aegis.core.types.GuardResult` to return to the caller.
    """
    if verdict.checked:
        return GuardResult(
            verdict=GuardVerdict.BLOCK,
            reason=f"Prompt injection blocked: {verdict.reason}",
            text=redacted,
            layer=INJECTION_LAYER,
        )
    return GuardResult(
        verdict=GuardVerdict.BLOCK,
        reason=(
            "Request refused unchecked — the prompt-injection screen is unavailable, "
            f"not triggered: {verdict.reason}"
        ),
        text=redacted,
        layer=INJECTION_UNAVAILABLE_LAYER,
    )


def _default_injection_cache() -> InjectionCache:
    """Build the injection cache the mode calls for, degrading loudly, never raising.

    Honors ``AEGIS_MODE`` via :func:`~aegis.guardrails.cache.make_injection_cache`:
    Redis-backed in ``full`` mode (when a ``REDIS_URL`` is configured), in-memory
    otherwise. The cache is a pure optimization — it must never be the thing that
    stops a :class:`Guardrails` from constructing — so a full-mode Redis that cannot
    be built degrades to in-memory with a warning instead of the factory's hard raise.
    """
    settings = CoreSettings()
    if settings.mode is AegisMode.full and settings.redis_url:
        try:
            import redis  # lazy: keeps lite/tests infra-free

            client = redis.from_url(settings.redis_url)
            return make_injection_cache(settings.mode, redis_client=client)
        except Exception:  # noqa: BLE001 - the cache is an optimization; never block on it
            logger.warning(
                "InjectionCache: Redis unavailable; using in-memory (non-durable).",
                exc_info=True,
            )
            return InMemoryInjectionCache()
    # lite/auto — or full-without-URL — get the in-memory backend.
    mode = AegisMode.lite if settings.mode is AegisMode.full else settings.mode
    return make_injection_cache(mode)

#: A custom rail: given the (already PII-redacted) payload, return a GuardResult to
#: block/redact/flag, or ``None`` to abstain. Sync or async. Give it a distinct
#: ``layer`` and it streams to the console like any built-in rail — this is the
#: extension seam for domain-specific policies ("block competitor names",
#: "enforce a JSON contract", "no medical advice") without forking the pipeline.
#:
#: **Widened from ``Callable[[str], ...]``** so a rail can screen an image or an
#: audio clip, not only a string — a rail that cannot receive an image can never
#: guard one, which is how a multimodal request used to reach the model having
#: passed through no rail at all.
#:
#: Existing ``str``-taking rails keep working untouched:
#: :func:`aegis.guardrails.media.call_rail` inspects what each rail was written to
#: accept and hands it exactly that (a legacy rail still receives
#: ``payload.text``). New rails should annotate their parameter with
#: ``MediaPayload`` — or wear :func:`aegis.guardrails.media.media_rail` when the
#: annotation is not introspectable — and will then be handed the payload itself.
#: Legacy string rails are *skipped* for image/audio payloads (they cannot judge
#: them) and every skip is recorded in the verdict, never silently swallowed.
Rail = Callable[[MediaPayload], "GuardResult | None | Awaitable[GuardResult | None]"]

#: The pre-media rail signature. **Deprecated but supported**: it keeps working for
#: text payloads and is not scheduled for removal, but it can only ever see text.
#: Port a rail by annotating its parameter ``MediaPayload`` when you want it to
#: screen images or audio too.
LegacyTextRail = Callable[[str], "GuardResult | None | Awaitable[GuardResult | None]"]

#: What the pipeline actually accepts: either shape.
AnyRail = Rail | LegacyTextRail


def _clean_terms(values: Sequence[str] | None) -> tuple[str, ...]:
    """Normalise a configured string collection to a de-duplicated, order-stable tuple.

    Args:
        values: Raw terms/entity names from a host or a settings resolution.

    Returns:
        The non-empty, stripped members in first-seen order.
    """
    seen: dict[str, None] = {}
    for value in values or ():
        if isinstance(value, str) and value.strip():
            seen.setdefault(value.strip(), None)
    return tuple(seen)


@register("guardrail", "default")
class Guardrails:
    """SOTA, LLM-agnostic input/output guardrail pipeline.

    Extensible: pass ``input_rails`` / ``output_rails`` to bolt domain-specific
    rails onto the built-in defense-in-depth chain. Custom rails run after the
    built-ins and any non-PASS verdict they return short-circuits the chain and
    streams to the frontend with its own ``layer`` label.
    """

    def __init__(
        self,
        *,
        completer: ChatCompleter | None = None,
        input_rails: list[AnyRail] | None = None,
        output_rails: list[AnyRail] | None = None,
        allowed_topics: str | list[str] | None = None,
        topical_block: bool = False,
        ground_answers: bool = False,
        grounding_block: bool = False,
        denylist_terms: Sequence[str] | None = None,
        pii_entities: Sequence[str] | None = None,
        injection_cache: InjectionCache | None = None,
        vision_completer: ChatCompleter | None = None,
        media_limits: MediaLimits | None = None,
        image_pii: bool = False,
        image_analyzer: object = None,
        image_redactor: object = None,
        transcriber: Transcriber | None = None,
    ) -> None:
        """Create the pipeline.

        Args:
            completer: Optional chat completer for the model-based injection and
                content-safety self-checks. If None, only deterministic layers run.
            injection_cache: Optional cache for model-based injection verdicts, keyed
                on a hash of the (already PII-redacted) text. Defaults to
                :func:`_default_injection_cache` (Redis in full mode, in-memory
                otherwise). Only the model-classifier verdict is cached; deterministic
                signature hits run first and are never cached around. Cache errors
                fail open (treated as a miss) — the cache never blocks a check.
            input_rails: Optional custom input rails, run after the built-ins.
            output_rails: Optional custom output rails, run after the built-ins.
            allowed_topics: Optional description/list of the permitted business
                domain. When set, the topical rail screens each inbound query; an
                off-topic query is an advisory FLAG (does not stop the request)
                unless ``topical_block`` is set. When None/empty the rail is off.
            topical_block: Make the topical rail a hard BLOCK instead of advisory.
            ground_answers: Enable the output grounding self-check. It runs only
                when ``check_output`` is given retrieval ``contexts``; an
                ungrounded answer is an advisory FLAG unless ``grounding_block``.
            grounding_block: Make the grounding rail a hard BLOCK instead of advisory.
            denylist_terms: Terms whose presence is a BLOCK on the inbound, tool-result
                and outbound paths (:func:`aegis.guardrails.schema.denied_term`). The
                host's own floor; a tenant's ``guardrails.denylist.terms`` is unioned
                onto it per request by :meth:`with_policy`.
            pii_entities: Entity kinds to screen for **in addition to** the ones the
                live PII engine already covers. Again a floor: a tenant's
                ``guardrails.pii.entities`` is unioned onto it, never assigned over it.
            vision_completer: A vision-capable completer for the image-injection
                screen (:mod:`aegis.guardrails.media.injection`). Kept separate
                from ``completer`` because screening pixels needs a multimodal
                model and the text rails deliberately do not. **With none wired,
                images fail closed** — there is no offline backstop for pixels,
                so an unscreened image is blocked rather than passed.
            media_limits: Payload-hygiene thresholds (size cap, decompression-bomb
                guard, accepted MIME sets). Defaults to
                :class:`~aegis.media.MediaLimits`.
            image_pii: Enable the ``presidio-image-redactor`` rail, which returns an
                actually-redacted image rather than a meaningless "redact" verdict.
                Requires the ``aegis[media]`` extra; declared-but-missing raises
                :class:`ImportError` naming the install command.
            image_analyzer: Advanced/test seam — a Presidio ``ImageAnalyzerEngine``
                instance to use instead of building one (implies ``image_pii``).
            image_redactor: Advanced/test seam — a Presidio ``ImageRedactorEngine``.
            transcriber: Injected speech-to-text used to guard audio by
                transcribing it and running the **full** text rail stack over the
                transcript. Transcription itself belongs to the ASR module, not
                here. With none wired, audio is blocked (fail-closed).
        """
        self._completer = completer
        self._input_rails = list(input_rails or [])
        self._output_rails = list(output_rails or [])
        self._allowed_topics = allowed_topics
        self._topical_block = topical_block
        self._ground_answers = ground_answers
        self._grounding_block = grounding_block
        self._denylist_terms = _clean_terms(denylist_terms)
        self._pii_entities = _clean_terms(pii_entities)
        self._injection_cache = (
            injection_cache if injection_cache is not None else _default_injection_cache()
        )
        self._media = MediaScreen(
            vision_completer=vision_completer,
            limits=media_limits,
            image_pii=image_pii,
            image_analyzer=image_analyzer,
            image_redactor=image_redactor,
            transcriber=transcriber,
        )

    @property
    def policy(self) -> GuardrailPolicy:
        """The four per-tenant controls this pipeline is currently enforcing.

        Read by a host so it can fold a tenant's resolved settings **onto** what it
        wired, rather than replacing them — see
        :func:`aegis.settings.guardrails.resolve_guardrail_policy`.
        """
        return GuardrailPolicy(
            topical_block=self._topical_block,
            grounding_block=self._grounding_block,
            denylist_terms=self._denylist_terms,
            pii_entities=self._pii_entities,
        )

    def with_policy(self, policy: GuardrailPolicy) -> Guardrails:
        """Return a pipeline enforcing ``policy``, sharing this one's expensive parts.

        **The point is that it returns a new object.** The pipeline a host builds is
        process-wide and a tenant's rails are not: writing one tenant's resolved policy
        onto the singleton would apply it to every other tenant's next request, which is
        the exact class of defect — one tenant's resolution reused for another's run —
        that :mod:`aegis.settings.agent` exists to avoid for the agent's config. So a
        caller resolves per request, calls this, uses the result for that request, and
        drops it.

        The copy is shallow on purpose: the completer, the injection cache and the media
        screen are stateless-by-contract collaborators that are expensive to build and
        safe to share, and none of them is policy-dependent.

        Args:
            policy: The tenant's resolved controls. Callers are expected to have folded
                it onto :attr:`policy` already (tighten-only for the two booleans, union
                for the two collections), so this method assigns rather than merges.

        Returns:
            ``self`` when nothing changed, otherwise a new :class:`Guardrails`.
        """
        if policy == self.policy:
            return self
        clone = copy.copy(self)
        clone._topical_block = policy.topical_block
        clone._grounding_block = policy.grounding_block
        clone._denylist_terms = _clean_terms(policy.denylist_terms)
        clone._pii_entities = _clean_terms(policy.pii_entities)
        return clone

    def with_completer(self, completer: ChatCompleter | None) -> Guardrails:
        """Return a pipeline whose model-backed layers use ``completer``.

        The sibling of :meth:`with_policy`, and it exists for the same reason: the
        pipeline a host builds is process-wide, and *whether the model layers run* is
        a per-call decision. The red-team harness needs both answers over the same
        rails — an offline run must wire **no** completer so the report is honestly
        "our deterministic signatures blocked N of M" and costs nothing, and a live
        run must wire the host's, over the identical policy, so the two reports differ
        by the model layer and nothing else. Rebuilding a second ``Guardrails`` from
        the same fields would let the two drift on the next field somebody adds.

        Args:
            completer: The chat completer for the injection / content-safety /
                topical layers, or ``None`` to run the deterministic layers only.

        Returns:
            ``self`` when the completer is already the one asked for, otherwise a
            shallow copy — the injection cache and media screen are stateless-by-
            contract collaborators and safe to share, exactly as in :meth:`with_policy`.
        """
        if completer is self._completer:
            return self
        clone = copy.copy(self)
        clone._completer = completer
        return clone

    @staticmethod
    async def _run_custom(
        payload: MediaPayload, rails: list[AnyRail], *, skipped: list[str] | None = None
    ) -> GuardResult | None:
        """Run custom rails in order; return the first non-PASS verdict, else None.

        Each rail is invoked through :func:`aegis.guardrails.media.call_rail`, which
        hands a legacy ``str``-taking rail the text (identical to the pre-media
        behaviour) and a payload-taking rail the payload. A legacy rail faced with
        an image or audio payload is skipped and its reason appended to ``skipped``
        so the verdict can report it — a rail that did not run is never counted.
        """
        for rail in rails:
            on_skip = skipped.append if skipped is not None else None
            result = call_rail(rail, payload, on_skip=on_skip)
            if inspect.isawaitable(result):
                result = await result
            if result is not None and result.verdict is not GuardVerdict.PASS:
                return result
        return None

    async def _detect_injection_cached(
        self, text: str, *, emitter: AegisEmitter | None = None
    ) -> InjectionVerdict:
        """Screen ``text`` for injection, caching only the model-classifier verdict.

        Layer order is preserved exactly: the deterministic signatures run first
        (offline, no LLM) and a hit is returned immediately — **never cached around**,
        since caching a free, deterministic decision buys nothing. Only text that
        clears the signatures reaches the model classifier, and *that* verdict is
        cached keyed on a SHA-256 of the (already PII-redacted) text. On a hit the LLM
        call is skipped. Cache reads/writes fail open (a broken cache is a miss, never
        a block). Emits a ``guardrail_cache`` hit/miss event when an emitter is given.

        Args:
            text: The (already PII-redacted) user text to screen.
            emitter: Optional AG-UI emitter for the cache hit/miss event.

        Returns:
            The :class:`InjectionVerdict`; ``injection=True`` blocks the request.
        """
        hit = deterministic_injection(text)
        if hit is not None:
            return hit  # free, offline signature block — nothing to cache
        if self._completer is None:
            # Model layer explicitly disabled; defer to the classifier's own logged
            # no-completer path. No LLM call happens, so there is nothing to cache.
            return await detect_injection(text, completer=None)

        key = self._injection_cache_key(text)
        cached = self._cache_get(key)
        if cached is not None:
            verdict = self._parse_cached_verdict(cached)
            if verdict is not None:
                await self._emit_injection_cache(emitter, event="hit", verdict=verdict)
                return verdict
        verdict = await classify_injection(text, completer=self._completer)
        # **Only a verdict is cached.** A ``checked=False`` result is not one: it says the
        # screen could not be completed, and storing it would make one dead-gateway moment
        # outlive the outage — the same question would keep being refused, from cache,
        # long after the classifier came back, with no call left to notice the recovery.
        # An outage must cost a retry, never become a permanent answer.
        if verdict.checked:
            self._cache_set(key, verdict.model_dump_json())
        await self._emit_injection_cache(emitter, event="miss", verdict=verdict)
        return verdict

    @staticmethod
    def _injection_cache_key(text: str) -> str:
        """Return the cache key: a SHA-256 of the exact (redacted) text.

        Keyed on the text alone — the verdict is a pure function of that exact string,
        so there is no tenant/persona to leak (and no cross-tenant reuse risk).
        """
        return "inj:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str) -> str | None:
        """Read ``key`` from the injection cache, failing open (a miss) on any error."""
        try:
            return self._injection_cache.get(key)
        except Exception:  # noqa: BLE001 - a cache miss is safe; never block on the cache
            logger.warning(
                "Injection cache read failed; treating as a miss (fail-open).", exc_info=True
            )
            return None

    def _cache_set(self, key: str, value: str) -> None:
        """Write ``key`` to the injection cache, swallowing any error (fail-open)."""
        try:
            self._injection_cache.set(key, value)
        except Exception:  # noqa: BLE001 - a failed write just means no cache hit later
            logger.warning(
                "Injection cache write failed; continuing uncached (fail-open).", exc_info=True
            )

    @staticmethod
    def _parse_cached_verdict(raw: str) -> InjectionVerdict | None:
        """Rehydrate a stored verdict, returning None on any corruption (→ recompute)."""
        try:
            return InjectionVerdict.model_validate_json(raw)
        except Exception:  # noqa: BLE001 - a bad cache entry must not fail the check
            return None

    @staticmethod
    async def _emit_injection_cache(
        emitter: AegisEmitter | None, *, event: str, verdict: InjectionVerdict
    ) -> None:
        """Emit a ``guardrail_cache`` hit/miss CustomEvent (no-op without an emitter).

        Carries only the outcome (event + the boolean verdict), never the raw or
        redacted text — the cache is keyed on a hash, and the event stays PII-free.
        """
        if emitter is None:
            return
        from aegis.core import stream_names

        await emitter.custom(
            stream_names.GUARDRAIL_CACHE,
            {
                "event": event,
                "layer": INJECTION_LAYER,
                "injection": verdict.injection,
                # Carried beside ``injection`` so a reader of the raw stream can tell a
                # cached *finding* from a cached "we could not check" without re-reading
                # the prose. They are different facts; see :class:`InjectionVerdict`.
                "checked": verdict.checked,
            },
        )

    async def _screen_topical(self, text: str) -> GuardResult | None:
        """Run the topical rail; return a BLOCK/FLAG GuardResult, or None when off.

        Advisory by default (an off-topic query is a non-blocking FLAG); a hard
        BLOCK when ``topical_block`` is set. Returns None when unconfigured/on-topic.
        """
        if not self._allowed_topics:
            return None
        verdict = await screen_topic(
            text,
            allowed_topics=self._allowed_topics,
            completer=self._completer,
            block=self._topical_block,
        )
        if verdict.on_topic:
            return None
        outcome = GuardVerdict.BLOCK if self._topical_block else GuardVerdict.FLAG
        prefix = "Off-topic query blocked" if self._topical_block else "Off-topic query flagged"
        return GuardResult(
            verdict=outcome,
            reason=f"{prefix}: {verdict.reason}",
            text=text,
            layer="topical",
        )

    async def _screen_input(
        self, text: str, *, emitter: AegisEmitter | None = None
    ) -> tuple[GuardResult, list[GuardResult]]:
        """Run the input rail, returning ``(primary, advisories)``.

        ``primary`` is the blocking / redaction / pass verdict (never FLAG); a
        BLOCK short-circuits the chain. ``advisories`` collects non-blocking FLAGs
        (e.g. off-topic) so they can be streamed without stopping the request.

        ``emitter`` (when given) receives the ``guardrail_cache`` hit/miss event for
        the model-based injection layer.
        """
        fmt = schema.validate_input_format(text)
        if not fmt.ok:
            return (
                GuardResult(
                    verdict=GuardVerdict.BLOCK, reason=fmt.reason, text=text, layer="schema"
                ),
                [],
            )
        denied = schema.denied_term(text, self._denylist_terms)
        if not denied.ok:
            return (
                GuardResult(
                    verdict=GuardVerdict.BLOCK,
                    reason=denied.reason,
                    text=text,
                    layer="denylist",
                ),
                [],
            )
        redacted, kinds = pii.redact(text, entities=self._pii_entities)
        verdict = await self._detect_injection_cached(redacted, emitter=emitter)
        if verdict.injection:
            return (_injection_block(verdict, redacted), [])
        safety = await screen_content(redacted, completer=self._completer)
        if safety.unsafe:
            reason = f"Unsafe content blocked ({safety.label() or 'hazard'}): {safety.reason}"
            return (
                GuardResult(
                    verdict=GuardVerdict.BLOCK,
                    reason=reason,
                    text=redacted,
                    layer="content_safety",
                ),
                [],
            )
        advisories: list[GuardResult] = []
        topical = await self._screen_topical(redacted)
        if topical is not None:
            if topical.verdict is GuardVerdict.BLOCK:
                return topical, []
            advisories.append(topical)
        custom = await self._run_custom(TextPayload.of(redacted), self._input_rails)
        if custom is not None:
            return custom, advisories
        if kinds:
            return (
                GuardResult(
                    verdict=GuardVerdict.REDACT,
                    reason=f"Redacted PII on the inbound path: {', '.join(kinds)}.",
                    text=redacted,
                    layer="pii",
                    redactions=kinds,
                ),
                advisories,
            )
        return (
            GuardResult(
                verdict=GuardVerdict.PASS,
                reason="Input passed schema, PII, injection, and content-safety rails.",
                text=text,
            ),
            advisories,
        )

    async def _screen_media(
        self,
        payload: MediaPayload,
        rails: list[AnyRail],
        *,
        emitter: AegisEmitter | None = None,
    ) -> MediaGuardResult:
        """Run the media rail chain over a non-text payload and emit its verdict."""
        skipped: list[str] = []

        async def _custom(current: MediaPayload) -> GuardResult | None:
            return await self._run_custom(current, rails, skipped=skipped)

        result = await self._media.check(
            payload,
            text_check=self.check_input,
            custom=_custom if rails else None,
            skipped_custom=skipped,
        )
        await self._emit_media(emitter, payload, result)
        return result

    @staticmethod
    async def _emit_media(
        emitter: AegisEmitter | None, payload: MediaPayload, result: MediaGuardResult
    ) -> None:
        """Emit the ``guardrail_media`` CustomEvent (no-op without an emitter).

        Carries only metadata and the itemised rail coverage — never the bytes and
        never the decoded text, so the event stays safe to log and render.
        """
        if emitter is None:
            return
        from aegis.core import stream_names

        await emitter.custom(
            stream_names.GUARDRAIL_MEDIA,
            {
                "kind": payload.kind.value,
                "mime_type": payload.mime_type,
                "byte_size": payload.byte_size,
                "provenance": payload.provenance.source.value,
                "verdict": result.verdict.value,
                "layer": result.layer,
                "rails_run": result.rails_run,
                "rails_skipped": result.rails_skipped,
                "redactions": result.redactions,
            },
        )

    async def check_input(
        self, text: str | MediaPayload, *, emitter: AegisEmitter | None = None
    ) -> GuardResult:
        """Run the full input rail (schema → PII → injection → content → topical).

        Accepts a plain ``str`` (wrapped as a :class:`~aegis.media.TextPayload`,
        behaviour identical to before the media seam) or any
        :class:`~aegis.media.MediaPayload`. An image or audio payload is routed to
        the media chain (:class:`aegis.guardrails.media.MediaScreen`) instead — the
        one path that used to reach the model with no rail in front of it.

        Args:
            text: The inbound user input — text, or a media payload.
            emitter: Optional AG-UI emitter for the injection ``guardrail_cache``
                and ``guardrail_media`` events.

        Returns:
            A :class:`GuardResult` with verdict and potentially redacted text. When
            the core rails pass but an advisory (e.g. off-topic) fired, the
            non-blocking FLAG is surfaced as the result so the single-result path
            (the agent graph) still shows it; a BLOCK always takes precedence. For
            media payloads the result is a
            :class:`~aegis.guardrails.media.MediaGuardResult`, which additionally
            itemises which rails ran and carries any rewritten payload.
        """
        payload = as_payload(text)
        if payload.kind is not MediaKind.TEXT:
            return await self._screen_media(payload, self._input_rails, emitter=emitter)
        # A ``str`` caller is handed straight to the unchanged text path — no
        # encode/decode round-trip, so the rails screen the exact string given.
        raw = text if isinstance(text, str) else payload.text  # type: ignore[union-attr]
        primary, advisories = await self._screen_input(raw, emitter=emitter)
        if primary.verdict is GuardVerdict.PASS and advisories:
            return advisories[0]
        return primary

    async def check_tool_result(
        self,
        text: str | MediaPayload,
        *,
        tool_name: str | None = None,
        emitter: AegisEmitter | None = None,
    ) -> GuardResult:
        """Screen a tool's output **before** it is allowed into any agent's context.

        The third rail stage (:attr:`~aegis.core.types.GuardStage.TOOL_RESULT`). It is
        deliberately the *input* chain — schema → PII → injection → content-safety →
        topical → custom ``input_rails`` — and not a new pipeline: a web page, a
        scraped record or a search snippet is untrusted text arriving from outside,
        which is exactly what the inbound rails were built to judge. Injection
        screening is the point. Text that a model will read as instructions-adjacent
        context is the OWASP LLM01 surface, and a tool result is the one place that
        text arrives without a human having typed it.

        This runs over the *whole* result string, so a rail that redacts hands back
        redacted text: callers must use :attr:`GuardResult.text`, not the string they
        passed in, or the redaction did not happen.

        Args:
            text: The tool's output — text, or a :class:`~aegis.media.MediaPayload`
                (an image a tool returned is screened by the media chain, fail-closed).
            tool_name: Optional name of the tool that produced ``text``. Recorded in
                the rationale so a console shows *which* tool was carrying the
                payload, rather than an anonymous block.
            emitter: Optional AG-UI emitter for the injection ``guardrail_cache``
                and ``guardrail_media`` events.

        Returns:
            A :class:`GuardResult`. ``BLOCK`` means the content must not reach the
            agent's context at all; ``REDACT`` means use ``result.text``; ``FLAG`` is
            advisory and does not stop the content.
        """
        payload = as_payload(text)
        if payload.kind is not MediaKind.TEXT:
            result = await self._screen_media(payload, self._input_rails, emitter=emitter)
        else:
            raw = text if isinstance(text, str) else payload.text  # type: ignore[union-attr]
            primary, advisories = await self._screen_input(raw, emitter=emitter)
            result = (
                advisories[0]
                if primary.verdict is GuardVerdict.PASS and advisories
                else primary
            )
        return self._attribute_tool(result, tool_name)

    @staticmethod
    def _attribute_tool(result: GuardResult, tool_name: str | None) -> GuardResult:
        """Name the tool in the rationale so a verdict is never anonymous."""
        if not tool_name:
            return result
        return result.model_copy(update={"reason": f"[tool:{tool_name}] {result.reason}"})

    async def stream_check_tool_result_agui(
        self,
        text: str | MediaPayload,
        emitter: AegisEmitter,
        *,
        tool_name: str | None = None,
    ) -> GuardResult:
        """Run the tool-result rail, streaming an AG-UI guardrail verdict.

        The same shape :meth:`stream_check_input_agui` emits, stamped
        ``stage="tool_result"`` so the console can render the rail firing *inside* a
        tool call rather than at the edges of the turn. A blocked tool result that
        streams nothing is the defect this stage exists to prevent: the run would
        simply look like a search that found nothing.

        Args:
            text: The tool's output to screen.
            emitter: The AG-UI emitter for streaming events.
            tool_name: Optional name of the tool that produced ``text``.

        Returns:
            The :class:`GuardResult`; ``BLOCK`` means the content is not fit for context.
        """
        from aegis.core import stream_names
        from aegis.core.types import GuardStage

        async with emitter.step("guard_tool_result", SpanKind.GUARDRAIL):
            t0 = time.monotonic()
            result = await self.check_tool_result(text, tool_name=tool_name, emitter=emitter)
            elapsed = round((time.monotonic() - t0) * 1000, 3)
            await emitter.custom(
                stream_names.GUARDRAIL_VERDICT,
                {
                    "stage": GuardStage.TOOL_RESULT.value,
                    "tool": tool_name,
                    "verdict": result.verdict.value,
                    "rules": [result.layer] if result.layer else [],
                    "rationale": result.reason,
                    "redactions": result.redactions,
                    "per_rail_timing_ms": {"total": elapsed},
                    "spanKind": SpanKind.GUARDRAIL.value,
                },
            )
        return result

    async def _screen_grounding(
        self, text: str, contexts: list[str] | None
    ) -> GuardResult | None:
        """Run the grounding rail; return a BLOCK/FLAG GuardResult, or None when off.

        Advisory by default (an ungrounded answer is a non-blocking FLAG); a hard
        BLOCK when ``grounding_block`` is set. Returns None when grounding is
        disabled, no contexts were supplied, or the answer is judged grounded.
        """
        if not self._ground_answers or not contexts:
            return None
        verdict = await check_grounding(
            text, contexts, completer=self._completer, block=self._grounding_block
        )
        if verdict.grounded:
            return None
        blocking = self._grounding_block
        outcome = GuardVerdict.BLOCK if blocking else GuardVerdict.FLAG
        prefix = "Ungrounded answer blocked" if blocking else "Ungrounded answer flagged"
        return GuardResult(
            verdict=outcome,
            reason=f"{prefix}: {verdict.reason}",
            text=text,
            layer="grounding",
        )

    async def check_output(
        self, text: str | MediaPayload, contexts: list[str] | None = None
    ) -> GuardResult:
        """Run the full output rail (schema → content filter → grounding → PII).

        Accepts a ``str`` (unchanged behaviour) or a :class:`~aegis.media.MediaPayload`.
        A non-text payload is routed to the media chain: a model that *returns* an
        image is a channel too, and a generated image carrying rendered text is the
        same exfiltration surface pointed the other way.

        Args:
            text: The outbound model response — text, or a media payload.
            contexts: Optional retrieved passages the answer should be grounded in.
                When provided and grounding is enabled, the answer is checked
                against them (advisory FLAG by default). Omit to skip grounding.

        Returns:
            A :class:`GuardResult` with verdict and potentially redacted text. When
            the core rails pass but the grounding advisory fired, the non-blocking
            FLAG is surfaced as the result; a BLOCK/REDACT takes precedence.
        """
        payload = as_payload(text)
        if payload.kind is not MediaKind.TEXT:
            return await self._screen_media(payload, self._output_rails)
        text = text if isinstance(text, str) else payload.text  # type: ignore[union-attr]
        fmt = schema.validate_output_format(text)
        if not fmt.ok:
            return GuardResult(
                verdict=GuardVerdict.BLOCK, reason=fmt.reason, text=text, layer="schema"
            )
        filtered = schema.content_filter(text)
        if not filtered.ok:
            return GuardResult(
                verdict=GuardVerdict.BLOCK, reason=filtered.reason, text=text, layer="content"
            )
        denied = schema.denied_term(text, self._denylist_terms)
        if not denied.ok:
            return GuardResult(
                verdict=GuardVerdict.BLOCK, reason=denied.reason, text=text, layer="denylist"
            )
        safety = await screen_content(text, completer=self._completer)
        if safety.unsafe:
            return GuardResult(
                verdict=GuardVerdict.BLOCK,
                reason=f"Unsafe output blocked ({safety.label() or 'hazard'}): {safety.reason}",
                text=text,
                layer="content_safety",
            )
        custom = await self._run_custom(TextPayload.of(text), self._output_rails)
        if custom is not None:
            return custom
        grounding = await self._screen_grounding(text, contexts)
        if grounding is not None and grounding.verdict is GuardVerdict.BLOCK:
            return grounding
        redacted, kinds = pii.redact(text, entities=self._pii_entities)
        if kinds:
            return GuardResult(
                verdict=GuardVerdict.REDACT,
                reason=f"Redacted PII on the outbound path: {', '.join(kinds)}.",
                text=redacted,
                layer="pii",
                redactions=kinds,
            )
        if grounding is not None:  # a non-blocking FLAG; surface it on the clean path
            return grounding
        return GuardResult(
            verdict=GuardVerdict.PASS,
            reason="Output passed schema, content-filter, content-safety, and PII rails.",
            text=text,
        )

    async def stream_check_input(
        self, text: str | MediaPayload
    ) -> AsyncIterator[AegisEvent]:
        """Run the input rail, yielding start → verdict → finish events.

        Args:
            text: The inbound user input — text, or a media payload (which is
                screened by the media chain and streams the same event shape).

        Yields:
            :class:`AegisEvent` objects in order: :class:`StepStarted`, the
            primary :class:`GuardrailEvent`, one :class:`GuardrailEvent` per
            non-blocking advisory (e.g. an off-topic ``verdict="flag"``), and
            :class:`StepFinished`. An advisory FLAG is emitted but never sets
            ``ok=False`` — only a BLOCK stops the request.
        """
        step_id = uuid.uuid4().hex
        yield StepStarted(
            module_id=_MODULE_ID, step_id=step_id, name="guard_input", span_kind=SpanKind.GUARDRAIL
        )
        payload = as_payload(text)
        if payload.kind is MediaKind.TEXT:
            raw = text if isinstance(text, str) else payload.text  # type: ignore[union-attr]
            result, advisories = await self._screen_input(raw)
        else:
            result = await self._screen_media(payload, self._input_rails)
            advisories = []
        yield GuardrailEvent(
            module_id=_MODULE_ID,
            step_id=step_id,
            verdict=result.verdict.value,
            rules=[result.layer] if result.layer else [],
            rationale=result.reason,
            redactions=result.redactions,
        )
        for advisory in advisories:
            yield GuardrailEvent(
                module_id=_MODULE_ID,
                step_id=step_id,
                verdict=advisory.verdict.value,
                rules=[advisory.layer] if advisory.layer else [],
                rationale=advisory.reason,
                redactions=advisory.redactions,
            )
        yield StepFinished(
            module_id=_MODULE_ID,
            step_id=step_id,
            name="guard_input",
            span_kind=SpanKind.GUARDRAIL,
            ok=result.verdict is not GuardVerdict.BLOCK,
        )

    async def stream_check_input_agui(
        self, text: str | MediaPayload, emitter: AegisEmitter
    ) -> GuardResult:
        """Run the input rail, streaming a rich AG-UI guardrail verdict.

        Emits STEP_STARTED -> CustomEvent(guardrail_verdict, ...) -> STEP_FINISHED via the
        shared emitter. The verdict payload carries which rail fired, per-rail timing, the
        exact PII spans, and the rationale — the 'show your work' detail for the UI.

        Args:
            text: The inbound user input text to screen.
            emitter: The AG-UI emitter for streaming events.

        Returns:
            A :class:`GuardResult` with verdict and potentially redacted text.
        """
        from aegis.core import stream_names

        timing: dict[str, float] = {}
        async with emitter.step("guard_input", SpanKind.GUARDRAIL):
            t0 = time.monotonic()
            result = await self.check_input(text, emitter=emitter)
            timing["total"] = round((time.monotonic() - t0) * 1000, 3)
            # PII spans are character offsets into text; a binary payload has none
            # (its PII, if any, is reported by the media chain's own redaction rail).
            spans = (
                [
                    {"kind": m.kind, "start": m.start, "end": m.end}
                    for m in pii.scan(text, entities=self._pii_entities)
                ]
                if isinstance(text, str)
                else []
            )
            await emitter.custom(
                stream_names.GUARDRAIL_VERDICT,
                {
                    "verdict": result.verdict.value,
                    "rules": [result.layer] if result.layer else [],
                    "rationale": result.reason,
                    "redactions": result.redactions,
                    "redaction_spans": spans,
                    "per_rail_timing_ms": {
                        "schema": None,
                        "pii": None,
                        "injection": None,
                        "total": timing["total"],
                    },
                    "spanKind": SpanKind.GUARDRAIL.value,
                },
            )
        return result
