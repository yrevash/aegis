# Business impact, market size and the ROI case — comparative research

Lane: *what is this worth, and can the number survive a CTO's cross-examination?*
Scope: PS-17 (Contract Obligation, SLA & Commercial Leakage Monitor) vs PS-04 (AI-Powered Dynamic Covenant Monitoring & Early Warning).

Every figure below is either (a) traced to a primary document I opened in this session, (b) derived by arithmetic from such a figure — with the arithmetic shown, or (c) explicitly labelled as an assumption or as unverified. Nothing is quoted second-hand without saying so.

---

## Executive answer

- **PS-04 has the stronger business case, and the gap is mostly about *forcing function*, not about size.** Both problems are real and both are large. Only one of them is something a regulator has already written down as a mandatory capability with a deadline that has passed.
- **The single most useful finding for PS-04:** the problem statement is, almost clause for clause, a restatement of **EBA Guidelines on loan origination and monitoring (EBA/GL/2020/06), §8.4 "Monitoring of covenants" and §8.5 "Use of early warning indicators/watch lists"**. §267 tells banks to use covenant adherence "as early warning tools" and names net debt/EBITDA, ICR and DSCR. §269 requires EWIs "supported by an appropriate IT and data infrastructure". The guidelines applied from 30 June 2021 and the transitional window for banks to "address possible data gaps and adjust their monitoring frameworks and infrastructure" **expired on 30 June 2024**. The budget line exists and is defended in supervisory dialogue.
- **The single most useful finding for PS-17:** the widely-repeated "9% of contract value" number **does** have a real primary source, and it has been *updated* — WorldCC + Deloitte, *The ROI of Contracting Excellence* (June 2023, >1,200 organisations): **average value erosion is now 8.6%**, down from the 2014 IACCM figure of 9.2%, "with the best performers operating at a little over 3% and the worst more than 20%." Most people pitching this number are quoting a stale 2014 figure. Quoting 8.6% with the 3%/20% spread is instantly more credible than quoting 9.2%.
- **The headline loss numbers, primary-sourced:**
  - PS-17: **8.6% of contract value eroded on average**; the *avoidable* band is the ~5.6-point gap to best-in-class. WorldCC/Deloitte also report that "the combined cost of contracting and value loss represents a relatively low 2–4% of revenue in the average consumer goods company. This rises to 15%+ in sectors engaged in high-value capital projects."
  - PS-04: EU/EEA banks reported **~€110bn of NPL *inflows* in H1 2025 alone** — "NPL inflows remain significant and have not slowed down" — of which SMEs were €32bn (EBA, Dec 2025). US commercial banks charged off C&I loans at **0.58% annualised in 2026:Q2** against a **$2,933.1bn** C&I book (Federal Reserve), ≈ **$17bn/year** in the US alone.
- **Do not confuse the CLM market with the leakage.** The CLM *software* market is **$3.32bn in 2026, forecast $8.84bn by 2035 (11.56% CAGR)** (Precedence Research). The leakage it addresses, at 8.6% of contract value, is two to three orders of magnitude bigger. That gap is the pitch — and also the warning: the software category is small, mature and crowded, so PS-17 must sell the *evidence-reconciliation* layer, not "another CLM".
- **My defensible ROI models** (full arithmetic below): PS-17 recovers **$13.4m–$36.4m/yr on a $2.0bn contracted-value book** (≈$2,690–$7,280 per contract per year), which is *below* WorldCC's own published ROI curve at every point — a deliberately defensive position. PS-04 avoids **€2.5m–€10.5m/yr on a €20bn corporate/SME book** (≈1.1–4.3 bps of exposure, i.e. a 2.3%–9.0% cut to a 48 bps cost of risk), scaling to **€43m–€210m/yr for a G-SIB-scale €400bn book**.
- **A finding that should change the pitch: do not lead PS-17 with SLA service credits.** They are the most demo-able sliver and the smallest. On $60m of SLA-bearing spend the recoverable credit pool is roughly **$84k/yr — 0.14% of spend**. Credits are structurally forfeitable (Google: claim within 60 days or "Customer will forfeit its right to receive a Financial Credit"; AWS: claim by end of the second billing cycle or you are "disqualified"), which makes them a *perfect demo*, but the money is in rate-card drift, unbilled milestones, missed price-review and renewal windows, and un-taken volume rebates.
- **Buyer and budget:** PS-04 sells to the CRO office out of a recurring regulatory-change/risk-technology budget, with four separate live mandates pushing it (EBA GL/2020/06; RBI Stressed Assets Directions 2025 of 28 Nov 2025; RBI Fraud Risk Management Master Directions of 15 Jul 2024; IFRS 9 forward-looking ECL, with EBA telling supervisors in Dec 2025 to "promote the use of forward-looking indicators"). PS-17 sells to procurement/legal ops/revenue assurance out of discretionary transformation budget — **but has one under-appreciated advantage: it can be sold on contingency/gainshare, i.e. with no budget approval at all**, because recovery-audit economics already work that way.
- **TCS relevance:** Financial Services is the single largest vertical for an Indian IT-services major — Infosys' FY2026 Form 20-F reports **Financial Services at 27.9% of $20,158m revenue**, the largest of eight segments, and lists a core-banking platform (Finacle) among its key products. PS-04 plugs directly into that book of business as a repeatable offering with a regulatory renewal cycle. PS-17 maps to a services firm's *own* commercial function (it is itself the SLA-bearing supplier) — interesting, but a smaller and more fragmented sale.
- **Score: PS-04 8.5/10, PS-17 6.5/10** on business case. Justification and the conditions that would flip it are at the end.

---

## PS-17: Contract obligation, SLA and commercial leakage — business analysis

### 1. The headline loss number, traced

**Primary source, verified.** WorldCC (World Commerce & Contracting, formerly IACCM) in collaboration with **Deloitte**, *The ROI of Contracting Excellence*, © World Commerce & Contracting 2023 (June 2023). I extracted the full text of this PDF. The executive summary reads:

> "In 2014, World Commerce & Contracting (then named IACCM) conducted research that indicated average value erosion of 9.2% of contract value. While there were wide variations between sectors and acceptance that some erosion is unavoidable, these findings indicated tremendous potential for improvement.
>
> Now almost ten years on WorldCC has collaborated with Deloitte to update its research. Drawing on data from **more than 1,200 organizations** … What we discovered was a marginal improvement. We estimate that **average value erosion now stands at 8.6%, with the best performers operating at a little over 3% and the worst more than 20%.**"

Signed by Tim Cummins (President, WorldCC), Craig Conte (Deloitte, Lead Partner for Legal Operate) and Mark Ross (Deloitte, Principal, Legal Business).

**Traceability verdict on the famous 9.2%:** the number is real, it originates in 2014 IACCM research, and the original write-up is *Overcoming the 10 Pitfalls of Contracting*. **I could not open that 2014 document** — both hosted copies (worldcc.com portal and worldcc.foundation static) returned HTTP 403 in this session. So: the 9.2% is *attested* by the 2023 WorldCC/Deloitte report and by Deloitte's own website, but the 2014 methodology (sample, definition of "anticipated value", how erosion was measured) is **not independently verifiable from the public web in this session**. If a juror presses on methodology, the honest answer is: "the 2023 update is the citable one — 1,200+ organisations, 8.6%; the 2014 figure is attested by both WorldCC and Deloitte but its underlying methodology is not published openly."

