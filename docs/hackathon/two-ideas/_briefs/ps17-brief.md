# PROBLEM STATEMENT 17 — Contract Obligation, SLA & Commercial Leakage Monitor

> Verbatim participant brief. AI Friday · National Finals · Season 2 · 29 August → 4 September 2026.

## 01 — Business Context & Why It Matters

**Why this problem matters**

Contracts contain obligations, service levels, credits and notice requirements that must be mapped to real operational evidence to prevent leakage and missed commitments.

The operating environment spans contracts, amendments, SLA records, invoices, service events, credits, notices, renewal dates and owner actions. Evidence can arrive asynchronously, be corrected later or conflict across systems, so the solution must preserve state, provenance and prior actions as the case evolves.

**Complexity layer:** The solution must reconcile changing evidence across multiple systems, maintain long-running state, support real approvals and continue safely through partial failures. Material changes must trigger targeted re-evaluation rather than silently preserving an outdated conclusion.

## 02 — The Challenge & Scope

Enterprise-Wide · Enterprise Operations · Decision Intelligence

**What teams must reimagine**

Extract obligations with provenance, monitor operational evidence and surface potential breaches, missed credits and leakage.

The outcome should be an evidence-backed operational workspace that can progress the journey, surface uncertainty and coordinate authorized actions while preserving accountability. Legal interpretation, contractual notice and material commercial settlement decisions remain human-owned.

## 03 — Core Solution Capabilities

Your solution should be able to:

- Assemble and reconcile contracts, amendments, SLA records, invoices, service events, credits, notices, renewal dates and owner actions into a coherent, time-aware operating view.
- Detect missing, stale, duplicated or contradictory information and make uncertainty explicit.
- Dynamically select the next useful evidence, analysis, coordination or permitted action based on the current state and constraints.
- Maintain source provenance, human intervention, approvals and a complete audit trail through the full journey.

## 04 — End-to-End Capability Expectations & Controls

The following capabilities are part of the main challenge scope and should be addressed in the solution:

- Build a durable domain model that links contracts, amendments, SLA records, invoices, service events, credits, notices, renewal dates and owner actions and can represent late, corrected or conflicting versions without losing earlier evidence.
- Determine the next-best evidence request or permitted action dynamically from current state, deadlines, dependencies, authority and expected value rather than relying on a fixed happy path.
- Track long-running workflow state, deadlines and cross-system dependencies while preventing duplicate requests, duplicate transactions or repeated external actions.
- Represent competing interpretations, plans or hypotheses and show which evidence supports, weakens or changes each option without hiding uncertainty.
- Maintain versioned state with source-level provenance and explicit separation of recorded fact, AI inference, user input, automated action and human decision.
- Coordinate approved tools or specialist AI components with permission checks, retries, timeouts, idempotency and safe recovery when only part of a workflow succeeds.
- Support role-based intervention, approval gates, audit replay and configurable action boundaries so reviewers can reconstruct what the system knew, why it acted and what changed afterward.

## 05 — Production-Ready Solution & Data Environment

**Production-ready solution expectation**

Build a production-ready, end-to-end working solution engineered for enterprise deployment and operational use. The solution should demonstrate production-grade architecture, security, resilience, observability, auditability, configurable controls, failure recovery and maintainable integration patterns. Where live enterprise, partner or external-system access is unavailable, teams may use representative mock interfaces or simulated services, but the solution itself must behave as a deployable product. It should ingest representative contracts, amendments, SLA records, invoices, service events, credits, notices, renewal dates and owner actions, execute the complete journey, react safely to late-arriving or changed information, recover from partial failure and retain a complete inspectable audit trail. Legal interpretation, contractual notice and material commercial settlement decisions remain human-owned.

**Data availability & synthetic data**

Use synthetic starter data where relevant. Teams may create additional synthetic contracts, amendments, SLA records, invoices, service events, credits, notices, renewal dates and owner actions plus representative edge cases, corrections, failure responses and approval states needed to demonstrate the complete solution. No live confidential enterprise data is required.

## 06 — End-to-End Journey

Process flow:

1. Contract ingested
2. Obligations extracted
3. Owners mapped
4. Evidence monitored
5. Exceptions detected
6. Impact quantified
7. Action prepared
8. Commercial review
9. Outcome tracked

## National Finale Inject

> A contract amendment changes an SLA threshold after potential breaches were flagged. The system must re-evaluate each event using the correct effective version.
