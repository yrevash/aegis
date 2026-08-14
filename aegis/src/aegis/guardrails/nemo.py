"""NeMo Guardrails wiring — load the Colang policy and run its rails.

This is the orchestration layer named as a *locked decision* in
``docs/module/MODULE_REFERENCE.md`` (Guardrails = NeMo Guardrails; the Colang policy file is
a readable security artifact). The policy lives under ``config/`` as a standard
NeMo Guardrails config directory; its Colang flows call the custom actions in
``config/actions.py``, which delegate straight back to
:func:`aegis.guardrails.pipeline.check_input` / :func:`aegis.guardrails.pipeline.check_output`. One
policy, two front doors: the fast programmatic API the agent graph uses, and the
declarative Colang the jury reads.

``nemoguardrails`` is imported **lazily** inside each function via :func:`aegis.core.lazy.require`
so that importing :mod:`aegis.guardrails.nemo` — and running the unit tests — never
requires the package (it is an optional dependency, gated on RAM/portability constraints).

Verified against NeMo Guardrails **0.23** with **Colang 1.0** (the current
default; the built-in rail catalogue and the ``define flow`` / ``execute`` /
``bot refuse`` grammar are authored in 1.0). Programmatic surface used:
``RailsConfig.from_path`` and ``LLMRails(config)``; custom actions are
auto-registered from ``config/actions.py``. August 2026.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aegis.core.lazy import require
from aegis.core.types import GuardResult, GuardVerdict
from aegis.guardrails import pii

if TYPE_CHECKING:  # pragma: no cover
    from nemoguardrails import LLMRails, RailsConfig

    from aegis.core.interfaces import ChatCompleter

logger = logging.getLogger(__name__)

#: Process-wide ``ChatCompleter`` the Colang custom actions use for their
#: model-based layers (injection + content-safety self-check), mirroring how the
#: programmatic pipeline is wired (:class:`aegis.guardrails.Guardrails`). The
#: Colang engine has no first-class way to thread a completer into a custom
#: action, so the actions read it from here. Defaults to ``None`` — the offline,
#: deterministic-only backstop — so importing the policy (and the unit tests)
#: never touches a network. The host wires its cheap-model completer in via
#: :func:`set_completer`.
_completer: ChatCompleter | None = None


def set_completer(completer: ChatCompleter | None) -> None:
    """Wire the ``ChatCompleter`` the NeMo custom actions use for model layers.

    Idempotent; call once at host startup (e.g. the backend guardrails shim wires
    its cost-routed cheap-model gateway). Passing ``None`` restores the offline,
    deterministic-only behaviour.

    Args:
        completer: An async chat-completion callable, or ``None`` to disable the
            model-based layers (deterministic signatures only).
    """
    global _completer
    _completer = completer


def get_completer() -> ChatCompleter | None:
    """Return the ``ChatCompleter`` the NeMo custom actions should use, if any."""
    return _completer


#: Process-wide allowed-topics config for the topical dialog rail, mirroring how
#: ``_completer`` is wired. ``None``/empty disables the topical rail (a no-op
#: PASS) — the offline default, so importing the policy touches no config. The
#: host wires its blind-domain topic description via :func:`set_allowed_topics`.
_allowed_topics: str | list[str] | None = None


def set_allowed_topics(allowed_topics: str | list[str] | None) -> None:
    """Wire the business-domain topics the NeMo topical rail screens against.

    Idempotent; call once at host startup. ``None``/empty disables the topical
    rail (the offline default). Domain-agnostic — the platform serves a blind
    domain, so topics always come from config, never hardcoded.

    Args:
        allowed_topics: A description or list of permitted domain topics, or
            ``None`` to disable the topical rail.
    """
    global _allowed_topics
    _allowed_topics = allowed_topics


def get_allowed_topics() -> str | list[str] | None:
    """Return the configured allowed-topics for the NeMo topical rail, if any."""
    return _allowed_topics


#: Process-wide **vision** completer for the Colang media rails, wired exactly like
#: ``_completer``. Screening pixels needs a multimodal model, which the text rails
#: deliberately do not require, so it is a separate seam. ``None`` (the default)
#: makes the media injection rail fail **closed** — see
#: :mod:`aegis.guardrails.media.injection`.
_vision_completer: ChatCompleter | None = None

#: Process-wide transcriber for the Colang audio rail. ``None`` blocks audio.
_transcriber: Any = None


def set_vision_completer(completer: ChatCompleter | None) -> None:
    """Wire the vision ``ChatCompleter`` the Colang media rails screen images with.

    Args:
        completer: A vision-capable async chat completer, or ``None`` (images then
            fail closed — there is no offline backstop for pixels).
    """
    global _vision_completer
    _vision_completer = completer


def get_vision_completer() -> ChatCompleter | None:
    """Return the vision completer the Colang media rails should use, if any."""
    return _vision_completer


def set_transcriber(transcriber: Any) -> None:  # noqa: ANN401 - media.Transcriber
    """Wire the speech-to-text callable the Colang audio rail transcribes with.

    Args:
        transcriber: An ``aegis.guardrails.media.Transcriber``, or ``None`` (audio
            is then blocked, fail-closed — an unguardable payload is not a safe one).
    """
    global _transcriber
    _transcriber = transcriber


def get_transcriber() -> Any:  # noqa: ANN401 - media.Transcriber | None
    """Return the transcriber the Colang audio rail should use, if any."""
    return _transcriber


# The refusal strings authored in the Colang policy (``config/rails/*.co``). They are
# **no longer the block signal** — see :func:`_stopped_rails`. Detecting a block by
# string-comparing the generated turn against these meant any edit to the policy
# text, or any reformatting by NeMo, silently turned every block into a PASS: a rail
# that fails OPEN on a typo. They are kept only to detect that drift and log it.
_INPUT_REFUSAL = "I can't process that request — it was stopped by the input guardrail."
_OUTPUT_REFUSAL = "The response was withheld by the output guardrail."

_engine: LLMRails | None = None

#: The completer the cached engine's ``main`` model was built from. The engine is
#: rebuilt when it changes, so ``set_completer`` after first use cannot leave a
#: stale (or absent) model wired into a live engine.
_engine_completer: ChatCompleter | None = None


def nemo_available() -> bool:
    """Return whether the optional ``nemoguardrails`` package is importable."""
    import importlib.util

    return importlib.util.find_spec("nemoguardrails") is not None


def config_path() -> Path:
    """Return the path to the bundled NeMo Guardrails config directory.

    Returns:
        The absolute path of ``aegis/guardrails/config``.
    """
    return Path(__file__).parent / "config"


def load_rails_config() -> RailsConfig:
    """Load the Colang policy into a :class:`RailsConfig`.

    Returns:
        The parsed NeMo Guardrails configuration.

    Raises:
        ImportError: If the optional ``nemoguardrails`` package is not installed.
    """
    nemoguardrails = require("aegis[nemo]", "nemoguardrails")
    RailsConfig = nemoguardrails.RailsConfig

    return RailsConfig.from_path(str(config_path()))


def build_rails(llm: Any = None) -> LLMRails:  # noqa: ANN401 - a LangChain model
    """Instantiate an :class:`LLMRails` engine from the bundled policy.

    The custom actions referenced by the Colang flows are auto-registered from
    ``config/actions.py`` when the directory is loaded.

    The engine's ``main`` model is the **host's** completer, adapted by
    :func:`aegis.guardrails._nemo_llm.chat_model_from_completer`. Without this the
    engine silently instantiated the ``models:`` block in ``config.yml`` — a
    separate provider, key and budget from the gateway the rest of the platform
    uses. Rail-only checks never invoke it, but anything that does (a dialog rail,
    an LLM-generated bot message, a NeMo-native self-check) now goes through the
    one configured model.

    Args:
        llm: An explicit LangChain model to use as ``main``. Defaults to the
            host-wired completer (:func:`set_completer`) when one exists; with no
            completer wired the config's declared model stands, as before.

    Returns:
        A ready-to-use :class:`LLMRails` engine.

    Raises:
        ImportError: If the optional ``nemoguardrails`` package is not installed.
    """
    nemoguardrails = require("aegis[nemo]", "nemoguardrails")
    LLMRails = nemoguardrails.LLMRails

    if llm is None and _completer is not None:
        from aegis.guardrails._nemo_llm import chat_model_from_completer

        llm = chat_model_from_completer(_completer)
    return LLMRails(load_rails_config(), llm=llm)


#: Every action name an ``execute`` in ``config/rails/*.co`` can resolve to, mapped
#: to its function in ``config/actions.py``. Kept as data so :func:`register_actions`
#: and :func:`registered_action_names` cannot fall out of step with each other —
#: the previous hand-written list omitted ``self_check_topic`` and
#: ``self_check_grounding``, which ``input.co`` and ``output.co`` actually execute,
#: so anyone constructing ``LLMRails`` themselves and calling this hit an
#: unregistered-action failure on the documented path.
def _action_table() -> dict[str, Any]:
    """Return ``{action name: callable}`` for every action the Colang policy executes."""
    from aegis.guardrails.config import actions

    return {
        "validate_input_schema": actions.validate_input_schema,
        "redact_pii_input": actions.redact_pii_input,
        "self_check_injection": actions.self_check_injection,
        "self_check_content_safety": actions.self_check_content_safety,
        "self_check_topic": actions.self_check_topic,
        "self_check_grounding": actions.self_check_grounding,
        "validate_output_schema": actions.validate_output_schema,
        "redact_pii_output": actions.redact_pii_output,
        "check_media_hygiene": actions.check_media_hygiene,
        "self_check_media_injection": actions.self_check_media_injection,
        "self_check_media_transcript": actions.self_check_media_transcript,
    }


def registered_action_names() -> frozenset[str]:
    """Return the action names :func:`register_actions` wires up.

    Exposed so a test can assert this set covers every ``execute`` in the ``.co``
    files — the mechanical check that stops the two from drifting again.
    """
    return frozenset(_action_table())


def register_actions(rails: Any) -> None:  # noqa: ANN401
    """Explicitly (re)register **every** guardrail action on an ``LLMRails`` engine.

    A safety net for callers that construct ``LLMRails`` themselves rather than
    via :func:`build_rails`; the config-dir load already auto-registers these.

    Args:
        rails: An ``nemoguardrails.LLMRails`` instance.
    """
    for name, fn in _action_table().items():
        rails.register_action(fn, name=name)


def get_engine() -> LLMRails:
    """Return the process-wide :class:`LLMRails` engine, building it once.

    The engine is built from the bundled Colang config with the custom actions
    registered. Rail-only checks (see :func:`nemo_check_input` /
    :func:`nemo_check_output`) never invoke the ``main`` model, so no API key or
    network is required to *screen* text — only the injection action makes the same
    cheap-model call the programmatic rail already does.

    The engine is rebuilt when :func:`set_completer` changes the wired completer,
    so a host that wires its gateway after first use never keeps screening through
    a stale model.
    """
    global _engine, _engine_completer
    if _engine is None or _engine_completer is not _completer:
        _engine = build_rails()
        register_actions(_engine)
        _engine_completer = _completer
    return _engine


def reset_engine() -> None:
    """Drop the cached engine (test isolation / config reload)."""
    global _engine, _engine_completer
    _engine = None
    _engine_completer = None


def _last_content(response: Any) -> str:  # noqa: ANN401
    """Extract the final assistant message text from a NeMo generation result."""
    payload = getattr(response, "response", response)
    if isinstance(payload, list):
        return str(payload[-1].get("content", "")) if payload else ""
    if isinstance(payload, dict):
        return str(payload.get("content", ""))
    return str(payload)


def _options(*, input_rails: bool, output_rails: bool) -> Any:  # noqa: ANN401
    """Build GenerationOptions for a rail-only run, with the execution log ON.

    ``log.activated_rails`` is what makes the block signal drift-proof (see
    :func:`_stopped_rails`), so it is requested on every call — never optional.
    """
    nemoguardrails = require("aegis[nemo]", "nemoguardrails")
    options_module = nemoguardrails.rails.llm.options
    return options_module.GenerationOptions(
        rails=options_module.GenerationRailsOptions(
            input=input_rails, output=output_rails, dialog=False, retrieval=False
        ),
        log=options_module.GenerationLogOptions(activated_rails=True),
    )


def _input_only() -> Any:  # noqa: ANN201, ANN401
    """Create NeMo GenerationOptions for input-only rail execution."""
    return _options(input_rails=True, output_rails=False)


def _output_only() -> Any:  # noqa: ANN201, ANN401
    """Create NeMo GenerationOptions for output-only rail execution."""
    return _options(input_rails=False, output_rails=True)


class _NoRailLog(RuntimeError):
    """The engine returned no execution log, so no block decision can be trusted."""


def _stopped_rails(response: Any) -> list[str]:  # noqa: ANN401
    """Return the names of the Colang flows that executed ``stop`` on this turn.

    **Why not string comparison.** The previous implementation decided a block by
    testing whether the generated turn was exactly equal to a refusal string
    hardcoded in this module *and* authored in the ``.co`` file. Those two copies
    have no mechanism keeping them equal: reword the policy, add a full stop, let
    NeMo normalise the text, and every block quietly became a PASS. A guardrail
    whose enforcement depends on two strings staying character-identical is a
    guardrail that fails **open** on a typo.

    ``GenerationLog.activated_rails[*].stop`` is the engine's own record of having
    halted the turn. It is structured state, not prose, so no amount of editing the
    refusal wording can change it.

    Args:
        response: The ``GenerationResponse`` from ``generate_async``.

    Returns:
        The names of the flows that stopped the turn (empty when none did).

    Raises:
        _NoRailLog: If the response carries no log. Callers turn this into a
            fail-closed BLOCK: with no log there is no evidence the rails ran, and
            "no evidence" is never a pass.
    """
    log = getattr(response, "log", None)
    activated = getattr(log, "activated_rails", None) if log is not None else None
    if activated is None:
        raise _NoRailLog(
            "NeMo returned no activated-rails log, so whether a rail stopped the "
            "turn cannot be determined"
        )
    return [rail.name for rail in activated if getattr(rail, "stop", False)]


def _warn_on_refusal_drift(response: Any, stopped: list[str], expected: str) -> None:  # noqa: ANN401
    """Log when the generated refusal and the stop signal disagree.

    Neither direction changes the verdict — :func:`_stopped_rails` is the
    authority — but a mismatch means the policy text and this module have drifted,
    and the operator should know before it matters.
    """
    content = _last_content(response).strip()
    if stopped and content and content != expected:
        logger.info(
            "NeMo rail %s stopped the turn with refusal text %r (policy text has drifted "
            "from the copy in aegis.guardrails.nemo; the stop signal is authoritative).",
            stopped,
            content[:120],
        )
    elif not stopped and content == expected:
        logger.warning(
            "NeMo emitted the guardrail refusal text but reported no rail stop; "
            "treating as NOT blocked. Inspect the policy — this should not happen."
        )


async def nemo_check_input(text: str) -> GuardResult:
    """Screen a user query by executing the **NeMo Colang input rail** for real.

    Runs the bundled Colang ``input`` flows (schema → PII → injection) via
    ``LLMRails`` with dialog/output rails disabled, so no ``main``-model generation
    happens — only the policy runs. A rail ``stop`` surfaces as the Colang refusal
    string, which maps to a ``block``; otherwise PII is reported exactly as the
    programmatic twin does. Same policy, declarative front door.

    Args:
        text: The raw inbound query.

    Returns:
        A :class:`GuardResult` mirroring :func:`aegis.guardrails.pipeline.check_input`.
    """
    result = await get_engine().generate_async(
        messages=[{"role": "user", "content": text}], options=_input_only()
    )
    redacted, kinds = pii.redact(text)
    try:
        stopped = _stopped_rails(result)
    except _NoRailLog as exc:
        return GuardResult(
            verdict=GuardVerdict.BLOCK,
            reason=f"NeMo Colang input rail produced no execution log ({exc}); blocked "
            "(fail-closed) rather than assumed clean.",
            text=redacted,
            layer="nemo-input",
        )
    _warn_on_refusal_drift(result, stopped, _INPUT_REFUSAL)
    if stopped:
        return GuardResult(
            verdict=GuardVerdict.BLOCK,
            reason=f"Blocked by the NeMo Colang input rail: {', '.join(stopped)}.",
            text=redacted,
            layer="nemo-input",
        )
    if kinds:
        return GuardResult(
            verdict=GuardVerdict.REDACT,
            reason=f"Redacted PII on the inbound path: {', '.join(kinds)}.",
            text=redacted,
            layer="nemo-input",
            redactions=kinds,
        )
    return GuardResult(
        verdict=GuardVerdict.PASS,
        reason="Input passed the NeMo Colang input rail.",
        text=text,
        layer="nemo-input",
    )


async def nemo_check_output(text: str) -> GuardResult:
    """Screen a model answer by executing the **NeMo Colang output rail** for real.

    Args:
        text: The model's answer text (assumed complete).

    Returns:
        A :class:`GuardResult` mirroring :func:`aegis.guardrails.pipeline.check_output`.
    """
    # The text under test is the *assistant* turn, so it must be presented as an
    # ``assistant`` message (preceded by a placeholder user turn) for NeMo to run
    # the output rails' ``execute``/``stop`` check flows against it. Passing it as a
    # ``user`` message runs only the variable-assignment flows (e.g. PII redaction)
    # and silently skips the schema + content-safety checks.
    result = await get_engine().generate_async(
        messages=[
            {"role": "user", "content": ""},
            {"role": "assistant", "content": text},
        ],
        options=_output_only(),
    )
    try:
        stopped = _stopped_rails(result)
    except _NoRailLog as exc:
        return GuardResult(
            verdict=GuardVerdict.BLOCK,
            reason=f"NeMo Colang output rail produced no execution log ({exc}); withheld "
            "(fail-closed) rather than assumed clean.",
            text=text,
            layer="nemo-output",
        )
    _warn_on_refusal_drift(result, stopped, _OUTPUT_REFUSAL)
    if stopped:
        return GuardResult(
            verdict=GuardVerdict.BLOCK,
            reason=f"Withheld by the NeMo Colang output rail: {', '.join(stopped)}.",
            text=text,
            layer="nemo-output",
        )
    redacted, kinds = pii.redact(text)
    if kinds:
        return GuardResult(
            verdict=GuardVerdict.REDACT,
            reason=f"Redacted PII on the outbound path: {', '.join(kinds)}.",
            text=redacted,
            layer="nemo-output",
            redactions=kinds,
        )
    return GuardResult(
        verdict=GuardVerdict.PASS,
        reason="Output passed the NeMo Colang output rail.",
        text=text,
        layer="nemo-output",
    )


#: The context variable the media rails read the payload from. A NeMo turn can
#: carry a ``{"role": "context", "content": {...}}`` message, which is the only
#: sanctioned way to hand a Colang flow structured data that is not the message
#: text — and a payload is exactly that: bytes plus metadata, not a string.
MEDIA_CONTEXT_KEY = "media"


async def nemo_check_media_input(payload: Any) -> GuardResult:  # noqa: ANN401 - MediaPayload
    """Screen a media payload by executing the **NeMo Colang media rails** for real.

    The payload travels as a JSON-serialised ``context`` message (bytes base64'd by
    :class:`aegis.media.MediaPayload`), which the media actions rebuild and hand to
    the same :class:`aegis.guardrails.media.MediaScreen` the programmatic pipeline
    uses. One policy, two front doors — extended to pixels and speech.

    Args:
        payload: An :class:`aegis.media.ImagePayload` or
            :class:`aegis.media.AudioPayload`.

    Returns:
        A :class:`GuardResult` mirroring
        :meth:`aegis.guardrails.Guardrails.check_input` for the same payload.
    """
    result = await get_engine().generate_async(
        messages=[
            {"role": "context", "content": {MEDIA_CONTEXT_KEY: payload.model_dump(mode="json")}},
            {"role": "user", "content": "[media payload]"},
        ],
        options=_input_only(),
    )
    try:
        stopped = _stopped_rails(result)
    except _NoRailLog as exc:
        return GuardResult(
            verdict=GuardVerdict.BLOCK,
            reason=f"NeMo Colang media rail produced no execution log ({exc}); blocked "
            "(fail-closed) rather than assumed clean.",
            text=payload.describe(),
            layer="nemo-media",
        )
    _warn_on_refusal_drift(result, stopped, _INPUT_REFUSAL)
    if stopped:
        return GuardResult(
            verdict=GuardVerdict.BLOCK,
            reason=f"Blocked by the NeMo Colang media rail: {', '.join(stopped)}.",
            text=payload.describe(),
            layer="nemo-media",
        )
    return GuardResult(
        verdict=GuardVerdict.PASS,
        reason="Media payload passed the NeMo Colang media rails.",
        text=payload.describe(),
        layer="nemo-media",
    )