Corroboration from the other side of the collaboration — Deloitte US, *Contract management lifecycle insights* (page dated 20 Sep 2023) repeats the same figures and adds a detail that matters enormously for this problem statement:

> contract-related data in large organisations "on average sits in **24 different systems**".

That is the PS-17 thesis in one statistic: the obligation is in one system, the evidence is in twenty-three others.

WorldCC's own *Contract Management Whitepaper — August 2025* repeats 8.6% and states businesses lose "almost 9% of value annually through poor contract management" with best performers at ~3% and worst at 15%+. Note the slight inconsistency with the 2023 report's "more than 20%" for worst performers; I would quote the 2023 report, which is the one with a stated sample.

**Two further primary figures from the same report, both useful and both rarely quoted:**

1. Sector variance in the *combined* cost: "the combined cost of contracting and value loss represents a relatively low 2–4% of revenue in the average consumer goods company. This rises to 15%+ in sectors engaged in high-value capital projects, where both buy-side and sell-side agreements incur costs and may be subject to value erosion."
2. **WorldCC publishes its own ROI curve** (Figure 10, Appendix A): indicative potential ROI as a percentage of the revenue or spend associated with a contract type, as a function of a 1–10 capability-shortfall score — approximately **0.5–0.8% at shortfall 1, 1.25% at 2, 2.0% at 3, 3.3% at 4, 4.5% at 5, 6.25% at 6 and above**. The report itself carries the caveat: *"This estimate is indicative and has been established using averages taken from workshops and research with a group of cross-sector organizations. You should not rely on this data without further validation."* Quote the caveat when you quote the curve; it makes you look like you read the document, because you did.

### 2. SLA service credits — the structural leak, sized honestly

The **mechanism** is verifiable from vendor contracts and is the cleanest thing in this whole problem statement:

- **Google Cloud Compute Engine SLA** (last modified 4 March 2025), under the heading *"Customer Must Request Financial Credit"*: "In order to receive any of the Financial Credits described above, Customer must notify Google technical support **within 60 days** from the time Customer becomes eligible… Customer must also **provide Google with log files showing Downtime Periods and the date and time they occurred. If Customer does not comply with these requirements, Customer will forfeit its right to receive a Financial Credit.**" Credit bands: 10% / 25% / 100% of the monthly bill for the affected service.
- **AWS Compute SLA**: "To receive a Service Credit, you must submit a claim by opening a case in the AWS Support Center. Your credit request **must be received by us by the end of the second billing cycle** after which the incident occurred… **Your failure to provide the requested and other information as required above will disqualify you from receiving a Service Credit.**" Credit bands: 10% / 30% / 100%.

So: credits are **claim-required, evidence-required and time-barred**, and the evidence required is exactly what PS-17's "service events" corpus contains. That is a genuine, primary-sourced market failure, and it demos beautifully.

**But size it honestly.** The most-cited number for how many credits go unclaimed — "60 to 80 percent of eligible credits went unclaimed" — comes from **Redress Compliance**, a Google Cloud licensing advisory, on a page dated 7 August 2026. I opened it and checked its provenance: it cites **no external source or dataset**; it attributes the figure to its own engagements, "Based on 25 to 35 commitment reviews run 2024 to 2025." That is a consultancy's self-reported sample of ~30 clients. **Label it as such if you use it.** It is not research.

Arithmetic on the sliver, with every assumption exposed:

| Line | Value | Basis |
|---|---|---|
| SLA-bearing IT/cloud/managed-services spend | $60,000,000/yr | Assumption: a large enterprise's covered spend |
| Share of service-months that miss an SLO | 2% | **Assumption.** No public dataset exists for this |
| Typical credit at the lowest band | 10% of that month's bill | Google and AWS SLAs, primary |
| Gross credit entitlement | 2% × 10% = 0.2% of spend = **$120,000/yr** | Arithmetic |
| Share never claimed | 70% | Redress Compliance self-reported 60–80%, **not research** |
| Recoverable | **$84,000/yr — 0.14% of spend** | Arithmetic |

**Finding:** SLA service credits alone do not fund a programme. Lead the pitch with obligation and entitlement leakage; use credits as the *visible* proof that the machinery works.

### 3. A defensible ROI model for PS-17

**The enterprise.** A large buyer (or an IT-services provider on the sell side) with $2.0bn of annual contracted value under management across 5,000 active contracts (average $400k/yr).

**Step 1 — the erosion envelope (top-down).** 8.6% × $2.0bn = **$172m/yr**. Do *not* claim this. WorldCC is explicit that "some erosion is unavoidable" and that best performers still run at ~3%.

**Step 2 — the avoidable band.** Average (8.6%) − best-in-class (3.0%) = **5.6 points = $112m/yr**. WorldCC uses the same framing in its worked example, describing a portfolio "suffering from avoidable value erosion of more than 5% due to inadequate capabilities."

**Step 3 — the post-award, evidence-detectable share.** PS-17 does not touch pre-award erosion (bad pricing, weak terms, slow negotiation). It touches missed credits, unbilled entitlements, un-enforced rate cards, missed notice/renewal/price-review windows, un-taken rebates.

> **Assumption A1 (attack this one first):** 30% of avoidable erosion is post-award and evidence-detectable (conservative) / 50% (optimistic). Partial support: WorldCC attributes part of the 9.2→8.6 improvement to "a greater focus on post-award contract management and increased integration of contract management resources."

**Step 4 — detection-to-recovery conversion.** Finding an entitlement is not collecting it: some claims are waived for relationship reasons, some are time-barred, some are disputed.

> **Assumption A2:** 40% conversion (conservative) / 65% (optimistic).

| | Conservative | Optimistic |
|---|---|---|
| Contracted value under management | $2,000m | $2,000m |
| × avoidable erosion band (8.6% − 3.0%) | $112.0m | $112.0m |
| × post-award, evidence-detectable share (A1) | 30% → $33.6m | 50% → $56.0m |
| × detection-to-recovery conversion (A2) | 40% → **$13.44m/yr** | 65% → **$36.40m/yr** |
| **Per contract per year** (÷5,000) | **$2,688** | **$7,280** |
| **As % of contracted value** | 0.67% | 1.82% |
| Annual cost: platform $600k + 4 analyst FTE @ $80k | $0.92m | $0.92m |
| **Net annual value / ROI multiple** | **$12.5m / 14.6×** | **$35.5m / 39.6×** |

**The cross-check that wins the Q&A.** WorldCC's own Figure 10 curve says a *mild* capability shortfall (score 3) is worth ~2.0% of revenue/spend = **$40m** on this book, and a moderate shortfall (score 4) ~3.3% = $66m. My conservative case (0.67%) sits **below their score-2 point (1.25%)**, and even my optimistic case (1.82%) sits below their score-3 point. So both cases are conservative against the industry body's own published estimate. That is the right place to stand in front of a CTO: your number is defensible because it is *smaller* than the one the incumbent authority publishes.

