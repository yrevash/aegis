"""Test the public API surface of aegis.guardrails and aegis.core."""

from __future__ import annotations

import pytest

from aegis.core import GuardResult, GuardVerdict
from aegis.guardrails import check_input, run_guards


class _Benign:
    """Mock completer that always returns benign result."""

    async def __call__(self, messages, *, response_format=None):
        return '{"injection": false, "unsafe": false, "reason": "benign"}'


@pytest.mark.asyncio
async def test_module_level_check_input():
    """Test check_input module-level function."""
    r = await check_input("what is the escalation policy?", completer=_Benign())
    assert isinstance(r, GuardResult) and r.verdict == GuardVerdict.PASS


@pytest.mark.asyncio
async def test_run_guards_input_and_output():
    """Test run_guards module-level function."""
    verdict_in, verdict_out = await run_guards("hi", "hello there", completer=_Benign())
    assert verdict_in.verdict == GuardVerdict.PASS and verdict_out.verdict == GuardVerdict.PASS
