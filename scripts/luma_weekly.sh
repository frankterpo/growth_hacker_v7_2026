#!/usr/bin/env bash
# Luma GTM weekly cron entrypoint for Hermes.
#
# One-shot pipeline:
#   layer 1 — discover + paginate            (luma_gtm_harvest.py)
#   diff    — vs. previous run's events.jsonl (luma_diff.py)
#   layer 2 — hydrate /event/get for delta + full set (luma_hydrate_events.py)
#   score   — topical lead extraction        (luma_score_leads.py)
#
# Layer 3 (guest list, auth-gated) is a separate browser-use job — see
# LUMA_GUESTLIST_BROWSER_USE.md. It reads `leads/events_scored.jsonl` and
# filters on `event.show_guest_list == true`.
#
# Usage:
#   scripts/luma_weekly.sh
#   # or override defaults:
#   LUMA_REGIONS="discplace-QCcNk3HXowOR97j discplace-OTHER" \
#   LUMA_RUN_ROOT=".gstack/luma/runs" \
#   LUMA_MAX_PLACES=0 LUMA_MAX_PAGES=0 \
#   scripts/luma_weekly.sh
#
# Idempotent within a run id (ISO week). Re-running the same week appends
# to the hydrated jsonl (resumable) and rewrites the scored leads.

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PY="${PYTHON_BIN:-/usr/bin/python3}"

LUMA_REGIONS="${LUMA_REGIONS:-discplace-QCcNk3HXowOR97j}"   # space-separated featured_place_api_id list
LUMA_RUN_ROOT="${LUMA_RUN_ROOT:-.gstack/luma/runs}"
LUMA_MAX_PLACES="${LUMA_MAX_PLACES:-0}"   # 0 = all cities in the region
LUMA_MAX_PAGES="${LUMA_MAX_PAGES:-0}"     # 0 = paginate until has_more is false
LUMA_PAGE_LIMIT="${LUMA_PAGE_LIMIT:-50}"
LUMA_HYDRATE_WORKERS="${LUMA_HYDRATE_WORKERS:-4}"
LUMA_SLEEP="${LUMA_SLEEP:-0.15}"
LUMA_MIN_LEAD_SCORE="${LUMA_MIN_LEAD_SCORE:-1}"
LUMA_TOPIC_PACK="${LUMA_TOPIC_PACK:-institutional}"

# Graph enrichment (Cala + Specter). The unified enricher always runs when
# LUMA_ENABLE_GRAPH=1; per-provider toggles let you turn one off without
# rewriting the cron. See scripts/SPECTER_LOOKUP.md for the one-time Specter
# session + endpoint setup that flips Specter from `unavailable` to `ok`.
LUMA_ENABLE_GRAPH="${LUMA_ENABLE_GRAPH:-${LUMA_ENABLE_CALA:-0}}"  # 1 = run unified enricher
LUMA_DISABLE_CALA="${LUMA_DISABLE_CALA:-0}"             # 1 = skip Cala lookups
LUMA_DISABLE_SPECTER="${LUMA_DISABLE_SPECTER:-0}"       # 1 = skip Specter lookups
LUMA_GRAPH_MAX_ORGS="${LUMA_GRAPH_MAX_ORGS:-${LUMA_CALA_MAX_ORGS:-50}}"
LUMA_GRAPH_MAX_PEOPLE="${LUMA_GRAPH_MAX_PEOPLE:-${LUMA_CALA_MAX_PEOPLE:-100}}"
LUMA_GRAPH_MIN_NAME_SIM="${LUMA_GRAPH_MIN_NAME_SIM:-${LUMA_CALA_MIN_NAME_SIM:-0.55}}"
LUMA_GRAPH_WORKERS="${LUMA_GRAPH_WORKERS:-3}"

RUN_ID="${LUMA_RUN_ID:-$(date -u +%G-W%V)}"            # ISO week, e.g. 2026-W19
RUN_DIR="$LUMA_RUN_ROOT/$RUN_ID"
mkdir -p "$RUN_DIR"

# Find the most-recent prior run dir (lex sort, exclude current)
PREV_DIR=""
if [[ -d "$LUMA_RUN_ROOT" ]]; then
  while IFS= read -r dir; do
    [[ -z "$dir" ]] && continue
    [[ "$dir" == "$RUN_ID" ]] && continue
    PREV_DIR="$LUMA_RUN_ROOT/$dir"
  done < <(ls -1 "$LUMA_RUN_ROOT" 2>/dev/null | sort)
fi

echo "==> Luma weekly run: $RUN_ID"
echo "    regions:   $LUMA_REGIONS"
echo "    run_dir:   $RUN_DIR"
echo "    prev_dir:  ${PREV_DIR:-<none>}"
echo

# ---- Layer 1: harvest --------------------------------------------------------
REGION_ARGS=()
for r in $LUMA_REGIONS; do
  REGION_ARGS+=(--featured-place-api-id "$r")
done

