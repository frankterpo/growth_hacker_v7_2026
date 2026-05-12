# Hermes — Operator Manual for this repo

> Always-on entry point so Hermes (and any other agent operating from this
> repo) knows what skills exist, when to use them, and what NOT to do. Pair
> with `cala/AGENTS.md` for Cala-specific behavior.

Source of truth: this file. If you (the agent) need to answer a question about
**Luma events, Specter enrichment, GTM leads, or HubSpot prep**, do NOT do a
generic `session_search` first — every Hermes-side skill that handles those
topics is listed below with its trigger phrases.

---

## ⚠️ READ BEFORE INVOKING ANY SKILL IN THIS REPO

1. **The repo is bind-mounted into the Hermes Docker sandbox** at
   `/Users/pablote/Projects/growth_hacker_v7_2026` (see `~/.hermes/config.yaml`
   → `docker_volumes`). All scripts and `.env` and `.gstack/` data live there
   inside the container, at the same path as on the host. **Use that absolute
   path in every command.**
2. **Source the paths config** if you need it:
   `. /Users/pablote/.hermes/skills/research/_growth_hacker_paths.env`
   This sets `GROWTH_HACKER_REPO`, `GROWTH_HACKER_LUMA_RUNS`, etc.
3. **DO NOT invent CLI flags.** Each skill's SKILL.md has a "COPY-PASTE THIS"
   block with the exact, working invocation and a complete flag set. If a flag
   you want isn't listed, it does not exist — switch skills, don't fabricate.
   Common hallucinations to avoid: `--region`, `--regions`, `--topic`,
   `--run-id`, `--output`, `--enable-guestlists`, `--scrape`, `--refresh`,
   `--push-hubspot`. None of these exist.
4. **DO NOT suggest using "the Luma official API."** There isn't one. We use
   the undocumented `api2.luma.com` endpoints the lu.ma web app uses. They are
   public for discover/hydrate and authenticated (via captured browser cookies)
   for guest lists.
5. **Verbatim execution over reasoning.** If you are running on a small model
   (≤8B params, e.g. ministral-3b, llama-3-8b, qwen-2.5-7b), DO NOT try to
   reason about path resolution or flag inference. Copy-paste the exact
   commands from SKILL.md. Reasoning at this scale produces hallucinated
   flags and broken paths — verbatim execution always wins.

### Recommended Hermes model for this repo's skills

The default `model: ministral-3:3b` in `~/.hermes/config.yaml` is too small
for multi-step skill routing in this repo. If you have credits, swap it for
one of the fallbacks already configured:

```yaml
# ~/.hermes/config.yaml
model:
  default: llama-3.3-70b-versatile   # via Groq, configured below
  provider: groq
  api_mode: chat_completions
```

…or `mistral-medium-latest` (via Mistral), or `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`
(via NVIDIA). The 70B-class options reliably parse SKILL.md and don't invent
flags. The 3B local model cannot.

---

## Installed Hermes skills (under `hermes/skills/research/`)

| Skill | Trigger phrases | What it does |
|---|---|---|
| **luma-event-lookup** | "is X at any Luma event", "who's at events", "does X show up", "find this person/company at an event" | Read-only grep over `events_hydrated.jsonl`. ~200ms. Honest scope (Europe-only by default, public layer only). |
| **luma-gtm-harvest** | "scrape Luma", "refresh events", "weekly cron", "new events this week", "generate HubSpot leads" | Full pipeline: discover → diff → hydrate → score → optional graph enrichment. One command: `scripts/luma_weekly.sh`. |
| **luma-guestlist-browseruse** | "pull guest list", "who's attending event X", "find attendees from company Y", "agentic browsing Luma", "browser-use Luma" | Authenticated Layer 3 — captures a logged-in lu.ma session once via browser-use, then fetches full attendee lists for events where `show_guest_list=true`. |
| **luma-graph-enrich** | "enrich these leads", "resolve in graph", "push to HubSpot", "Cala+Specter merge" | Unified Cala + Specter enrichment with HONEST disregard semantics. |
| **hermes-specter-enrich** | "Specter", "movers", "ex-investors", "warm intros", "private market signals" | Specter session capture + HTTP replay. See `scripts/SPECTER_LOOKUP.md`. |
| **cala-lookup-by-domain** | "find X on Cala by domain", "look up <url>" | Domain-first Cala disambiguation (avoids name-fuzzy false matches). |
| **cala-outreach-paths** | "warm intro to X", "who in our network knows X" | Multi-hop Cala graph traversal for outreach paths. |
| **cala-output-style** | always-on render rules | Entity IDs + sources + next hops in every Cala answer. |

To rescan after edits in this repo:

```bash
cd <repo-root>
bash scripts/install_hermes_skills.sh
```

That rsyncs every directory under `hermes/skills/` into `~/.hermes/skills/`.

---

## Routing rules (read these before any "research" tool call)

