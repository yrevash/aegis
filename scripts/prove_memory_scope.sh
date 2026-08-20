#!/usr/bin/env bash
#
# Prove the memory subsystem's scoping against a live server.
#
# The claim the memory screens make is that one person's record is private to them
# and that an administrator's reach stops at its own tenant's edge. This script does
# not assert that — it asks the running API, as four different principals, and prints
# what each one actually got back.
#
# Six checks, in the order they build on each other:
#
#   1. a client's subject list holds exactly one subject — its own
#   2. that same client is refused (403) when it names a colleague's subject
#   3. its own facts read back fine, so the refusal above is scope and not breakage
#   4. its tenant's administrator DOES reach that colleague's record — the wider
#      reach, inside one tenant
#   5. the same administrator is refused (403) at the other tenant's edge
#   6. the platform admin reaches both tenants
#
# Usage:  scripts/prove_memory_scope.sh [base_url]     (default http://localhost:8110)
#
# Exits non-zero if any check does not answer the way the subsystem claims it will.

set -uo pipefail

BASE="${1:-http://localhost:8110}"
PASSWORD="${AEGIS_SEED_PASSWORD:-demo}"
FAILED=0

login() {
  curl -s -X POST "$BASE/v1/auth/login" \
    -H 'content-type: application/json' \
    -d "{\"username\":\"$1\",\"password\":\"$PASSWORD\"}" |
    python3 -c 'import sys,json; print(json.load(sys.stdin)["token"])'
}

# status_of <token> <path>  → the HTTP status only.
status_of() {
  curl -s -o /dev/null -w '%{http_code}' "$BASE$2" -H "authorization: Bearer $1"
}

check() {
  local label="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    printf '  PASS  %-58s %s\n' "$label" "$actual"
  else
    printf '  FAIL  %-58s expected %s, got %s\n' "$label" "$expected" "$actual"
    FAILED=1
  fi
}

# self_subject <token> → the subject key the SERVER composes for this principal.
# Never composed here: the subject shape is the isolation key, and a proof that
# builds its own would be asserting the thing it is meant to be checking.
self_subject() {
  curl -s "$BASE/v1/memory/subjects" -H "authorization: Bearer $1" |
    python3 -c 'import sys,json; print(json.load(sys.stdin)["self_subject"])'
}

CLIENT=$(login northwind.client)
ADMIN=$(login northwind.admin)
ANALYST=$(login northwind.analyst)
PLATFORM=$(login admin)

CLIENT_SUBJECT=$(self_subject "$CLIENT")
ANALYST_SUBJECT=$(self_subject "$ANALYST")
VCLIENT_SUBJECT=$(self_subject "$(login vertex.client)")

echo "── 1 · a client's subject list ────────────────────────────────────────────"
curl -s "$BASE/v1/memory/subjects" -H "authorization: Bearer $CLIENT" |
  python3 -m json.tool

echo
echo "── 2 · the same client, naming a colleague's subject ──────────────────────"
curl -s "$BASE/v1/memory/facts?subject=$ANALYST_SUBJECT" -H "authorization: Bearer $CLIENT" |
  python3 -m json.tool

echo
echo "── 3 · the tenant administrator, reading that same colleague ──────────────"
curl -s "$BASE/v1/memory/facts?subject=$ANALYST_SUBJECT" -H "authorization: Bearer $ADMIN" |
  python3 -c 'import sys,json; d=json.load(sys.stdin); print(d["subject"], "→", len(d["rows"]), "facts"); [print("   ", r["predicate"], "·", r["text"][:78]) for r in d["rows"]]'

echo
echo "── 4 · the same administrator, at the other tenant's edge ─────────────────"
curl -s "$BASE/v1/memory/facts?subject=$VCLIENT_SUBJECT" -H "authorization: Bearer $ADMIN" |
  python3 -m json.tool

echo
echo "── 5 · the platform admin, reaching both tenants ──────────────────────────"
curl -s "$BASE/v1/memory/subjects" -H "authorization: Bearer $PLATFORM" |
  python3 -c 'import sys,json; d=json.load(sys.stdin); print(len(d["rows"]), "subjects across every tenant, may_manage_others =", d["may_manage_others"]); [print("   ", r["subject"], r["label"], "tenant", r["tenant_id"], "·", r["fact_count"], "facts") for r in d["rows"]]'

echo
echo "── verdict ────────────────────────────────────────────────────────────────"
check "client reads its own record"                200 "$(status_of "$CLIENT"   "/v1/memory/facts?subject=$CLIENT_SUBJECT")"
check "client reaching a colleague is refused"     403 "$(status_of "$CLIENT"   "/v1/memory/facts?subject=$ANALYST_SUBJECT")"
check "tenant admin reaches its own tenant's user" 200 "$(status_of "$ADMIN"    "/v1/memory/facts?subject=$ANALYST_SUBJECT")"
check "tenant admin at the other tenant's edge"    403 "$(status_of "$ADMIN"    "/v1/memory/facts?subject=$VCLIENT_SUBJECT")"
check "platform admin reaches tenant 1"            200 "$(status_of "$PLATFORM" "/v1/memory/facts?subject=$CLIENT_SUBJECT")"
check "platform admin reaches tenant 2"            200 "$(status_of "$PLATFORM" "/v1/memory/facts?subject=$VCLIENT_SUBJECT")"

exit "$FAILED"