echo "==> Layer 1: discover + paginate"
"$PY" scripts/luma_gtm_harvest.py \
  "${REGION_ARGS[@]}" \
  --out-dir "$RUN_DIR" \
  --pagination-limit "$LUMA_PAGE_LIMIT" \
  --max-places "$LUMA_MAX_PLACES" \
  --max-pages "$LUMA_MAX_PAGES" \
  --sleep "$LUMA_SLEEP"
echo

# ---- Diff vs. previous run ---------------------------------------------------
if [[ -n "$PREV_DIR" && -f "$PREV_DIR/events.jsonl" ]]; then
  echo "==> Diff vs $PREV_DIR/events.jsonl"
  "$PY" scripts/luma_diff.py \
    --prev "$PREV_DIR/events.jsonl" \
    --new  "$RUN_DIR/events.jsonl" \
    --out-dir "$RUN_DIR/diff"
  echo
else
  echo "==> Diff skipped (no prior run yet)"
  echo
fi

# ---- Layer 2: hydrate --------------------------------------------------------
echo "==> Layer 2: /event/get hydration (resumable)"
"$PY" scripts/luma_hydrate_events.py \
  --in  "$RUN_DIR/events.jsonl" \
  --out "$RUN_DIR/events_hydrated.jsonl" \
  --workers "$LUMA_HYDRATE_WORKERS" \
  --sleep "$LUMA_SLEEP"
echo

# ---- Score / lead extraction -------------------------------------------------
echo "==> Score + lead extraction (topic_pack=$LUMA_TOPIC_PACK)"
"$PY" scripts/luma_score_leads.py \
  --in "$RUN_DIR/events_hydrated.jsonl" \
  --out-dir "$RUN_DIR/leads" \
  --topic-pack "$LUMA_TOPIC_PACK" \
  --min-score "$LUMA_MIN_LEAD_SCORE"
echo

# ---- Optional: unified graph enrichment (Cala + Specter) ---------------------
if [[ "$LUMA_ENABLE_GRAPH" == "1" ]]; then
  GRAPH_FLAGS=()
  [[ "$LUMA_DISABLE_CALA"    == "1" ]] && GRAPH_FLAGS+=(--disable-cala)
  [[ "$LUMA_DISABLE_SPECTER" == "1" ]] && GRAPH_FLAGS+=(--disable-specter)

  echo "==> Graph enrichment (orgs) — Cala $([[ "$LUMA_DISABLE_CALA"    == "1" ]] && echo OFF || echo ON), Specter $([[ "$LUMA_DISABLE_SPECTER" == "1" ]] && echo OFF || echo ON)"
  "$PY" scripts/luma_graph_enrich.py \
    --in  "$RUN_DIR/leads/leads_orgs.jsonl" \
    --out "$RUN_DIR/leads/leads_orgs_graph.jsonl" \
    --kind org \
    --workers "$LUMA_GRAPH_WORKERS" --sleep 0.3 \
    --max "$LUMA_GRAPH_MAX_ORGS" \
    --cala-min-name-sim    "$LUMA_GRAPH_MIN_NAME_SIM" \
    --specter-min-name-sim "$LUMA_GRAPH_MIN_NAME_SIM" \
    "${GRAPH_FLAGS[@]}"
  echo

  echo "==> Graph enrichment (people) — Cala $([[ "$LUMA_DISABLE_CALA"    == "1" ]] && echo OFF || echo ON), Specter $([[ "$LUMA_DISABLE_SPECTER" == "1" ]] && echo OFF || echo ON)"
  "$PY" scripts/luma_graph_enrich.py \
    --in  "$RUN_DIR/leads/leads_people.jsonl" \
    --out "$RUN_DIR/leads/leads_people_graph.jsonl" \
    --kind person \
    --workers "$LUMA_GRAPH_WORKERS" --sleep 0.3 \
    --max "$LUMA_GRAPH_MAX_PEOPLE" \
    --cala-min-name-sim    "$LUMA_GRAPH_MIN_NAME_SIM" \
    --specter-min-name-sim "$LUMA_GRAPH_MIN_NAME_SIM" \
    "${GRAPH_FLAGS[@]}"
  echo
else
  echo "==> Graph enrichment skipped (set LUMA_ENABLE_GRAPH=1 to enable)"
  echo
fi

echo "==> Done. Artifacts in $RUN_DIR"
echo "    events.jsonl                       — layer 1 raw (this week)"
[[ -f "$RUN_DIR/diff/new_events.jsonl" ]] && echo "    diff/new_events.jsonl              — events new this week"
echo "    events_hydrated.jsonl              — layer 2 enriched"
echo "    leads/events_scored.jsonl          — every event with topic_score"
echo "    leads/leads_orgs.jsonl             — organising calendars (HubSpot Companies)"
echo "    leads/leads_people.jsonl           — hosts + featured_guests (HubSpot Contacts)"
[[ -f "$RUN_DIR/leads/leads_orgs_graph.jsonl"   ]] && echo "    leads/leads_orgs_graph.jsonl       — orgs + Cala+Specter graph_match"
[[ -f "$RUN_DIR/leads/leads_people_graph.jsonl" ]] && echo "    leads/leads_people_graph.jsonl     — people + Cala+Specter graph_match"
echo "    leads/score_summary.json           — counts + topic histogram"
