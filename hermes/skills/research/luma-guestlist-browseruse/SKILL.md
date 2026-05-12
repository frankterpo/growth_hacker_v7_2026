---
name: luma-guestlist-browseruse
description: >-
  Authenticated lu.ma guest-list extraction via browser-use. Captures a
  signed-in session once (operator does email-link sign-in manually), then
  replays cookies as plain GETs to api2.luma.com/event/get-guest-list to pull
  full attendee lists for events where show_guest_list=true. This is Layer 3
  of the Luma GTM pipeline — the gold payload that public scraping cannot
  reach. Use when the user asks "who is attending event X", "pull the guest
  list", "find attendees from company Y at recent events", or "find attendees
  matching our ICP".
version: 1.0.0
author: local
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [luma, gtm, browser-use, agentic-browsing, authenticated, guest-list, hubspot]
    related_skills: [luma-gtm-harvest, luma-event-lookup, luma-graph-enrich, hermes-specter-enrich]
prerequisites:
  commands: [python3, browser-use]
  env_vars: []
---

# Luma Layer 3 — authenticated guest-list harvest (browser-use)

> **Bind-mount note:** repo at `/Users/pablote/Projects/growth_hacker_v7_2026`
> in both sandbox and host. All commands use that absolute path.

## What this does

`api2.luma.com/event/get-guest-list` returns **the full attendee list** for
any event where the organiser has `show_guest_list = true`. It returns **401**
to anonymous requests. This skill:

1. Captures a signed-in lu.ma session via browser-use (one-time, ~30s manual)
2. Replays the cookies as plain HTTP GETs (~1 req/sec) — no UI driving per event
3. Writes one JSONL line per guest with all the GTM fields lu.ma exposes:
   `name`, `email_obfuscated`, `linkedin_handle`, `twitter_handle`, `website`,
   `bio_short`, `registered_at`, `approval_status`, `role`, etc.

`email_obfuscated` is the format lu.ma serves on guest-list APIs (`j***@v7labs.com`).
Full emails require **event-host privilege** on each calendar — we don't get
those. Do not pretend you can.

## 🔥 COPY-PASTE THIS (two commands, in order)

### Step 1 — one-time sign-in (or after cookies expire, ~weeks)

```bash
cd /Users/pablote/Projects/growth_hacker_v7_2026 && \
  python3 scripts/luma_login_harvest.py
```

This opens lu.ma in browser-use and prints **wait instructions**. You sign in
manually (email-link or Google). The script polls every 5s, detects the
signed-in state, exports cookies to `.gstack/luma/session/`, and probes
`api2.luma.com/user/get-self` to confirm.

Re-check that the session is good without re-signing-in:

```bash
cd /Users/pablote/Projects/growth_hacker_v7_2026 && \
  python3 scripts/luma_login_harvest.py --skip-signin-wait
```

### Step 2 — harvest guest lists for filtered events

```bash
cd /Users/pablote/Projects/growth_hacker_v7_2026 && \
  python3 scripts/luma_guestlist_harvest.py \
  --in  .gstack/luma/runs/<RUN_ID>/leads/events_scored.jsonl \
  --out .gstack/luma/runs/<RUN_ID>/leads/guests.jsonl \
  --report .gstack/luma/runs/<RUN_ID>/leads/guests_report.jsonl \
  --min-guests 25 --min-score 4 --sleep 1.0
```

To find `<RUN_ID>`:

```bash
ls -1 /Users/pablote/Projects/growth_hacker_v7_2026/.gstack/luma/runs/ | sort | tail -1
```

### Flag set (do not invent flags)

`luma_login_harvest.py`:
```
--out-dir DIR             default: .gstack/luma/session
--start-url URL           default: https://lu.ma/signin
--skip-signin-wait        don't pause for human; just export cookies
--wait-timeout-s N        default: 300
```

`luma_guestlist_harvest.py`:
```
--in PATH                 events_scored.jsonl or events_hydrated.jsonl (required)
--out PATH                JSONL output (required, resumable on event_api_id)
--report PATH             optional per-event outcome log
--session-dir DIR         default: .gstack/luma/session
--page-size N             default: 100 (lu.ma rejects > 100 with 422)
--sleep FLOAT             inter-request sleep, default 1.0
--min-guests N            default: 25 (skip low-value events)
--min-score N             default: 0 (raise to filter on topic_score)
--max-events N            default: 0 (all)
```

