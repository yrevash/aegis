import pytest

from aegis.core.config import AegisMode, CoreSettings


def test_default_mode_is_full() -> None:
    assert CoreSettings(redis_url="r", database_url="d").mode is AegisMode.full


def test_full_mode_missing_infra_raises() -> None:
    s = CoreSettings(mode="full", redis_url=None, database_url=None)
    with pytest.raises(RuntimeError) as ei:
        s.require_full_infra()
    assert "REDIS_URL" in str(ei.value) and "AEGIS_MODE=lite" in str(ei.value)


def test_lite_mode_tolerates_missing_infra() -> None:
    CoreSettings(mode="lite").require_full_infra()  # no raise
