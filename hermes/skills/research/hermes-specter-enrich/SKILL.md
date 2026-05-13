---
name: hermes-specter-enrich
description: >-
  TrySpecter (Specter) private-market enrichment: companies, people, investors,
  products, web traction, reported clients, news, and investor-company ties — via
  browser-captured session + HTTP replay (not Specter API credits). Use for GTM,
  warm intros, ex-investor bridges, and outreach when Cala alone is thin.
---

# Hermes — Specter enrichment (terminal agent)

## What Specter is (one paragraph)

**Specter** (`app.tryspecter.com`) is a **logged-in** private-markets UI: **signals** (company, revenue, talent, strategic, etc.), **tables** (people, investors, funding, M&A, IPO), and rich **entity cards** (products, **web traction**, **reported clients**, **recent news**, **investor profiles / ties**). The **dense JSON** usually comes from **`POST`** calls to `app.tryspecter.com/api/...` and sometimes to **`https://specter-api-prod.up.railway.app/private/...`** (Bearer JWT + body; **GET alone often returns 405**).

This repo treats Specter as **browser-first + replay**: capture cookies (and Clerk JWT when possible), then **`scripts/specter_lookup.py`** once URL templates exist — see **`scripts/SPECTER_LOOKUP.md`**.

## Install into Hermes (every machine once)

From this repo root:

```bash
# Install this skill + all sibling Luma/Cala skills under hermes/skills/
bash scripts/install_hermes_skills.sh

# Or just this one (back-compat alias)
bash scripts/install_hermes_specter_skill.sh
```

That copies this folder to **`~/.hermes/skills/research/hermes-specter-enrich/`** (same layout as your Cala skills). Re-run the script after you edit the skill here so Hermes picks up iterations.

Sibling skills installed by the generic script:

- **luma-event-lookup** — "is X at any Luma event?" against on-disk corpus
- **luma-gtm-harvest** — weekly cron pipeline (discover → hydrate → score)
- **luma-graph-enrich** — Cala+Specter merger with honest disregard semantics

See [`hermes/AGENTS.md`](../../AGENTS.md) for the full skill map.

**Always-on in every Hermes chat:** if your Hermes build supports a **default preamble** / **auto-loaded skills** list, add this skill there (same idea as `cala-output-style` being “always-on” in `cala/AGENTS.md`). Otherwise invoke explicitly with **`/hermes-specter-enrich`** when you need Specter context.

## Fast path (every enrichment request)

1. **`cd` to this repo** (scripts assume repo root for `.env` and `.gstack/specter/`).
2. Read **`scripts/SPECTER_LOOKUP.md`**.
3. Run **`python3 scripts/specter_lookup.py --readiness`**:
   - **`not_ready`** → `./scripts/run_specter_harvest.sh --probe-limit 12`, then re-check.
   - **`partial_ready`** → DevTools → Network; capture templates into `.env` (runbook).
   - **`ready`** → `python3 scripts/specter_lookup.py --kind org --name "…"` or `--kind person … --linkedin /in/…`.

## GTM brain (why Hermes should reach for Specter)

When the user wants **outreach paths** (sale, investor intro, portfolio co-intro, ex-investor / ex-employee bridge), Specter often has **operational signals** Cala does not. Prefer Specter **before** burning Cala graph hops when the ask is “who touches this company,” “who funded them lately,” “traction inflection,” or “news hook.”

## Answer contract

- State Specter **readiness** and never treat **`unavailable`** as **`no_match`** for unified **disregard** semantics (see runbook).
- Summarize **`specter_lookup.py` JSON** honestly.

## Iterations

Append new endpoints and payloads to **[reference.md](reference.md)**; extend **`SPECTER_RAILWAY_POST_PATHS`** / harvest as needed.

**Tables + script index:** [reference.md](reference.md)
