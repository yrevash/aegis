"""NeMo Guardrails wiring — load the Colang policy and run its rails.

This is the orchestration layer named as a *locked decision* in
``docs/AGENT_BRIEF.md`` (Guardrails = NeMo Guardrails; the Colang policy file is
a readable security artifact). The policy lives under ``config/`` as a standard
NeMo Guardrails config directory; its Colang flows call the custom actions in
``config/actions.py``, which delegate straight back to
:func:`app.guardrails.check_input` / :func:`app.guardrails.check_output`. One
policy, two front doors: the fast programmatic API the agent graph uses, and the
declarative Colang the jury reads.

``nemoguardrails`` is imported **lazily** inside each function so that importing
:mod:`app.guardrails` — and running the unit tests — never requires the package
(it is an optional dependency, gated on RAM/portability constraints).

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

from app.api.schemas import GuardVerdict
from app.guardrails import pii
from app.guardrails.models import GuardResult

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids importing the package
    from nemoguardrails import LLMRails, RailsConfig

logger = logging.getLogger(__name__)

# The exact refusal strings authored in the Colang policy (``config/rails/*.co``).
# A generated turn equal to one of these means an input/output rail fired ``stop``.
_INPUT_REFUSAL = "I can't process that request — it was stopped by the input guardrail."
_OUTPUT_REFUSAL = "The response was withheld by the output guardrail."

_engine: LLMRails | None = None


def nemo_available() -> bool:
    """Return whether the optional ``nemoguardrails`` package is importable."""
    import importlib.util

    return importlib.util.find_spec("nemoguardrails") is not None


def config_path() -> Path:
    """Return the path to the bundled NeMo Guardrails config directory.

    Returns:
        The absolute path of ``app/guardrails/config``.
    """
    return Path(__file__).parent / "config"


def load_rails_config() -> RailsConfig:
    """Load the Colang policy into a :class:`RailsConfig`.

    Returns:
        The parsed NeMo Guardrails configuration.

    Raises:
        ImportError: If the optional ``nemoguardrails`` package is not installed.
    """
    from nemoguardrails import RailsConfig

    return RailsConfig.from_path(str(config_path()))


def build_rails() -> LLMRails:
    """Instantiate an :class:`LLMRails` engine from the bundled policy.

    The custom actions referenced by the Colang flows are auto-registered from
    ``config/actions.py`` when the directory is loaded.

    Returns:
        A ready-to-use :class:`LLMRails` engine.

    Raises:
        ImportError: If the optional ``nemoguardrails`` package is not installed.
    """
    from nemoguardrails import LLMRails

    return LLMRails(load_rails_config())


def register_actions(rails: Any) -> None:  # noqa: ANN401 - LLMRails, kept import-free
    """Explicitly (re)register the guardrail actions on an ``LLMRails`` engine.

    A safety net for callers that construct ``LLMRails`` themselves rather than
    via :func:`build_rails`; the config-dir load already auto-registers these.

    Args:
        rails: An ``nemoguardrails.LLMRails`` instance.
    """
    from app.guardrails.config import actions

    rails.register_action(actions.self_check_injection, name="self_check_injection")
    rails.register_action(actions.redact_pii_input, name="redact_pii_input")
    rails.register_action(actions.validate_input_schema, name="validate_input_schema")
    rails.register_action(actions.redact_pii_output, name="redact_pii_output")
    rails.register_action(actions.validate_output_schema, name="validate_output_schema")


def get_engine() -> LLMRails:
    """Return the process-wide :class:`LLMRails` engine, building it once.

    The engine is built from the bundled Colang config with the custom actions
    registered. Rail-only checks (see :func:`nemo_check_input` /
    :func:`nemo_check_output`) never invoke the ``main`` model, so no API key or
    network is required to *screen* text — only the injection action makes the same
    cheap-model call the programmatic rail already does.
    """
    global _engine
    if _engine is None:
        _engine = build_rails()
        register_actions(_engine)
    return _engine


def reset_engine() -> None:
    """Drop the cached engine (test isolation / config reload)."""
    global _engine
    _engine = None


def _last_content(response: Any) -> str:  # noqa: ANN401 - NeMo GenerationResponse|dict|str
    """Extract the final assistant message text from a NeMo generation result."""
    payload = getattr(response, "response", response)
    if isinstance(payload, list):
        return str(payload[-1].get("content", "")) if payload else ""
    if isinstance(payload, dict):
        return str(payload.get("content", ""))
    return str(payload)


def _input_only():  # noqa: ANN202 - NeMo GenerationOptions
    from nemoguardrails.rails.llm.options import GenerationOptions, GenerationRailsOptions

    return GenerationOptions(
        rails=GenerationRailsOptions(input=True, output=False, dialog=False, retrieval=False)
    )


def _output_only():  # noqa: ANN202 - NeMo GenerationOptions
    from nemoguardrails.rails.llm.options import GenerationOptions, GenerationRailsOptions

    return GenerationOptions(
        rails=GenerationRailsOptions(input=False, output=True, dialog=False, retrieval=False)
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
        A :class:`GuardResult` mirroring :func:`app.guardrails.check_input`.
    """
    result = await get_engine().generate_async(
        messages=[{"role": "user", "content": text}], options=_input_only()
    )
    redacted, kinds = pii.redact(text)
    if _last_content(result).strip() == _INPUT_REFUSAL:
        return GuardResult(
            verdict=GuardVerdict.BLOCK,
            reason="Blocked by the NeMo Colang input rail (schema/PII/injection).",
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
        A :class:`GuardResult` mirroring :func:`app.guardrails.check_output`.
    """
    result = await get_engine().generate_async(
        messages=[{"role": "user", "content": text}], options=_output_only()
    )
    if _last_content(result).strip() == _OUTPUT_REFUSAL:
        return GuardResult(
            verdict=GuardVerdict.BLOCK,
            reason="Withheld by the NeMo Colang output rail (schema/content filter).",
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
