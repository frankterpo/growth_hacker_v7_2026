---
name: luma-event-lookup
description: >-
  Answer "is X person/company at any Luma event?" by grepping on-disk Luma
  corpus. Read-only, ~200ms. Use when the user asks whether a specific person,
  founder, company, LinkedIn handle, or domain shows up in recent Luma events.
  Returns event id, slug, calendar, public URL, and what field matched. Does
  NOT scrape — works on already-harvested data only.
version: 1.1.0
author: local
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [luma, gtm, search, events, public-data, no-auth]
    related_skills: [luma-gtm-harvest, luma-guestlist-browseruse, hermes-specter-enrich]
prerequisites:
  commands: [python3]
---

# Luma event lookup

> **Bind-mount note:** the Hermes Docker sandbox mounts the host repo at
> `/Users/pablote/Projects/growth_hacker_v7_2026` (see `~/.hermes/config.yaml`
> → `docker_volumes`). Every command in this skill uses that absolute path —
> it works identically inside the sandbox and on the host.

## 🔥 COPY-PASTE THIS FIRST (do not invent flags)

For "is `<X>` at any Luma event":

```bash
python3 /Users/pablote/Projects/growth_hacker_v7_2026/scripts/luma_event_lookup.py \
  --name "<X>"
```

Multi-needle (recommended — name + LinkedIn + domain):

```bash
python3 /Users/pablote/Projects/growth_hacker_v7_2026/scripts/luma_event_lookup.py \
  --name "Browser Use" --name "browser-use" \
  --name "Gregor Zunic" --name "Magnus Müller" --name "Manuel Suess" \
  --linkedin /in/gregorzunic --linkedin /in/magnusmueller \
  --linkedin /company/browser-use \
  --domain browser-use.com \
  --json
```

The script picks the latest run dir automatically. **Do not pass any flag not listed in `--help`.** The full flag set is:

```
--in PATH         explicit JSONL (else picks latest run)
--run-root DIR    default: .gstack/luma/runs
--name STR        name needle (repeatable)
--linkedin STR    LinkedIn handle (repeatable)
--domain STR      domain (repeatable)
--json            emit a single JSON document
--limit N         max matches (default 50, 0 = unlimited)
```

There is **no** `--region`, `--topic`, `--run-id`, `--output`, or `--enable-guestlists`. If you think you need a flag like that, you want a different skill (`luma-gtm-harvest` or `luma-guestlist-browseruse`).

## When to use

- User names a specific entity and asks if it's in our event corpus
- Pre-deal-cycle: existence check before spending effort on enrichment

## When NOT to use

- User wants to **scrape new events** → `luma-gtm-harvest`
- User wants **attendee data** (not hosts/featured guests) → `luma-guestlist-browseruse`
- User wants to **enrich a known person** with movers data → `hermes-specter-enrich`

## What it searches (read-only, no network)

Each row in `.gstack/luma/runs/<latest>/events_hydrated.jsonl` is matched on:

- `event.name`, `event.url_slug`, `event.description` (Pretext-flattened)
- `hosts[].{name, username, bio_short, linkedin_handle, twitter_handle, website}`
- `featured_guests[].{name, username, bio_short, linkedin_handle, website}`
- `featured_infos[].{name, name_raw}` (sponsor strip)
- `calendar.{name, website, linkedin_url, twitter_handle, instagram_handle}`
- `sessions[].{name, description}`

Name needles get a `tidy` form: `browser-use` and `browser use` match the same row. LinkedIn handles are normalised to slug-only.

## Output contract (JSON when `--json`)

```jsonc
{
  "query": { "name": [...], "linkedin": [...], "domain": [...] },
  "source_file": ".gstack/luma/runs/2026-W19-final/events_hydrated.jsonl",
  "summary": {
    "events_scanned": 100,
    "matches_returned": 0,
    "any_guest_list_open": 0,           // events where show_guest_list=true
    "calendar_breakdown": {}            // top organising calendars among hits
  },
  "matches": [ /* event objects */ ]
}
```

## Answer rules (read these before responding)

1. **State the source file and events scanned.** Honesty about scope.
2. If `matches_returned == 0`, explicitly list **what we did NOT scrape**:
   - the harvest is **Europe-only** by default (`LUMA_REGIONS=discplace-QCcNk3HXowOR97j`),
   - the public layer only carries **hosts + featured_guests**, **not attendees**,
   - attendee data lives behind authenticated `/event/get-guest-list` — use `luma-guestlist-browseruse`.
3. **Do NOT suggest using a "Luma official API"** — there is none. The harvest hits the same undocumented endpoints `api2.luma.com` that the web app uses. The right next steps are:
   - re-harvest with broader regions → `luma-gtm-harvest`
   - or pull guest lists via browser-use → `luma-guestlist-browseruse`
4. If `matches_returned > 0`, prefer events with `show_guest_list == true` for follow-up — those let you discover *who else* is going.

## Common queries (paste-ready)

```bash
# Founders of a specific company across all known events
python3 /Users/pablote/Projects/growth_hacker_v7_2026/scripts/luma_event_lookup.py \
  --name "Anthropic" --name "Dario Amodei" --name "Daniela Amodei" \
  --linkedin /company/anthropicresearch --domain anthropic.com --json

# Anyone from a domain
python3 /Users/pablote/Projects/growth_hacker_v7_2026/scripts/luma_event_lookup.py \
  --domain v7labs.com --json

# A specific LinkedIn handle
python3 /Users/pablote/Projects/growth_hacker_v7_2026/scripts/luma_event_lookup.py \
  --linkedin /in/alberto-rizzoli --json
```

## Boomerang to other skills

- Got matches → `hermes-specter-enrich` each person/org for movers + warm intros
- Got matches with `show_guest_list: true` → `luma-guestlist-browseruse` for attendees
- Got zero matches and need more coverage → `luma-gtm-harvest` with broader `LUMA_REGIONS`
