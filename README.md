# V7 GTM Engine

Evidence-first go-to-market engine for V7: a technical static deck plus local Hermes agents, Obsidian CRM memory, and enrichment adapters for turning market signals into reviewable outbound routes.

This repo is the working V1 of a GTM system: not a generic slide deck, not a CRM export, and not a pile of scrapers. The front door is a static presentation in `public/index.html`; behind it are the operator manuals, skills, browser runtime, and data contracts that make the proposal executable.

## Live / Demo

Run the deck locally:

```bash
npm install
npm run start
```

Then open `http://localhost:3000`.

Production-style static output is the `public/` directory. Vercel is configured to install with `npm install`, validate with `npm run build`, and serve `public` via `vercel.json`.

Validate the deck contract:

```bash
npm run build
```

The build runs `scripts/validate_static_deck.mjs`, which checks the canonical slide routes, inline JS parseability, and core GTM claims around Hermes, Obsidian, Specter, Cala, Luma, LinkedIn Sales Navigator, Nitter/X, Browser Use, and the evidence flywheel.

## Architecture

```text
market surfaces
  Specter | Cala | Luma | Browser Use | LinkedIn/Sales Nav | Nitter/X
        |
        v
research Hermes
  evidence packet, entity resolution, route hypothesis
        |
        v
Obsidian CRM / writer
  company, person, deal, meeting, source, activity notes
        |
        v
CRM Hermes + human review
  source-backed opener, next action, outcome logging
        |
        v
learning loop
  route quality and source quality feed the next run
```

The system has a few deliberately separate layers:

- **Static deck + validator:** `public/index.html` is the investor/operator-facing narrative. `scripts/validate_static_deck.mjs` keeps route order, key copy, and interaction assumptions from drifting.
- **Hermes agents:** `hermes/AGENTS.md` is the operator manual for research, enrichment, Luma, Specter, and HubSpot-prep workflows. The split is research Hermes first, CRM/ops Hermes second.
- **Obsidian CRM/writer:** `hermes/skills/productivity/obsidian-crm-data-contract/SKILL.md` defines the HubSpot-shaped markdown contract, provenance rules, association logic, pipeline stages, and handoff block.
- **Luma pipeline:** `hermes/skills/research/luma-gtm-harvest/SKILL.md` documents the weekly event discovery, hydration, diffing, scoring, and optional graph enrichment path. `luma-event-lookup` and `luma-guestlist-browseruse` cover targeted lookup and authenticated attendee workflows.
- **Specter + Cala enrichment:** Specter is browser-session capture plus HTTP replay via `scripts/specter_harvest.py`, `scripts/specter_lookup.py`, and `scripts/SPECTER_LOOKUP.md`. Cala is the graph layer documented in `cala/AGENTS.md`, with domain-first lookup and outreach path conventions.
- **Browser Use runtime:** `hermes/docker/browser-agent.Dockerfile`, `hermes/DOCKER_BROWSER_AGENTS.md`, and `scripts/hermes_browser_agent_image.sh` define the browser-capable Docker image for Hermes profiles that need Playwright Chromium, `browser-use`, `profile-use`, and Cloudflare tunnel smoke tests.
- **Telegram/Ollama/MCP tooling:** `hermes/TELEGRAM_DUAL_BOTS_SETUP.md` and `scripts/hermes_bootstrap_dual_telegram.sh` describe the two-profile Telegram gateway setup for `jerme` and `archap`. The deck and manuals also document local Docker agents, Ollama local model usage, and MCP wiring for Cala.
- **Artifacts:** `.gstack/` and `.env*` are local runtime state, logs, sessions, QA output, and credentials. They are intentionally ignored and should not be committed.

## Repo Map