1. **"Is X person/company at a Luma event?"** → invoke **luma-event-lookup**. Do NOT session_search; do NOT scrape; do NOT call Cala/Specter first. The answer lives at `/Users/pablote/Projects/growth_hacker_v7_2026/.gstack/luma/runs/<latest>/events_hydrated.jsonl`. ~200ms.

2. **"Find Luma events this week / scrape Luma / refresh leads"** → invoke **luma-gtm-harvest**. Cron-grade, ~3–10 min depending on scope. Don't paginate the Luma API by hand — the pipeline already does it correctly (`pagination_cursor` field, not `?cursor=`; `GET`, not `POST`).

3. **"Who's attending event X / pull the guest list / find attendees from company Y"** → invoke **luma-guestlist-browseruse**. This is the authenticated motion: browser-use captures a signed-in lu.ma session once, then the harvest replays cookies on `api2.luma.com/event/get-guest-list` for every filtered event. Don't try to drive the UI per event — cookies + plain GETs is the right shape.

4. **"Enrich these leads / push to HubSpot"** → invoke **luma-graph-enrich** on the `leads_*.jsonl`. Filter on `graph_match.verdict in {verified, strong}` before any CRM mutation.

5. **"Get Specter data on X"** → invoke **hermes-specter-enrich**. Always start with `python3 /Users/pablote/Projects/growth_hacker_v7_2026/scripts/specter_lookup.py --readiness` and surface the state honestly. Specter `unavailable` is **not** the same as Specter `no_match`.

6. **"Cala lookup / graph traversal"** → use the **MCP Cala tools** (5 of them, registered in `~/.hermes/config.yaml`). Prefer `cala-lookup-by-domain` when the user gives a URL/domain.

---

## What you should NOT do

- ❌ **Do not** use `session_search` to "find Luma scripts" — they are listed above. Use them directly.
- ❌ **Do not** scrape `lu.ma` by hand with `curl` for discovery; the pipeline already handles the API contract correctly (see `scripts/LUMA_GTM_HERMES.md`).
- ❌ **Do not** call `/event/get-guest-list` unauthenticated; it returns 401. Use `luma-guestlist-browseruse` which captures a real signed-in session.
- ❌ **Do not** treat Specter `status: unavailable` as `no_match`. It's an infrastructure state, not a data state. The unified verdict downgrades to `needs_review`, not `disregard`.
- ❌ **Do not** invent CLI flags. Every skill has an exact paste-ready command in its SKILL.md plus a complete flag set. If you "need" a flag like `--region`, `--enable-guestlists`, `--push-hubspot`, you are wrong — you want a different skill or env var.
- ❌ **Do not** suggest "the Luma official API" — lu.ma does not publish one. The pipeline uses `api2.luma.com` (undocumented but stable, same endpoints the lu.ma web app calls).
- ❌ **Do not** run with `ministral-3b` (or any <7B model) for the planning step of any of these skills — the routing reasoning won't fit. Use the largest model you have for plans; small models are fine for executing one specific shell command verbatim. See the model recommendation at the top of this file.

---

## Where data lives

```
.gstack/luma/runs/<ISO-week>/
  events.jsonl                  Layer 1 raw
  events_hydrated.jsonl         Layer 2 enriched (full hosts/sponsors/sessions)
  diff/                         week-over-week new/gone
  leads/
    leads_orgs.jsonl            organising calendars (HubSpot Companies candidates)
    leads_people.jsonl          hosts + featured_guests (HubSpot Contacts candidates)
    leads_*_graph.jsonl         + Cala + Specter + graph_match  (if enrichment ran)
.gstack/specter/                 Specter session state (cookies, optional JWT)
.env                             CALA_API_KEY, SPECTER_*, etc.
```

---

## Honest scope of the public Luma layer

The harvester captures, **without login**:

- Event metadata (name, slug, start/end, location, ticket gating)
- Hosts (full roster after `/event/get` hydration)
- Featured guests
- Organising calendar (org slug, website, LinkedIn/Twitter/Instagram, plan)
- Sponsors (`featured_infos`)
- Sessions (multi-track agenda)
- Full description

The harvester does **not** capture:

- General attendees / guest list — Layer 3, auth-gated
- DMs, RSVPs, payment info
- Private events the operator can't see

If the user asks for attendee data, escalate to the browser-use guest-list job
(`scripts/LUMA_GUESTLIST_BROWSER_USE.md`), don't pretend the public layer has
it.

---

## Default regions

The harvest defaults to `LUMA_REGIONS="discplace-QCcNk3HXowOR97j"` (Europe).
If the user's question is plausibly answered by a different region (e.g.
"Browser Use is Zürich-based but the team travels to SF and NYC"), explicitly
recommend running with the relevant `featured_place_api_id` and surface that
the current corpus may not have it.

Region hub ids are read from `/discover/bootstrap-page` — see
`scripts/LUMA_GTM_HERMES.md` § "featured_place_api_id examples".
