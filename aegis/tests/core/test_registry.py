import pytest

from aegis.core.registry import available, get, register


def test_register_and_get():
    @register("guardrail", "dummy")
    class Dummy:
        pass

    assert get("guardrail", "dummy") is Dummy
    assert "dummy" in available("guardrail")


def test_unknown_raises():
    with pytest.raises(KeyError):
        get("guardrail", "nope")
