#!/usr/bin/env bash
# Download the Phase 4 ingestion fixtures and verify each against its recorded digest.
# Idempotent: a file that is already present and correct is left alone.
#
# A checksum mismatch is a hard failure, not a warning. These documents are the baseline
# every retrieval number is measured against; one silently changing upstream would move the
# baseline without anyone noticing, which is worse than not having it.
set -euo pipefail
cd "$(dirname "$0")"

FIXTURES=(
  "bert-two-column.pdf|https://arxiv.org/pdf/1810.04805v2|5692a5514787a8c6727b4ff3b726a3385798bc68e12138d1d4af83947e2acf6e"
  "transformer-single-column.pdf|https://arxiv.org/pdf/1706.03762v7|bdfaa68d8984f0dc02beaca527b76f207d99b666d31d1da728ee0728182df697"
  "irs-1040-instructions-tables.pdf|https://www.irs.gov/pub/irs-pdf/i1040gi.pdf|482e9c487c608f1bbeaceef35bc3c0933e8b35443cfff447e4279d590468364a"
  "census-income-tables.pdf|https://www.census.gov/content/dam/Census/library/publications/2023/demo/p60-280.pdf|6c5d9798c31150da219cbb3cf35d478cb246e1d874eaf05f0b511336fb3b4537"
)

fail=0
for entry in "${FIXTURES[@]}"; do
  IFS='|' read -r name url want <<<"$entry"
  if [[ -f "$name" ]]; then
    have=$(shasum -a 256 "$name" | cut -d' ' -f1)
    if [[ "$have" == "$want" ]]; then echo "ok       $name (already present)"; continue; fi
    echo "REFETCH  $name (digest differs)"
  fi
  curl -sSL --max-time 120 -A "Mozilla/5.0" -o "$name" "$url" || { echo "FAILED   $name — download"; fail=1; continue; }
  if [[ "$(file -b --mime-type "$name")" != "application/pdf" ]]; then
    echo "FAILED   $name — not a PDF (the server likely returned an error page)"; rm -f "$name"; fail=1; continue
  fi
  have=$(shasum -a 256 "$name" | cut -d' ' -f1)
  if [[ "$have" != "$want" ]]; then
    echo "FAILED   $name — digest mismatch"; echo "           want $want"; echo "           have $have"; fail=1; continue
  fi
  echo "ok       $name (downloaded)"
done
exit $fail
