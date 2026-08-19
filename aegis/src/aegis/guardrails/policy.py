"""The four per-tenant guardrail controls, as one frozen object the pipeline reads.

**Why this exists at all.** :class:`~aegis.guardrails.pipeline.Guardrails` is built once
in a host's composition root, synchronously, from environment configuration. The
catalogue keys that govern the same four behaviours — ``guardrails.grounding.block``,
``guardrails.topical.block``, ``guardrails.denylist.terms`` and
``guardrails.pii.entities`` — are **per tenant** and resolved **asynchronously**, and
the tenant is not known until a request arrives. That mismatch is the whole reason those
four keys were writable, auditable, badged "Your setting" on a settings screen, and read
by nothing: there was no object to hand a resolved value to that was not the
process-wide singleton, and writing one tenant's rails onto the singleton would apply
them to every other tenant's next request.

:class:`GuardrailPolicy` is that object. It is the exact analogue of
:class:`~aegis.agent.deps.AgentConfig` for the rails, and
:mod:`aegis.settings.guardrails` is the exact analogue of :mod:`aegis.settings.agent`:
resolve per request, fold with the spec's own merge rule, hand the result to
:meth:`~aegis.guardrails.pipeline.Guardrails.with_policy`, and never keep it.

Deliberately a leaf module with no imports beyond the standard library, so the settings
package can manipulate it structurally (:func:`dataclasses.replace`) without importing
:mod:`aegis.guardrails` and everything that package pulls in — the same separation
:mod:`aegis.settings.agent` keeps from :mod:`aegis.agent`.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["GuardrailPolicy"]


@dataclass(frozen=True, slots=True)
class GuardrailPolicy:
    """What one tenant's rails do, over and above what the host wired.

    Every field is *additive against the host*: the two booleans only ever turn a rail
    from advisory to blocking (``TIGHTEN_ONLY``), and the two collections only ever grow
    (``UNION``). There is no value of this object that makes the rails weaker than the
    :class:`~aegis.guardrails.pipeline.Guardrails` the host constructed, which is what
    makes it safe to fold a tenant's writes onto a host's configuration at all.

    Attributes:
        topical_block: Hard-BLOCK an off-topic query instead of flagging it. Only
            observable where the host wired ``allowed_topics``; with no topics
            configured the topical rail does not run and this changes nothing.
        grounding_block: Hard-BLOCK an answer the grounding rail cannot support from the
            retrieved contexts, instead of flagging it.
        denylist_terms: Extra terms whose presence is a BLOCK, screened on the inbound,
            tool-result and outbound paths. Case-insensitive substring matching, which
            is what a denylist of project codenames and client names needs.
        pii_entities: PII entity kinds to screen for **in addition to** the detection
            engine's own curated allowlist. Additive by construction — naming fewer
            kinds than the engine already screens cannot switch any of them off, because
            the effective set is a union and never an assignment.
    """

    topical_block: bool = False
    grounding_block: bool = False
    denylist_terms: tuple[str, ...] = ()
    pii_entities: tuple[str, ...] = ()
