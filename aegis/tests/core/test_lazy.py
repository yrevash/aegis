import pytest

from aegis.core.lazy import require


def test_require_present_returns_module():
    mod = require("aegis", "json")
    assert mod.dumps({"a": 1}) == '{"a": 1}'


def test_require_missing_raises_with_hint():
    with pytest.raises(ImportError) as ei:
        require("aegis[nemo]", "definitely_not_a_real_module_xyz")
    assert "pip install aegis[nemo]" in str(ei.value)
