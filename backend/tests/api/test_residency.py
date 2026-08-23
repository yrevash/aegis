"""Data residency — the inventory must follow the wiring, not flatter it.

The whole value of ``app.platform.residency`` is that it is *derived*. A hand-written
"all data stays in India" is true on the day it is typed and false the first time
somebody edits an environment variable, and nothing catches the difference. So the
tests here are about the two ways a derived claim still goes wrong:

* **It falls behind.** A new outbound dependency is added, its destination setting is
  never declared, and the inventory quietly describes a smaller system than the one
  running. :func:`test_every_destination_setting_is_declared` reads
  ``Settings.model_fields`` directly, so that failure is a red test, not a discovery.
* **It rounds in the reassuring direction.** An offshore store must read ``external``
  and an unreadable destination must never read ``local`` — a residency surface that
  guesses optimistically is worse than none, because it is believed.

One more, because this payload is read by a human: the Postgres password must not be in
it.
"""

from __future__ import annotations

import pytest

from app.config import Settings, get_settings
from app.platform.residency import (
    _DECLARED,
    NOTE,
    ChannelRole,
    Locality,
    build_residency,
    destination_fields,
)


def _live() -> Settings:
    """The process's settings, as the residency surface would read them."""
    return get_settings()


def test_every_destination_setting_is_declared() -> None:
    """A new outbound destination cannot be added without appearing in the inventory.

    This is the anti-rot mechanism the surface's credibility rests on: the rule for
    "this field names a network destination" is read from ``Settings`` itself, so a
    field added tomorrow is compared against the declared table today.
    """
    claimed = {setting for setting, *_ in _DECLARED}
    missing = destination_fields() - claimed
    assert not missing, (
        f"{sorted(missing)} name network destinations on Settings but no residency channel "
        "claims them — the inventory would describe a smaller system than the one running."
    )
    stale = claimed - destination_fields()
    assert not stale, f"{sorted(stale)} are declared as channels but are not Settings fields."


def test_credentials_never_reach_the_report() -> None:
    """A DSN password must not travel in a payload a reviewer reads on a screen."""
    settings = _live().model_copy(
        update={"postgres_dsn": "postgresql://aegis_app:hunter2@localhost:5432/taif"}
    )
    body = build_residency(settings).model_dump_json()
    assert "hunter2" not in body
    assert "aegis_app" not in body


def test_an_offshore_store_is_reported_external() -> None:
    """Point the system of record at a public host and the verdict flips. That is the point.

    The failure mode this guards is the one that matters commercially: a residency claim
    that keeps reading "local" after the database moved is a false statement about
    personal data, made with a number attached.
    """
    settings = _live().model_copy(
        update={"postgres_dsn": "postgresql://u:p@db.example.com:5432/taif"}
    )
    report = build_residency(settings)
    postgres = next(channel for channel in report.channels if channel.id == "postgres")
    assert postgres.locality is Locality.EXTERNAL
    assert postgres.role is ChannelRole.STORE
    assert report.stores_external >= 1


@pytest.mark.parametrize(
    "raw", ["not a url at all", "://", "http://"], ids=["garbage", "bare-scheme", "no-host"]
)
def test_an_unreadable_destination_is_never_reported_local(raw: str) -> None:
    """An unparseable address is unknown, never local. Guessing has one safe direction."""
    settings = _live().model_copy(update={"qdrant_url": raw})
    qdrant = next(c for c in build_residency(settings).channels if c.id == "qdrant")
    assert qdrant.locality is not Locality.LOCAL


def test_the_local_deployment_keeps_every_store_on_the_host() -> None:
    """The claim this surface exists to support, asserted against the default wiring.

    Postgres, Qdrant, Neo4j and Redis all default to loopback, so tenant documents,
    embeddings, the knowledge graph, memory and the audit trail are at rest on the
    deployment host. The model gateway is deliberately *not* asserted local — it is the
    one channel that carries content off the host, and the report says so.
    """
    report = build_residency(Settings())
    assert report.stores_external == 0
    assert report.stores_local >= 4
    gateway = next(c for c in report.channels if c.id == "model-gateway")
    assert gateway.role is ChannelRole.PROCESS
    assert "chunk text" in gateway.carries


def test_the_note_refuses_to_claim_geolocation() -> None:
    """It reports where a destination is addressed. It does not know where a region sits."""
    assert "does not geolocate" in NOTE
    assert build_residency().note == NOTE
