# Hermes + Specter — reference

## Repo map (run from `growth_hacker_v7_2026` root)

| Path | Role |
|------|------|
| `scripts/SPECTER_LOOKUP.md` | Operator runbook, readiness meanings, cron flags |
| `scripts/specter_lookup.py` | HTTP adapter: `--readiness`, `--kind org|person`, `--name`, `--linkedin` |
| `scripts/specter_harvest.py` | Browser Use login → cookie export → Performance URL dump → GET/POST probes |
| `scripts/run_specter_harvest.sh` | zsh-safe wrapper (no inline `#` after `export`) |
| `scripts/install_hermes_specter_skill.sh` | Copies this skill → `~/.hermes/skills/research/hermes-specter-enrich/` |
| `.gstack/specter/` | **gitignored** — `cookies.export.json`, `probe_results.json`, `resource_urls.json`, optional `clerk.jwt` |

## Session / auth env (`.env`)

| Variable | Purpose |
|----------|---------|
| `SPECTER_LOGIN_EMAIL` | Clerk email |
| `SPECTER_LOGIN_PASSWORD` | Clerk password |
| `SPECTER_COMPANY_FEED_URL` | Preferred start URL for harvest (redirects through login) |
| `SPECTER_LOGIN_URL` | Fallback if feed URL unset |
| `SPECTER_BROWSER_USE_BIN` | Optional path to `browser-use` binary |

## Lookup templates (after DevTools discovery)

| Variable | Purpose |
|----------|---------|
| `SPECTER_COMPANY_SEARCH_URL_TEMPLATE` | URL with `{name}` or `{query}` |
| `SPECTER_PERSON_SEARCH_URL_TEMPLATE` | Same for people |
| `SPECTER_RESPONSE_RESULTS_PATH` | Dot path into JSON (default `results`) |
| `SPECTER_RESPONSE_*_FIELD` | Field remaps (see `specter_lookup.py` docstring) |
| `SPECTER_SESSION_DIR` | Override session dir (default `.gstack/specter`) |

## Railway smoke probes (harvest)

| Variable | Default |
|----------|---------|
| `SPECTER_RAILWAY_API_BASE` | `https://specter-api-prod.up.railway.app` |
| `SPECTER_RAILWAY_POST_PATHS` | Comma-separated paths, default `/private/users/company-connections` |

Harvest sends **`POST {}`** as a connectivity test; real enrichment needs the **exact body** copied from Network.

## Example: IPO table XHR (from a live session)

Illustrative URLs the SPA hit (product may differ):

- `GET …/api/saved-searches?product=ipo&limit=3`
- `GET …/api/lists?product=ipo&omitMockLists=true&limit=3&skipSignalCounts=true`
- `POST …/api/signals/ipo`
- `POST …/api/signals/ipo/count`

Use these as patterns when mapping **other products** (`company`, `people`, `investors`, …): same host, different `product=` or path segment.

## Extension checklist

1. Capture **method, URL, headers, body, response** from DevTools.
2. If repeatable → add to `.env` or to a small Python helper (future).
3. Add one bullet to this file: **trigger** + **endpoint** + **notes** (pagination, filters).
