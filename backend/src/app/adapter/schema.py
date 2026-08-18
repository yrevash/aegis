"""Domain schema — the *shape of the world* for the example adapter.

This is **piece 1 of 10** of the swappable adapter (see ``adapter/README.md``).
The illustrative domain is a neutral **service-request / case-management** world:
customers raise :class:`ServiceRequest` records, support agents work them, and a
small knowledge :class:`Document` corpus backs retrieval. Nothing here is tied to
the real hackathon problem — it exists so the vertical slice runs end-to-end
*before* the true domain is revealed.

To swap the domain, replace the entities below (and the four sibling modules that
reference them). The core never imports these classes directly; it reaches them
only through the registry re-exports in ``adapter/__init__.py``.

Design rules honoured here:

* Pydantic v2 models everywhere, fully typed and validated on construction.
* Enums (not free strings) for every categorical, so the generator, the ML spec,
  and the tools share one vocabulary.
* Records are immutable-friendly: mutation goes through ``model_copy(update=...)``
  in :mod:`app.adapter.tools`, keeping actions idempotent and reversible.

Targeted API: pydantic ``2.x`` (verified against pydantic 2.13).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

SCHEMA_VERSION = "1.0.0"
"""Bumped whenever the record shapes change; embedded in generated datasets."""


# ─────────────────────────────────────────────────────────────────────────────
# Categorical vocabularies (shared by generator, tools, ml_spec, prompts)
# ─────────────────────────────────────────────────────────────────────────────


class Priority(StrEnum):
    """How urgently a request should be worked (drives routing and SLA)."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class Category(StrEnum):
    """The subject area of a request; picks the responsible team and KB slice."""

    BILLING = "billing"
    TECHNICAL = "technical"
    ACCOUNT = "account"
    SHIPPING = "shipping"
    GENERAL = "general"


class Channel(StrEnum):
    """The intake channel a request arrived through."""

    EMAIL = "email"
    CHAT = "chat"
    PHONE = "phone"
    PORTAL = "portal"


class Region(StrEnum):
    """Coarse geographic region (affects staffing windows and latency)."""

    NA = "na"
    EU = "eu"
    APAC = "apac"
    LATAM = "latam"


class CustomerTier(StrEnum):
    """Commercial tier of the customer; higher tiers get faster handling."""

    STANDARD = "standard"
    PREMIUM = "premium"
    ENTERPRISE = "enterprise"


class RequestStatus(StrEnum):
    """Lifecycle state of a service request."""

    NEW = "new"
    TRIAGED = "triaged"
    IN_PROGRESS = "in_progress"
    WAITING_CUSTOMER = "waiting_customer"
    RESOLVED = "resolved"
    CLOSED = "closed"
    REOPENED = "reopened"


class DocumentKind(StrEnum):
    """Type of knowledge document ingested into retrieval."""

    KB_ARTICLE = "kb_article"
    POLICY = "policy"
    FAQ = "faq"
    RUNBOOK = "runbook"


# Ordered statuses that count as "the request is finished and was measured", i.e.
# the rows the ML spine can learn a resolution time from.
RESOLVED_STATUSES: frozenset[RequestStatus] = frozenset(
    {RequestStatus.RESOLVED, RequestStatus.CLOSED}
)


# ─────────────────────────────────────────────────────────────────────────────
# Entities
# ─────────────────────────────────────────────────────────────────────────────


class Customer(BaseModel):
    """A customer who can raise service requests."""

    id: str = Field(description="Stable customer id, e.g. 'cust-0001'.")
    name: str = Field(description="Display name of the customer/organisation.")
    email: str = Field(description="Primary contact email.")
    region: Region
    tier: CustomerTier
    created_at: datetime = Field(description="When the customer account was opened.")


class SupportAgent(BaseModel):
    """A support agent who works requests (the 'assignee' of an action)."""

    id: str = Field(description="Stable agent id, e.g. 'agent-007'.")
    name: str = Field(description="Display name of the agent.")
    team: str = Field(description="Team the agent belongs to, e.g. 'Tier-2 Billing'.")
    tenure_months: int = Field(ge=0, description="Months of experience on the desk.")
    region: Region
    specialties: list[Category] = Field(
        default_factory=list, description="Categories this agent handles best."
    )


class CaseNote(BaseModel):
    """A single note appended to a request's timeline.

    Notes are keyed by a deterministic :attr:`id` so appending the *same* note
    twice is a no-op — this is what makes the ``add_case_note`` tool idempotent.
    """

    id: str = Field(description="Deterministic note id (dedupes identical notes).")
    author: str = Field(description="Persona or agent id that wrote the note.")
    body: str = Field(description="Free-text note content.")
    created_at: datetime = Field(description="When the note was recorded.")