## When to use

- User asks "who is attending event X / these events"
- User wants to find attendees from a specific company / role at events we know about
- After a `luma-gtm-harvest` run, before pushing to HubSpot — guest lists are the highest-leverage Contacts
- Operator has approved spending compute on browser-use (it's not free per call, but is per-session)

## When NOT to use

- The question is "is X at any Luma event" using **already-harvested data** → `luma-event-lookup`
- The question is "find new events" → `luma-gtm-harvest`
- The user wants to push **public-only** hosts/featured_guests → use the existing `leads_people.jsonl` directly

## Filter strategy (matters — running on everything is wasteful)

Default filters keep the run cheap:

- `event.show_guest_list == true`  (organiser opted in; 403 otherwise)
- `event.guest_count >= --min-guests` (default 25 — small events are noise)
- `topic_score >= --min-score` (raise to 4+ for institutional ICP only)

On a typical W19 European run, this yields ~25 events out of 100. ~1s per page × ~3 pages average × 25 events ≈ 75s of network time.

## Output schema (one row per guest)

```jsonc
{
  "event_api_id": "evt-...",
  "event_name": "...",
  "event_url": "https://lu.ma/...",
  "event_start_at": "2026-05-13T17:00:00Z",
  "guest_count_advertised": 240,
  "calendar_api_id": "cal-...",
  "calendar_name": "London VC Network",
  "calendar_website": "https://...",
  "topic_score": 16,
  "topics": ["institutional_lp","venture"],

  "guest_api_id": "...",
  "user_api_id": "usr-...",
  "name": "...", "first_name": "...", "last_name": "...",
  "email_obfuscated": "j***@v7labs.com",
  "approval_status": "approved",
  "registered_at": "...", "approved_at": "...",
  "linkedin_handle": "/in/...",
  "twitter_handle": "...", "instagram_handle": "...",
  "website": "...", "bio_short": "...", "avatar_url": "...",
  "ticket_type_api_id": "...", "role": null
}
```

## Failure modes (the script returns honest statuses — DON'T invent recoveries)

| status | http | meaning | what to do |
|---|---|---|---|
| `ok` | 200 | success | nothing |
| `session_expired` | 401 | cookies stale | run `luma_login_harvest.py` again; the harvest is resumable so just re-run step 2 |
| `forbidden_or_private` | 403 | `show_guest_list = false` mid-run, or rate-limited | filter pre-emptied this; skip and continue |
| `http_error` | 4xx/5xx other | network / lu.ma weirdness | retry the event individually with `--max-events 1` after fixing |
| `error` | — | python-level exception | check stderr; report |

## Answer rules

1. **Always cite `events_processed`, `guests_written_this_run`, `status_counts`, `filter_reasons`** from the script's stdout summary. Be honest about how many events were filtered and why.
2. **Never claim full email addresses** — only `email_obfuscated` is available without host privilege.
3. **Never suggest a "Luma official API"** — lu.ma does not publish one. We use the same `api2.luma.com` endpoints the web app uses, with the same cookies the web app stores.
4. If the user wants to push attendees to HubSpot Contacts, surface that the `email_obfuscated` field is **not** enough for outreach — they need LinkedIn-based enrichment (`hermes-specter-enrich`) or to register on the event themselves to see the full email.
5. If session keeps expiring, suggest checking `~/.gstack/luma/session/session_meta.json` for the `signed_in` field — if it's `false`, the cookie capture didn't actually authenticate (likely the operator closed the browser-use window before sign-in completed).

## Boomerang

- After guests are harvested → `hermes-specter-enrich` each guest's LinkedIn handle to find movers / ex-coworkers / warm intros
- Found a high-value attendee at multiple events → `luma-event-lookup --linkedin /in/<them>` to see the full pattern
- Found an interesting calendar repeatedly hosting ICP events → re-run `luma-gtm-harvest` with broader region scope to catch sibling events
