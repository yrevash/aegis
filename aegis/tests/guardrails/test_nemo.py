"""Tests for optional NeMo Colang guardrails engine."""

import pytest

from aegis.guardrails import nemo


def test_nemo_available_is_bool() -> None:
    """Test that nemo_available returns a boolean."""
    assert isinstance(nemo.nemo_available(), bool)


@pytest.mark.skipif(nemo.nemo_available(), reason="nemoguardrails installed")
def test_require_raises_when_absent() -> None:
    """Test that build_rails raises ImportError when nemoguardrails is absent."""
    with pytest.raises(ImportError) as ei:
        nemo.build_rails()
    assert "pip install aegis[nemo]" in str(ei.value)
