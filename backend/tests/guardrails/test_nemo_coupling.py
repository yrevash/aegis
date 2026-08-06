"""Guard against a silent NeMo fail-open: the Python refusal constants that
``nemo_check_input``/``nemo_check_output`` string-match on MUST stay identical to the
``bot refuse`` messages in the Colang policy. If someone edits a ``.co`` refusal (or a
NeMo version reflows the turn) without updating the constant, the equality check would
stop matching and the rail would silently fall through to PASS. This test fails loudly on
that drift. It reads only files + constants — no ``nemoguardrails`` package required.
"""

from __future__ import annotations

from app.guardrails.nemo import _INPUT_REFUSAL, _OUTPUT_REFUSAL, config_path


def _co_text(name: str) -> str:
    return (config_path() / "rails" / name).read_text(encoding="utf-8")


def test_input_refusal_constant_matches_colang():
    assert _INPUT_REFUSAL in _co_text("input.co"), (
        "The Python _INPUT_REFUSAL no longer matches the Colang 'bot refuse input' message — "
        "the NeMo input rail would silently fail open. Update both together."
    )


def test_output_refusal_constant_matches_colang():
    assert _OUTPUT_REFUSAL in _co_text("output.co"), (
        "The Python _OUTPUT_REFUSAL no longer matches the Colang 'bot refuse output' message — "
        "the NeMo output rail would silently fail open. Update both together."
    )
