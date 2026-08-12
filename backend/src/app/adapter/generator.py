"""Synthetic-data generator — the single highest-value pre-build.

This is **piece 2 of 5** of the adapter and the reason the vertical slice can run
before the real hackathon domain is known: it fabricates a realistic, fully-typed
:class:`~app.adapter.schema.SyntheticDataset` (customers, agents, requests and a
knowledge-document corpus) on demand.

Design — a **hybrid** generator, which is the state-of-the-art pattern for
label-consistent synthetic data:

1. **Procedural, seeded structure.** All *feature-bearing* fields (priority,
   channel, region, queue depth, agent tenure, timestamps) are drawn from a
   seeded :class:`random.Random`. The **target** (``resolution_hours``) is then
   computed from :func:`app.adapter.ml_spec.latent_resolution_hours` plus seeded
   Gaussian noise — so the label is a genuine function of the features and the ML
   spine can actually learn it. A fixed ``seed`` makes the whole structural draw
   reproducible.

2. **LLM-fabricated content.** The realistic *text* — request titles/descriptions
   and the document corpus — is written by the model gateway through
   :func:`app.core.llm.complete`, requested by **role** (``ModelRole.CHEAP`` for
   bulk request text, ``ModelRole.GENERATION`` for richer KB documents), never by
   a hard-coded model id. Content is requested as JSON and parsed defensively.

3. **Graceful degradation.** If no LLM is available (or a response is malformed),
   the generator falls back to deterministic templated text, so it *always*
   returns schema-valid data — tests and offline runs never break.

The gateway call is **dependency-injected** (the ``complete`` parameter). Passing
a stub makes the generator trivially unit-testable with no network; leaving it
``None`` lazily imports the real ``app.core.llm.complete`` at call time (so this
module imports cleanly even before that core module exists).

Targeted API: ``app.core.llm.complete(role, messages, *, tools, temperature,
response_format) -> LLMResult`` (LLMResult has a ``.content: str``), matching the
contract in ``docs/module/MODULE_REFERENCE.md``.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from typing import Protocol

from pydantic import BaseModel, Field

from app.adapter import ml_spec
from app.adapter.schema import (
    Category,
    Channel,
    Customer,
    CustomerTier,
    DatasetMetadata,
    Document,
    DocumentKind,
    Priority,
    Region,
    RequestStatus,
    ServiceRequest,
    SupportAgent,
    SyntheticDataset,
)
from app.core.models import ModelRole

# Base instant for deterministic timestamps (kept fixed so a seed fully pins the
# world). Chosen well in the past so every generated request predates "now".
_EPOCH = datetime(2024, 1, 1, 9, 0, 0)


# ─────────────────────────────────────────────────────────────────────────────
# Injected LLM contract (structural — avoids a hard import of app.core.llm)
# ─────────────────────────────────────────────────────────────────────────────


class _LLMResultLike(Protocol):
    """Structural view of ``app.core.llm.LLMResult`` (only ``.content`` is used)."""

    content: str


class CompleteFn(Protocol):
    """The subset of ``app.core.llm.complete`` this generator depends on."""

    async def __call__(
        self,
        role: ModelRole,
        messages: list[dict],
        *,
        tools: list[dict] | None = None,
        temperature: float = 0.0,
        response_format: dict | None = None,
    ) -> _LLMResultLike:
        """Complete a chat request for the given model role."""
        ...


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────


class GeneratorConfig(BaseModel):
    """Config-driven knobs for a generation run (all counts overridable)."""

    num_customers: int = Field(default=12, ge=1)
    num_agents: int = Field(default=6, ge=1)
    num_requests: int = Field(default=40, ge=1)
    num_documents: int = Field(default=6, ge=0)
    resolved_fraction: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description="Share of requests that are resolved (i.e. ML-labelled).",
    )
    seed: int | None = Field(
        default=None, description="RNG seed; set for a fully reproducible structure."
    )
    noise_scale: float = Field(
        default=4.0, ge=0.0, description="Std-dev of Gaussian noise on the target (hours)."
    )
    use_llm: bool = Field(
        default=True, description="If False, skip the LLM and use templated text."
    )
    llm_temperature: float = Field(
        default=0.7, ge=0.0, le=2.0, description="Sampling temperature for text."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────


async def generate_synthetic(
    config: GeneratorConfig | None = None,
    *,
    complete: CompleteFn | None = None,
) -> SyntheticDataset:
    """Fabricate a complete, schema-valid synthetic world.

    Args:
        config: Generation knobs; defaults to :class:`GeneratorConfig` defaults.
        complete: The LLM completion function (dependency injection). If ``None``
            and ``config.use_llm`` is set, ``app.core.llm.complete`` is imported
            lazily. Pass a stub in tests to avoid any network.

    Returns:
        A :class:`SyntheticDataset` whose customers/agents/requests seed the data
        layer and whose documents seed retrieval. Every record has already been
        validated by pydantic on construction.
    """
    cfg = config or GeneratorConfig()
    rng = random.Random(cfg.seed)

    resolved_complete = _resolve_complete(complete, cfg)

    customers = _build_customers(rng, cfg.num_customers)
    agents = _build_agents(rng, cfg.num_agents)

    # LLM (or fallback) text, fetched in batches keyed by category.
    request_text = await _fabricate_request_text(resolved_complete, rng, cfg)
    documents = await _fabricate_documents(resolved_complete, rng, cfg)

    return _assemble(
        cfg, rng, customers, agents, request_text, documents,
        llm_used=resolved_complete is not None,
    )


def generate_synthetic_sync(config: GeneratorConfig | None = None) -> SyntheticDataset:
    """Fabricate the synthetic world **synchronously** with deterministic templated text.

    Identical structure and labels to :func:`generate_synthetic` but with no LLM and
    no ``await``, so it is safe to call from synchronous code *and* from inside a
    running event loop (where ``asyncio.run`` would raise). This is what seeds the
    process-wide record store and the ML spine's training frame — neither needs
    LLM-written prose, only schema-valid records whose ``resolution_hours`` label is
    the real latent function of the features.

    Args:
        config: Generation knobs; ``use_llm`` is forced off. Defaults apply otherwise.

    Returns:
        A schema-valid :class:`SyntheticDataset` (deterministic under a fixed seed).
    """
    cfg = (config or GeneratorConfig()).model_copy(update={"use_llm": False})
    rng = random.Random(cfg.seed)

    customers = _build_customers(rng, cfg.num_customers)
    agents = _build_agents(rng, cfg.num_agents)
    request_text = _template_request_pool(rng, cfg)
    documents = _template_documents(rng, cfg)

    return _assemble(cfg, rng, customers, agents, request_text, documents, llm_used=False)


def _assemble(
    cfg: GeneratorConfig,
    rng: random.Random,
    customers: list[Customer],
    agents: list[SupportAgent],
    request_text: dict[Category, list[dict]],
    documents: list[Document],
    *,
    llm_used: bool,
) -> SyntheticDataset:
    """Assemble requests + metadata into a dataset (shared sync/async core)."""
    requests = _build_requests(rng, cfg, customers, agents, request_text)
    num_labelled = sum(1 for r in requests if r.is_resolved)
    metadata = DatasetMetadata(
        seed=cfg.seed,
        llm_used=llm_used,
        num_customers=len(customers),
        num_agents=len(agents),
        num_requests=len(requests),
        num_documents=len(documents),
        num_labelled=num_labelled,
    )
    return SyntheticDataset(
        metadata=metadata,
        customers=customers,
        agents=agents,
        requests=requests,
        documents=documents,
    )


def _template_request_pool(
    rng: random.Random, cfg: GeneratorConfig
) -> dict[Category, list[dict]]:
    """Deterministic templated (title, description) pool — the no-LLM fallback path."""
    per_category = max(3, cfg.num_requests // (len(Category) or 1))
    return {
        category: [
            {"title": t, "description": d}
            for t, d in (
                _template_request_text(category, rng) for _ in range(per_category)
            )
        ]
        for category in Category
    }


def _template_documents(rng: random.Random, cfg: GeneratorConfig) -> list[Document]:
    """Deterministic templated knowledge-document corpus — the no-LLM fallback path."""
    if cfg.num_documents <= 0:
        return []
    categories = list(Category)
    docs: list[Document] = []
    for i in range(cfg.num_documents):
        category = categories[i % len(categories)]
        title, body = _template_document(category, i)
        docs.append(
            Document(
                id=f"doc-{i:04d}",
                kind=rng.choice(list(DocumentKind)),
                title=title,
                body=body,
                category=category,
                tags=[category.value, "synthetic"],
                source="synthetic",
            )
        )
    return docs


class DatasetQualityReport(BaseModel):
    """A quick, dependency-free quality gate over a generated dataset.

    These are the checks worth running on the day *before* trusting synthetic data:
    referential integrity (no dangling foreign keys), class coverage/balance, a
    learnable label present, temporal consistency, and PII-free-by-construction. See
    ``docs/architecture/synthetic-data.md`` for what each check buys you.
    """

    referential_integrity: bool = Field(description="Every FK resolves to a record.")
    category_coverage: bool = Field(description="Every category appears at least once.")
    has_labels: bool = Field(description="At least one request carries an ML target.")
    temporal_consistency: bool = Field(description="resolved_at ≥ created_at everywhere.")
    pii_free: bool = Field(
        description="Reserved .example emails AND the generated free text scanned "
        "clean by the guardrail PII detector."
    )
    num_labelled: int = Field(description="Count of ML-labelled (resolved) requests.")
    category_counts: dict[str, int] = Field(description="Requests per category (balance).")

    @property
    def ok(self) -> bool:
        """Whether every hard quality check passed."""
        return (
            self.referential_integrity
            and self.category_coverage
            and self.has_labels
            and self.temporal_consistency
            and self.pii_free
        )


def _is_pii_free(dataset: SyntheticDataset) -> bool:
    """Whether the dataset carries no real-looking PII — actually scanned, not assumed.

    Checks two things: (1) every customer email is a reserved ``.example`` address,
    and (2) the **generated free text** (request titles/descriptions and document
    titles/bodies) contains no detectable PII per the guardrail PII detector. The
    second check matters when text is LLM-fabricated (``use_llm=True``): a model can
    slip a real-looking email/phone into a description, which "emails end in
    .example" would never catch.
    """
    from app.guardrails.pii import contains_pii

    emails_ok = all(c.email.endswith(".example") for c in dataset.customers)
    if not emails_ok:
        return False
    texts: list[str] = []
    for r in dataset.requests:
        texts.append(r.title)
        texts.append(r.description)
    for d in dataset.documents:
        texts.append(d.title)
        texts.append(d.body)
    return not any(contains_pii(t) for t in texts)


def assess_quality(dataset: SyntheticDataset) -> DatasetQualityReport:
    """Run the synthetic-data quality checks over ``dataset`` and report the verdict.

    Pure, offline, and cheap — safe to call after every generation run (and asserted
    in tests) so a malformed world is caught before it seeds the stores.

    Args:
        dataset: The generated dataset to inspect.

    Returns:
        A :class:`DatasetQualityReport` with per-check booleans and balance counts.
    """
    customer_ids = {c.id for c in dataset.customers}
    agent_ids = {a.id for a in dataset.agents}
    referential = all(
        r.customer_id in customer_ids
        and (r.assigned_agent_id is None or r.assigned_agent_id in agent_ids)
        for r in dataset.requests
    )
    counts: dict[str, int] = dict.fromkeys((c.value for c in Category), 0)
    for r in dataset.requests:
        counts[r.category.value] = counts.get(r.category.value, 0) + 1
    coverage = all(v > 0 for v in counts.values())
    temporal = all(
        r.resolved_at is None or r.resolved_at >= r.created_at for r in dataset.requests
    )
    pii_free = _is_pii_free(dataset)
    return DatasetQualityReport(
        referential_integrity=referential,
        category_coverage=coverage,
        has_labels=dataset.metadata.num_labelled > 0,
        temporal_consistency=temporal,
        pii_free=pii_free,
        num_labelled=dataset.metadata.num_labelled,
        category_counts=counts,
    )


def _resolve_complete(
    complete: CompleteFn | None, cfg: GeneratorConfig
) -> CompleteFn | None:
    """Pick the completion function to use (injected, lazily imported, or none)."""
    if not cfg.use_llm:
        return None
    if complete is not None:
        return complete
    try:  # Lazy import: app.core.llm may not exist yet during parallel builds.
        from app.core.llm import complete as core_complete  # noqa: PLC0415
    except ImportError:
        return None
    return core_complete


# ─────────────────────────────────────────────────────────────────────────────
# Structural (procedural, seeded) generation
# ─────────────────────────────────────────────────────────────────────────────

_FIRST_NAMES = ("Ava", "Liam", "Noah", "Mia", "Ravi", "Sara", "Chen", "Ines", "Omar", "Kai")
_LAST_NAMES = ("Kim", "Patel", "Silva", "Nguyen", "Haddad", "Rossi", "Meyer", "Costa")
_ORGS = ("Northwind", "Contoso", "Globex", "Initech", "Umbrella", "Soylent", "Acme", "Hooli")


def _pick(rng: random.Random, seq: tuple) -> str:
    """Return a uniformly random element of ``seq``."""
    return rng.choice(seq)


def _build_customers(rng: random.Random, n: int) -> list[Customer]:
    """Create ``n`` deterministic customers."""
    tiers = list(CustomerTier)
    regions = list(Region)
    customers: list[Customer] = []
    for i in range(n):
        org = f"{_pick(rng, _ORGS)} {_pick(rng, ('Ltd', 'Inc', 'GmbH', 'Group'))}"
        customers.append(
            Customer(
                id=f"cust-{i:04d}",
                name=org,
                email=f"contact{i:04d}@{org.split()[0].lower()}.example",
                region=rng.choice(regions),
                tier=rng.choices(tiers, weights=[6, 3, 1])[0],
                created_at=_EPOCH - timedelta(days=rng.randint(30, 900)),
            )
        )
    return customers


def _build_agents(rng: random.Random, n: int) -> list[SupportAgent]:
    """Create ``n`` deterministic support agents."""
    regions = list(Region)
    categories = list(Category)
    agents: list[SupportAgent] = []
    for i in range(n):
        name = f"{_pick(rng, _FIRST_NAMES)} {_pick(rng, _LAST_NAMES)}"
        specialties = rng.sample(categories, k=rng.randint(1, 2))
        agents.append(
            SupportAgent(
                id=f"agent-{i:03d}",
                name=name,
                team=f"Tier-{rng.randint(1, 3)} {specialties[0].value.title()}",
                tenure_months=rng.randint(1, 60),
                region=rng.choice(regions),
                specialties=specialties,
            )
        )
    return agents


def _build_requests(
    rng: random.Random,
    cfg: GeneratorConfig,
    customers: list[Customer],
    agents: list[SupportAgent],
    request_text: dict[Category, list[dict]],
) -> list[ServiceRequest]:
    """Assemble service requests, computing the target from the latent signal."""
    priorities = list(Priority)
    channels = list(Channel)
    categories = list(Category)
    text_cursor: dict[Category, int] = dict.fromkeys(categories, 0)

    requests: list[ServiceRequest] = []
    for i in range(cfg.num_requests):
        # Coverage guarantee (class balance): the first pass round-robins every
        # category so no class is ever missing, even for a small N; the remainder is
        # drawn at random for realistic imbalance. Deterministic under a fixed seed.
        category = categories[i] if i < len(categories) else rng.choice(categories)
        customer = rng.choice(customers)
        # Prefer an agent who specialises in the category (realistic routing).
        specialists = [a for a in agents if category in a.specialties]
        agent = rng.choice(specialists or agents)

        priority = rng.choices(priorities, weights=[4, 5, 3, 1])[0]
        channel = rng.choice(channels)
        queue_depth = rng.randint(0, 40)
        reopened = rng.choices([0, 1, 2], weights=[8, 2, 1])[0]

        title, description = _next_text(request_text, category, text_cursor, rng)

        created_at = _EPOCH - timedelta(hours=rng.randint(1, 24 * 120))
        is_resolved = rng.random() < cfg.resolved_fraction

        partial = ServiceRequest(
            id=f"req-{i:06d}",
            title=title,
            description=description,
            category=category,
            priority=priority,
            channel=channel,
            region=customer.region,
            status=RequestStatus.IN_PROGRESS,
            customer_id=customer.id,
            assigned_agent_id=agent.id,
            created_at=created_at,
            updated_at=created_at,
            queue_depth_at_open=queue_depth,
            reopened_count=reopened,
            first_response_minutes=rng.randint(2, 240),
            sla_hours=float(rng.choice([8, 24, 48, 72])),
        )

        if is_resolved:
            requests.append(_finalise_resolved(partial, customer, agent, cfg, rng))
        else:
            requests.append(
                partial.model_copy(
                    update={"status": rng.choice([RequestStatus.NEW, RequestStatus.TRIAGED])}
                )
            )
    return requests


def _finalise_resolved(
    request: ServiceRequest,
    customer: Customer,
    agent: SupportAgent,
    cfg: GeneratorConfig,
    rng: random.Random,
) -> ServiceRequest:
    """Compute the target + resolution timestamps for a resolved request."""
    features = ml_spec.features_for_request(request, agent=agent, customer=customer)
    mean_hours = ml_spec.latent_resolution_hours(features)
    noisy = max(0.25, mean_hours + rng.gauss(0.0, cfg.noise_scale))
    resolution_hours = round(noisy, 2)
    resolved_at = request.created_at + timedelta(hours=resolution_hours)
    return request.model_copy(
        update={
            "status": rng.choices(
                [RequestStatus.RESOLVED, RequestStatus.CLOSED], weights=[3, 2]
            )[0],
            "resolved_at": resolved_at,
            "updated_at": resolved_at,
            "resolution_hours": resolution_hours,
            "satisfaction_score": rng.randint(3, 5),
        }
    )


def _next_text(
    request_text: dict[Category, list[dict]],
    category: Category,
    cursor: dict[Category, int],
    rng: random.Random,
) -> tuple[str, str]:
    """Pull the next (title, description) for ``category``, cycling if needed."""
    pool = request_text.get(category) or []
    if not pool:
        return _template_request_text(category, rng)
    idx = cursor[category] % len(pool)
    cursor[category] += 1
    item = pool[idx]
    title = str(item.get("title") or "").strip() or _template_request_text(category, rng)[0]
    body = str(item.get("description") or "").strip()
    if not body:
        body = _template_request_text(category, rng)[1]
    return title, body


# ─────────────────────────────────────────────────────────────────────────────
# LLM-fabricated content (with deterministic fallbacks)
# ─────────────────────────────────────────────────────────────────────────────

_CATEGORY_HINTS: dict[Category, str] = {
    Category.BILLING: "invoices, refunds, double charges, plan changes",
    Category.TECHNICAL: "errors, outages, integration failures, login problems",
    Category.ACCOUNT: "profile changes, permissions, password resets, seats",
    Category.SHIPPING: "delivery delays, damaged goods, tracking, returns",
    Category.GENERAL: "how-to questions, feedback, general enquiries",
}


async def _fabricate_request_text(
    complete: CompleteFn | None,
    rng: random.Random,
    cfg: GeneratorConfig,
) -> dict[Category, list[dict]]:
    """Fetch realistic (title, description) pairs per category via the LLM.

    Falls back to templated text for any category the LLM cannot supply.
    """
    per_category = max(3, cfg.num_requests // (len(Category) or 1))
    out: dict[Category, list[dict]] = {}
    for category in Category:
        items: list[dict] = []
        if complete is not None:
            items = await _llm_request_text(complete, category, per_category, cfg)
        if not items:
            items = [
                {"title": t, "description": d}
                for t, d in (_template_request_text(category, rng) for _ in range(per_category))
            ]
        out[category] = items
    return out


async def _llm_request_text(
    complete: CompleteFn,
    category: Category,
    count: int,
    cfg: GeneratorConfig,
) -> list[dict]:
    """Ask the CHEAP model for ``count`` request title/description pairs."""
    system = (
        "You fabricate realistic but entirely fictional customer-support tickets "
        "for a synthetic dataset. Never reference real people or companies."
    )
    user = (
        f"Produce {count} distinct support tickets in the '{category.value}' category "
        f"(themes: {_CATEGORY_HINTS[category]}). Return JSON of the form "
        '{"tickets": [{"title": "...", "description": "..."}, ...]}. '
        "Titles under 80 chars; descriptions 1–3 sentences."
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    return await _call_and_parse(complete, ModelRole.CHEAP, messages, "tickets", cfg)


async def _fabricate_documents(
    complete: CompleteFn | None,
    rng: random.Random,
    cfg: GeneratorConfig,
) -> list[Document]:
    """Fabricate a small knowledge-document corpus via the GENERATION model."""
    if cfg.num_documents <= 0:
        return []
    categories = list(Category)
    docs: list[Document] = []
    llm_docs: list[dict] = []
    if complete is not None:
        llm_docs = await _llm_documents(complete, cfg.num_documents, cfg)

    for i in range(cfg.num_documents):
        category = categories[i % len(categories)]
        source = llm_docs[i] if i < len(llm_docs) else {}
        title = str(source.get("title") or "").strip()
        body = str(source.get("body") or "").strip()
        if not title or not body:
            title, body = _template_document(category, i)
        docs.append(
            Document(
                id=f"doc-{i:04d}",
                kind=rng.choice(list(DocumentKind)),
                title=title,
                body=body,
                category=category,
                tags=[category.value, "synthetic"],
                source="synthetic",
            )
        )
    return docs


async def _llm_documents(complete: CompleteFn, count: int, cfg: GeneratorConfig) -> list[dict]:
    """Ask the GENERATION model for ``count`` short knowledge documents."""
    system = (
        "You write concise internal knowledge-base articles for a fictional "
        "customer-support organisation. Content must be self-contained and generic."
    )
    user = (
        f"Write {count} short KB documents spanning billing, technical, account, "
        "shipping and general topics. Return JSON of the form "
        '{"documents": [{"title": "...", "body": "..."}, ...]}. '
        "Each body 3–6 sentences of actionable guidance."
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    return await _call_and_parse(complete, ModelRole.GENERATION, messages, "documents", cfg)


async def _call_and_parse(
    complete: CompleteFn,
    role: ModelRole,
    messages: list[dict],
    key: str,
    cfg: GeneratorConfig,
) -> list[dict]:
    """Call the gateway for JSON, parse defensively, and return ``result[key]``.

    Any transport or parsing failure returns ``[]`` so the caller can fall back to
    templated content — the generator must never raise on LLM problems.

    Args:
        complete: The injected completion function.
        role: Which model role to bill the call to.
        messages: Chat messages to send.
        key: The top-level JSON key holding the list of items.
        cfg: Generation config (supplies the temperature).

    Returns:
        The parsed list of dict items, or ``[]`` on any failure.
    """
    try:
        result = await complete(
            role,
            messages,
            temperature=cfg.llm_temperature,
            response_format={"type": "json_object"},
        )
        payload = json.loads(result.content)
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError, KeyError):
        return []
    items = payload.get(key) if isinstance(payload, dict) else None
    if isinstance(items, list):
        return [it for it in items if isinstance(it, dict)]
    return []


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic templated fallbacks (guarantee schema-valid output offline)
# ─────────────────────────────────────────────────────────────────────────────

_TEMPLATE_TITLES: dict[Category, tuple[str, ...]] = {
    Category.BILLING: (
        "Unexpected charge on invoice",
        "Refund not received",
        "Plan upgrade query",
    ),
    Category.TECHNICAL: (
        "Login fails with 500 error",
        "API integration timing out",
        "Data export stuck",
    ),
    Category.ACCOUNT: (
        "Cannot reset password",
        "Add a team seat",
        "Update billing contact",
    ),
    Category.SHIPPING: (
        "Order delayed in transit",
        "Package arrived damaged",
        "Wrong item shipped",
    ),
    Category.GENERAL: (
        "How do I enable feature X",
        "Question about data retention",
        "Feedback on the portal",
    ),
}


def _template_request_text(category: Category, rng: random.Random) -> tuple[str, str]:
    """Return a deterministic (title, description) for ``category`` (LLM fallback)."""
    title = rng.choice(_TEMPLATE_TITLES[category])
    description = (
        f"A customer reports a {category.value} issue: {title.lower()}. "
        "They ask for help resolving it as soon as possible."
    )
    return title, description


def _template_document(category: Category, index: int) -> tuple[str, str]:
    """Return a deterministic (title, body) knowledge doc (LLM fallback)."""
    title = f"{category.value.title()} handling guide #{index}"
    body = (
        f"This guide covers common {category.value} scenarios ({_CATEGORY_HINTS[category]}). "
        "Confirm the customer's identity, reproduce the issue, apply the standard remedy, "
        "and record the outcome as a case note. Escalate to Tier-2 if unresolved within SLA."
    )
    return title, body
