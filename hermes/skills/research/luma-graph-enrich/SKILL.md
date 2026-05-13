---
name: luma-graph-enrich
description: >-
  Unified Cala + Specter graph enrichment for Luma leads with HONEST confidence.
  Calls both providers per row, attaches both result blocks, computes a merged
  graph_match verdict (verified|strong|weak|rejected|disregard|needs_review).
  Disregard fires ONLY when BOTH providers explicitly return no_match — never
  when Specter is just offline (that becomes needs_review). Use to take a
  leads_*.jsonl from luma-gtm-harvest and decide what to push to HubSpot,
  what to send to a review queue, and what to drop.
version: 1.1.0
author: local
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [cala, specter, gtm, enrichment, hubspot, honest-confidence]
    related_skills: [luma-gtm-harvest, luma-event-lookup, hermes-specter-enrich]
prerequisites:
  commands: [python3]
  env_vars: [CALA_API_KEY]
---

# Luma graph enrichment (Cala + Specter, unified)

> **Bind-mount note:** the Hermes sandbox sees the repo at
> `/Users/pablote/Projects/growth_hacker_v7_2026`. Commands below use that
> absolute path and work in-sandbox and on host.

## 🔥 COPY-PASTE THIS FIRST

Orgs (HubSpot Companies):

```bash
cd /Users/pablote/Projects/growth_hacker_v7_2026 && \
  python3 scripts/luma_graph_enrich.py \
  --in  .gstack/luma/runs/<RUN_ID>/leads/leads_orgs.jsonl \
  --out .gstack/luma/runs/<RUN_ID>/leads/leads_orgs_graph.jsonl \
  --kind org --workers 3 --sleep 0.3
```

People (HubSpot Contacts):

```bash
cd /Users/pablote/Projects/growth_hacker_v7_2026 && \
  python3 scripts/luma_graph_enrich.py \
  --in  .gstack/luma/runs/<RUN_ID>/leads/leads_people.jsonl \
  --out .gstack/luma/runs/<RUN_ID>/leads/leads_people_graph.jsonl \
  --kind person --workers 3 --sleep 0.3
```

To find `<RUN_ID>` (latest week):

```bash
ls -1 /Users/pablote/Projects/growth_hacker_v7_2026/.gstack/luma/runs/ | sort | tail -1
```

### Flag set (do not invent flags)

```
--in PATH                  input JSONL (required)
--out PATH                 output JSONL (required)
--kind person|org          required
--workers N                concurrent rows (default 3)
--sleep FLOAT              inter-future sleep (default 0.25)
--max N                    cap rows this run (0 = all)
--disable-cala             skip Cala lookups
--disable-specter          skip Specter lookups
--cala-min-name-sim FLOAT  default 0.55
--specter-min-name-sim FLOAT default 0.55
--env-file PATH            default: <repo>/.env
```

No `--region`, `--topic`, `--run-id`, `--push-hubspot`, etc.

## When to use

- A `leads_orgs.jsonl` or `leads_people.jsonl` exists (from `luma-gtm-harvest`)
- User asks to "enrich", "resolve", "graph", "push to HubSpot"

## When NOT to use

- No leads file yet → run `luma-gtm-harvest` first
- Single-shot enrichment for one named entity → `cala-lookup-by-domain` or `hermes-specter-enrich`

## Verdict ladder (the thing Hermes filters on)

| `graph_match.verdict` | Trust as fact? | Trigger |
|---|---|---|
| `verified` | ✅ ship to HubSpot | Either provider returned `confidence: verified` (LinkedIn-confirmed). |
| `strong` | ✅ ship | Either returned `exact_name_no_li_hint` or `absent_strong_name`. |
| `weak` | 🟡 review queue | Either returned `weak`. Hint, not fact. |
| `rejected` | ❌ drop | Provider rejected on LinkedIn handle mismatch, nothing else passed. |
| `disregard` | ❌ drop | **Every enabled provider explicitly returned `no_match`** — the user's "drop it" rule. |
| `needs_review` | 🟡 retry | Mixed states. Specifically: a provider was `unavailable` and the rest didn't clear. Cannot honestly disregard. |
| `error` | n/a retry | All enabled providers errored. |
| `disabled` | n/a | Both providers turned off. |

## Filter snippet

```python
import json
for line in open(".gstack/luma/runs/.../leads_people_graph.jsonl"):
    r = json.loads(line)
    v = r["graph_match"]["verdict"]
    if v in ("verified", "strong"):
        push_to_hubspot(r)
    elif v == "weak":
        route_to_review_queue(r)
    elif v == "needs_review":
        retry_when_provider_online(r)
    # v in ("disregard", "rejected") → drop
```

## Specter readiness gate

Before enabling Specter, check it's ready:

```bash
cd /Users/pablote/Projects/growth_hacker_v7_2026 && \
  python3 scripts/specter_lookup.py --readiness
```

| state | meaning |
|---|---|
| `ready` | session + endpoint templates configured; lookups run for real |
| `partial_ready` | session OK, no endpoint templates yet → every Specter call returns `unavailable`; `disregard` cannot fire |
| `not_ready` | no Specter cookies → every Specter call returns `unavailable` |

To flip from `partial_ready` → `ready`, run the one-time discovery in `scripts/SPECTER_LOOKUP.md`. Until then, **disregard is intentionally rarer** — that's correct behavior, not a bug.

## Honest-confidence guarantee

- `disregard` ⟺ every enabled provider returned `no_match`. Read the `providers` sub-object to confirm.
- `needs_review` with `providers.specter == "unavailable"` means we couldn't ask Specter. Retry queue, not discard.
- Cala filters out `is_personal == true` Luma calendars — those rows aren't enriched at all.

## Answer rules

1. **Report the verdict histogram** after each run (the script prints it on stdout).
2. **Cite Specter readiness state** when explaining any `needs_review` count.
3. **Never claim disregard happened "because Specter doesn't have them"** unless Specter's readiness is `ready` AND `providers.specter == "no_match"` on that row.
4. If running with `--disable-specter`, **say so** — disregard now means "Cala-only no_match".
