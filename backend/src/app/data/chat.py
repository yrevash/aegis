"""The console's chat transcript — sessions and their turns (task 6.2).

Two tables (:class:`~app.data.models.ChatSession` / :class:`~app.data.models.ChatMessage`)
and the typed CRUD over them. Everything here is scoped by **both** ``tenant_id`` and
``user_id``, and neither is optional in the way ``None`` usually is:

* ``tenant_id`` arrives already resolved by
  :func:`aegis.retrieval.types.tenant_filter`, so a ``None`` here can only have come
  from :data:`~aegis.retrieval.types.ALL_TENANTS` — a platform operator reading
  deliberately — never from a principal that merely lacks a tenant.
* ``user_id`` is the owner. A chat is *personal*: a tenant-admin does not get to read a
  colleague's conversation just because they share a tenant, so every query filters on
  it and :func:`~app.api.routes_console` refuses a principal that carries none, rather
  than letting a missing id widen the predicate to "everybody's".

The row's ``tenant_id`` is also what the ``tenant_isolation`` RLS policy compares
against once :func:`app.data.set_tenant_scope` has bound the scope for the connection —
the database-enforced half of the same boundary.

**Who writes the transcript.** ``POST /query`` does, through :func:`append_chat_turn`:
one row for the user's turn before the run and one for the assistant's answer when the
run finishes. There is deliberately no ``POST /sessions/{id}/messages`` — a client that
could write its own transcript could write one that never happened, and the run that
produced the answer is the only honest author of it.

**A session id is an identifier, not a capability.** This is worth stating because for
a while it was not true. ``chat_sessions.id`` is deliberately the same string as
``memory_session.id``, and ``GET /memory/sessions?subject=user:<colleague>`` hands a
tenant admin every one of a colleague's ids — so an id is *learnable*, not guessable.
When :func:`append_chat_turn` checked only that the session existed in the tenant, that
made every learnable id a write capability into somebody else's transcript.

The fix is the owner predicate on the write, not secrecy for the id, and that ordering
is deliberate. The admin's read stays: ``_authorize_subject``'s admin branch is how a
tenant admin administers their tenant's memory — it is the same branch
``DELETE /memory/facts/{id}`` needs for a right-to-erasure request, and it already
exposes far more than ids (``turn_count``, ``last_active_at``, and the conversation
``summary``). Narrowing it to hide ids would remove a governance capability to buy
secrecy for a string that is not, and must not be, a secret. What was wrong was never
that the id could be learned; it was that knowing it was enough to act. It no longer is
— every function in this module, read and write alike, filters on the owner — and note
that the memory *summary* an admin can read is a different resource from this
transcript, which no admin branch reaches at all.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update

from .models import ChatMessage, ChatSession
from .session import get_sessionmaker, set_tenant_scope

__all__ = [
    "append_chat_turn",
    "create_chat_session",
    "delete_chat_session",
    "get_chat_session",
    "list_chat_messages",
    "list_chat_sessions",
    "rename_chat_session",
]


def _now() -> datetime:
    """Return the current time as a timezone-aware UTC datetime."""
    return datetime.now(UTC)


async def create_chat_session(
    session_id: str,
    *,
    tenant_id: int | None,
    user_id: int,
    title: str = "New chat",
) -> ChatSession:
    """Insert one chat session and return it.

    Args:
        session_id: The id the console minted. It is also used as
            ``memory_session.id``, so the transcript and the recall agree on what a
            conversation is.
        tenant_id: The owning tenant, or ``None`` for an un-tenanted platform account.
        user_id: The owning principal.
        title: A human label for the session rail.

    Returns:
        The persisted :class:`~app.data.models.ChatSession`.
    """
    async with get_sessionmaker()() as db:
        await set_tenant_scope(db, tenant_id)
        row = ChatSession(
            id=session_id, tenant_id=tenant_id, user_id=user_id, title=title
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row


async def list_chat_sessions(
    *, tenant_id: int | None, user_id: int, limit: int = 50
) -> list[ChatSession]:
    """Return the caller's own sessions, most recently active first."""
    async with get_sessionmaker()() as db:
        await set_tenant_scope(db, tenant_id)
        stmt = (
            select(ChatSession)
            .where(ChatSession.user_id == user_id)
            .order_by(ChatSession.last_active_at.desc(), ChatSession.id.desc())
            .limit(limit)
        )
        if tenant_id is not None:
            stmt = stmt.where(ChatSession.tenant_id == tenant_id)
        return list((await db.execute(stmt)).scalars().all())


async def get_chat_session(
    session_id: str, *, tenant_id: int | None, user_id: int
) -> ChatSession | None:
    """Return one of the caller's own sessions, or ``None``.

    ``None`` covers "no such session" and "somebody else's session" alike: telling the
    two apart would let a caller enumerate other people's session ids.
    """
    async with get_sessionmaker()() as db:
        await set_tenant_scope(db, tenant_id)
        stmt = select(ChatSession).where(
            ChatSession.id == session_id, ChatSession.user_id == user_id
        )
        if tenant_id is not None:
            stmt = stmt.where(ChatSession.tenant_id == tenant_id)
        return (await db.execute(stmt)).scalars().first()


