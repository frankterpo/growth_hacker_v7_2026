---
name: luma-gtm-harvest
description: >-
  Weekly Luma GTM pipeline — discover AI/Tech events across configured regions,
  hydrate full event details (hosts, sponsors, sessions, full description),
  diff against prior week, score by topic pack, emit HubSpot-ready leads as
  JSONL. Single command. Cron-friendly, resumable, no LLM, no official Luma
  API (uses the same undocumented api2.luma.com endpoints the web app uses).
  Use when the user asks to scrape Luma, refresh the GTM event corpus, find
  new events this week, or generate HubSpot leads from public Luma data.
version: 1.1.0
author: local
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [luma, gtm, hubspot, cron, scraping, public-data, no-auth]
    related_skills: [luma-event-lookup, luma-graph-enrich, luma-guestlist-browseruse]
prerequisites:
  commands: [bash, python3]
---

# Luma GTM harvest (weekly cron)

> **Bind-mount note:** the Hermes sandbox sees the repo at
> `/Users/pablote/Projects/growth_hacker_v7_2026`. All commands below use that
> absolute path — they work identically inside the sandbox and on the host.

## 🔥 COPY-PASTE THIS FIRST (do not invent flags)

Default run (Europe, all places, institutional topic pack):

```bash
cd /Users/pablote/Projects/growth_hacker_v7_2026 && bash scripts/luma_weekly.sh
```

Variations — all controlled by **env vars**, NOT CLI flags:

```bash
# Fintech topic pack
cd /Users/pablote/Projects/growth_hacker_v7_2026 && \
  LUMA_TOPIC_PACK=fintech bash scripts/luma_weekly.sh

# With unified graph enrichment (Cala + Specter)
cd /Users/pablote/Projects/growth_hacker_v7_2026 && \
  LUMA_ENABLE_GRAPH=1 bash scripts/luma_weekly.sh

# Multi-region (Europe + Asia)
cd /Users/pablote/Projects/growth_hacker_v7_2026 && \
  LUMA_REGIONS="discplace-QCcNk3HXowOR97j discplace-OTHER" \
  bash scripts/luma_weekly.sh

# Quick smoke (10 places, 1 page each)
cd /Users/pablote/Projects/growth_hacker_v7_2026 && \
  LUMA_MAX_PLACES=10 LUMA_MAX_PAGES=1 bash scripts/luma_weekly.sh
```

### Flag-set reality check

The shell script does **not** accept these flags (the model often hallucinates them — do not use):

- ❌ `--region`, `--regions`, `--topic`, `--topic-pack`, `--run-id`, `--output`, `--enable-guestlists`, `--scrape`, `--refresh`

All configuration is via **environment variables** (see table below). If you want a flag-style invocation, call the underlying Python script directly — but `scripts/luma_weekly.sh` is the canonical entry point and you almost never need to bypass it.

## When to use

- "Refresh / scrape / harvest Luma events"
- "Find new AI events this week"
- "Generate HubSpot leads from Luma"
- "Run the weekly Luma cron"

## When NOT to use

- User asks "is X at an event" → `luma-event-lookup` (fast, read-only)
- User wants attendee guest lists → `luma-guestlist-browseruse` (browser-use, authenticated)
- User wants Cala/Specter enrichment without re-running harvest → `luma-graph-enrich`

## Pipeline (what the one command does)

1. **Layer 1 — discover** (`scripts/luma_gtm_harvest.py`): `GET /discover/bootstrap-page` then paginated `GET /discover/get-paginated-events` across all places under each region hub for both `cat-ai` and `cat-tech`. Public, no auth.
2. **Diff** (`scripts/luma_diff.py`): compares `events.jsonl` against previous run — surfaces **new this week** vs **gone**.
3. **Layer 2 — hydrate** (`scripts/luma_hydrate_events.py`): `GET /event/get` for each unique event id. Adds full host roster, organising calendar, sponsors, sessions, full description. Resumable.
4. **Score** (`scripts/luma_score_leads.py`): applies the topic pack → emits `events_scored.jsonl`, `leads_orgs.jsonl`, `leads_people.jsonl`.
5. **Optional Layer 2c — graph enrichment** (`scripts/luma_graph_enrich.py`): unified Cala + Specter resolver. See sibling skill `luma-graph-enrich`. Gated by `LUMA_ENABLE_GRAPH=1`.

## Env vars (most-touched first)

| var | default | meaning |
|---|---|---|
| `LUMA_REGIONS` | `discplace-QCcNk3HXowOR97j` (Europe) | space-separated `featured_place_api_id` list |
| `LUMA_TOPIC_PACK` | `institutional` | topic pack name (see `scripts/luma_topic_packs/`) |
| `LUMA_ENABLE_GRAPH` | `0` | set `1` to run Cala+Specter enrichment after scoring |
| `LUMA_DISABLE_CALA` | `0` | set `1` to run graph enrichment with Specter only |
| `LUMA_DISABLE_SPECTER` | `0` | set `1` to run graph enrichment with Cala only |
| `LUMA_MAX_PLACES` | `0` (all) | cap places per region for smoke runs |
| `LUMA_MAX_PAGES` | `0` (all) | cap event pages per place for smoke runs |
| `LUMA_RUN_ID` | ISO week (`2026-W19`) | overrides run dir name |
| `LUMA_RUN_ROOT` | `.gstack/luma/runs` | where run dirs live |

## Output layout (relative to repo root)

```
.gstack/luma/runs/<ISO-week>/
  events.jsonl                  Layer 1 raw (deduped on event_api_id)
  manifest.json                 request counters + parameters
  diff/
    new_events.jsonl            new this week (after first run)
    gone_events.jsonl           dropped vs previous week
  events_hydrated.jsonl         Layer 2 enriched
  leads/
    events_scored.jsonl         every event + topic_score
    leads_orgs.jsonl            organising calendars  → HubSpot Companies
    leads_people.jsonl          hosts ∪ featured_guests → HubSpot Contacts
    leads_orgs_graph.jsonl      orgs + cala + specter + graph_match  (if graph enabled)
    leads_people_graph.jsonl    people + cala + specter + graph_match (if graph enabled)
    score_summary.json
```

## Answer rules

1. **Always cite the run_dir** so the user knows which week's data you used.
2. **Do NOT suggest "the Luma official API"** — there isn't one. We use undocumented `api2.luma.com` endpoints the web app uses (public, no auth required for discover + event/get).
3. After a run, report: events scanned, deduped, new vs gone, top 5 calendars by `topic_score`, and (if enrichment ran) the `graph_match` verdict histogram.
4. If a region returns zero new events, **say so** and suggest broadening `LUMA_REGIONS` or relaxing `LUMA_MIN_LEAD_SCORE`.

## Cron recipe

```cron
# Sunday 03:00 UTC — full weekly run, no enrichment by default
0 3 * * 0  cd /Users/pablote/Projects/growth_hacker_v7_2026 && bash scripts/luma_weekly.sh >> .gstack/luma/runs/last.log 2>&1

# With graph enrichment
0 3 * * 0  cd /Users/pablote/Projects/growth_hacker_v7_2026 && LUMA_ENABLE_GRAPH=1 bash scripts/luma_weekly.sh >> .gstack/luma/runs/last.log 2>&1
```

## Boomerang

- After a run → `luma-event-lookup` for targeted existence checks
- After `LUMA_ENABLE_GRAPH=1` → inspect `leads_*_graph.jsonl` per `luma-graph-enrich` rules
- For attendee lists on top-scoring events → `luma-guestlist-browseruse`
