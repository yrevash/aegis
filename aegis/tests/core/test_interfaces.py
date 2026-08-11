"""Tests for aegis.core.interfaces Protocols."""

from aegis.core.interfaces import ChatCompleter, Guardrail
from aegis.core.types import GuardResult, GuardVerdict


class _FakeCompleter:
    async def __call__(self, messages, *, response_format=None):
        return '{"injection": false, "reason": "benign"}'


class _FakeGuard:
    async def check_input(self, text):
        return GuardResult(verdict=GuardVerdict.PASS, reason="ok", text=text)

    async def check_output(self, text):
        return GuardResult(verdict=GuardVerdict.PASS, reason="ok", text=text)


def test_structural_conformance():
    assert isinstance(_FakeCompleter(), ChatCompleter)
    assert isinstance(_FakeGuard(), Guardrail)
