# Luma GTM harvest — Hermes / browser-use handoff

This is **not** a Cala/Specter/V7 pipeline. It is a **single motion**: pull public Luma discovery data for **AI (`cat-ai`)** and **Tech (`cat-tech`)** events across **all cities returned by** `discover/bootstrap-page`, with correct pagination, so Hermes can cron it without burning local LLM credits.

## Why Hermes “failed” before

The mobile/web app calls **`GET`** endpoints on `https://api2.luma.com`. Guessing `POST` or using `?cursor=` returns **405** or repeats the first page. The real continuation field is **`pagination_cursor`**, populated from the prior response’s **`next_cursor`**.

## Contract (verified 2026-05-12)

| Step | Method | URL | Notes |
|------|--------|-----|--------|
| 1 | `GET` | `/discover/bootstrap-page?featured_place_api_id={hub}` | `hub` = region row id (Europe, Asia & Pacific, …). Response includes flat `places[]` (~79 for Europe) with `place.api_id`, `place.slug`, `place.coordinate.{latitude,longitude}`. |
| 2 | `GET` | `/discover/get-paginated-events` | Query: `discover_category_api_id`, `latitude`, `longitude`, `pagination_limit`, optional `pagination_cursor`. |
| 3 | loop | same | While `has_more` is true: set `pagination_cursor` = previous `next_cursor`. |

**Page size:** use `pagination_limit` (e.g. `50`). Plain `limit` is ignored (server returns 50).

**Dedupe:** the same event can surface under different city coordinates; dedupe on `entries[].api_id` (event id).

## Rich public fields (no login)

Each `entries[]` row already includes **`hosts`**, **`featured_guests`**, **`guest_count`**, **`calendar`** (often the organizing calendar / personal org). That is enough for GTM prospecting without the guest list.

## Logged-in only

`GET /event/get-guest-list?event_api_id=…` → **401** when logged out. Hermes + browser-use with an authenticated session can layer this on later.

## Layer 2 — `/event/get` hydration (public, free)

`scripts/luma_hydrate_events.py` reads the JSONL from layer 1 and N+1 fetches
`GET /event/get?event_api_id=…` for each unique event. It projects to a slim
GTM record that adds, vs. the discover row:

- **full host roster** (often 2-3× larger than the 4 returned in discover)
- **organising `calendar`** (org slug, website, linkedin/twitter/instagram/tiktok handles, geo, plan) — the **GTM gold record**
- **`featured_infos`** — Luma's sponsor strip
- **`featured_guests`** — public spotlight users
- **`categories`** — event taxonomy
- **`sessions`** — multi-track agenda
- **`description`** — full body
- **location + ticket facets** — `country_code`, `full_address`, pricing, approval gate

Resumable (appends to output; skips ids already present). Expected drift:
some discover events return `400 event-deleted` mid-run — these are logged
to `hydrate_errors.json` next to the output.

## Layer 3 — guest list (auth-gated)

`/event/get-guest-list` returns **401** anonymous. See
[`LUMA_GUESTLIST_BROWSER_USE.md`](LUMA_GUESTLIST_BROWSER_USE.md) for the
browser-use recipe Hermes should run after layer 2.

## Topic packs

Topical regexes live in JSON files under `scripts/luma_topic_packs/` so you
can ship more than one without editing Python:

| Pack | Surface |
|---|---|
| [`institutional.json`](luma_topic_packs/institutional.json) | private markets, allocators / LP-GP, asset management, hedge fund, venture, fintech-generic, enterprise AI |
| [`fintech.json`](luma_topic_packs/fintech.json) | payments, embedded finance, neobanks, regtech / compliance, crypto / DeFi, AI for finance |

Pass `--topic-pack institutional` (default) or `--topic-pack fintech` to the
scorer, or a full path to a custom pack JSON. Each pack has shape:

```json
{
  "name": "institutional",
  "topics": {
    "<bucket>": { "weight": 4, "patterns": ["\\bregex1\\b", "..."] }
  }
}
```

Hermes can ship vertical packs (`healthcare_ai.json`, `legal_ai.json`, etc.)
by dropping a file in the directory — no code change needed.

## Layer 2b — topical lead extraction (Hermes-ready)

`scripts/luma_score_leads.py` reads `events_hydrated.jsonl` and emits three
JSONL files in `<run_dir>/leads/`:

- **`events_scored.jsonl`** — every event, plus `topics[]` + `topic_score` + `topic_hits{}`
- **`leads_orgs.jsonl`** — unique organising calendars (HubSpot **Companies**) ranked by total topic score across their events
- **`leads_people.jsonl`** — unique hosts ∪ featured_guests (HubSpot **Contacts**) that have at least one social handle, ranked by aggregate topic score across the events they appear at

