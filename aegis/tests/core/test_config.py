import pytest

from aegis.core.config import AegisMode, CoreSettings


def test_default_mode_is_full() -> None:
    assert CoreSettings(redis_url="r", database_url="d", qdrant_url="q").mode is AegisMode.full


def test_full_mode_missing_infra_raises() -> None:
    s = CoreSettings(mode="full", redis_url=None, database_url=None, qdrant_url=None)
    with pytest.raises(RuntimeError) as ei:
        s.require_full_infra()
    assert "REDIS_URL" in str(ei.value) and "AEGIS_MODE=lite" in str(ei.value)


def test_full_mode_requires_qdrant() -> None:
    # Qdrant is a hard full-mode dependency (vectors never fall back to RAM).
    s = CoreSettings(mode="full", redis_url="r", database_url="d", qdrant_url=None)
    with pytest.raises(RuntimeError) as ei:
        s.require_full_infra()
    assert "QDRANT_URL" in str(ei.value)


def test_full_mode_with_all_infra_ok() -> None:
    CoreSettings(
        mode="full", redis_url="r", database_url="d", qdrant_url="q"
    ).require_full_infra()  # no raise


def test_lite_mode_tolerates_missing_infra() -> None:
    CoreSettings(mode="lite").require_full_infra()  # no raise


# ── The error names the variables an operator must actually set ──


def test_error_names_the_prefixed_env_vars() -> None:
    """REGRESSION: the message said ``REDIS_URL`` while ``env_prefix="AEGIS_"`` means
    the real variable is ``AEGIS_REDIS_URL`` — an operator following it verbatim would
    set a variable nothing reads."""
    s = CoreSettings(mode="full", redis_url=None, database_url=None, qdrant_url=None)
    with pytest.raises(RuntimeError) as ei:
        s.require_full_infra()
    message = str(ei.value)
    for name in ("AEGIS_REDIS_URL", "AEGIS_DATABASE_URL", "AEGIS_QDRANT_URL"):
        assert name in message


# ── AEGIS_MODE=auto actually probes ──


@pytest.mark.asyncio
async def test_auto_probes_and_resolves_to_full_when_every_backend_answers(monkeypatch) -> None:
    """REGRESSION: ``auto`` was documented as "probes then drops to lite" with NO probe
    anywhere — so it silently behaved as lite regardless of what was reachable."""
    import aegis.core.health as health
    from aegis.core.health import DependencyStatus

    probed: list[str] = []

    async def up(name: str):
        async def probe(url: str, **kwargs):  # noqa: ANN003
            probed.append(name)
            return DependencyStatus(name=name, status="up")

        return probe

    monkeypatch.setattr(health, "probe_redis", await up("redis"))
    monkeypatch.setattr(health, "probe_postgres", await up("postgres"))
    monkeypatch.setattr(health, "probe_qdrant", await up("qdrant"))

    s = CoreSettings(mode="auto", redis_url="r", database_url="d", qdrant_url="q")
    assert await s.resolve_mode() is AegisMode.full
    assert sorted(probed) == ["postgres", "qdrant", "redis"]


@pytest.mark.asyncio
async def test_auto_drops_to_lite_loudly_when_a_backend_is_down(monkeypatch, caplog) -> None:
    import logging

    import aegis.core.health as health
    from aegis.core.health import DependencyStatus

    async def up(url: str, **kwargs):  # noqa: ANN003
        return DependencyStatus(name="ok", status="up")

    async def down(url: str, **kwargs):  # noqa: ANN003
        return DependencyStatus(name="qdrant", status="down", detail="unreachable")

    monkeypatch.setattr(health, "probe_redis", up)
    monkeypatch.setattr(health, "probe_postgres", up)
    monkeypatch.setattr(health, "probe_qdrant", down)

    s = CoreSettings(mode="auto", redis_url="r", database_url="d", qdrant_url="q")
    with caplog.at_level(logging.WARNING, logger="aegis.core.config"):
        assert await s.resolve_mode() is AegisMode.lite
    assert "qdrant" in caplog.text and "LITE" in caplog.text


@pytest.mark.asyncio
async def test_auto_without_urls_resolves_to_lite_and_says_so(caplog) -> None:
    import logging

    s = CoreSettings(mode="auto", redis_url=None, database_url=None, qdrant_url=None)
    with caplog.at_level(logging.WARNING, logger="aegis.core.config"):
        assert await s.resolve_mode() is AegisMode.lite
    assert "AEGIS_REDIS_URL" in caplog.text


@pytest.mark.asyncio
async def test_resolve_mode_still_enforces_full() -> None:
    s = CoreSettings(mode="full", redis_url=None, database_url=None, qdrant_url=None)
    with pytest.raises(RuntimeError):
        await s.resolve_mode()
    assert await CoreSettings(mode="lite").resolve_mode() is AegisMode.lite
