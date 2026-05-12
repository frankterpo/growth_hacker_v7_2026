#!/usr/bin/env bash
# cala/test_endpoints.sh
#
# Reproducible smoke test for every documented Cala endpoint.
# Writes fresh fixtures to cala/fixtures/ and exits non-zero on contract breaks.
#
# Usage:
#   export CALA_API_KEY=clsk_...
#   bash cala/test_endpoints.sh
#
# Re-run after any Cala release; diff the fixtures to catch breaking changes.

set -euo pipefail

: "${CALA_API_KEY:?CALA_API_KEY env var is required}"
BASE="${CALA_BASE:-https://api.cala.ai}"

ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT="$ROOT/fixtures"
mkdir -p "$OUT"

pass=0
fail=0

# -- helpers -----------------------------------------------------------------
expect() {
  # expect <label> <expected_status> <actual_status>
  if [[ "$2" == "$3" ]]; then
    echo "  ✓ $1 → HTTP $3"
    pass=$((pass+1))
  else
    echo "  ✗ $1 → HTTP $3 (expected $2)"
    fail=$((fail+1))
  fi
}

post_json() {
  # post_json <url> <body> <out_file>
  curl -sS -o "$3" -w "%{http_code}" \
    -X POST "$1" \
    -H "X-API-KEY: $CALA_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$2"
}

get_url() {
  # get_url <url> <out_file>
  curl -sS -o "$2" -w "%{http_code}" \
    -H "X-API-KEY: $CALA_API_KEY" \
    "$1"
}

# -- 1. knowledge_search ------------------------------------------------------
echo "[1/5] knowledge_search (slow ~50s)…"
status=$(post_json "$BASE/v1/knowledge/search" \
  '{"input":"What are the most well-funded AI startups in Spain?"}' \
  "$OUT/01_knowledge_search.json")
expect "knowledge_search" "200" "$status"

# -- 2. knowledge_query -------------------------------------------------------
echo "[2/5] knowledge_query (slow ~50s)…"
status=$(post_json "$BASE/v1/knowledge/query" \
  '{"input":"startups.location=Spain.industry=AI.funding>5M"}' \
  "$OUT/02_knowledge_query.json")
expect "knowledge_query" "200" "$status"

# -- 3. entity_search (also gives us a UUID for steps 4–5) --------------------
echo "[3/5] entity_search…"
status=$(curl -sS -o "$OUT/03_entity_search.json" -w "%{http_code}" \
  -G "$BASE/v1/entities" \
  -H "X-API-KEY: $CALA_API_KEY" \
  --data-urlencode "name=OpenAI" \
  --data-urlencode "entity_types=Company" \
  --data-urlencode "limit=3")
expect "entity_search" "200" "$status"

ID=$(jq -r '.entities[0].id' "$OUT/03_entity_search.json")
echo "    using entity_id=$ID"

# -- 4. entity_introspection --------------------------------------------------
echo "[4/5] entity_introspection…"
status=$(get_url "$BASE/v1/entities/$ID/introspection" \
  "$OUT/04_entity_introspection.json")
expect "entity_introspection" "200" "$status"

# -- 5. retrieve_entity (default + filtered) ---------------------------------
echo "[5/5] retrieve_entity (default body)…"
status=$(post_json "$BASE/v1/entities/$ID" '{}' \
  "$OUT/05a_retrieve_entity_default.json")
expect "retrieve_entity (default)" "200" "$status"

echo "      retrieve_entity (filtered body)…"
status=$(post_json "$BASE/v1/entities/$ID" \
  '{"properties":["name","aliases","employee_count","headquarters_address"],"relationships":{"incoming":{"IS_CEO_OF":{"limit":3},"FOUNDED":{"limit":5}}}}' \
  "$OUT/05b_retrieve_entity_filtered.json")
expect "retrieve_entity (filtered)" "200" "$status"

# -- error matrix -------------------------------------------------------------
echo "[errors] 401 / 422 / 404 sanity…"

# 401 — missing key
no_key_status=$(curl -sS -o /dev/null -w "%{http_code}" \
  -X POST "$BASE/v1/knowledge/query" \
  -H "Content-Type: application/json" -d '{"input":"x"}')
expect "401 on missing key" "401" "$no_key_status"

# 422 — bad UUID
bad_uuid_status=$(post_json "$BASE/v1/entities/not-a-uuid" '{}' \
  "$OUT/err_422_bad_uuid.json")
expect "422 on bad UUID" "422" "$bad_uuid_status"

# 404 — unknown UUID
not_found_status=$(post_json "$BASE/v1/entities/00000000-0000-0000-0000-000000000000" '{}' \
  "$OUT/err_404_not_found.json")
expect "404 on unknown UUID" "404" "$not_found_status"

# -- summary ------------------------------------------------------------------
echo
echo "Results: $pass passed, $fail failed"
echo "Fixtures: $OUT"

[[ "$fail" -eq 0 ]]