```text
.
|-- public/
|   |-- index.html                  # static V7 GTM Engine deck
|   `-- assets/
|       |-- v7labs-logo.svg
|       `-- obsidian-logo-gradient.svg
|-- scripts/
|   |-- validate_static_deck.mjs     # build-time deck contract
|   |-- specter_harvest.py           # Browser Use session capture for Specter
|   |-- specter_lookup.py            # Specter readiness + lookup adapter
|   |-- SPECTER_LOOKUP.md            # Specter replay runbook
|   |-- luma_event_lookup.py         # targeted lookup over Luma corpus
|   |-- luma_login_harvest.py        # Luma auth/session helper
|   |-- luma_guestlist_harvest.py    # authenticated guest-list harvesting
|   |-- install_hermes_skills.sh     # rsync repo skills into Hermes
|   |-- install_hermes_specter_skill.sh
|   |-- hermes_browser_agent_image.sh
|   |-- hermes_browser_docker_smoke.sh
|   |-- hermes_bootstrap_dual_telegram.sh
|   `-- hermes_jerme_kanban_sweep.sh
|-- hermes/
|   |-- AGENTS.md                    # repo-level Hermes operator manual
|   |-- DOCKER_BROWSER_AGENTS.md     # browser-capable Hermes Docker runtime
|   |-- TELEGRAM_DUAL_BOTS_SETUP.md  # jerme + Archap Telegram setup
|   |-- docker/browser-agent.Dockerfile
|   `-- skills/
|       |-- research/                # Luma, Specter, graph enrichment skills
|       `-- productivity/            # Obsidian CRM, jerme, Archap skills
|-- cala/
|   `-- AGENTS.md                    # Cala API/MCP graph operator manual
|-- .agents/skills/
|   |-- obsidian-cli/
|   |-- obsidian-markdown/
|   `-- obsidian-bases/
|-- package.json
|-- vercel.json
`-- skills-lock.json
```

## Quickstart

Requirements:

- Node.js 18+
- npm
- Docker, only for Hermes browser-agent image work
- Hermes, Obsidian CLI, Browser Use, Cala credentials, Specter credentials, Telegram tokens, or Ollama only for the corresponding operator workflows

Install and run the deck:

```bash
npm install
npm run start
```

Build and validate:

```bash
npm run build
```

Preview on a Vercel-like port:

```bash
npm run preview
```

Install Hermes skills from this repo:

```bash
bash scripts/install_hermes_skills.sh
```

Build and verify the browser-capable Hermes image:

```bash
bash scripts/hermes_browser_agent_image.sh build
bash scripts/hermes_browser_agent_image.sh verify
```

Check Specter readiness before enrichment:

```bash
python3 scripts/specter_lookup.py --readiness
```

## Evidence / Artifacts

Important proof surfaces in the repo:

- `public/index.html` includes the proposal map, route replay, Obsidian proof layer, Hermes proof, and tools proof slides.
- `scripts/validate_static_deck.mjs` is the executable guardrail for deck structure and claims.
- `hermes/AGENTS.md` is the current source of truth for Hermes routing rules and installed skills.
- `hermes/skills/research/luma-gtm-harvest/SKILL.md` documents the public Luma pipeline: discover, diff, hydrate, score, and optional Cala/Specter graph enrichment.
- `hermes/skills/research/luma-guestlist-browseruse/SKILL.md` covers authenticated guest-list collection where public Luma data stops.
- `hermes/skills/research/hermes-specter-enrich/SKILL.md` and `scripts/SPECTER_LOOKUP.md` describe Specter session capture and replay.
- `cala/AGENTS.md` documents the Cala graph workflow, API shape, MCP wiring, and output contract.
- `hermes/skills/productivity/obsidian-crm-data-contract/SKILL.md` defines the CRM schema, provenance block, and handoff format.
- `hermes/DOCKER_BROWSER_AGENTS.md` and `hermes/docker/browser-agent.Dockerfile` describe the browser-capable local runtime.

Local artifacts live under `.gstack/` during real runs, for example Luma corpora, Specter sessions, QA reports, browser logs, and smoke-test output. Those are operational evidence, not source files.

## Security Notes

Do not commit credentials or local runtime state.

This repo intentionally ignores:

- `.env`, `.env.*`, `.env*.local`
- `.gstack/`
- `.vercel/`
- `node_modules/`
- Python caches and OS noise

Keep API keys, Telegram bot tokens, Specter login details, Cala keys, browser cookies, and smoke output local. Docker images should not bake secrets; pass keys through Hermes profile environments or another approved secret source.

## Current State

V1 is local and demonstrable:

- The deck runs as a static site and validates with `npm run build`.
- Hermes has repo-local operator manuals and installable skills.
- Obsidian CRM conventions are captured as a reusable data contract.
- Specter, Cala, Luma, Browser Use, LinkedIn/Sales Navigator, and Nitter/X are represented as source surfaces in the route architecture.
- The browser-capable Hermes runtime is Docker-local today.
- Telegram is documented as the text/audio command surface for split Hermes profiles.

The next production path, as stated in the deck, is moving or adding the browser-capable Hermes unit to Oracle Cloud while preserving the same boundaries: no secrets in images, mounted/source-controlled skills, local artifact discipline, and human review before outreach.
