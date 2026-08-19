"""SQLAlchemy ORM for the LLM-Ops loop — eval results + versioned prompts.

These register on the shared :class:`aegis.data.AegisBase` metadata (like
``aegis.governance.models`` / ``aegis.memory.stores``), so a host's
``AegisBase.metadata.create_all`` materialises them — on PostgreSQL with native ``jsonb``
columns, and on the SQLite test database via the cross-dialect :data:`aegis.data.JsonB`
decorator (JSON fallback).

The ``tenant_id`` is a **plain indexed column** (no cross-package foreign key to the
separate ``aegis.governance`` ``tenants`` table — mirroring how ``aegis.memory`` isolates
at the query/RLS layer); the belt-and-suspenders tenant scoping + Postgres RLS provide the
isolation, not a DDL foreign key.

Columns are preserved exactly from the pre-extraction platform schema:
``eval_results{id, ts, run_id, prompt_key, tenant_id, metric, score, passed, detail}`` and
``prompt_versions{id, tenant_id, prompt_key, version, system_prompt, config, status,
parent_version, created_by, notes, created_at, activated_at}`` with a unique
``(coalesce(tenant_id,0), prompt_key, version)`` index and a
``(tenant_id, prompt_key, status)`` lookup index — both keyed on the tenant, because a
version number and an "active" row belong to one tenant, not to the platform.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from aegis.data import AegisBase, JsonB

__all__ = [
    "EvalResult",
    "PromptStatus",
    "PromptVersion",
]


class EvalResult(AegisBase):
    """A single offline-eval measurement (RAGAS metric or LLM-as-judge verdict)."""

    __tablename__ = "eval_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    # The prompt_key (persona) the graded run used — the scoping key so Diagnose only
    # clusters a prompt's OWN failures and /ops/evals?prompt_key filters for real.
    prompt_key: Mapped[str | None] = mapped_column(String(128), default=None, index=True)
    # Plain indexed column (no cross-package FK to aegis.governance ``tenants``).
    tenant_id: Mapped[int | None] = mapped_column(default=None, index=True)
    metric: Mapped[str] = mapped_column(String(128), index=True)
    score: Mapped[float] = mapped_column()
    passed: Mapped[bool] = mapped_column(default=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)


class PromptStatus(StrEnum):
    """Lifecycle of a versioned system prompt in the LLM-Ops registry."""

    DRAFT = "draft"        # proposed by the optimizer/human; not live
    STAGED = "staged"      # passed the eval gate; awaiting promotion/approval
    ACTIVE = "active"      # the one live version for its prompt_key (at most one)
    ARCHIVED = "archived"  # a former active version, retained for rollback + audit


class PromptVersion(AegisBase):
    """A versioned system prompt + config — the seam the LLM-Ops loop writes back.

    The harness reads the ACTIVE version for a ``prompt_key`` (via an in-process cache,
    :mod:`aegis.ops.registry`) and falls back to the injected floor renderer when none
    exists, so the adapter prompt is always the floor. Every version is retained
    (``archived``), making promotion reversible and auditable — the platform never
    silently mutates its own instructions.
    """

    __tablename__ = "prompt_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Plain indexed column (no cross-package FK to aegis.governance ``tenants``).
    tenant_id: Mapped[int | None] = mapped_column(default=None, index=True)
    prompt_key: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[int] = mapped_column()
    system_prompt: Mapped[str] = mapped_column(Text())
    config: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    status: Mapped[PromptStatus] = mapped_column(
        SAEnum(PromptStatus, name="prompt_status"), default=PromptStatus.DRAFT, index=True
    )
    parent_version: Mapped[int | None] = mapped_column(default=None)
    created_by: Mapped[str | None] = mapped_column(String(128), default=None)
    notes: Mapped[str | None] = mapped_column(Text(), default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    activated_at: Mapped[datetime | None] = mapped_column(default=None)

    __table_args__ = (
        # Unique per **tenant** + key + version, not per key + version.
        #
        # The old ``ux_prompt_version(prompt_key, version)`` conflated two tenants under
        # one identifier, and under RLS that is not a cosmetic problem: a tenant-scoped
        # session reads ``max(version)`` over the rows it can see, allocates the next
        # number, and collides with an invisible row belonging to somebody else — on
        # every retry, forever. The first tenant to write a version would have owned the
        # number line for the whole platform.
        #
        # ``coalesce(tenant_id, 0)`` rather than a plain ``tenant_id`` column because
        # PostgreSQL treats NULLs as distinct in a unique index (``NULLS NOT DISTINCT``
        # is 15+, and this deploys on 14), which would have left the **platform** rows —
        # the ones every tenant without a version of its own falls back to — with no
        # uniqueness at all. Folding NULL to 0 keeps one live number line per scope, and
        # 0 is safe because ``tenants.id`` is an identity column starting at 1.
        Index(
            "ux_prompt_version_tenant",
            text("coalesce(tenant_id, 0)"),
            "prompt_key",
            "version",
            unique=True,
        ),
        Index("ix_prompt_tenant_key_status", "tenant_id", "prompt_key", "status"),
    )