Topic buckets (regex, tunable at the top of the file):

| Bucket | Weight | Examples of what it catches |
|---|---|---|
| `private_markets` | 4 | private markets, private equity / credit / debt, secondaries, GP-led, buyouts |
| `institutional_lp` | 4 | allocators, LPs, family offices, endowments, pensions, sovereign wealth, institutional capital |
| `asset_management` | 3 | asset / wealth management, AUM, RIAs, multi-asset |
| `hedge_fund` | 3 | hedge fund, quants, long/short, systematic trading |
| `venture` | 2 | venture capital, VC, seed/Series A-D, emerging managers |
| `finance_generic` | 2 | fintech, capital markets, M&A, IPO, investment banking |
| `enterprise_ai` | 1 | enterprise AI, CIO/CDO/CTO, data infra/platform, unstructured data, document intelligence |

The score is `weight × (1 + ln(hits))` per bucket, summed. The org and people
files are sorted by `topic_score DESC`, then `event_count DESC`. People are
filtered to those with at least one of `linkedin_handle`, `twitter_handle`,
or `website` so Hermes doesn't push ghost contacts into HubSpot.

## Layer 2c — graph enrichment (Cala + Specter, unified)

`scripts/luma_graph_enrich.py` calls **both** Cala and Specter per row and
emits one merged record with a `graph_match` verdict. This is what the
weekly cron runs. The single-provider `scripts/luma_cala_enrich.py` is kept
for Cala-only ad-hoc runs.

### Why both?

Cala is a knowledge-graph (clean entity identity, relationships, sturdy
`linkedin_url` verification) but its coverage is the institutional /
public-figure long tail. Specter is the live SaaS that tracks **movers**
— who just left a company for another company — and operational signals
Cala doesn't model. Running them together gives:

1. A high-confidence verdict when **either** provider matches the row.
2. The user's "**if it doesn't show up in Cala AND it doesn't show up in
   Specter, disregard**" rule — implemented honestly (see verdict ladder).
3. A `needs_review` bucket when one provider is offline so we never
   silently drop leads because of infrastructure state.

### Output: `graph_match` verdict ladder

Each row gets `cala`, `specter`, and `graph_match`. Filter on
`graph_match.verdict`:

| Verdict | Trust as fact? | Trigger |
|---|---|---|
| `verified` | ✅ ship to HubSpot | Either provider returned `confidence: verified` (LinkedIn-confirmed). |
| `strong` | ✅ ship | Either returned `exact_name_no_li_hint` or `absent_strong_name`. |
| `weak` | 🟡 review queue | Either returned `weak`. Use as a hint, not a fact. |
| `rejected` | ❌ drop | At least one provider rejected on LinkedIn handle mismatch and no other provider passed. |
| `disregard` | ❌ drop | **Every enabled provider explicitly returned `no_match`** — the user's rule. |
| `needs_review` | 🟡 retry / human | Mixed states. Includes the case where Specter is `unavailable` and Cala said `no_match` — we cannot honestly disregard until Specter is online. |
| `error` | n/a | All enabled providers errored — safe to retry. |
| `disabled` | n/a | Both providers were turned off via flags. |

The `providers` sub-object preserves each provider's raw status so Hermes
can route on it directly:

```json
"graph_match": {
  "verdict": "needs_review",
  "best_provider": null,
  "best_confidence": null,
  "providers": { "cala": "no_match", "specter": "unavailable" },
  "reason": "provider_unavailable_blocks_honest_disregard"
}
```

### Specter session readiness

Specter has no public REST API. The lookup module talks to it via the
authenticated browser session captured by `scripts/specter_harvest.py` and
a search-endpoint URL template observed once in the Network panel. Full
runbook in [`SPECTER_LOOKUP.md`](SPECTER_LOOKUP.md). Until that runbook is
executed once, every Specter call returns:

```json
{ "status": "unavailable",
  "reason": "no_authenticated_specter_session" | "no_endpoint_template_configured" }
```

…and the unified verdict downgrades from `disregard` to `needs_review`
for Cala-only misses. This is intentional — see "Why both?" above.

### Cala side (unchanged from prior layer)

`scripts/luma_cala_enrich.py` is still the source of truth for the Cala
provider. The unified enricher imports its `enrich_one()` function so
behavior, confidence ladder, and `is_personal` filter stay identical.

Per-provider confidence (each is mapped into the unified verdict above):

