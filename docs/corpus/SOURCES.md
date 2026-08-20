# The demo corpus

Four real, publicly-published documents in the platform's loaded domain —
**service request / case management**: refunds, billing disputes, complaint
handling and the deadlines that govern them.

They are small on purpose (4–10 pages each). The point is a corpus that is
*searchable and provably isolated*, not a corpus that is large.

Nothing here is written by us. No file in this directory is synthesised,
paraphrased or edited; each is the byte-for-byte PDF served by the publisher at
the URL below, fetched **2026-08-20**.

## Why these, and not an ML paper

The input guardrail and Azure's own content filter both read the question
*against the tenant's corpus and persona*. A service-request persona asked to
ground an answer in a machine-learning paper looks, to Azure's classifier, like
a prompt-injection attempt: the run is rejected with
`Response content blocked by label 'Jailbreak'` before an answer exists. The
corpus has to match the domain, so every document here is consumer-finance
complaint, refund and dispute material.

## The documents

| File | What it is | Pages | Source | Rights |
|---|---|---|---|---|
| `ftc-mail-internet-telephone-order-merchandise-rule-16cfr435.pdf` | 16 CFR Part 435 — the FTC's Mail, Internet, or Telephone Order Merchandise Rule. Shipment deadlines, the buyer's consent to a delay, and when a refund must be made. | 5 | <https://www.govinfo.gov/content/pkg/CFR-2023-title16-vol1/pdf/CFR-2023-title16-vol1-part435.pdf> (CFR, 1 Jan 2023 edition, via GPO govinfo) | Edict of the U.S. Government; a work of the United States Government, not subject to copyright (17 U.S.C. § 105). Free to reuse and redistribute. |
| `ftc-informal-dispute-settlement-procedures-16cfr703.pdf` | 16 CFR Part 703 — Informal Dispute Settlement Procedures. A complete case-management procedure: intake, investigation, the 40-day decision deadline, records to keep, and the annual audit. | 6 | <https://www.govinfo.gov/content/pkg/CFR-2023-title16-vol1/pdf/CFR-2023-title16-vol1-part703.pdf> | As above — U.S. Government work, public domain. |
| `reg-z-billing-error-resolution-12cfr1026-13.pdf` | 12 CFR § 1026.13 — Regulation Z, billing error resolution. What counts as a billing error, the 60-day notice window, and the creditor's two- and 90-day response duties. | 4 | <https://www.govinfo.gov/content/pkg/CFR-2023-title12-vol9/pdf/CFR-2023-title12-vol9-sec1026-13.pdf> | As above — U.S. Government work, public domain. |
| `cfpb-consumer-complaint-database-breakdown-2013.pdf` | CFPB, *Consumer Complaint Database Breakdown* (March 2013). Complaint volumes by product, sub-product and issue, and how a complaint moves through the Bureau's process. Mostly tables, which exercises the table-chunk path. | 10 | <https://files.consumerfinance.gov/f/201303_cfpb_Consumer-Complaint-Database-Fact-Sheet.pdf> | Consumer Financial Protection Bureau; a U.S. Government work. The Bureau publishes its material in the public domain except where a third-party credit is noted, and none is noted here. |

`files.consumerfinance.gov` is behind Akamai and returns **403** to a bare
`curl`. It serves the file to a request carrying an ordinary browser
`User-Agent`, `Accept-Language` and a `consumerfinance.gov` `Referer`.

## SHA-256 (as committed)

```
2dfbc30a04fa082d81e863df5258f27713c67451595783d0b5360ae275ba8b0e  cfpb-consumer-complaint-database-breakdown-2013.pdf
e60d236f7b216d3555059d7e81076e47530ccf5a7d8a59c2ac03b95e67d3ede1  ftc-informal-dispute-settlement-procedures-16cfr703.pdf
e00399c7262320e93c391b771ac166dfcc421cef539003d77c55b797f5f033d6  ftc-mail-internet-telephone-order-merchandise-rule-16cfr435.pdf
388be34e1ee1a7a5829d001171794efe50d7b740d0758035b1a6510c404da3d6  reg-z-billing-error-resolution-12cfr1026-13.pdf
```

These are the bytes; `documents.content_sha256` for each ingested row is the
same value, which is what makes "the row in the database is this file" checkable
rather than asserted.

## Which tenant holds which

Split across both tenants deliberately, so tenant isolation is something the
demo can *show* rather than claim.

| Tenant | Document |
|---|---|
| 1 (Northwind) | 16 CFR 435 — refunds and shipment deadlines |
| 1 (Northwind) | 16 CFR 703 — informal dispute settlement |
| 2 (Vertex) | 12 CFR 1026.13 — billing error resolution |
| 2 (Vertex) | CFPB complaint database breakdown |

A question that only 16 CFR 703 can answer ("how many days does the mechanism
have to decide a dispute?") must retrieve nothing for tenant 2, and a question
that only Regulation Z can answer ("how long does a consumer have to send a
billing-error notice?") must retrieve nothing for tenant 1.

## Re-fetching

```bash
UA='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'

curl -sL --compressed -H "User-Agent: $UA" \
  -o ftc-mail-internet-telephone-order-merchandise-rule-16cfr435.pdf \
  https://www.govinfo.gov/content/pkg/CFR-2023-title16-vol1/pdf/CFR-2023-title16-vol1-part435.pdf

curl -sL --compressed -H "User-Agent: $UA" \
  -o ftc-informal-dispute-settlement-procedures-16cfr703.pdf \
  https://www.govinfo.gov/content/pkg/CFR-2023-title16-vol1/pdf/CFR-2023-title16-vol1-part703.pdf

curl -sL --compressed -H "User-Agent: $UA" \
  -o reg-z-billing-error-resolution-12cfr1026-13.pdf \
  https://www.govinfo.gov/content/pkg/CFR-2023-title12-vol9/pdf/CFR-2023-title12-vol9-sec1026-13.pdf

curl -sL --compressed -H "User-Agent: $UA" -H 'Accept-Language: en-US,en;q=0.9' \
  -H 'Referer: https://www.consumerfinance.gov/' \
  -o cfpb-consumer-complaint-database-breakdown-2013.pdf \
  https://files.consumerfinance.gov/f/201303_cfpb_Consumer-Complaint-Database-Fact-Sheet.pdf
```

## Ingesting them

Ingest is a normal upload — the same path a user's file takes — so it needs a
tenant-bound principal (a platform admin has no tenant, and a chunk with no
tenant is a chunk no tenant can read):

```bash
TOKEN=$(curl -s localhost:8110/v1/auth/login -H 'content-type: application/json' \
  -d '{"username":"northwind.admin","password":"demo"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

curl -s localhost:8110/v1/documents -H "Authorization: Bearer $TOKEN" \
  -F 'file=@ftc-mail-internet-telephone-order-merchandise-rule-16cfr435.pdf;type=application/pdf' \
  -F 'doc_type=regulation' -F 'doc_date=2023-01-01'
```

**Temporal must be up** or the upload stores the bytes and returns 503 with the
ingest never started: `temporal server start-dev`.