async def rename_chat_session(
    session_id: str, title: str, *, tenant_id: int | None, user_id: int
) -> ChatSession | None:
    """Retitle one of the caller's sessions, returning it, or ``None`` if not theirs."""
    async with get_sessionmaker()() as db:
        await set_tenant_scope(db, tenant_id)
        stmt = (
            update(ChatSession)
            .where(ChatSession.id == session_id, ChatSession.user_id == user_id)
            .values(title=title)
        )
        if tenant_id is not None:
            stmt = stmt.where(ChatSession.tenant_id == tenant_id)
        result = await db.execute(stmt)
        await db.commit()
        if result.rowcount != 1:
            return None
        return await get_chat_session(session_id, tenant_id=tenant_id, user_id=user_id)


async def delete_chat_session(
    session_id: str, *, tenant_id: int | None, user_id: int
) -> bool:
    """Delete one of the caller's sessions and its turns; ``False`` if not theirs.

    The messages go with it explicitly rather than relying on the ``ON DELETE CASCADE``:
    the cascade is a Postgres-side belt, and an ORM-level delete of the parent alone
    would leave orphan turns on any backend that did not honour it.
    """
    async with get_sessionmaker()() as db:
        await set_tenant_scope(db, tenant_id)
        owned = select(ChatSession.id).where(
            ChatSession.id == session_id, ChatSession.user_id == user_id
        )
        if tenant_id is not None:
            owned = owned.where(ChatSession.tenant_id == tenant_id)
        if (await db.execute(owned)).scalars().first() is None:
            return False
        await db.execute(
            delete(ChatMessage).where(ChatMessage.session_id == session_id)
        )
        await db.execute(delete(ChatSession).where(ChatSession.id == session_id))
        await db.commit()
        return True


async def list_chat_messages(
    session_id: str, *, tenant_id: int | None, user_id: int, limit: int = 500
) -> list[ChatMessage] | None:
    """Return one session's turns in order, or ``None`` when it is not the caller's.

    The ``None`` is what keeps the 404 from being an oracle: the ownership check runs
    against ``chat_sessions`` first, so "this id is not yours" and "this id does not
    exist" produce the same answer at the HTTP boundary.
    """
    async with get_sessionmaker()() as db:
        await set_tenant_scope(db, tenant_id)
        owned = select(ChatSession.id).where(
            ChatSession.id == session_id, ChatSession.user_id == user_id
        )
        if tenant_id is not None:
            owned = owned.where(ChatSession.tenant_id == tenant_id)
        if (await db.execute(owned)).scalars().first() is None:
            return None
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.turn_index.asc(), ChatMessage.id.asc())
            .limit(limit)
        )
        return list((await db.execute(stmt)).scalars().all())


async def append_chat_turn(
    session_id: str,
    *,
    role: str,
    content: str,
    tenant_id: int | None,
    user_id: int,
    run_id: str | None = None,
) -> int | None:
    """Append one turn to the caller's own session and bump its activity clock.

    Called by ``POST /query`` for the user's question and again for the answer. A
    ``session_id`` that names no row **of the caller's** is a no-op returning ``None``
    rather than an error: the transcript is a record of the run, and failing the run
    because the record could not be written would trade a working product for a
    bookkeeping detail.

    ``user_id`` is not optional and not a courtesy. This function used to check only
    that the session existed *in the tenant*, which made ``session_id`` a write
    capability for anyone who could learn one: a colleague could POST ``/query`` with
    a co-worker's id and both turns landed in that person's transcript — the assistant
    turn carrying a ``run_id`` from a run they never made, and ``last_active_at`` bumped
    so their session rail re-ordered under them. Every sibling in this module filters on
    the owner; the write is the one that most needed to.

    **The ``UPDATE`` is deliberately first.** It is both the ownership check (a
    ``rowcount`` of zero means "no such session of yours") and the serialisation point:
    it takes the session row's write lock, so a second concurrent appender on the same
    conversation blocks there rather than racing the ``count(*)`` below and minting a
    duplicate ``turn_index``. Ordering it after the count — as it was — left the two
    writers counting the same value.

    Args:
        session_id: The conversation the turn belongs to.
        role: ``user`` | ``assistant``.
        content: The turn's text.
        tenant_id: Scope bound for RLS and stamped on the row.
        user_id: The owner. A turn is only ever appended to that person's own session.
        run_id: The run that produced an assistant turn; ``None`` for a user turn.

    Returns:
        The 0-based ``turn_index`` written, or ``None`` when the caller owns no such
        session.
    """
    async with get_sessionmaker()() as db:
        await set_tenant_scope(db, tenant_id)
        bump = (
            update(ChatSession)
            .where(ChatSession.id == session_id, ChatSession.user_id == user_id)
            .values(last_active_at=_now())
        )
        if tenant_id is not None:
            bump = bump.where(ChatSession.tenant_id == tenant_id)
        if (await db.execute(bump)).rowcount != 1:
            await db.rollback()
            return None
        next_index = (
            await db.execute(
                select(func.count()).where(ChatMessage.session_id == session_id)
            )
        ).scalar_one()
        db.add(
            ChatMessage(
                session_id=session_id,
                tenant_id=tenant_id,
                turn_index=next_index,
                role=role,
                content=content,
                run_id=run_id,
            )
        )
        await db.commit()
        return int(next_index)
