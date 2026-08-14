"""The usage ledger must record non-token billable units, and cap on their cost.

Whisper bills per minute of audio and a vision call may be billed per image, so a
ledger that only knows ``prompt_tokens``/``completion_tokens`` would write
``$0.00`` for every such call — and a tenant with a USD cap could transcribe
without limit. These run against the in-memory aiosqlite database bound by the
``db`` fixture: no host, no network.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from aegis.gateway.types import BudgetExceededError
from aegis.governance import (
    Budget,
    BudgetScope,
    BudgetWindow,
    UsageLedger,
    enforce_governance,
    record_usage,
    usage_rollup,
)


async def _seed(db, *rows):
    async with db() as session:
        for row in rows:
            session.add(row)
        await session.commit()


async def _rows(db, tenant_id=1):
    async with db() as session:
        return (
            await session.execute(
                select(UsageLedger).where(UsageLedger.tenant_id == tenant_id)
            )
        ).scalars().all()


async def test_record_usage_persists_audio_seconds(db):
    """A 2-minute transcription is a real, attributable row — not a $0.00 blank."""
    await record_usage(
        tenant_id=1,
        user_id=2,
        model="genailab-maas-whisper",
        prompt_tokens=0,
        completion_tokens=0,
        audio_seconds=120.0,
        cost_usd=0.012,
        trace_id="t-voice",
    )
    rows = await _rows(db)
    assert len(rows) == 1
    assert rows[0].audio_seconds == pytest.approx(120.0)
    assert rows[0].images == 0
    assert rows[0].cost_usd == pytest.approx(0.012)


async def test_record_usage_persists_image_counts(db):
    await record_usage(
        tenant_id=1,
        user_id=2,
        model="genailab-maas-Llama-3.2-90B-Vision-Instruct",
        prompt_tokens=1500,
        completion_tokens=200,
        images=3,
        cost_usd=0.02,
        trace_id="t-vision",
    )
    rows = await _rows(db)
    assert rows[0].images == 3
    assert rows[0].prompt_tokens == 1500


async def test_token_only_record_usage_is_unchanged(db):
    """Existing call sites pass no units and must behave exactly as before."""
    await record_usage(
        tenant_id=1,
        user_id=2,
        model="genailab-maas-gpt-4o",
        prompt_tokens=11,
        completion_tokens=7,
        cost_usd=0.0002,
        trace_id="t-1",
    )
    rows = await _rows(db)
    assert rows[0].audio_seconds == 0.0
    assert rows[0].images == 0


async def test_a_usd_cap_bites_on_audio_only_spend(db):
    """The point of the whole exercise: per-minute spend is capped like any other.

    The row carries zero tokens, so only the USD cap can see it — and it does,
    because the ledgered cost is the audio charge rather than a token product.
    """
    await _seed(
        db,
        Budget(
            scope_type=BudgetScope.TENANT,
            scope_id=1,
            window=BudgetWindow.DAY,
            usd_cap=0.05,
        ),
        UsageLedger(
            tenant_id=1,
            model="genailab-maas-whisper",
            prompt_tokens=0,
            completion_tokens=0,
            audio_seconds=600.0,
            cost_usd=0.06,
        ),
    )
    with pytest.raises(BudgetExceededError) as ei:
        await enforce_governance(tenant_id=1, user_id=2)
    assert ei.value.limit_type == "usd_cap"


async def test_a_token_cap_ignores_audio_seconds(db):
    """An audio minute is not a token — the token cap must not pretend otherwise."""
    await _seed(
        db,
        Budget(scope_type=BudgetScope.TENANT, scope_id=1, token_cap=100),
        UsageLedger(tenant_id=1, prompt_tokens=0, audio_seconds=99_999.0, cost_usd=10.0),
    )
    # No token cap breach: the tenant has consumed zero tokens.
    await enforce_governance(tenant_id=1, user_id=2)


async def test_usage_rollup_includes_audio_spend(db):
    """The dashboard's cost total sees per-minute spend like any other spend."""
    await _seed(
        db,
        UsageLedger(
            tenant_id=1, model="genailab-maas-whisper", audio_seconds=60.0, cost_usd=0.006
        ),
        UsageLedger(
            tenant_id=1, model="genailab-maas-gpt-4o", prompt_tokens=100, cost_usd=0.001
        ),
    )
    _pt, _ct, total_cost, by_model, _series = await usage_rollup(tenant_id=1)
    assert total_cost == pytest.approx(0.007)
    assert {m.model for m in by_model} == {
        "genailab-maas-whisper",
        "genailab-maas-gpt-4o",
    }
