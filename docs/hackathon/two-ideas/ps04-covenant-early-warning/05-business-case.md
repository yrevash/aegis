# PS-04 — business case

**PS-04's strongest lane: 8.5 vs PS-17's 6.5.** Lead with this.

## PS-04 is a regulation written as a problem statement

This is the single best sentence in the PS-04 pitch, and it is literally true.

**EBA Guidelines on loan origination and monitoring (EBA/GL/2020/06)**, applicable from
**30 June 2021**:

| Para | Requirement |
| --- | --- |
| **§267** | Covenant adherence "should be utilised as early warning tools… early detection of deviations is key" — naming **net debt/EBITDA, ICR and DSCR** |
| **§269** | EWIs must be "supported by an appropriate IT and data infrastructure" |
| **§270–272** | Trigger levels, escalation procedures with assigned follow-up responsibilities, a watch list, and reporting to the management body |
| **§274(o)** | Late delivery of a certificate of adherence, a waiver request, or a covenant breach is itself a deterioration signal |
| **§275–277** | Designated functions analyse severity "without undue delay"; decisions documented and communicated onward |

**The infrastructure-remediation window closed 30 June 2024.** Banks that have not built this are
already late.

**India:** RBI's **Stressed Assets Directions of 28 Nov 2025** — SMA classification on default,
CRILC monthly reporting, a weekly Friday default report, a 30-day review. RBI's July 2024 Master
Directions enumerate **~42 EWS indicators**, with a TAT "preferably not more than 30 days" under
Risk Management Committee oversight.

**Contrast PS-17:** no equivalent forcing function. DORA Arts. 28–30 force contractual SLA content
and registers for ICT third parties at financial entities only, and do not mandate
obligation-evidence reconciliation. PS-17's driver is commercial; PS-04's is supervisory.

## The buyer

**CRO office / credit risk.** A named function, a recurring ring-fenced budget, and — the decisive
point — **a supervisor who will ask them about this whether or not they buy anything.**

That is a materially better sales position than PS-17's diffuse procurement / legal ops / revenue
assurance / vendor management buyer, even though PS-17's recoverable pool is arguably larger.

## What you cannot claim

**There is no published elasticity for loss avoided per month of earlier detection.** None. If a
rival team shows one, they invented it.

**The honest substitute, and it is a better answer anyway:** make the system **measure its own
realised lead time**. Instrument it so that after six months the bank can compute its own
elasticity from its own book. Saying *"nobody can source this number, so we built the instrument
that produces it"* is a stronger answer than a fabricated statistic, and a CTO will recognise it.

## The ROI frame

Because the loss-avoidance elasticity is unsourceable, build the case on **cost of compliance and
capacity** rather than avoided losses:

1. **Regulatory cost avoidance** — the EBA/RBI infrastructure obligation exists and is overdue.
   The alternative to buying is building, and the deadline has passed.
2. **RM capacity recovered** — the alert-fatigue arithmetic from `02-architecture.md`: ~168 alerts
   per RM per month at naive settings ≈ 3–4 hours/day. Capacity-constrained ranking against a
   stated alert budget is a *measurable* saving.
3. **Provisioning accuracy** — IFRS 9 forward-looking ECL and SICR staging.

**IFRS 9 is two-edged and you should say so before a judge does:** building the forecast arguably
creates an obligation to *use* it for SICR staging. That is a real adoption risk, and naming it
first is a credibility win rather than a loss.

## The three numbers for the slide

1. **30 June 2024** — the EBA infrastructure-remediation deadline that has already passed.
2. **~42** RBI-enumerated EWS indicators, with a 30-day TAT under RMC oversight.
3. **3–4 hours/day** of RM time consumed by naive alerting — the capacity the alert-budget dial
   recovers.

Note what is *not* on that list: any accuracy figure, and any avoided-loss figure. That is
deliberate.

## Sector relevance

`tcs.com` was 403-blocked during research; **Infosys' FY26 20-F is the verifiable sector proxy** —
Financial Services at **27.9%** of $20,158m revenue. Commercial banking credit risk maps directly
onto a services major's largest vertical, which is a genuine advantage for PS-04 at a TCS event.
Substitute a TCS figure you can source before the pitch.
