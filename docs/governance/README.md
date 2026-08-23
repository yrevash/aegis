# Governance artefacts

**NIST AI RMF is a management framework, and its GOVERN and MAP functions are satisfied by
written, maintained governance artefacts.** That is the intended form of compliance for
those two functions, not a substitute for one — which is why these five documents are cited
as the *mechanism* behind the GOVERN and MAP rows in `docs/compliance/README.md` and in
`backend/src/app/platform/compliance.py`, in the same way `aegis/src/aegis/governance/rls.py`
is the mechanism behind a tenancy row.

| Document | What it settles | Function |
|---|---|---|
| [`ai-policy.md`](ai-policy.md) | What Aegis is for, what it may not be used for, the acceptable-use boundary, model sourcing, and where human oversight binds. Every clause names the mechanism that enforces it. | GOVERN 1.1–1.4, 2.1, 6.1 |
| [`accountable-roles.md`](accountable-roles.md) | Who owns what, mapped to the five roles the software actually enforces and the guards that bound them — plus who really holds them today. | GOVERN 2.1–2.3 |
| [`incident-response.md`](incident-response.md) | Detection, triage, containment, notification, review — keyed to signals this system already emits. | GOVERN 1.5, 4.3 · MANAGE 4.1 |
| [`review-cadence.md`](review-cadence.md) | When each document is reviewed, by which role, and what triggers an off-cycle review. Sized for a two-person team. | GOVERN 1.3, 1.5 |
| [`context-and-impact.md`](context-and-impact.md) | The deployment context, the people affected, thirteen harms, and the mitigation that exists for each — four of them `NONE`. | MAP 1, 2, 3, 5 |
| [`incidents/`](incidents/README.md) | The incident register. Empty, with the format fixed. | — |

## Two rules these documents live under

1. **Every repository path cited in them is resolved against the real filesystem** by
   `backend/tests/api/test_governance_docs.py`, and each document is checked for the specific
   commitments its compliance control claims. Deleting or emptying one of these files fails
   the test suite. That is the same discipline `backend/tests/api/test_compliance.py` applies
   to every evidence reference on the compliance surface, and it is what makes a document
   citable as a control rather than as a promise.
2. **An absence is written down as an absence.** These documents say, in their own text,
   that CERT-In's six-hour reporting is not automated, that nothing is encrypted at rest,
   that there is no backup or restore, that there is no paging, and that four of the harms in
   the impact assessment have no mitigation at all. A governance pack with no gaps in it is
   the thing a reviewer stops believing first.

## What they do not claim

Nothing here is certified, audited or attested. These documents move NIST AI RMF's GOVERN
and MAP to `enforced` because GOVERN and MAP are documentation-and-process functions and
this is that documentation. They do **not** move ISO/IEC 42001 A.2 or A.3, which ask for a
management *system* with an external assurance loop around the same text, and they do not
move any DPDP or CERT-In row.
