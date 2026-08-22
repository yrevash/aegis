#!/bin/bash
# Audit probe: send exactly 10 distinct queries, capture each run_finished payload.
TOK=$(curl -s -X POST http://localhost:8110/v1/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"northwind.analyst","password":"demo"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

OUT=/Users/yrevash/aegis/testdata/audit/ten_runs.jsonl
: > "$OUT"

QS=(
 "Name one primary colour. One word only."
 "What is the capital of France? One word."
 "How many days in a leap year? Number only."
 "Name one noble gas. One word."
 "What is 7 times 8? Number only."
 "Name one ocean. One word."
 "What is the chemical symbol for gold? Symbol only."
 "How many continents are there? Number only."
 "Name one prime number under 10. Number only."
 "What is the boiling point of water in Celsius? Number only."
)

for i in "${!QS[@]}"; do
  Q="${QS[$i]}"
  echo "--- [$((i+1))/10] $Q"
  curl -s -m 300 -N -X POST http://localhost:8110/v1/query \
    -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
    -d "$(python3 -c 'import json,sys;print(json.dumps({"query":sys.argv[1]}))' "$Q")" \
  | grep '^data: ' | sed 's/^data: //' \
  | python3 -c '
import sys,json
for line in sys.stdin:
    try: d=json.loads(line)
    except Exception: continue
    if d.get("type")=="run_finished":
        print(json.dumps(d))
' | tee -a "$OUT"
done
echo "=== captured $(wc -l < "$OUT") run_finished events ==="
