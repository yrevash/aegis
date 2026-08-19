"""``python -m app.seed`` — the two-tenant starting state (§3.8).

Measured against the live database on 2026-08-17, ``tenants``, ``users`` and ``budgets``
all held **zero rows**: every login fell through a hardcoded demo table in
:mod:`app.api.routes`, so nothing per-tenant had ever run with a tenant. Row-Level
Security, the budget enforcement and every per-tenant console screen were untested end
to end because there was no tenant to test them against. That demo table is deleted with
this module — the rows written here are the only principals that can sign in.

What it writes:

* the five **platform-staff** principals the console's quick-in buttons post
  (``admin`` / ``ai`` / ``aiteam`` / ``devops`` / ``client``), un-tenanted as they have
  always been, now as real ``users`` rows with Argon2-hashed passwords rather than as a
  branch in the login handler;
* **two tenants**, each with a tenant admin and two users;
* a day **budget** for each tenant and a tighter one for each tenant's client user, so
  the nearest-binding-limit resolution has something real to resolve;
* three **documents** per tenant, each inserted with that tenant's RLS scope bound, so
  the seed itself exercises the isolation it exists to make testable;
* one parked **human gate** per tenant, raised by that tenant's client user, and one
  un-tenanted gate that is Aegis's own action — so the approvals inbox (§7.1) has
  something real in all three of its scopes on a fresh database instead of demoing as
  an empty queue.

**Idempotency is a property of every step, not a flag.** Each row is looked up by its
natural key — tenant name, username, ``(scope_type, scope_id, window)``,
``(tenant_id, content_sha256)`` — and created only when absent, so a second run writes
nothing, changes nothing and exits ``0``. An existing row is never re-hashed, re-roled or
re-scoped: an operator who changed a seeded account's password keeps it, and the seed
says ``exists`` rather than pretending it wrote something.

**The documents are records, not bytes.** Nothing is uploaded to a blob store here, so
the rows rest at :data:`~aegis.jobs.models.JobStatus.PENDING` with a NULL ``page_count``
— "awaiting ingestion", which is true — instead of claiming a parse and a chunk count
that never happened.

The seed password defaults to ``demo`` (the credential the console's quick-in buttons and
``INSTALL.md`` document) and is overridden with the ``AEGIS_SEED_PASSWORD`` environment
variable. It is hashed with Argon2id before it is persisted; the plaintext is never
stored.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import sys
from dataclasses import dataclass, field

from aegis.governance.types import Role
from aegis.jobs.models import Document, JobStatus
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

# Through the ``app.data`` shim rather than straight from ``aegis.governance``: importing
# it is what injects this deployment's session factory and RLS scope binder into the
# governance package (see ``app.data.governance``), so a governed write from here takes
# the identical path a request's does.
from app.api.schemas import RiskLevel
from app.data import (
    Budget,
    BudgetScope,
    BudgetWindow,
    DuplicateTenantError,
    DuplicateUserError,
    Tenant,
    User,
    create_tenant,
    create_user,
    enqueue_approval,
    get_approval,
    get_sessionmaker,
    set_tenant_scope,
    upsert_budget,
)

__all__ = [
    "DEFAULT_SEED_PASSWORD",
    "PLATFORM_APPROVAL",
    "PLATFORM_PRINCIPALS",
    "SEED_PASSWORD_ENV",
    "TENANTS",
    "ApprovalSpec",
    "DocumentSpec",
    "PrincipalSpec",
    "SeedSummary",
    "TenantSpec",
    "ensure_principal",
    "ensure_tenant",
    "main",
    "platform_principal",
    "seed",
    "seed_password",
    "seed_platform_principals",
]

logger = logging.getLogger(__name__)

#: Environment variable naming the password every seeded account is given.
SEED_PASSWORD_ENV = "AEGIS_SEED_PASSWORD"

#: The documented dev/demo password. Not a secret, and not treated as one: a real
#: deployment sets :data:`SEED_PASSWORD_ENV` before seeding, or rotates each account
#: through ``POST /admin/users`` afterwards.
DEFAULT_SEED_PASSWORD = "demo"


@dataclass(frozen=True, slots=True)
class PrincipalSpec:
    """One account the seed guarantees exists.

    Attributes:
        username: The unique login name — also the idempotency key.
        role: The coarse RBAC role granted on creation.
        email: Contact address, so the admin surfaces render a real column.
    """

    username: str
    role: Role
    email: str


@dataclass(frozen=True, slots=True)
class DocumentSpec:
    """One tenant document, described by the text whose hash anchors it.

    ``content_sha256`` is the ingestion pipeline's idempotency anchor and is unique per
    ``(tenant_id, content_sha256)``, so it is derived here from :attr:`body` rather than
    invented: the digest the row carries is genuinely the digest of the text this spec
    describes, and ``size_bytes`` is genuinely that text's length.

    Attributes:
        filename: The document's name as a tenant would see it.
        mime_type: The media type the (not-yet-uploaded) bytes would carry.
        body: The document's text, hashed and measured to fill the row.
    """

    filename: str
    mime_type: str
    body: str

    @property
    def content_sha256(self) -> str:
        """Return the SHA-256 of :attr:`body` — the per-tenant idempotency key."""
        return hashlib.sha256(self.body.encode("utf-8")).hexdigest()

    @property
    def size_bytes(self) -> int:
        """Return the byte length of :attr:`body`."""
        return len(self.body.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class ApprovalSpec:
    """One parked human gate the seed guarantees exists.

    The approvals inbox is the first screen §7.1 builds and the one the whole phase
    is a prerequisite of, and a queue with nothing in it demos as a blank page. So
    the starting state carries real ``PENDING`` rows: one per tenant, raised by that
    tenant's own client user, plus one un-tenanted gate that is Aegis's own action.
    Between them the three inbox scopes are all non-empty on a fresh database — a
    tenant admin has something to decide, the platform operator has something of its
    own to decide and two tenants' gates it may only watch, and the client who
    raised one can see what became of it.

    Attributes:
        approval_id: The gate id — the primary key, and therefore the idempotency
            key: a second seed run finds the row and writes nothing.
        run_id: The run the gate parked. No LangGraph checkpoint backs a seeded id,
            so deciding one resolves the row without resuming a run — which is the
            honest behaviour for a gate whose run ended before the demo began.
        action: The representative (highest-risk) call awaiting the decision.
        args: That call's arguments.
        actions: Every call approving authorises. More than one on purpose: the card
            counts them out loud, and a seed that only ever showed one would never
            exercise the count on screen.
        risk: The gate's declared risk.
        rationale: Why the gate fired, in the words a reviewer reads.
    """

    approval_id: str
    run_id: str
    action: str
    args: dict[str, object]
    actions: tuple[dict[str, object], ...]
    risk: RiskLevel
    rationale: str


@dataclass(frozen=True, slots=True)
class TenantSpec:
    """One tenant and everything that hangs off it.

    Attributes:
        name: The tenant's unique name — the idempotency key.
        admin: The tenant admin. An ``admin`` role *with* a tenant is the
            ``tenant_admin`` tier (:func:`aegis.governance.security.principal_role`).
        users: The two non-admin members.
        token_cap: The tenant's daily token ceiling.
        usd_cap: The tenant's daily spend ceiling, in USD.
        rpm: The tenant's requests-per-minute ceiling.
        tpm: The tenant's tokens-per-minute ceiling.
        client_token_cap: The daily token ceiling for the tenant's client user —
            deliberately below :attr:`token_cap`, since a user budget nests inside its
            tenant's rather than widening it.
        client_usd_cap: The same, in USD.
        documents: The documents the tenant starts with.
        approval: The parked human gate this tenant's inbox starts with, raised by
            its client user.
    """

    name: str
    admin: PrincipalSpec
    users: tuple[PrincipalSpec, ...]
    token_cap: int
    usd_cap: float
    rpm: int
    tpm: int
    client_token_cap: int
    client_usd_cap: float
    documents: tuple[DocumentSpec, ...]
    approval: ApprovalSpec


#: The platform's own staff, plus the un-tenanted client the console's quick-in button
#: posts. They carry no ``tenant_id`` — as the deleted demo table's principals did — so
#: their runs are ungoverned; a tenant's user (below) is the governed case.
#:
#: ``ai`` and ``aiteam`` are two separate accounts rather than an alias pair: the demo
#: table could alias a username onto a role, a ``users`` table has rows.
PLATFORM_PRINCIPALS: tuple[PrincipalSpec, ...] = (
    PrincipalSpec("admin", Role.ADMIN, "admin@aegis.local"),
    PrincipalSpec("ai", Role.AI_TEAM, "ai@aegis.local"),
    PrincipalSpec("aiteam", Role.AI_TEAM, "aiteam@aegis.local"),
    PrincipalSpec("devops", Role.DEVOPS, "devops@aegis.local"),
    PrincipalSpec("client", Role.CLIENT, "client@aegis.local"),
)


def platform_principal(username: str) -> PrincipalSpec:
    """Return the platform principal called ``username``.

    Exists so a caller that needs one account — a fixture logging in as the platform
    admin, an operator provisioning a single staff login — takes the spec the seed
    itself uses instead of restating the role and the email somewhere they can drift.

    Args:
        username: One of the usernames in :data:`PLATFORM_PRINCIPALS`.

    Raises:
        KeyError: If no platform principal has that username.
    """
    for spec in PLATFORM_PRINCIPALS:
        if spec.username == username:
            return spec
    known = ", ".join(spec.username for spec in PLATFORM_PRINCIPALS)
    raise KeyError(f"no platform principal named {username!r}; the seed defines: {known}")


def _documents(slug: str, subject: str) -> tuple[DocumentSpec, ...]:
    """Return one tenant's starting documents.

    The bodies differ per tenant (they carry the tenant's own name), which is what makes
    the isolation check meaningful: the two tenants' ``content_sha256`` values differ, so
    a row seen under the wrong scope cannot be mistaken for one's own.

    Args:
        slug: The tenant's short name, used in the filenames.
        subject: The tenant's full name, written into each document body.
    """
    return (
        DocumentSpec(
            filename=f"{slug}-supplier-agreement.pdf",
            mime_type="application/pdf",
            body=(
                f"Supplier agreement between {subject} and its logistics vendors. "
                "Sets the service levels, the escalation path and the termination "
                "notice period."
            ),
        ),
        DocumentSpec(
            filename=f"{slug}-incident-postmortem.md",
            mime_type="text/markdown",
            body=(
                f"Post-incident review for {subject}: a queue backlog delayed order "
                "confirmations by four hours. Root cause, timeline and the three "
                "follow-up actions."
            ),
        ),
        DocumentSpec(
            filename=f"{slug}-service-report-q3.pdf",
            mime_type="application/pdf",
            body=(
                f"Quarterly service report for {subject}. Volumes, resolution times "
                "and the cost breakdown for the quarter."
            ),
        ),
    )


#: The two tenants. Two, not one, because a single tenant proves nothing about isolation:
#: every assertion that one tenant cannot see another's rows needs another's rows.
TENANTS: tuple[TenantSpec, ...] = (
    TenantSpec(
        name="Northwind Trading",
        admin=PrincipalSpec("northwind.admin", Role.ADMIN, "admin@northwind.example"),
        users=(
            PrincipalSpec("northwind.analyst", Role.AI_TEAM, "analyst@northwind.example"),
            PrincipalSpec("northwind.client", Role.CLIENT, "ops@northwind.example"),
        ),
        token_cap=2_000_000,
        usd_cap=50.0,
        rpm=120,
        tpm=200_000,
        client_token_cap=200_000,
        client_usd_cap=5.0,
        documents=_documents("northwind", "Northwind Trading"),
        approval=ApprovalSpec(
            approval_id="seed-gate-northwind",
            run_id="seed-run-northwind",
            action="issue_supplier_credit",
            args={"supplier": "Halden Freight", "amount_usd": 4200, "reason": "SLA breach"},
            actions=(
                {
                    "id": "seed-gate-northwind-1",
                    "name": "issue_supplier_credit",
                    "args": {
                        "supplier": "Halden Freight",
                        "amount_usd": 4200,
                        "reason": "SLA breach",
                    },
                    "risk": "high",
                },
                {
                    "id": "seed-gate-northwind-2",
                    "name": "notify_account_owner",
                    "args": {"supplier": "Halden Freight", "channel": "email"},
                    "risk": "low",
                },
            ),
            risk=RiskLevel.HIGH,
            rationale=(
                "The credit exceeds the desk's own authority and the supplier is "
                "inside its notice period, so the write needs a person."
            ),
        ),
    ),
    TenantSpec(
        name="Vertex Logistics",
        admin=PrincipalSpec("vertex.admin", Role.ADMIN, "admin@vertex.example"),
        users=(
            PrincipalSpec("vertex.analyst", Role.AI_TEAM, "analyst@vertex.example"),
            PrincipalSpec("vertex.client", Role.CLIENT, "ops@vertex.example"),
        ),
        token_cap=1_000_000,
        usd_cap=25.0,
        rpm=60,
        tpm=100_000,
        client_token_cap=100_000,
        client_usd_cap=2.5,
        documents=_documents("vertex", "Vertex Logistics"),
        approval=ApprovalSpec(
            approval_id="seed-gate-vertex",
            run_id="seed-run-vertex",
            action="cancel_shipment",
            args={"shipment": "VX-88104", "reason": "customs hold"},
            actions=(
                {
                    "id": "seed-gate-vertex-1",
                    "name": "cancel_shipment",
                    "args": {"shipment": "VX-88104", "reason": "customs hold"},
                    "risk": "high",
                },
            ),
            risk=RiskLevel.HIGH,
            rationale=(
                "Cancelling a shipment already in transit is not reversible from "
                "the agent's side."
            ),
        ),
    ),
)

#: Aegis's own parked gate — the one a platform admin may actually decide. It carries
#: no ``tenant_id`` because no tenant raised it, and that is exactly what makes it the
#: platform operator's to decide: since §7.1 deleted the platform-admin exemption, a
#: gate that names a tenant belongs to that tenant's admin and the operator may only
#: watch it. Without this row the platform inbox would be a screen of controls the
#: operator is never allowed to press.
PLATFORM_APPROVAL: ApprovalSpec = ApprovalSpec(
    approval_id="seed-gate-platform",
    run_id="seed-run-platform",
    action="rotate_gateway_credential",
    args={"provider": "llm-gateway", "scope": "platform"},
    actions=(
        {
            "id": "seed-gate-platform-1",
            "name": "rotate_gateway_credential",
            "args": {"provider": "llm-gateway", "scope": "platform"},
            "risk": "high",
        },
    ),
    risk=RiskLevel.HIGH,
    rationale=(
        "Rotating the shared gateway credential interrupts every tenant's runs "
        "until the new key propagates."
    ),
)


@dataclass(slots=True)
class SeedSummary:
    """What one seed run actually did, per table.

    Counted rather than assumed: the second run of an idempotent seed must report every
    row as *existing*, and a summary that cannot tell the two apart cannot prove it.

    Attributes:
        created: Rows this run inserted, keyed by table.
        existing: Rows this run found already present, keyed by table.
    """

    created: dict[str, int] = field(default_factory=dict)
    existing: dict[str, int] = field(default_factory=dict)

    def record(self, table: str, *, created: bool) -> None:
        """Count one row against ``table``."""
        bucket = self.created if created else self.existing
        bucket[table] = bucket.get(table, 0) + 1

    @property
    def total_created(self) -> int:
        """Return the number of rows this run inserted."""
        return sum(self.created.values())

    def lines(self) -> list[str]:
        """Return one human-readable line per table, in table order."""
        tables = sorted(set(self.created) | set(self.existing))
        return [
            f"  {table:<10} {self.created.get(table, 0):>3} created, "
            f"{self.existing.get(table, 0):>3} already present"
            for table in tables
        ]


def seed_password() -> str:
    """Return the password every seeded account is created with.

    Reads :data:`SEED_PASSWORD_ENV`, falling back to :data:`DEFAULT_SEED_PASSWORD`. An
    empty variable is treated as unset rather than as an empty password, which would
    create accounts nobody can log in to.
    """
    return os.environ.get(SEED_PASSWORD_ENV) or DEFAULT_SEED_PASSWORD


async def ensure_tenant(name: str, summary: SeedSummary, *, usd_cap: float) -> int:
    """Return the id of the tenant called ``name``, creating it if absent.

    Args:
        name: The tenant's unique name — the idempotency key.
        summary: Counter to record the outcome against.
        usd_cap: The tenant's spend cap. Required because
            :func:`aegis.governance.enforcement.create_tenant` writes the tenant and its
            ``budgets`` row in one transaction — a capless tenant is uncapped, and this
            seed is what a demo logs in against.

    Returns:
        The tenant's primary key.
    """
    async with get_sessionmaker()() as session:
        existing = (
            await session.execute(select(Tenant).where(Tenant.name == name))
        ).scalars().first()
        if existing is not None:
            summary.record("tenants", created=False)
            return existing.id
    try:
        row = await create_tenant(name, usd_cap=usd_cap)
    except DuplicateTenantError:
        # Lost a race with a concurrent seed: the row exists, which is the outcome
        # asked for. Re-read it rather than failing the run.
        async with get_sessionmaker()() as session:
            row_id = (
                await session.execute(select(Tenant.id).where(Tenant.name == name))
            ).scalar_one()
        summary.record("tenants", created=False)
        return row_id
    summary.record("tenants", created=True)
    return row.id


async def ensure_principal(
    spec: PrincipalSpec,
    summary: SeedSummary,
    *,
    tenant_id: int | None = None,
    password: str | None = None,
) -> int:
    """Return the id of ``spec``'s account, creating it if absent.

    An account that already exists is left exactly as it is — password, role and tenant.
    Re-hashing on every run would silently revert a rotated password, and re-roling would
    let the seed quietly re-grant an authority an operator had removed. A row that has
    since been moved to another tenant is reported at WARNING and still left alone: the
    operator moved it, and a seed that moved it back would be undoing a decision.

    The lookup binds **no** tenant scope, matching the login path: ``username`` is
    globally unique, so a scoped lookup would miss a row belonging to another tenant and
    then collide on the insert.

    Args:
        spec: The account to guarantee.
        summary: Counter to record the outcome against.
        tenant_id: The owning tenant, or ``None`` for a platform-staff account.
        password: Plaintext to hash on creation; defaults to :func:`seed_password`.

    Returns:
        The user's primary key.
    """
    existing = await _find_user(spec.username)
    if existing is not None:
        if existing[1] != tenant_id:
            logger.warning(
                "Seed account %r belongs to tenant %s, not %s; left unchanged.",
                spec.username,
                existing[1],
                tenant_id,
            )
        summary.record("users", created=False)
        return existing[0]
    try:
        row = await create_user(
            spec.username,
            role=spec.role,
            tenant_id=tenant_id,
            email=spec.email,
            password=password or seed_password(),
        )
    except DuplicateUserError:
        # Lost a race with a concurrent seed; the row exists, which is what was asked for.
        raced = await _find_user(spec.username)
        if raced is None:  # pragma: no cover - a unique violation with no row behind it
            raise
        summary.record("users", created=False)
        return raced[0]
    summary.record("users", created=True)
    return row.id


async def _find_user(username: str) -> tuple[int, int | None] | None:
    """Return ``(id, tenant_id)`` for ``username``, or ``None`` if there is no such row."""
    async with get_sessionmaker()() as session:
        row = (
            await session.execute(
                select(User.id, User.tenant_id).where(User.username == username)
            )
        ).first()
    return (row[0], row[1]) if row is not None else None


async def _ensure_budget(
    summary: SeedSummary,
    *,
    scope_type: str,
    scope_id: int,
    tenant_id: int,
    token_cap: int,
    usd_cap: float,
    rpm: int | None,
    tpm: int | None,
) -> None:
    """Guarantee one budget row for a ``(scope_type, scope_id, day)`` triple.

    :func:`aegis.governance.enforcement.upsert_budget` is already idempotent on that
    natural key, so this only has to decide whether the row was there first — which is
    what makes the second run's summary honest.
    """
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        existing = (
            await session.execute(
                select(Budget.id).where(
                    Budget.scope_type == BudgetScope(scope_type),
                    Budget.scope_id == scope_id,
                    Budget.window == BudgetWindow.DAY,
                )
            )
        ).scalars().first()
    # The upsert runs either way. ``create_tenant`` now writes the tenant's ``budgets``
    # row with its USD cap, so on a fresh run that row already exists — returning early
    # here would leave it without the token/rpm/tpm caps this spec also carries.
    summary.record("budgets", created=existing is None)
    await upsert_budget(
        scope_type=scope_type,
        scope_id=scope_id,
        window="day",
        token_cap=token_cap,
        usd_cap=usd_cap,
        rpm=rpm,
        tpm=tpm,
        tenant_id=tenant_id,
    )


async def _ensure_document(spec: DocumentSpec, summary: SeedSummary, *, tenant_id: int) -> None:
    """Guarantee one document row for ``tenant_id``, under that tenant's RLS scope.

    The scope is bound before both the lookup and the insert, so this write is subject to
    the ``tenant_isolation`` policy exactly as a request's would be: seeding two tenants
    over one connection with the scope left unbound would prove nothing about the
    isolation the seed exists to make testable.
    """
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        digest = spec.content_sha256
        existing = (
            await session.execute(
                select(Document.id).where(
                    Document.tenant_id == tenant_id,
                    Document.content_sha256 == digest,
                )
            )
        ).scalars().first()
        if existing is not None:
            summary.record("documents", created=False)
            return
        session.add(
            Document(
                tenant_id=tenant_id,
                filename=spec.filename,
                content_sha256=digest,
                mime_type=spec.mime_type,
                size_bytes=spec.size_bytes,
                status=JobStatus.PENDING,
            )
        )
        await session.commit()
    summary.record("documents", created=True)


#: The SLA window seeded gates are given: 30 days. The sweeper is real and it runs —
#: at the configured hour-long default a gate seeded before breakfast is auto-rejected
#: before the demo, and a HIGH-risk one is auto-*rejected* rather than merely expired
#: (decision D5). Widening the window for these rows is honest (they genuinely are not
#: urgent) and it leaves the sweeper's policy untouched for every real gate.
_SEED_APPROVAL_SLA_SECONDS = 30 * 24 * 60 * 60


async def _ensure_approval(
    spec: ApprovalSpec,
    summary: SeedSummary,
    *,
    tenant_id: int | None,
    requested_by: int | None,
) -> None:
    """Guarantee one parked ``PENDING`` gate exists, owned by ``tenant_id``.

    Idempotent through :func:`app.data.enqueue_approval`, which is keyed on the
    approval id: a second run finds the row and returns it untouched, so a gate an
    operator has already decided is never resurrected as pending.

    Args:
        spec: The gate to guarantee.
        summary: Counter to record the outcome against.
        tenant_id: The owning tenant, or ``None`` for Aegis's own gate.
        requested_by: The ``users.id`` whose run raised it — what lets that user, and
            only that user, see the gate's fate on the client portal.
    """
    existed = await get_approval(spec.approval_id) is not None
    await enqueue_approval(
        approval_id=spec.approval_id,
        run_id=spec.run_id,
        action=spec.action,
        args=dict(spec.args),
        actions=[dict(a) for a in spec.actions],
        risk=spec.risk,
        rationale=spec.rationale,
        tenant_id=tenant_id,
        requested_by=requested_by,
        persona="operations_lead",
        sla_seconds=_SEED_APPROVAL_SLA_SECONDS,
    )
    summary.record("approvals", created=not existed)


async def seed_platform_principals(
    summary: SeedSummary | None = None, *, password: str | None = None
) -> SeedSummary:
    """Guarantee the five un-tenanted platform accounts exist.

    Split out from :func:`seed` because it is the half a login needs: the console's
    quick-in buttons and the API's own operators authenticate against these rows.

    Args:
        summary: Counter to accumulate into; a fresh one is made when omitted.
        password: Plaintext for accounts this call creates.

    Returns:
        The summary, so a caller can print or assert on it.
    """
    summary = summary if summary is not None else SeedSummary()
    for spec in PLATFORM_PRINCIPALS:
        await ensure_principal(spec, summary, tenant_id=None, password=password)
    return summary


async def seed(password: str | None = None) -> SeedSummary:
    """Write (or confirm) the whole starting state and return what it did.

    Args:
        password: Plaintext for accounts this run creates; defaults to
            :func:`seed_password`.

    Returns:
        The per-table created/existing counts. A second run returns a summary whose
        :attr:`SeedSummary.total_created` is ``0``.
    """
    summary = SeedSummary()
    await seed_platform_principals(summary, password=password)
    platform_client = await _find_user("client")
    await _ensure_approval(
        PLATFORM_APPROVAL,
        summary,
        tenant_id=None,
        requested_by=platform_client[0] if platform_client is not None else None,
    )
    for spec in TENANTS:
        tenant_id = await ensure_tenant(spec.name, summary, usd_cap=spec.usd_cap)
        await ensure_principal(
            spec.admin, summary, tenant_id=tenant_id, password=password
        )
        client_user_id: int | None = None
        for member in spec.users:
            user_id = await ensure_principal(
                member, summary, tenant_id=tenant_id, password=password
            )
            if member.role is Role.CLIENT:
                client_user_id = user_id
        await _ensure_budget(
            summary,
            scope_type="tenant",
            scope_id=tenant_id,
            tenant_id=tenant_id,
            token_cap=spec.token_cap,
            usd_cap=spec.usd_cap,
            rpm=spec.rpm,
            tpm=spec.tpm,
        )
        if client_user_id is not None:
            await _ensure_budget(
                summary,
                scope_type="user",
                scope_id=client_user_id,
                tenant_id=tenant_id,
                token_cap=spec.client_token_cap,
                usd_cap=spec.client_usd_cap,
                rpm=None,
                tpm=None,
            )
        for document in spec.documents:
            await _ensure_document(document, summary, tenant_id=tenant_id)
        await _ensure_approval(
            spec.approval,
            summary,
            tenant_id=tenant_id,
            requested_by=client_user_id,
        )
    return summary


async def _run() -> int:
    """Bootstrap the schema, seed, and print what happened.

    The bootstrap is the shipped :func:`app.data.session.bootstrap` — ``create_all`` plus
    the additive reconcile, the serving-role grants and the RLS policies — so seeding a
    fresh database is one command rather than "start the API first, then seed". It is
    additive and idempotent; nothing here drops or rewrites a thing.

    Returns:
        The process exit code: ``0`` on success, ``1`` when the database refused the run.
    """
    from app.data.session import bootstrap  # noqa: PLC0415 - CLI-only dependency

    try:
        await bootstrap()
        summary = await seed()
    except SQLAlchemyError as exc:
        print(f"SEED FAILED  {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"SEED FAILED  database unreachable: {exc}", file=sys.stderr)
        return 1
    verb = "seeded" if summary.total_created else "already seeded — nothing to do"
    print(f"Aegis {verb}")
    for line in summary.lines():
        print(line)
    if summary.created.get("users"):
        source = (
            f"the {SEED_PASSWORD_ENV} environment variable"
            if os.environ.get(SEED_PASSWORD_ENV)
            else f"the default {DEFAULT_SEED_PASSWORD!r} (override with {SEED_PASSWORD_ENV})"
        )
        print(f"  accounts created by this run use the password from {source}")
    return 0


def main() -> int:
    """Entry point: run the seed and return its exit code."""
    logging.basicConfig(level=logging.WARNING)
    return asyncio.run(_run())


if __name__ == "__main__":  # pragma: no cover - process entry point
    sys.exit(main())