**What a CTO will attack, and the honest answers:**
- *"A1 and A2 are invented."* Correct — they are stated assumptions, not sourced facts, which is why they are on the slide. The model is linear in both, so a juror can re-run it live: halve both and you still clear $3.4m/yr against $0.92m of cost.
- *"You are double-counting with existing recovery audits."* Partly true. Contract-compliance recovery audits already run in most large enterprises. This is a *displacement* sale (continuous vs. annual, and provenance-preserving vs. spreadsheet), not purely incremental. Say so first.
- *"8.6% is self-reported by a membership body with a commercial interest."* True. Deloitte co-authored it; sample >1,200; that is the best available. There is no regulator-published equivalent.

### 4. Market size — kept strictly separate from the leakage number

**CLM software market** (Precedence Research, page dated 11 Feb 2026): **$2.96bn (2025) → $3.32bn (2026) → $8.84bn (2035), 11.56% CAGR (2026–2035)**. North America 40% of 2025; US alone $830m (2025) → $2.577bn (2035). Analyst estimates for the same category diverge widely (roughly $1.3bn–$3.0bn for 2025 across five firms), which is itself worth saying: the category boundary between "CLM software" and "source-to-pay / legal ops / revenue assurance" is not agreed, so treat any single TAM as indicative.

**Interpretation.** The software market ($3.3bn) is roughly **1.9% of the erosion on a single $2bn enterprise book × 100 such enterprises** — i.e. the tooling market is tiny relative to the problem. Two readings, and you should offer both:
1. *Bull:* the category is under-penetrated because current CLM is a repository, not a reconciliation engine. The value is stranded.
2. *Bear:* a $3bn market with entrenched incumbents (Icertis, Sirion, Agiloft, DocuSign, SAP Ariba, Coupa) is a hard place to be a new entrant, and a CTO will know that. PS-17 must not be pitched as CLM.

### 5. Buyer, budget, urgency

| | Detail |
|---|---|
| **Economic buyer** | CPO / procurement operations; Legal Operations (under the GC); **Revenue Assurance** (a real, funded function with a P&L line in telecom and IT services); Vendor Management Office |
| **Budget line** | Procurement/source-to-pay transformation, legal-ops tooling, or — critically — **recovery-audit budgets, which are typically contingency-funded** |
| **The wedge** | Because recovery audit is already sold on gainshare, PS-17 can be sold with **zero budget approval**: paid from what it finds. This is a genuine commercial advantage over PS-04 and should be on the slide |
| **Regulatory forcing** | **Weak, but not zero.** The strongest is **DORA — Regulation (EU) 2022/2554, in application since 17 January 2025.** Art. 28 requires financial entities to maintain a **register of information** covering all ICT third-party contractual arrangements, producible to competent authorities on request. Art. 30 requires contracts for critical or important functions to contain "detailed service level descriptions with **quantitative and qualitative performance targets**", notice periods, reporting obligations for material developments, audit rights and exit strategies. So for financial-sector buyers there *is* a mandate to hold structured contractual SLA terms and evidence them |
| **Why this year** | DORA's register-of-information obligation is live and being examined; and every enterprise is re-papering AI and cloud contracts. But there is no rule that says "you must detect a missed service credit" |

**Honest verdict on urgency:** PS-17's drivers are commercial (margin, working capital, audit findings). DORA is real but partial — it forces contractual *content and registers* for ICT third parties at financial entities; it does not force obligation-to-evidence reconciliation, and it does not apply outside financial services. A CFO buys this because the payback is fast, not because a supervisor will write it up.

### 6. Relevance to a large IT-services firm

PS-17 has an unusual double life at a services major, and this is worth saying explicitly because most teams will miss it:

- **Sell-side (defensive):** the firm *is* the SLA-bearing supplier under thousands of MSAs and SOWs. A system that predicts, evidences and defends against *customer* service-credit claims and change-order disputes protects delivery margin directly. This is an internal-value story with a real owner (the Commercial/Contract Management function) and a real number (credits paid out).
- **Buy-side (offering):** sold into procurement and BPS engagements, adjacent to existing source-to-pay and finance-and-accounting outsourcing work.

Both are credible. Neither is as large or as repeatable as a regulated-capability sale into BFSI.

### 7. Value pools mapped to sub-problems (for the pitch spine)

| Sub-problem | Value pool it unlocks | Source anchor |
|---|---|---|
| Obligation extraction with provenance | Makes every other pool auditable; without it nothing is claimable | Deloitte: contract data across 24 systems |
| Temporal/versioned model (the amendment inject) | Prevents wrong-version claims — the failure mode that kills recovery credibility | PS-17 National Finale inject |
| Evidence reconciliation across systems | The 30–50% "post-award detectable" share in the model | A1 |
| Exception detection and quantification | Converts detection into a claimable number | A2 |
| Claim preparation before the bar date | Directly counters the Google 60-day / AWS 2-billing-cycle forfeiture | Google, AWS SLAs |

---

## PS-04: Dynamic covenant monitoring and early warning — business analysis

### 1. The headline loss numbers, all primary

