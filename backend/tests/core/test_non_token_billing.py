"""End-to-end: a non-token-billed call reaches the durable ledger with real cost.

This is the whole chain, with nothing stubbed between the ends — the gateway's
``transcribe`` → this platform's injected ``_GovernanceHook`` → ``record_usage``
→ a ``UsageLedger`` row — against the real PostgreSQL database bound by the ``db``
fixture. Only ``litellm`` is faked, because the gateway credential is a placeholder and
no network call is possible here.

Whisper bills per minute of audio. Before non-token units existed, this row would
have been ``prompt_tokens=0`` → ``$0.00``, so a tenant with a USD cap could
transcribe without limit and the savings dashboard under-reported real spend.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import aegis.gateway.llm as llm_mod
import pytest
import pytest_asyncio
from sqlalchemy import select

from app.api.schemas import Role
from app.core.governance import (
    GovernanceContext,
    reset_governance_context,
    set_governance_context,
)
from app.core.llm import BudgetExceededError, transcribe
from app.data import (
    Budget,
    BudgetScope,
    BudgetWindow,
    Tenant,
    UsageLedger,
    User,
    get_sessionmaker,
)

pytestmark = pytest.mark.asyncio


class _FakeLiteLLM:
    """A litellm stand-in whose ``atranscription`` reports a known duration."""

    def __init__(self, *, duration=120.0):
        self.ssl_verify = None
        self.transcription_calls: list[dict] = []
        self._duration = duration

    async def atranscription(self, **kwargs):
        self.transcription_calls.append(kwargs)
        return SimpleNamespace(
            text="the quarterly report is late",
            duration=self._duration,
            language="en",
            segments=None,
            model="genailab-maas-whisper",
            usage=None,
        )

    def completion_cost(self, *, completion_response):
        # The real cost map has no entry for an audio deployment.
        return 0.0


@pytest.fixture
def fake_litellm(monkeypatch):
    fake = _FakeLiteLLM()
    monkeypatch.setitem(sys.modules, "litellm", fake)
    monkeypatch.setattr(llm_mod, "_ssl_configured", False)
    monkeypatch.setattr(llm_mod, "_tally", llm_mod._UsageTally())
    return fake


@pytest.fixture
def audio_file(tmp_path):
    path = tmp_path / "clip.wav"
    path.write_bytes(b"RIFF....WAVEfake")
    return path


async def _seed(*rows):
    async with get_sessionmaker()() as session:
        for row in rows:
            session.add(row)
        await session.commit()


@pytest_asyncio.fixture
async def principals(db):
    """Seed the tenant and user the transcriptions below are attributed to.

    PostgreSQL enforces ``usage_ledger.tenant_id → tenants.id`` and
    ``usage_ledger.user_id → users.id``; SQLite, where this file used to run, does not
    enforce foreign keys at all by default. So the ledger row this test exists to prove
    was previously written against a tenant that did not exist — the row was real, the
    attribution it claimed was not.

    Returns:
        The session factory from ``db``, so a test needs only this fixture.
    """
    async with get_sessionmaker()() as session:
        session.add_all(
            [
                Tenant(id=1, name="Tenant One"),
                User(id=2, username="member", role=Role.CLIENT, tenant_id=1),
            ]
        )
        await session.commit()
    return db


async def _ledger_rows(tenant_id=1):
    async with get_sessionmaker()() as session:
        return (
            await session.execute(
                select(UsageLedger).where(UsageLedger.tenant_id == tenant_id)
            )
        ).scalars().all()


async def test_transcription_with_a_known_duration_ledgers_a_non_zero_cost(
    fake_litellm, principals, audio_file
):
    """A 2-minute clip writes one ledger row priced from its audio minutes."""
    await _seed(
        Budget(tenant_id=1, scope_type=BudgetScope.TENANT, scope_id=1, usd_cap=100.0)
    )

    tok = set_governance_context(GovernanceContext(tenant_id=1, user_id=2))
    try:
        result = await transcribe(audio_file)
    finally:
        reset_governance_context(tok)

    assert result.text == "the quarterly report is late"
    assert result.duration_seconds == pytest.approx(120.0)

    rows = await _ledger_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row.model == "genailab-maas-whisper"
    assert row.audio_seconds == pytest.approx(120.0)
    assert row.prompt_tokens == 0  # genuinely no tokens — the point of the fix
    assert row.cost_usd > 0.0
    assert row.cost_usd == pytest.approx(result.usage.cost_usd)


async def test_a_usd_capped_tenant_cannot_transcribe_without_limit(
    fake_litellm, principals, audio_file
):
    """Audio spend accumulates against the USD cap and then refuses the next call."""
    await _seed(
        Budget(
            tenant_id=1,
            scope_type=BudgetScope.TENANT,
            scope_id=1,
            window=BudgetWindow.DAY,
            usd_cap=0.02,
        )
    )
    tok = set_governance_context(GovernanceContext(tenant_id=1, user_id=2))
    try:
        # Each 2-minute clip costs 2 × $0.006 = $0.012, so the second call takes
        # the tenant to $0.024 — over the $0.02 cap — and the third is refused.
        await transcribe(audio_file)
        await transcribe(audio_file)
        with pytest.raises(BudgetExceededError) as ei:
            await transcribe(audio_file)
    finally:
        reset_governance_context(tok)

    assert ei.value.limit_type == "usd_cap"
    # Refused BEFORE spend: only the two allowed calls ever reached the provider.
    assert len(fake_litellm.transcription_calls) == 2


async def test_an_ungoverned_transcription_writes_no_ledger_row(
    fake_litellm, db, audio_file
):
    """No bound tenant → the whole governance path stays a no-op, as for chat."""
    result = await transcribe(audio_file)

    assert result.text
    assert await _ledger_rows() == []