| Provider confidence | Used in unified verdict |
|---|---|
| `verified` (LinkedIn-confirmed) | → `verified` |
| `exact_name_no_li_hint` (name sim == 1.0, no LI hint) | → `strong` |
| `absent_strong_name` (LI hint, Cala has none, name sim ≥ 0.90) | → `strong` |
| `weak` (below either bar) | → `weak` |
| `rejected` (LinkedIn mismatch) | contributes to `rejected` verdict if nothing positive cleared it |
| `no_match` | contributes to `disregard` only if every provider returned `no_match` |
| `error` | unchanged |

Cala fields fetched per row:

- **orgs**: `aliases`, `description`, `website`, `headquarters_address`,
  `employee_count`, `founding_date`, `HAS_HEADQUARTERS_IN`,
  `IS_REGISTERED_IN`, plus current `IS_CEO_OF` / `IS_CFO_OF` / `IS_CTO_OF`
  officers from Cala's incoming relationships. Skips `is_personal == true`
  calendars (Luma "Personal" orgs trigger generic-noun false matches).
- **people**: `description`, `linkedin_url`, `personal_website`, `aliases`,
  plus `WORKS_AT`, `FOUNDED`, `HAS_NATIONALITY`.

In the London-AI smoke set (122 people / 30 orgs), the verified+strong bands
caught ~2 orgs (CASE, NayaOne) and 0 people — random local meetup attendees
aren't in Cala's institutional graph, but the organising entities sometimes
are. Specter is expected to lift the people-side hit-rate once its session
is live (movers / past-position data is its core surface).

### Hermes filter

```python
v = r["graph_match"]["verdict"]
if v in ("verified", "strong"):
    push_to_hubspot(r)
elif v == "weak":
    route_to_review_queue(r)
elif v == "needs_review":
    retry_when_specter_online(r)   # don't drop — infrastructure-blocked
elif v in ("disregard", "rejected"):
    discard(r)                     # both providers said no / wrong
```

### Running it manually

```bash
# Unified (Cala + Specter)
python3 scripts/luma_graph_enrich.py \
  --in  .gstack/luma/runs/2026-W19/leads/leads_orgs.jsonl \
  --out .gstack/luma/runs/2026-W19/leads/leads_orgs_graph.jsonl \
  --kind org --workers 3 --sleep 0.3 --max 50

python3 scripts/luma_graph_enrich.py \
  --in  .gstack/luma/runs/2026-W19/leads/leads_people.jsonl \
  --out .gstack/luma/runs/2026-W19/leads/leads_people_graph.jsonl \
  --kind person --workers 3 --sleep 0.3 --max 100

# Cala-only ad-hoc (unchanged)
python3 scripts/luma_cala_enrich.py --in ... --out ... --kind org
```

Both `luma_graph_enrich.py` calls are **resumable** — re-running appends
only rows whose `api_id` isn't already in the output, so a network hiccup
or expired Specter session doesn't cost duplicate API spend.

### Cron-side env vars

The weekly cron runs the unified enricher when `LUMA_ENABLE_GRAPH=1`.
Per-provider kill switches let you keep one provider on while the other is
in a known-bad state:

| env var | default | what it does |
|---|---|---|
| `LUMA_ENABLE_GRAPH` | `0` (legacy `LUMA_ENABLE_CALA` accepted as alias) | gate; set `1` to run org+people enrichment |
| `LUMA_DISABLE_CALA` | `0` | set `1` to skip Cala lookups (records `cala.status=disabled`) |
| `LUMA_DISABLE_SPECTER` | `0` | set `1` to skip Specter lookups (records `specter.status=disabled`) |
| `LUMA_GRAPH_MAX_ORGS` | `50` (legacy `LUMA_CALA_MAX_ORGS`) | cap orgs hit per run |
| `LUMA_GRAPH_MAX_PEOPLE` | `100` (legacy `LUMA_CALA_MAX_PEOPLE`) | cap people hit per run |
| `LUMA_GRAPH_MIN_NAME_SIM` | `0.55` (legacy `LUMA_CALA_MIN_NAME_SIM`) | floor for considering a provider result |
| `LUMA_GRAPH_WORKERS` | `3` | concurrent rows (each row = 1 Cala + 1 Specter call) |
| `LUMA_TOPIC_PACK` | `institutional` | which JSON pack to score with |

## Layer 3 — guest list (auth-gated, browser-use)

`/event/get-guest-list` returns **401** anonymous. See
[`LUMA_GUESTLIST_BROWSER_USE.md`](LUMA_GUESTLIST_BROWSER_USE.md) for the
browser-use recipe Hermes should run **after** scoring — filtered to
`event.show_guest_list == true` AND `topic_score >= threshold`.

