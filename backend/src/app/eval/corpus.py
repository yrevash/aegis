"""Fixed seed corpus + labelled eval cases for the offline quality gate.

The corpus is a small, self-contained customer-support knowledge base (mirroring the
adapter's seed corpus theme) with deliberate **distractor** documents so retrieval is
discriminative — a query about refunds must out-rank the login runbook, not merely
return every chunk. Each :class:`EvalCase` carries the gold document id(s) a correct
retrieval should surface and the claim keywords a grounded answer must be able to cite
from the retrieved context.

Everything here is a constant: the gate is deterministic because its inputs are frozen.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.retrieval.models import Chunk


@dataclass(frozen=True)
class SeedDoc:
    """One seed document: a stable id and its body text."""

    id: str
    text: str


@dataclass(frozen=True)
class EvalCase:
    """A single labelled retrieval/answer eval case.

    Attributes:
        query: The user question.
        gold_doc_ids: The document id(s) a correct retrieval must surface.
        claims: Keywords/phrases a grounded answer should be able to cite from the
            retrieved context (the groundedness proxy checks they appear in it).
    """

    query: str
    gold_doc_ids: frozenset[str]
    claims: tuple[str, ...] = field(default_factory=tuple)


# ── Seed corpus (5 topical docs; the last two are distractors) ────────────────
SEED_CORPUS: tuple[SeedDoc, ...] = (
    SeedDoc(
        id="kb-refunds",
        text=(
            "How refunds are processed. Refunds are issued to the original payment "
            "method within 5 to 7 business days. Before issuing a refund, verify the "
            "customer's identity and confirm the charge on the invoice. For duplicate "
            "charges, refund the duplicate only and keep the original invoice open. "
            "Enterprise-tier customers may request an expedited refund, approved by a "
            "Tier-2 agent."
        ),
    ),
    SeedDoc(
        id="policy-escalation",
        text=(
            "Escalation and SLA policy. Every request carries a resolution SLA based on "
            "priority: urgent is 8 hours, high is 24 hours, medium is 48 hours, low is "
            "72 hours. If a request is at risk of breaching its SLA, escalate it to "
            "Tier-2 and set its priority no lower than high. Enterprise and premium "
            "customers are escalated one tier earlier than standard."
        ),
    ),
    SeedDoc(
        id="runbook-login-500",
        text=(
            "Runbook for login failures returning HTTP 500. First check the auth service "
            "status page for an active incident. If an incident is open, link the request "
            "to it and set status to waiting_customer with an ETA. If not, confirm the "
            "customer's region and clear their cached session, then ask them to retry "
            "from an incognito window."
        ),
    ),
    SeedDoc(
        id="guide-password-reset",
        text=(
            "Password reset guide. To reset a password, send the customer a signed reset "
            "link that expires in 30 minutes. The customer must set a new password of at "
            "least twelve characters. Password resets do not change the account's billing "
            "plan or open invoices."
        ),
    ),
    SeedDoc(
        id="policy-data-export",
        text=(
            "Data export and privacy policy. Customers may request an export of their "
            "personal data under GDPR. Fulfil a verified export request within 30 days "
            "and deliver it as an encrypted archive. Never include another customer's "
            "records in an export."
        ),
    ),
)


# ── Labelled eval cases ───────────────────────────────────────────────────────
SEED_CASES: tuple[EvalCase, ...] = (
    EvalCase(
        query="How long does a refund take and how is it returned to the customer?",
        gold_doc_ids=frozenset({"kb-refunds"}),
        claims=("refund", "original payment method", "business days"),
    ),
    EvalCase(
        query="A duplicate charge was made — what should I refund?",
        gold_doc_ids=frozenset({"kb-refunds"}),
        claims=("duplicate", "refund", "invoice"),
    ),
    EvalCase(
        query="What is the SLA for an urgent request and when do we escalate?",
        gold_doc_ids=frozenset({"policy-escalation"}),
        claims=("urgent", "8 hours", "escalate", "Tier-2"),
    ),
    EvalCase(
        query="A customer gets an HTTP 500 error when logging in. What do I do first?",
        gold_doc_ids=frozenset({"runbook-login-500"}),
        claims=("auth service", "incident", "500"),
    ),
    EvalCase(
        query="How do I reset a customer's password and how long is the link valid?",
        gold_doc_ids=frozenset({"guide-password-reset"}),
        claims=("reset link", "30 minutes", "password"),
    ),
    EvalCase(
        query="A customer wants an export of their personal data under GDPR.",
        gold_doc_ids=frozenset({"policy-data-export"}),
        claims=("export", "GDPR", "30 days", "encrypted"),
    ),
)


def corpus_chunks() -> list[Chunk]:
    """Return the seed corpus as retrieval :class:`~app.retrieval.models.Chunk` objects.

    Each doc becomes a single chunk whose ``doc_id`` is the seed id, so retrieval
    provenance (``metadata["doc"]``) maps straight back to the gold labels.
    """
    return [
        Chunk(id=f"{doc.id}#0", doc_id=doc.id, ordinal=0, text=doc.text)
        for doc in SEED_CORPUS
    ]