**Europe — European Banking Authority, *Risk Assessment Report*, December 2025** (full text extracted from the EBA's own PDF; data as of June 2025 with Q3 2025 preliminaries):

- NPLs: **EUR 373bn**, NPL ratio **1.84%**, NPL coverage ratio **41.7%** (42% a year earlier).
- **The number that matters most for early warning:** "An examination of flows in and out of non-performing status, reveals that **the NPL inflows remain significant and have not slowed down. In the first half of 2025, banks reported total NPL inflows of around EUR 110bn**, broadly unchanged from the previous year, against outflows of nearly EUR 114 bn." Segment split of inflows: **SMEs EUR 32bn**, consumer credit more than EUR 20bn.
- IFRS 9 staging: "banks reported close to **EUR 1.6 tn of exposures at amortised cost in Stage 2. This equals 9.4% of total loans classified at amortised cost**" — near the highest since IFRS 9 began (9.7% in Dec 2024); Q3 2025 preliminary ~9.2%. Stage 2 share by segment: **CRE 17.1%, SMEs 14.9%, consumer credit 11.5%, mortgages 7.9%.**
- NPL ratio by segment: consumer credit 5.4%, **SME 4.6%**, CRE 4.2%, mortgages 1.4%.
- **Cost of risk: 0.48%** (fell 3bps YoY), most countries 0–50bps.

Why the inflow number is the right headline: the *stock* of NPLs is at a historic low everywhere, which superficially weakens a "banks are drowning" pitch. The *flow* is not. €110bn per half-year of newly non-performing exposure entering EU/EEA balance sheets is the population an early-warning system is actually aimed at, and the EBA says it is not slowing.

**Derived, showing the arithmetic** (label it as derived, not quoted): if Stage 2 is €1.6tn at 9.4% of amortised-cost loans, the EU/EEA loan base is ≈ €17.0tn; at a 0.48% cost of risk that is ≈ **€82bn/year of impairment charge across EU/EEA banks**. Annualising H1 2025 inflows gives ≈ **€220bn/yr of new NPL inflow**; at the 41.7% coverage ratio (an approximation — coverage is measured on stock, not flow) that is on the order of **€90bn/yr of expected loss entering the system in the EU alone**.

**United States — Federal Reserve, *Charge-Off and Delinquency Rates on Loans and Leases at Commercial Banks*, release dated 25 August 2026** (rates are net of recoveries, as a percentage of average loans, annualised, seasonally adjusted):

| 2026:Q2 | C&I loans | CRE (domestic offices) | Total loans and leases |
|---|---|---|---|
| Net charge-off rate | **0.58%** | 0.14% | 0.55% |
| Delinquency rate | **1.27%** | 1.53% | 1.42% |

Federal Reserve **H.8** (release 21 Aug 2026, week ending 12 Aug 2026): **C&I loans $2,933.1bn**; total loans and leases $13,982.6bn.

Derived: 0.58% × $2,933.1bn ≈ **$17.0bn/yr of US C&I net charge-offs**. Total loans and leases: 0.55% × $13,982.6bn ≈ **$77bn/yr**.

**India — RBI.** Reported GNPA for scheduled commercial banks has fallen to a **multi-decadal low of ~2.2–2.3%**, with FSR stress tests projecting ~1.9% by March 2027 under baseline and 3.2%/4.2% under adverse scenarios. **Caveat, stated plainly: I could not open the RBI Financial Stability Report December 2025 PDF** — rbidocs.rbi.org.in served a CAPTCHA challenge for both direct download and fetch. These India figures therefore reach me only through secondary reporting of the FSR and should be **re-verified against the FSR PDF before being put on a slide**. The direction of travel (multi-decadal lows) is not in dispute.

**An inconvenient finding you should own, not hide.** Across all three jurisdictions, credit quality in 2025–26 is *benign*: EU NPL ratio 1.84% and cost of risk 48bps, US C&I charge-offs 0.58%, India GNPA at multi-decadal lows. A jury member who follows banking will know this. The correct rebuttal is three-part and is stronger than pretending otherwise:
1. **Flows, not stock.** €110bn/half-year of NPL inflows, "not slowed down" (EBA, verbatim).
2. **Stage 2 is the tell.** €1.6tn — near an all-time high — sitting in "significant increase in credit risk", with **CRE at 17.1% and SMEs at 14.9%**. The EBA's own diagnosis is that this "is hardly linked to neither the evolution of NPLs, nor the cost of risk", that banks "seem reluctant to transfer these loans back to stage 1", and that this may reflect "model deficiencies". That is a regulator saying, in supervisory language, *banks cannot evidence their staging decisions in either direction*.
3. **Benign is exactly when you buy this.** You build early warning in the good years; nobody procures a monitoring system in the middle of a credit event.

### 2. The regulatory forcing function — this is PS-04's real moat

**EBA/GL/2020/06, Guidelines on loan origination and monitoring** (Final Report; applied from 30 June 2021; renegotiated existing loans from 30 June 2022; **data-gap and infrastructure remediation permitted only until 30 June 2024**). I extracted the text. The relevant sections are effectively a specification for PS-04:

> **§267:** "Where applicable, institutions should monitor borrowers' adherence to the covenants agreed in the credit agreements. The borrower's adherence to covenants, as well as the timely delivery of covenant compliance certificates, where applicable, **should be utilised as early warning tools. Early detection of deviations is key to protecting the institution's position** towards the borrower and other possible creditors involved. The ongoing monitoring of financial covenants should include all relevant ratios specified in the covenants (e.g. net debt/EBITDA, interest coverage ratio, debt service coverage ratio (DSCR))."

> **§268:** monitor **non-financial** covenants too, not only via the certificate but "by other means, e.g. through close contact with the borrower by the client executive."

> **§269:** "institutions should develop, maintain and regularly evaluate relevant quantitative and qualitative EWIs that are **supported by an appropriate IT and data infrastructure** that would allow the timely detection of increased credit risk in their aggregate portfolio as well as in portfolios, sub-portfolios, industries, geographies and individual exposures."

> **§270:** EWIs must have "defined trigger levels", "assigned escalation procedures, including assigned responsibilities for the follow-up actions", and a **watch list**.

> **§272:** on a triggered EWI, apply more frequent monitoring, consider the watch list, undertake "predefined measures and mitigation actions", and the watch list must produce "specific reports being regularly reviewed by the head of the risk management function, the heads of functions involved in credit granting and **the management body**."

> **§274:** enumerates the credit-quality deterioration signals to consider, opening with "negative macroeconomic events (including… changes in legislation and technological threats to an industry) affecting the future profitability of an industry, a geographical segment, a group of borrowers or an individual corporate borrower" and "known adverse changes in the financial position of borrowers".

Compare that list against PS-04's stated capabilities — covenant thresholds and testing frequency, utilisation, payment behaviour, treasury flows, industry/news deterioration, concentration, ranked interventions, auditable warning trail. It is the same document written twice.

**India — two live mandates, both recent:**

- **RBI (Commercial Banks – Resolution of Stressed Assets) Directions, 2025**, RBI/DOR/2025-26/165, **issued 28 November 2025, effective immediately.** Banks must "recognise incipient stress in loan accounts, **immediately on default**, by classifying such assets as special mention accounts (SMA)" (¶15), across all loans regardless of exposure size (¶16); report SMA classification to **CRILC monthly for all borrowers with aggregate exposure ≥ ₹5 crore** (¶17); submit a **weekly default report by close of business every Friday** (¶18); undertake a prima facie review within a **30-day Review Period** (¶23); implement resolution plans **within 180 days from the end of the Review Period** (¶41). The SMA-0/1/2 ladder (≤30 / 30–60 / 60–90 days overdue) carries over from the **Prudential Framework for Resolution of Stressed Assets, RBI/2018-19/203, 7 June 2019**.
- **RBI Master Directions on Fraud Risk Management, 15 July 2024** — consolidating and withdrawing 36 circulars, extended to Regional Rural Banks, Rural Cooperative Banks and Housing Finance Companies. Banks "must establish a framework for identifying early warning signals, **integrating with Core Banking Solutions (CBS) for real-time monitoring**" (Clause 8.3); accounts meeting the CRILC threshold and identified as Red-Flagged must be **reported to the RBI within seven days** (Clause 8.3.3); a **Data Analytics and Market Intelligence Unit** is mandated. *(Source note: I read this via Economic Laws Practice's clause-referenced summary PDF; the RBI original was not directly retrievable in this session — verify clause numbers against the RBI text before quoting them on a slide.)*

**Global — Basel.** BCBS **d403**, *Prudential treatment of problem assets — definitions of non-performing exposures and forbearance* (4 April 2017), harmonises NPE and forbearance definitions across supervisors to improve "identification and monitoring" of problem assets. It is the definitional substrate the other rules sit on.

**IFRS 9 and the Stage 2 problem.** The EBA's Dec 2025 conclusion is a direct instruction that maps onto PS-04:

> "Supervisors should encourage transparency in the classification and reporting of Stage 2 loans and **promote the use of forward-looking indicators — such as scenario analysis and qualitative assessments — to capture risks that may not be immediately evident in quantitative metrics.**"

### 3. A defensible ROI model for PS-04

**The bank.** €20bn corporate + SME loan book, 4,000 borrowers, average exposure €5m. Cost of risk at the EU average 0.48% → **€96m/yr impairment charge**.

**Pool A — credit loss avoided through earlier intervention.**

> **Assumption B1:** 60% (conservative) / 75% (optimistic) of impairment is *monitorable* — i.e. preceded by observable deterioration in the signals PS-04 names. The remainder (fraud, sudden single-event failure, exogenous shock) is not addressable by any monitoring system. Nobody publishes this split; it is a judgement.

> **Assumption B2:** the system converts 15% (conservative) / 30% (optimistic) of monitorable cases from "detected at or after breach" to "detected 30–90 days earlier". Note this is an *incremental* lead-time gain: EBA GL/2020/06 already forces banks to run *some* EWI framework, so the baseline is not zero.

> **Assumption B3:** loss reduction conditional on earlier detection = 25% (conservative) / 40% (optimistic). Levers: cure without default; cancelling undrawn commitments before they are drawn; collateral or guarantee top-up; better-priced restructuring.

| | Conservative | Optimistic |
|---|---|---|
| Loan book | €20,000m | €20,000m |
| × cost of risk 0.48% (EBA, EU average) | €96.0m | €96.0m |
| × monitorable share (B1) | 60% → €57.6m | 75% → €72.0m |
| × cases moved earlier (B2) | 15% → €8.64m | 30% → €21.6m |
| × loss reduction on those cases (B3) | 25% → **€2.16m/yr** | 40% → **€8.64m/yr** |
| **Per borrower per year** (÷4,000) | **€540** | **€2,160** |
| **Per € of exposure** | **1.1 bps** | **4.3 bps** |
| **As a reduction in cost of risk** | 48 → 46.9 bps (**−2.3%**) | 48 → 43.7 bps (**−9.0%**) |

**Pool B — IFRS 9 staging, in both directions.** On a €20bn book, applying the EU CRE/SME-weighted Stage 2 share of ~15% gives **€3.0bn in Stage 2**. If evidence-backed re-staging returns even **5% (€150m)** to Stage 1 — the direction the EBA says banks are "reluctant" to move in — the provision release is the lifetime-vs-12-month ECL differential on €150m. **At an assumed 100bps differential that is a ~€1.5m one-off release.** Flag clearly: *the 100bps ECL step is an unsourced modelling assumption, not a published figure.* Include this pool only in the optimistic case, and present it as *defensible staging in both directions* — earlier into Stage 2 when evidence supports it, back out when it does not — which is precisely what the EBA asked supervisors to promote.

**Pool C — analyst time (small, but the fastest sign-off).** EBA GL requires covenant-certificate collection, ratio recomputation, watch-list reporting to the head of risk and the management body. 4,000 borrowers × 4 covenant tests/yr × 45 minutes manual ≈ **12,000 hours ≈ 6.7 FTE**. Automate 60% → **4 FTE × €90k = €360k/yr**. This one is auditable from timesheets and is what a CFO actually signs.

| Total | Conservative | Optimistic |
|---|---|---|
| Pool A — loss avoided | €2.16m | €8.64m |
| Pool B — staging release | — (excluded) | €1.50m |
| Pool C — FTE | €0.36m | €0.36m |
| **Annual value** | **€2.52m** | **€10.50m** |
| Annual cost (platform + 2 FTE run) | €1.00m | €1.00m |
| **Net / ROI multiple** | **€1.52m / 2.5×** | **€9.50m / 10.5×** |
| **Scaled to a €400bn G-SIB corporate book (×20)** | **≈ €43m/yr net** | **≈ €210m/yr net** |

**The cross-check.** My optimistic case cuts cost of risk by 9.0%. McKinsey's published claims for credit-risk digitisation projects are materially higher — a 10–20% reduction in the cost of risk, 20–40% lower credit losses from high-performance decisioning models, ~25% improvement in EWS predictability from ML, and best-practice banks identifying risky customers **six to nine months** before serious problems and cutting watch-list unsecured exposure by ~60% within nine months versus ~20% at average banks. **I must be straight about these: I could not open either McKinsey document.** `mckinsey.com/~/media/.../credit_monitoring_for_competitive_advantage(1).ashx` returned HTTP 403 to a direct download and timed out twice through a fetching proxy; the companion article page timed out as well. The claims are visible in search indexes of those McKinsey URLs but **I did not read the source**, so treat them as **[UNVERIFIED — indexed but not opened]**. Use them only as a ceiling ("even the consultancy claims are 2–4× our number"), never as the model's foundation.

**The claim I explicitly could not source, and you should not fake.** The task asked for "how much loss is avoided per month of earlier detection". **There is no traceable published elasticity for this.** I found no regulator, standards body or peer-reviewed source that quantifies loss avoided per month of lead time. This is a genuinely useful negative finding: if a competing team puts "€X per month of earlier detection" on a slide, they either have a proprietary study or they made it up. The honest construction is B2 × B3 above — an explicit, attackable pair of assumptions — and saying out loud that the elasticity is unpublished is a *credibility gain* in front of a CTO panel, not a weakness. It also implies a strong product feature: the system should *measure its own realised lead time* and let the bank derive the elasticity from its own book. That is a defensible thing to build in seven days and a genuinely good demo moment.

### 4. Buyer, budget, urgency

| | Detail |
|---|---|
| **Economic buyer** | CRO office / Credit Risk; the portfolio-monitoring or watch-list unit; Head of Credit Risk Models; secondarily Finance (IFRS 9 / ECL) and Internal Audit |
| **User** | Relationship managers and credit analysts — the people §272 makes responsible for watch-list follow-up |
| **Budget line** | Risk technology / regulatory change. **Recurring, ring-fenced, and defended in supervisory dialogue** — the single best budget line in a bank |
| **Regulatory forcing** | EBA/GL/2020/06 §§267–274 (remediation window closed 30 Jun 2024); RBI Stressed Assets Directions 2025 (28 Nov 2025); RBI FRM Master Directions (15 Jul 2024, EWS integrated with CBS); BCBS d403; IFRS 9 forward-looking ECL |
| **Why this year** | Two things are live *right now*: (1) the RBI Directions of **28 November 2025** are months old and Indian banks are implementing against them; (2) the EBA in **December 2025** told supervisors to press banks on Stage 2 transparency and forward-looking indicators, against €1.6tn of Stage 2 exposure the regulator suspects reflects "model deficiencies" |

### 5. Relevance to a large IT-services firm

**Verified:** Infosys' Form 20-F for fiscal 2026 (filed with the SEC on 15 June 2026) reports revenue by business segment as **Financial Services 27.9%** (FY2025: 27.7%; FY2024: 27.4%), Manufacturing 16.3%, EURS 13.3%, Retail 12.9%, Communication 12.2%, Hi-Tech 7.8%, Life Sciences 6.9%, other 2.7% — on **total revenues of $20,158m**. Financial Services is the largest single vertical by a wide margin, ≈ **$5.6bn/yr**. The same filing lists **Infosys Finacle** among its key products and platforms, describing it as "an industry leader in digital banking solutions… across journeys and workflows in core banking, digital engagement, lending, payments".

**Applied to TCS:** the structural point is the same for any Indian IT-services major — BFSI is the largest vertical and the firm owns a core-banking/financial-platform franchise (TCS BaNCS is the direct analogue to Finacle). **Caveat: tcs.com returned HTTP 403 to every attempt in this session** (press releases, investor pages, product pages), so TCS's own segment mix and BaNCS scale claims are **not verified here** — cite the Infosys 20-F as the verifiable evidence for the sector pattern, and verify TCS specifics from a TCS quarterly press release before the pitch.

**Why PS-04 is the better offering for a services major:**
1. It sells into the biggest existing vertical, to a buyer the firm already has relationships with.
2. It is **repeatable across every commercial bank and NBFC in every jurisdiction** — the *domain* is identical, only the covenant taxonomy and the reporting format change (CRILC in India, FINREP/COREP and the EBA EWI framework in the EU).
3. It has a **renewal cycle built in**: every new guideline (EBA update, RBI direction, Basel revision) is a new implementation project on the same platform. That is the classic services annuity.
4. It is defensible in a procurement bake-off because compliance is binary — the bank either can evidence its EWI framework to the supervisor or it cannot.

PS-17, by contrast, is horizontal (every vertical, no single owner), sold into fragmented budgets, and its most natural buyer at a services major is the firm's own commercial function rather than a client.

### 6. Value pools mapped to sub-problems

| Sub-problem | Value pool | Regulatory anchor |
|---|---|---|
| Covenant definition extraction (thresholds, testing frequency, exceptions) | Pool C (FTE) and the precondition for A | EBA §267–268 |
| Deterministic ratio computation from financials | Pool C; audit defensibility | EBA §267 (net debt/EBITDA, ICR, DSCR) |
| Multi-signal deterioration detection (utilisation, payments, treasury, industry) | Pool A (the lead time) | EBA §269, §274 |
| 30/60/90-day breach probability with drivers | Pool A and Pool B (staging evidence) | IFRS 9; EBA Dec 2025 on forward-looking indicators |
| Portfolio ranking by urgency × exposure | Pool A (intervention capacity is finite; ranking is what converts lead time into loss avoided) | EBA §270–272 (watch list) |
| Auditable warning trail | Pool B — the *release* direction is only bankable if the evidence survives audit | EBA §272 (management-body reporting); RBI CRILC |

---

## The one slide, drafted

### PS-17 — business impact

> **8.6%** — average share of contract value eroded, across 1,200+ organisations. Best performers ~3%; worst >20%.
> *World Commerce & Contracting + Deloitte, "The ROI of Contracting Excellence", June 2023*
>
> **24** — the number of separate systems that contract-related data sits in, on average, in a large organisation. The obligation is in one; the evidence is in the other twenty-three.
> *Deloitte, "Contract management lifecycle insights", 20 Sep 2023*
>
> **60 days, then forfeited** — the window to claim a Google Cloud service credit, with log-file evidence: *"If Customer does not comply with these requirements, Customer will forfeit its right to receive a Financial Credit."* AWS: claim by the end of the second billing cycle or be "disqualified".
> *cloud.google.com/compute/sla · aws.amazon.com/compute/sla*
>
> **Our model:** $13.4m–$36.4m recovered per year on a $2.0bn contracted-value book ($2,690–$7,280 per contract). Both cases sit *below* WorldCC's own published ROI curve. Assumptions on the slide, not in the appendix.

### PS-04 — business impact

> **€110bn** — new non-performing loan *inflows* at EU/EEA banks in H1 2025 alone. "NPL inflows remain significant and have not slowed down." SMEs alone: €32bn.
> *European Banking Authority, Risk Assessment Report, December 2025*
>
> **€1.6tn / 9.4%** — exposures sitting in IFRS 9 Stage 2, near the highest level since IFRS 9 began. CRE 17.1%, SMEs 14.9%. The EBA's own read: this is "hardly linked to" NPLs or cost of risk and may reflect "model deficiencies"; supervisors should "promote the use of forward-looking indicators".
> *Same source*
>
> **A mandate, not a wish** — EBA/GL/2020/06 §267: covenant adherence "should be utilised as early warning tools. Early detection of deviations is key." §269: EWIs "supported by an appropriate IT and data infrastructure". Applied from 30 Jun 2021; the infrastructure-remediation window closed **30 Jun 2024**. India: RBI Directions of **28 Nov 2025** — SMA on default, CRILC monthly, weekly default report, 30-day review, 180-day resolution.
>
> **Our model:** €2.5m–€10.5m per year on a €20bn book (1.1–4.3 bps of exposure; a 2.3%–9.0% cut to a 48 bps cost of risk) → **€43m–€210m/yr at G-SIB scale**. Deliberately 2–4× below the published consultancy claims.

---

## Head-to-head verdict for this lane

| Criterion | PS-17 | PS-04 |
|---|---|---|
| Headline loss number is primary-sourced | Yes — WorldCC/Deloitte 2023, n>1,200 | Yes — EBA, Federal Reserve, RBI |
| Source is a regulator or an industry body | Industry body (with a commercial interest) | **Regulators and central banks** |
| Loss number is a *flow* the product can act on | Partly — 8.6% is a stock-like average | **Yes — €110bn/half-year of NPL inflow** |
| Value model survives halving every assumption | Yes ($3.4m vs $0.92m cost) | Marginally (€1.0m value vs €1.0m cost) |
| Regulation forces the spend | Weak (DORA, financial sector, partial) | **Very strong, four separate mandates** |
| Budget line exists and recurs | Discretionary — but **can be sold on contingency** | **Recurring, ring-fenced, supervisor-facing** |
| Buyer is single and identifiable | No — procurement / legal ops / RA / VMO | **Yes — the CRO office** |
| Maps to an IT-services major's largest vertical | No (horizontal) | **Yes — BFSI, 27.9% of Infosys FY26 revenue** |
| Repeatable, sellable offering with a renewal cycle | Moderate | **High — every new guideline is a project** |
| Incumbent crowding | High (Icertis, Sirion, Agiloft, DocuSign, SAP, Coupa) | High but more fragmented and bank-internal |

**Scores on this lane's criterion — strength and defensibility of the business case:**

- **PS-04: 8.5 / 10.** Regulator-published loss data in three jurisdictions; a €110bn/half-year flow that a regulator says is not slowing; €1.6tn of Stage 2 with the regulator openly questioning banks' ability to evidence it; and a mandate — EBA §§267–274 — that reads like the problem statement's requirements document, whose remediation deadline has already passed. The single largest vertical of the host company's industry. **Deductions:** headline *stock* metrics (NPL ratio 1.84%, US C&I charge-offs 0.58%, India GNPA 2.2%) are at multi-decade lows, so "the sky is falling" is not available as a framing; the per-unit value (€540–€2,160/borrower/yr) only becomes impressive at portfolio scale; the crucial "loss avoided per month of earlier detection" elasticity is **not published anywhere**; and the conservative case is only 2.5× on cost, which is thin.
- **PS-17: 6.5 / 10.** A real, well-attested headline number with a named methodology and a sample of 1,200+, an unusually good ROI multiple (14.6× conservative), and one commercial advantage PS-04 does not have — **it can be sold on contingency with no budget approval**. **Deductions:** the number is self-reported by a membership body co-authoring with a Big Four firm that sells the remedy; there is no regulator forcing the spend outside DORA's partial ICT-contract scope; the buyer is diffuse; the demo-able sliver (SLA credits, ~0.14% of spend) is the *smallest* pool, which invites a jury to under-size the whole thing; and the software category it will be mentally filed under is a $3.3bn market with six entrenched incumbents.

**Margin: PS-04 wins by 2 points.** Not because the loss is bigger — on a per-enterprise basis PS-17's is arguably bigger and certainly easier to recover — but because PS-04's number comes from a regulator, its buyer has a name and a standing budget, and a supervisor will ask the buyer about it whether or not they buy anything.

### What would change the verdict

1. **If the jury is composed of enterprise-IT CTOs rather than BFSI CTOs**, PS-17's horizontal applicability and 14.6× ROI may land harder than a compliance argument they do not personally own. PS-04's regulatory moat is only a moat if the room cares about regulators.
2. **If a primary source can be found for SLA-credit under-claiming** — a real study rather than a licensing consultancy's ~30 engagements — PS-17's most demo-able pool becomes citable and the whole pitch tightens. Worth 30 minutes of searching before the pitch.
3. **If TCS's own segment mix turns out to be materially less BFSI-weighted than Infosys' 27.9%**, the "maps to the book of business" argument weakens. Verify from a TCS quarterly press release; tcs.com blocked every attempt here.
4. **If a credit event breaks between now and the finals** (CRE, private credit, or an NBFI transmission — the EBA flags rising bank exposure to non-bank financial institutions in third countries), PS-04's stock-metric weakness disappears overnight and the score goes to 9.5.
5. **If the team can build the lead-time measurement loop** — the system measuring its own realised days-of-warning and letting the bank derive the loss elasticity from its own book — PS-04 stops needing the unpublished elasticity at all, and gains a genuinely novel, demo-able artefact. This is the highest-leverage business-case improvement available in the seven-day window.

---

## Risks and open questions

1. **The 8.6%/9.2% chain has one unverifiable link.** The 2023 update is solid and I read it end to end. The 2014 origin document (*Overcoming the 10 Pitfalls of Contracting*) returned 403 from both hosts. Present 8.6% (2023, n>1,200) and mention 9.2% only as the historical baseline the 2023 study updated.
2. **WorldCC has a commercial interest** in the number being large; Deloitte sells contracting-excellence services. This does not make it wrong — it is the only large-sample study in existence — but say it before a juror does.
3. **The McKinsey early-warning statistics are indexed but unread.** Two separate URLs, three attempts, 403 and two timeouts. Do not put "20–40% lower credit losses" on a slide until someone opens the document.
4. **The India numbers rest on secondary reporting.** The RBI FSR December 2025 PDF is CAPTCHA-gated. Re-derive GNPA, slippage and stress-test projections from the FSR itself before the pitch.
5. **The RBI Fraud Risk Management clause numbers (8.3, 8.3.1, 8.3.3) come from a law-firm summary,** not the RBI text. The substance is well corroborated; the clause references are not independently verified.
6. **No published elasticity exists for loss avoided per month of earlier detection.** This is the central quantitative gap in PS-04's business case and cannot be closed with a citation. Handle it by making B2 and B3 explicit assumptions and by building self-measurement into the product.
7. **Assumption A1/A2 and B1/B2/B3 are judgements, not findings.** Every number in both ROI models is linear in them. Put them on the slide. A CTO who can re-run your model in their head trusts you more than one who cannot.
8. **Double-counting risk in PS-17.** Contract-compliance recovery audits already recover some of this pool in most large enterprises. The sale is displacement plus continuity, not a greenfield claim.
9. **TM Forum telecom revenue-assurance figures were not verifiable.** Search indexes attribute ~1.5% of revenue leakage (2019/20 survey), a fall to 0.52% (2025), and a 51% average recovery value (2017/18) to TM Forum reports. The 2017/18 PDF returned 403. **Do not cite these.**
10. **Currency and vintage mixing.** The models above mix USD (PS-17, Fed data) and EUR (PS-04, EBA data) deliberately, because that is where the primary data is. Normalise before the slide or state the convention.

---

## Sources

Opened and read in full or in substantial part:

1. World Commerce & Contracting + Deloitte, *The ROI of Contracting Excellence*, © WorldCC 2023 (June 2023) — https://passle-net.s3.amazonaws.com/Passle/5d1eec76989b6e0f3cff1041/MediaLibrary/Document/2023-08-04-13-34-26-203-ROI-of-contracting-excellence.pdf — **full text extracted.** Source of 8.6% / 9.2% / 3% / >20%, n>1,200, the 2–4% vs 15%+ sector figures, and the Figure 10 ROI curve with its caveat.
2. Deloitte US, *Contract management lifecycle insights*, page dated 20 Sep 2023 — https://www.deloitte.com/us/en/services/tax/articles/contract-management-lifecycle-insights.html — corroborates 8.6%/9.2%; source of "24 different systems".
3. World Commerce & Contracting, *Contract Management Whitepaper — August 2025* — https://info.worldcc.com/contract-management-aug-2025 — 8.6%, ~3% best, 15%+ worst; **no sample size stated**.
4. Google Cloud, *Compute Engine Service Level Agreement*, last modified 4 March 2025 — https://cloud.google.com/compute/sla — "Customer Must Request Financial Credit", 60 days, log-file evidence, forfeiture; credit bands 10%/25%/100%.
5. Amazon Web Services, *Amazon Compute Service Level Agreement* — https://aws.amazon.com/compute/sla/ — claim by end of second billing cycle, disqualification for incomplete evidence; credit bands 10%/30%/100%.
6. Precedence Research, *Contract Lifecycle Management Software Market*, page dated 11 Feb 2026 — https://www.precedenceresearch.com/contract-lifecycle-management-software-market — $2.96bn (2025), $3.32bn (2026), $8.84bn (2035), 11.56% CAGR, NA 40%.
7. Redress Compliance, *Google Cloud SLA 2026: Uptime Tiers and Credit Ladder*, 7 Aug 2026 — https://redresscompliance.com/google-cloud-sla-uptime-contract-terms — "60 to 80 percent of eligible credits went unclaimed across our reviews", **self-reported, based on 25–35 engagements, no external source cited**.
8. Regulation (EU) 2022/2554 (DORA), consolidated text — https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32022R2554 — Arts. 28–30: register of information; mandatory contractual provisions; quantitative and qualitative service level targets; exit strategies.
9. EIOPA, *Digital Operational Resilience Act (DORA)* — https://www.eiopa.europa.eu/digital-operational-resilience-act-dora_en — "It entered into application on 17 Jan 2025".
10. European Banking Authority, *Risk Assessment Report — December 2025* — https://www.eba.europa.eu/sites/default/files/2025-12/8acb45c4-912b-4f0f-b887-37b8fc058779/Risk%20Assessment%20Report%20Autumn%202025.pdf — **full text extracted.** NPL €373bn / 1.84% / coverage 41.7%; NPL inflows ~€110bn H1 2025 (SMEs €32bn) vs outflows ~€114bn; Stage 2 €1.6tn = 9.4% (CRE 17.1%, SME 14.9%, consumer 11.5%, mortgages 7.9%); NPL by segment (consumer 5.4%, SME 4.6%, CRE 4.2%, mortgages 1.4%); cost of risk 0.48%; the Stage 2 "model deficiencies" passage and the forward-looking-indicators recommendation.
11. European Banking Authority, *Final Report — Guidelines on loan origination and monitoring* (EBA/GL/2020/06) — https://www.eba.europa.eu/sites/default/files/document_library/Publications/Guidelines/2020/Guidelines%20on%20loan%20origination%20and%20monitoring/884283/EBA%20GL%202020%2006%20Final%20Report%20on%20GL%20on%20loan%20origination%20and%20monitoring.pdf — **full text extracted.** §§266–274 quoted above; application dates and the 30 June 2024 data-gap window.
12. Federal Reserve Board, *Charge-Off and Delinquency Rates on Loans and Leases at Commercial Banks* (charge-offs, SA), release 25 Aug 2026 — https://www.federalreserve.gov/releases/chargeoff/chgallsa.htm — 2026:Q2 C&I 0.58%, CRE 0.14%, total 0.55%.
13. Federal Reserve Board, same release, delinquency table — https://www.federalreserve.gov/releases/chargeoff/delallsa.htm — 2026:Q2 C&I 1.27%, CRE 1.53%, total 1.42%.
14. Federal Reserve Board, *H.8 Assets and Liabilities of Commercial Banks in the United States*, release 21 Aug 2026 (week ending 12 Aug 2026) — https://www.federalreserve.gov/releases/h8/current/default.htm — C&I loans $2,933.1bn; total loans and leases $13,982.6bn.
15. Reserve Bank of India, *Reserve Bank of India (Commercial Banks – Resolution of Stressed Assets) Directions, 2025*, RBI/DOR/2025-26/165, 28 Nov 2025 — https://www.rbi.org.in/Scripts/BS_ViewMasDirections.aspx?id=13145 — ¶15 SMA on default; ¶16 all loans; ¶17 CRILC monthly ≥ ₹5 crore; ¶18 weekly default report; ¶23 30-day Review Period; ¶41 180-day resolution.
16. Reserve Bank of India, *Prudential Framework for Resolution of Stressed Assets*, RBI/2018-19/203, 7 June 2019 — https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=11580 — SMA-0/1/2 definitions; CRILC thresholds and frequency; 30-day review.
17. Economic Laws Practice, *RBI Revised Master Directions on Fraud Risk Management: July 2024* — https://elplaw.in/wp-content/uploads/2024/07/RBI-Revised-Master-Directions-on-Fraud-Risk-Management-July-2024.pdf — **full text extracted.** Clause-referenced summary of the 15 July 2024 Master Directions: EWS integrated with CBS (8.3), RFA (8.3.1), CRILC reporting within 7 days (8.3.3), 36 circulars withdrawn, extension to RRBs / rural co-ops / HFCs. *Law-firm summary, not the RBI original.*
18. Basel Committee on Banking Supervision, *Prudential treatment of problem assets — definitions of non-performing exposures and forbearance*, BCBS d403, 4 April 2017 — https://www.bis.org/bcbs/publ/d403.htm
19. Infosys Limited, *Form 20-F for fiscal year ended 31 March 2026*, filed 15 June 2026 — https://www.sec.gov/Archives/edgar/data/1067491/000119312526270520/infy-20260331.htm — **full text extracted.** Financial Services 27.9% of revenue (FY26), total revenues $20,158m; Finacle listed among key products and platforms.
20. Cheng, Yang, Wang, Xiang, Zhang, *Research on Credit Risk Early Warning Model of Commercial Banks Based on Neural Network Algorithm*, arXiv:2405.10762 (May 2024) — https://arxiv.org/abs/2405.10762 — establishes that academic EWS modelling exists; **the abstract reports no quantitative accuracy figures**.

Attempted and **not** verified — do not cite these as read:

21. WorldCC/IACCM, *Overcoming the 10 Pitfalls of Contracting* (the 2014 origin of the 9.2% figure) — https://www.worldcc.foundation/static/207508a1-01d8-4c47-bd8bd930e3a3e39c/Overcoming-the-10-pitfalls.pdf and https://www.worldcc.com/Portals/IACCM/Resources/10655_0_Overcoming-the-10-pitfalls.pdf — **both HTTP 403.**
22. WorldCC, *Benchmark Report 2023* — https://www.worldcc.com/Portals/IACCM/Reports/Benchmark-report-2023.pdf — **HTTP 403.**
23. McKinsey & Company, *A better way for banks to monitor credit* / "credit monitoring for competitive advantage" working paper — https://www.mckinsey.com/~/media/mckinsey/business%20functions/risk/our%20insights/a%20better%20way%20for%20banks%20to%20monitor%20credit/credit_monitoring_for_competitive_advantage(1).ashx — **403 on direct download, timeout via proxy (2 attempts).** Source of the widely-repeated "six to nine months earlier" and "60% vs 20% watch-list unsecured-exposure reduction" claims. **[UNVERIFIED — indexed but not opened.]**
24. McKinsey & Company, *The value in digitally transforming credit risk management* — https://www.mckinsey.com/capabilities/risk-and-resilience/our-insights/the-value-in-digitally-transforming-credit-risk-management — **timeout (2 attempts).** Source of "10–20% reduction in cost of risk" and "20–40% lower credit losses". **[UNVERIFIED.]**
25. Reserve Bank of India, *Financial Stability Report December 2025* — https://rbidocs.rbi.org.in/rdocs/PublicationReport/Pdfs/0FSRDEC25D1EB9AAEE5724BD5A3E068490996BAD5.PDF — **CAPTCHA-gated on both direct download and proxy fetch.** India GNPA (~2.2% at end-Sep 2025), the March 2027 baseline projection (1.9%) and the 3.2%/4.2% adverse scenarios reach me only through secondary reporting. **[UNVERIFIED — verify before use.]**
26. TM Forum, *Revenue Assurance Survey Report 2017/18* — https://www.tmforum.org/wp-content/uploads/2018/03/TMF_Revenue-Assurance_Survey_201718_v1_1.pdf — **HTTP 403.** Telecom revenue-leakage percentages (~1.5%, 0.52%, 51% recovery) are **not verified and should not be cited.**
27. Tata Consultancy Services — every attempted page (quarterly press release, investor financial statements, TCS BaNCS product page, press-release PDFs on the TCS CDN) returned **HTTP 403**. TCS's own segment mix and BaNCS scale claims are **unverified in this session**; the Infosys 20-F is used as the verifiable sector proxy.
