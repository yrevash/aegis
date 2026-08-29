# PROBLEM STATEMENT 04 — AI-Powered Dynamic Covenant Monitoring & Early Warning

> Verbatim participant brief. AI Friday · National Finals · Season 2 · 29 August → 4 September 2026.

## 01 — Business Context & Why It Matters

Commercial Banking · Credit Risk

Build an AI-driven early-warning capability that continuously evaluates borrower financials, account activity, payment behaviour, utilization, covenant thresholds, industry deterioration, news, concentration exposure and treasury flows to predict potential covenant breaches 30, 60 and 90 days in advance.

**Complexity: High** · 30/60/90-Day Prediction · Financial Reasoning · Risk Signals · Explainable Intervention

**Business context**

Commercial lending covenants are often monitored through periodic borrower reporting and manual review. A borrower can deteriorate between reporting cycles, while warning signals may already exist across account activity, utilization, payments, treasury flows, industry conditions and concentration exposure.

Earlier detection can help relationship, credit and risk teams intervene before a formal breach occurs.

## 02 — The Challenge & Scope

Design and build a production-ready AI solution that monitors contractual covenant thresholds together with borrower financial and behavioural signals, forecasts breach risk over 30-, 60- and 90-day horizons, explains the drivers of deterioration, and recommends prioritized interventions.

**Core challenge:** The system must distinguish meaningful deterioration from temporary noise and provide evidence-backed early warning rather than merely flagging a covenant after it has already been breached.

## 03 — Core Solution Capabilities

Required solution capabilities:

- Ingest borrower financial statements and calculate relevant financial ratios.
- Extract and represent covenant definitions, thresholds, testing frequency and exceptions.
- Monitor account activity, payment behaviour and credit/facility utilization.
- Evaluate treasury flows and changes in cash movement patterns.
- Incorporate concentration exposure and synthetic industry/news deterioration signals.
- Predict probability of covenant breach at 30-, 60- and 90-day horizons.
- Identify the primary drivers contributing to forecast risk.
- Rank borrowers/facilities by urgency and expected impact.
- Recommend appropriate relationship-manager, credit or risk-team interventions.
- Generate an auditable warning trail showing data, trends, calculations and reasoning.

**Early-warning intelligence**

The solution should combine deterministic covenant calculations with predictive indicators. Examples include:

- Weakening debt-service or leverage position
- Rapid utilization increase
- Delayed or irregular payments
- Deteriorating cash inflows
- Industry stress
- Concentration risk
- Unusual treasury-flow changes

## 05 — Production-Ready Solution & Data Environment

**Development environment**

Teams should create synthetic commercial borrower portfolios with financials, covenants, account activity, payment history, facility utilization, industry indicators and treasury flows.

- No live core-banking or credit system integration is required.
- External news/industry signals may be supplied or synthetically generated.
- The full scoring and alerting workflow should be executable locally.

## 06 — End-to-End Journey

Process flow:

1. **Borrower & Covenant Intake** — Load financials, facilities, covenant definitions and thresholds.
2. **Signal Monitoring** — Track payments, utilization, account activity, treasury and external risk indicators.
3. **Risk Forecast** — Estimate breach probability at 30, 60 and 90 days.
4. **Driver Explanation** — Explain which signals and covenant movements are creating risk.
5. **Portfolio Prioritization** — Rank borrowers by urgency, confidence and potential exposure.
6. **Intervention** — Recommend actions and maintain an auditable early-warning history.