class ServiceRequest(BaseModel):
    """The central record of the domain — one customer support case.

    The numeric/categorical fields (``priority``, ``channel``, ``queue_depth_at_open``,
    …) are the ML feature source; :attr:`resolution_hours` is the ML **target** and
    is populated only once the request reaches a resolved status.
    """

    id: str = Field(description="Stable request id, e.g. 'req-000123'.")
    title: str = Field(description="Short human summary of the issue.")
    description: str = Field(description="Full free-text description of the issue.")

    category: Category
    priority: Priority
    channel: Channel
    region: Region
    status: RequestStatus

    customer_id: str = Field(description="FK → :class:`Customer`.")
    assigned_agent_id: str | None = Field(
        default=None, description="FK → :class:`SupportAgent`, or None if unassigned."
    )

    created_at: datetime = Field(description="When the request was opened.")
    updated_at: datetime = Field(description="Last time the record changed.")
    resolved_at: datetime | None = Field(
        default=None, description="When it was resolved/closed, else None."
    )

    # ── Feature-bearing operational fields ───────────────────────────────────
    queue_depth_at_open: int = Field(
        ge=0, description="Backlog size when the request was opened (load signal)."
    )
    reopened_count: int = Field(
        default=0, ge=0, description="How many times the request was reopened."
    )
    first_response_minutes: int | None = Field(
        default=None, ge=0, description="Time to first human response, if measured."
    )
    sla_hours: float = Field(
        gt=0, description="Contractual resolution target for this request."
    )
    satisfaction_score: int | None = Field(
        default=None, ge=1, le=5, description="Post-resolution CSAT (1–5), if given."
    )

    # ── Target ───────────────────────────────────────────────────────────────
    resolution_hours: float | None = Field(
        default=None,
        ge=0,
        description="ML TARGET — wall-clock hours to resolve (None while open).",
    )

    tags: list[str] = Field(default_factory=list, description="Free-form labels.")
    notes: list[CaseNote] = Field(
        default_factory=list, description="Timeline of case notes."
    )

    @property
    def is_resolved(self) -> bool:
        """Whether this request has a measured resolution (i.e. is ML-labelled)."""
        return self.status in RESOLVED_STATUSES and self.resolution_hours is not None


class Document(BaseModel):
    """A knowledge document for the retrieval corpus (graph/vector ingest)."""

    id: str = Field(description="Stable document id, e.g. 'doc-kb-0007'.")
    kind: DocumentKind
    title: str = Field(description="Document title.")
    body: str = Field(description="Full document text to index.")
    category: Category | None = Field(
        default=None, description="Primary category the doc pertains to, if any."
    )
    tags: list[str] = Field(default_factory=list, description="Retrieval tags.")
    source: str = Field(
        default="synthetic", description="Provenance, e.g. 'seed' or 'synthetic'."
    )


class DatasetMetadata(BaseModel):
    """Provenance for a generated dataset (what was made and how)."""

    schema_version: str = Field(default=SCHEMA_VERSION)
    seed: int | None = Field(default=None, description="RNG seed, if deterministic.")
    llm_used: bool = Field(description="Whether an LLM fabricated the text content.")
    num_customers: int
    num_agents: int
    num_requests: int
    num_documents: int
    num_labelled: int = Field(description="Requests with a resolution_hours target.")


class SyntheticDataset(BaseModel):
    """A complete, typed synthetic world ready to seed data + retrieval.

    The data layer persists :attr:`customers`, :attr:`agents` and
    :attr:`requests`; :mod:`app.retrieval` ingests :attr:`documents`. The whole
    object validates on construction, so a malformed generator can never leak
    downstream.
    """

    metadata: DatasetMetadata
    customers: list[Customer]
    agents: list[SupportAgent]
    requests: list[ServiceRequest]
    documents: list[Document]

    def customer_by_id(self, customer_id: str) -> Customer | None:
        """Return the customer with ``customer_id`` (or None if absent)."""
        return next((c for c in self.customers if c.id == customer_id), None)

    def agent_by_id(self, agent_id: str) -> SupportAgent | None:
        """Return the agent with ``agent_id`` (or None if absent)."""
        return next((a for a in self.agents if a.id == agent_id), None)

    def labelled_requests(self) -> list[ServiceRequest]:
        """Return only requests that carry an ML target (for training)."""
        return [r for r in self.requests if r.is_resolved]
