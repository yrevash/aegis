"""The host's half of D4: which process loads the Docling models, and what a failure does."""

from __future__ import annotations

import threading

import pytest

import app.config as config_mod
from app.ingestion import warm_parser


@pytest.fixture
def settings_env(monkeypatch):
    """Give each test a fresh settings object built from the env it sets."""
    config_mod.get_settings.cache_clear()
    yield monkeypatch
    config_mod.get_settings.cache_clear()


async def test_warming_is_off_unless_the_deployment_says_it_parses(settings_env):
    # The default. An API-only process and a test worker must not hold ~1 GB of models
    # for a stage they will never run.
    settings_env.setenv("DOCLING_WARM_ON_START", "false")
    called = []
    import aegis.ingestion as ingestion

    settings_env.setattr(ingestion, "warm_converter", lambda: called.append(1))

    assert await warm_parser() == 0.0
    assert called == []


async def test_warming_loads_the_models_off_the_event_loop(settings_env):
    # Model loading is blocking, CPU-bound work; doing it on the loop would stall the
    # worker it is meant to be starting alongside.
    settings_env.setenv("DOCLING_WARM_ON_START", "true")
    import aegis.ingestion as ingestion

    threads: list[int] = []

    def fake_warm() -> float:
        threads.append(threading.get_ident())
        return 2.5

    settings_env.setattr(ingestion, "warm_converter", fake_warm)

    assert await warm_parser() == 2.5
    assert threads and threads[0] != threading.get_ident()


async def test_a_failed_warm_up_never_stops_the_worker_starting(settings_env):
    settings_env.setenv("DOCLING_WARM_ON_START", "true")
    import aegis.ingestion as ingestion

    def explode() -> float:
        raise RuntimeError("no model cache and no network")

    settings_env.setattr(ingestion, "warm_converter", explode)

    assert await warm_parser() == 0.0
