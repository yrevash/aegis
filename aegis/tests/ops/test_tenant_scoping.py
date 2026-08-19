"""The prompt registry is keyed on the tenant — in both directions (§7.7).

Every test here fails if the tenant is taken out of one key. That is the point: the
defect this file exists to close could not manifest with one tenant in the database, so
the whole suite passed while ``_ACTIVE_CACHE`` was a ``dict[prompt_key]``, ``get_active``
treated ``tenant_id=None`` as "any tenant", and ``promote`` archived every tenant's active
row that happened to share a ``prompt_key``.

Two tenants, one prompt key, and the four things that go wrong without the tenant:

1. **The read leaks.** Whichever tenant promoted last is served to every run in the
   process — a cross-tenant leak wearing a working feature's clothes.
2. **The history leaks.** ``list_versions`` hands tenant A tenant B's drafts.
3. **The write leaks.** Activating for A archives B's live version, and B's cache keeps
   serving a row the database has retired.
4. **The invalidation leaks.** Refreshing after A's activation either misses B (stale) or
   clears B (floor prompt for everybody).

There is no fixture magic: each test promotes for both tenants and asserts each one sees
only its own. Delete ``tenant_id`` from the key under test and it goes red.
"""

from __future__ import annotations

from aegis.ops import registry
from aegis.ops.models import PromptStatus

PK = "operations_lead"
ACME = 1
GLOBEX = 2


async def _promote(session, *, tenant_id: int | None, body: str):
    """Create and activate one version for ``tenant_id``, committing it."""
    pv = await registry.create_draft(
        session, prompt_key=PK, system_prompt=body, tenant_id=tenant_id
    )
    await registry.promote(session, pv.id)
    await session.commit()
    return pv


async def test_two_tenants_hold_different_active_prompts_at_once(db):
    """The cache serves each tenant its own active version, not the last one promoted.

    **The mutation.** Key ``_ACTIVE_CACHE`` on ``prompt_key`` alone and the second
    ``_promote`` overwrites the first entry: both reads return ``"globex prompt"`` and
    this fails. That is exactly what a run for Acme would have sent to the model.
    """
    async with db() as s:
        await _promote(s, tenant_id=ACME, body="acme prompt")
        await _promote(s, tenant_id=GLOBEX, body="globex prompt")

    acme = registry.get_cached_active(PK, ACME)
    globex = registry.get_cached_active(PK, GLOBEX)
    assert acme is not None and acme[0] == "acme prompt"
    assert globex is not None and globex[0] == "globex prompt"


async def test_activating_for_one_tenant_does_not_archive_anothers(db):
    """Acme's promotion leaves Globex's live row ACTIVE, in the database and the cache.

    **The mutation.** Drop ``_tenant_clause`` from ``promote``'s archive UPDATE and
    Globex's version is ARCHIVED by a statement Acme ran — while the cache, unaware,
    keeps handing Globex a prompt the registry says is retired.
    """
    async with db() as s:
        globex_v1 = await _promote(s, tenant_id=GLOBEX, body="globex prompt")
        await _promote(s, tenant_id=ACME, body="acme prompt")

        await s.refresh(globex_v1)
        assert globex_v1.status is PromptStatus.ACTIVE

        live = await registry.get_active(s, PK, GLOBEX)
        assert live is not None and live.system_prompt == "globex prompt"

    assert registry.get_cached_active(PK, GLOBEX)[0] == "globex prompt"


async def test_history_and_version_numbers_are_per_tenant(db):
    """``list_versions`` shows one tenant its own drafts, numbered from 1.

    **Two mutations.** Drop the tenant from ``list_versions`` and Acme is shown Globex's
    drafts. Drop it from ``create_draft``'s ``max(version)`` scan (and from the unique
    index underneath) and Acme's first version is numbered 3 because Globex wrote two.
    """
    async with db() as s:
        await registry.create_draft(
            s, prompt_key=PK, system_prompt="globex a", tenant_id=GLOBEX
        )
        await registry.create_draft(
            s, prompt_key=PK, system_prompt="globex b", tenant_id=GLOBEX
        )
        await registry.create_draft(
            s, prompt_key=PK, system_prompt="acme a", tenant_id=ACME
        )
        await s.commit()

        acme = await registry.list_versions(s, PK, ACME)
        globex = await registry.list_versions(s, PK, GLOBEX)

    assert [(v.version, v.system_prompt) for v in acme] == [(1, "acme a")]
    assert [v.version for v in globex] == [2, 1]


async def test_refreshing_one_tenant_leaves_another_tenants_cache_intact(db):
    """Re-reading Acme after an activation neither misses nor clears Globex.

    Invalidation is the other half of keying, and it fails in both directions: a
    whole-cache ``clear()`` on one tenant's activation drops every other tenant to the
    floor prompt until the next restart, and no clear at all leaves them on a version the
    database has archived.
    """
    async with db() as s:
        await _promote(s, tenant_id=GLOBEX, body="globex prompt")
        await _promote(s, tenant_id=ACME, body="acme v1")

    async with db() as s:
        acme_v2 = await registry.create_draft(
            s, prompt_key=PK, system_prompt="acme v2", tenant_id=ACME
        )
        await registry.promote(s, acme_v2.id)
        await s.commit()
        loaded = await registry.refresh_cache(s, ACME)

    assert loaded == 1  # Acme's row only — Globex's was not re-read
    assert registry.get_cached_active(PK, ACME)[0] == "acme v2"
    assert registry.get_cached_active(PK, GLOBEX)[0] == "globex prompt"


async def test_a_tenant_without_a_version_falls_back_to_the_platform_row(db):
    """No version of your own ⇒ the platform's, never another tenant's.

    The platform row (``tenant_id IS NULL``) is the *only* prompt outside a tenant's own
    that it may resolve to, and the fallback is one-way: a platform read never picks up a
    tenant's row.
    """
    async with db() as s:
        await _promote(s, tenant_id=None, body="platform prompt")
        await _promote(s, tenant_id=ACME, body="acme prompt")

    assert registry.get_cached_active(PK, GLOBEX)[0] == "platform prompt"
    assert registry.get_cached_active(PK, ACME)[0] == "acme prompt"
    assert registry.get_cached_active(PK)[0] == "platform prompt"


async def test_rollback_reverts_within_one_tenant_only(db):
    """Acme's revert walks Acme's history — it cannot reactivate Globex's archived row.

    **The mutation.** Drop the tenant from ``rollback``'s revert-target query and the
    most-recently-archived row *on the platform* wins, which here is Globex's: one
    tenant's rollback button puts another tenant's prompt live under their own key.
    """
    async with db() as s:
        await _promote(s, tenant_id=ACME, body="acme v1")
        await _promote(s, tenant_id=GLOBEX, body="globex v1")
        await _promote(s, tenant_id=GLOBEX, body="globex v2")
        await _promote(s, tenant_id=ACME, body="acme v2")

        rolled = await registry.rollback(s, PK, ACME)
        await s.commit()

    assert rolled is not None and rolled.system_prompt == "acme v1"
    assert registry.get_cached_active(PK, ACME)[0] == "acme v1"
    assert registry.get_cached_active(PK, GLOBEX)[0] == "globex v2"