## Weekly orchestration (cron entrypoint)

`scripts/luma_weekly.sh` is the single command Hermes invokes. It:

1. Creates `<LUMA_RUN_ROOT>/<ISO-week>/` (default `.gstack/luma/runs/YYYY-WNN/`)
2. Runs **layer 1** (`luma_gtm_harvest.py`) across all `LUMA_REGIONS`
3. Diffs `events.jsonl` against the most-recent prior run dir (`luma_diff.py`)
4. Runs **layer 2** (`luma_hydrate_events.py`, resumable)
5. Runs **scoring** (`luma_score_leads.py`)

```bash
# Defaults (Europe hub, all cities, all pages, week id = `date -u +%G-W%V`)
scripts/luma_weekly.sh

# Multi-region weekly cron line (Sunday 03:00 UTC)
# 0 3 * * 0  cd /opt/v7-growth && LUMA_REGIONS="discplace-QCcNk3HXowOR97j discplace-OTHER" scripts/luma_weekly.sh >> .gstack/luma/runs/last.log 2>&1
```

Tunable env vars: `LUMA_REGIONS`, `LUMA_RUN_ROOT`, `LUMA_MAX_PLACES`,
`LUMA_MAX_PAGES`, `LUMA_PAGE_LIMIT`, `LUMA_HYDRATE_WORKERS`, `LUMA_SLEEP`,
`LUMA_MIN_LEAD_SCORE`, `LUMA_RUN_ID`.

After the run completes Hermes consumes:

```
<run_dir>/diff/new_events.jsonl       — surface "new this week" headlines
<run_dir>/leads/leads_orgs.jsonl      — push to HubSpot via MCP as Companies
<run_dir>/leads/leads_people.jsonl    — push to HubSpot via MCP as Contacts
                                        (associate each contact with its
                                         org via calendar.api_id)
```

The contact-org join is **lossless** because every host/featured_guest row
in `leads_people.jsonl` carries the `events[]` array, and every event in
there carries its `calendar` via the parent `events_scored.jsonl` row — so
HubSpot deal threading is just a left-join on `event_api_id` → `calendar.api_id`.

## Repo entrypoints

```bash
# Full weekly run (cron)
scripts/luma_weekly.sh

# With fintech pack + graph enrichment (Cala + Specter) turned on
LUMA_TOPIC_PACK=fintech LUMA_ENABLE_GRAPH=1 scripts/luma_weekly.sh

# Cala only (until Specter session is set up)
LUMA_ENABLE_GRAPH=1 LUMA_DISABLE_SPECTER=1 scripts/luma_weekly.sh

# Specter readiness check (run before flipping LUMA_DISABLE_SPECTER off)
python3 scripts/specter_lookup.py --readiness

# Individual layers (manual / debugging)
python3 scripts/luma_gtm_harvest.py     --featured-place-api-id discplace-QCcNk3HXowOR97j --out-dir .gstack/luma/runs/manual
python3 scripts/luma_diff.py            --prev .gstack/luma/runs/2026-W18/events.jsonl --new .gstack/luma/runs/2026-W19/events.jsonl --out-dir .gstack/luma/runs/2026-W19/diff
python3 scripts/luma_hydrate_events.py  --in  .gstack/luma/runs/manual/events.jsonl --out .gstack/luma/runs/manual/events_hydrated.jsonl
python3 scripts/luma_score_leads.py     --in  .gstack/luma/runs/manual/events_hydrated.jsonl --out-dir .gstack/luma/runs/manual/leads --topic-pack institutional
python3 scripts/luma_graph_enrich.py    --in  .gstack/luma/runs/manual/leads/leads_orgs.jsonl --out .gstack/luma/runs/manual/leads/leads_orgs_graph.jsonl --kind org
```

- Remove `--max-places` / `--max-pages` for full recursive crawl (long run, many HTTP calls).
- Repeat `--featured-place-api-id` for each region hub id you care about.

## Output

- `events.jsonl` — one JSON object per unique event (deduped), with place + category provenance and host/guest facets.
- `manifest.json` — request counters and parameters for auditing.

## Cron guidance for Hermes

1. Run the Python harvester on a schedule (no Ollama).
2. Diff `events.jsonl` day-over-day by `event_api_id` for **new events**.
3. Optionally open `public_event_url` in browser-use for signup flows or authenticated guest export.

## `featured_place_api_id` examples

- Europe hub (from your link): `discplace-QCcNk3HXowOR97j`
- Discover other hubs by loading `bootstrap-page` once and reading `featured_place.place.api_id` from the UI network tab, or by inspecting `places_by_continent` in the same JSON.
