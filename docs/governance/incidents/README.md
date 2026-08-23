# Incident register

**No incidents have been recorded.** This register is empty, and that is a statement about
a platform that has never run in production — not a claim that nothing has ever gone wrong.

It exists now, before the first incident, so that the format is fixed while nobody is under
pressure. The triage, containment and review steps it records are defined in
[`../incident-response.md`](../incident-response.md).

## Format

One file per incident, named `YYYY-MM-DD-short-slug.md`, with these headings and no others:

```markdown
# YYYY-MM-DD — short title

- **Severity:** S1 / S2 / S3 / S4 (see incident-response.md §1)
- **Opened:** ISO-8601 UTC, and by which signal (§2 of the plan)
- **Closed:** ISO-8601 UTC
- **Tenants in scope:**
- **Data subjects in scope:**

## What happened
## How it was detected
## Containment taken
(which lever from §4, and when)
## Who was notified, and when
## Which control should have caught this
(existed and failed / did not exist / risk accepted — one of the three, with the follow-up)
## Follow-up
(links to the commit, the test added, or the compliance row changed)
```

## Rules

- **An incident is written up even when the outcome was harmless.** A gate that failed to
  fire and proposed nothing is still an S2.
- **The register is never edited to look better after the fact.** A correction is a new
  paragraph with a date, not a rewrite — the same discipline the memory layer applies to a
  corrected fact.
- **An empty register is only honest while it is true.** If this file still says "no
  incidents" after the platform has served real traffic, that is itself a finding for the
  next review.
