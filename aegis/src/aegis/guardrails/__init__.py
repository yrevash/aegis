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
from aegis.guardrails import pii, schema
from aegis.guardrails.pipeline import Guardrails


async def check_input(text: str, *, completer: ChatCompleter | None = None) -> GuardResult:
    """Screen inbound ``text`` with a fresh :class:`Guardrails` pipeline.

    Args:
        text: The inbound user input text to screen.
        completer: Optional chat completer for model-based injection detection.
            If None, only deterministic injection signatures are checked.

    Returns:
        A GuardResult with the verdict and potentially redacted text.
    """
    return await Guardrails(completer=completer).check_input(text)


async def check_output(text: str, *, completer: ChatCompleter | None = None) -> GuardResult:
    """Screen outbound ``text`` with a fresh :class:`Guardrails` pipeline.

    Args:
        text: The outbound model response text to screen.
        completer: Optional chat completer for model-based injection detection.
            If None, only deterministic injection signatures are checked.

    Returns:
        A GuardResult with the verdict and potentially redacted text.
    """
    return await Guardrails(completer=completer).check_output(text)


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
    "Guardrails",
    "check_input",
    "check_output",
    "pii",
    "run_guards",
    "schema",
]
