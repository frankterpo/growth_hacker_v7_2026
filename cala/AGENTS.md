# Cala API — Operator Manual for Hermes

> Verified knowledge graph for AI agents. Replaces "scrape + parse + hope" with typed,
> sourced, deterministic queries. **5 endpoints, one API key, no SDK required.**
>
> Source of truth: <https://docs.cala.ai/llms.txt> · OpenAPI: <https://api.cala.ai/openapi.json>
>
> Every example below was executed live on 2026-05-12 against `https://api.cala.ai`.
> Raw responses are checked into `cala/fixtures/` — diff against them when the API changes.

---

## 0. Setup (10 seconds)

```bash
export CALA_API_KEY="clsk_..."          # from https://console.cala.ai/api-keys
export CALA_BASE="https://api.cala.ai"
```

### 0.1a Cursor wiring (recommended path, done 2026-05-12)

Cala is registered in `~/.cursor/mcp.json` alongside other Cursor MCP servers:

```json
{
  "mcpServers": {
    "Cala": {
      "url": "https://api.cala.ai/mcp/",
      "headers": { "X-API-KEY": "clsk_…" }
    }
  }
}
```

Restart Cursor (or wait ~30s) for it to discover the 5 Cala tools. Then ask any
Cursor chat in natural language — the hosted chat model resolves to
`mcp_Cala_*` calls in 1–3 seconds. **This is the fast path. Use it for any
graph-traversal or interactive Cala work.**

### 0.1b Hermes wiring (also done 2026-05-12, slow on local hardware)

Cala is registered as a **remote MCP server** in `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  cala:
    url: https://api.cala.ai/mcp/
    headers:
      X-API-KEY: ${MCP_CALA_API_KEY}
    enabled: true
```

The key lives in `~/.hermes/.env` as `MCP_CALA_API_KEY=…`. Verify with:

```bash
hermes mcp list                 # cala → ✓ enabled
hermes mcp test cala            # 5 tools discovered
```

**CRITICAL gotcha — do NOT add this entry by `cat >> config.yaml`.**
Hermes' YAML writer normalizes the file on every save (model swap, session
start, etc.) and silently drops any top-level keys that aren't in its schema.
A raw append survives the first session but vanishes on the next config
rewrite. Add the entry through Hermes' own config helper instead:

```bash
cd ~/.hermes/hermes-agent && venv/bin/python -c "
import sys; sys.path.insert(0, '.')
from hermes_cli.config import load_config, save_config
cfg = load_config()
cfg.setdefault('mcp_servers', {})['cala'] = {
    'url': 'https://api.cala.ai/mcp/',
    'headers': {'X-API-KEY': '\${MCP_CALA_API_KEY}'},
    'enabled': True,
}
save_config(cfg)
"
```

Or use `hermes mcp add cala --url https://api.cala.ai/mcp/ --auth header` —
it auto-picks `MCP_CALA_API_KEY` from `~/.hermes/.env` if already set, but
defaults the header to `Authorization: Bearer ${…}` (Cala wants `X-API-KEY`),
so you'll need to patch the saved entry afterward.

Inside Hermes sessions, Cala tools appear with the prefix `mcp_cala_`
(`mcp_cala_entity_search`, `mcp_cala_knowledge_query`, etc.) — reference them
by that namespaced name when prompting.

### 0.2 Three-stage Hermes pipeline (customer stories → Cala → HubSpot)

For **live outbound / CRM sync** workflows (scrape a vendor’s public customer-stories
page → enrich with Cala → upsert HubSpot), **do not use one mega-prompt**. Run three
separate user turns so the agent keeps a stable plan and you can re-run stage 2–3 for
another vendor by swapping the URL.

1. **Stage 1 — domains only:** browser (or web extract) on the customer-stories URL;
   output JSON of `{ brand_name, domain, evidence_href }`. No Cala, no HubSpot.
2. **Stage 2 — Cala only:** `entity_search` → `entity_introspection` → `retrieve_entity`,
   optional `knowledge_query` for movers / leadership; write enrichment JSON.
3. **Stage 3 — HubSpot only:** `search_crm_objects` then `manage_crm_objects` (search
   before create for idempotency).

Operator skill (prompt templates + pitfalls) is installed at:

`~/.hermes/skills/productivity/customer-stories-cala-hubspot/SKILL.md`

**Model:** use a strong tool-calling default (e.g. `gpt-oss:20b` on Ollama Cloud with
`ministral-3:8b` / local Qwen as fallbacks). **Pre-flight:** `hermes mcp test cala` and
`hermes mcp test hubspot` *before* the demo so OAuth never races a 30s MCP reload join.

**Cala tool names Hermes sees** (use these verbatim in prompts):
`knowledge_search`, `knowledge_query`, `entity_search`, `retrieve_entity`,
`entity_introspection`.

**Important caveat — model capability matters more than the wiring.**
On `hermes-llama3.2-3b-fast` (the current default), Hermes will frequently
hallucinate plain-text or Python code instead of emitting a real MCP tool call.
For any Cala work, switch to a tool-capable model — the configured fallback
`hermes-qwen2.5-7b-16k:latest` is the cheapest option that reliably tool-calls,
or use a hosted model (Claude/GPT) via `hermes model`. The 3B local model is
fine for chat but not for multi-step graph traversal against Cala.

**Canonical smoke prompt:**

> "Use the Cala MCP tool `entity_search` to look up Stripe with `entity_types=Company`,
> `limit=2`. Then call `entity_introspection` on the first id. Return only the relationship
> edge names you discover."

All requests:

- Auth header: `X-API-KEY: $CALA_API_KEY` (the `Authorization: Bearer` pattern is **not** accepted).
- Body content type: `application/json` for POSTs.
- Server: `https://api.cala.ai` (no `/api`, no version segment beyond `/v1`).

---

## 1. Endpoint Cheat Sheet

| # | Method + Path                                     | Purpose                                           | Latency (p50) | Cost trait  |
|---|---------------------------------------------------|---------------------------------------------------|---------------|-------------|
| 1 | `POST /v1/knowledge/search`                       | Natural-language Q&A → markdown + sources         | ~45–50 s      | Heavy       |
| 2 | `POST /v1/knowledge/query`                        | Cala QL or NL → typed JSON rows                   | ~45–55 s      | Heavy       |
| 3 | `GET  /v1/entities?name=…`                        | Fuzzy name lookup → list of entity IDs            | < 1 s         | Cheap       |
| 4 | `POST /v1/entities/{entity_id}`                   | Full profile by UUID (props + relationships)      | < 1 s         | Cheap       |
| 5 | `GET  /v1/entities/{entity_id}/introspection`     | Discover the schema for a specific entity         | < 0.5 s       | Cheap       |

**Rule of thumb for Hermes:**

> If you can resolve a question with `entity_search → introspection → retrieve_entity`,
> do it — three sub-second calls beat one 50-second `/knowledge/*` call almost every time.
> Reserve `/knowledge/search` and `/knowledge/query` for genuine open-ended discovery.

Set HTTP client timeouts to **at least 90 seconds** for the two `/knowledge/*` endpoints.

---

## 2. Decision Flow

```
                ┌──────────────────────────────┐
                │  What does the user need?    │
                └──────────────┬───────────────┘
                               │
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
   "Tell me about       "Give me a list of      "Explain X with
    company X"          things matching Y"       sources"
        │                      │                      │
        ▼                      ▼                      ▼
  entity_search          knowledge_query        knowledge_search
        │                      │                      │
        ▼                      │                      │
  introspection                │                      │
        │                      │                      │
        ▼                      ▼                      ▼
  retrieve_entity        results[] + entities[]  content (md) +
  (filtered body)                                 explainability +
                                                  context + entities
```

---

## 2.5 Hermes — grow the working graph one hop at a time

**Mental model.** Treat Cala as a *materialized knowledge graph you traverse on demand*.
You are not done when you get one JSON blob: you keep a **frontier** (a set of entity UUIDs
you have seen but not yet expanded), and each hop is **at most three cheap calls**
(`introspection` → `retrieve_entity` → enqueue new neighbor IDs). Heavy `/knowledge/*`
calls are optional *overlays* that pour fresh UUIDs into the frontier from `entities[]`.

### The expansion loop (Hermes should implement this as policy)

1. **Seed** — `GET /v1/entities?name=…&entity_types=…` *or* take `entities[]` / `context[]`
   from a prior `knowledge_search` / `knowledge_query` response. You always start from **IDs**.
2. **Schema (per node)** — `GET /v1/entities/{id}/introspection`. Relationship names differ
   by entity type (`Company` vs `Person` vs `Organization`). Never guess edge names;
   introspect once per *type pattern* you care about, then cache the edge lists in session.
3. **Expand** — `POST /v1/entities/{id}` with a **narrow** `relationships` object: only the
   edge families you need (`incoming.IS_CEO_OF`, `outgoing.WORKS_AT`, …). Every neighbor
   object includes `id`, `name`, `entity_type` — those IDs are your next frontier.
4. **Classify & pivot** — push neighbors into buckets (`Person`, `Company`, `Organization`, …).
   For each high-priority UUID, repeat steps 2–3 until depth or token budget.
5. **Overlay (optional)** — `POST /v1/knowledge/query` or `POST /v1/knowledge/search` with
   the *same* natural language or Cala QL you would have used standalone. Harvest
   `results[]` *and* `entities[]`. **Every element of `entities[]` is one introspection away**
   from a full structural profile.
6. **Dedup & merge** — the same real-world company can appear as **multiple UUIDs**
   (e.g. `STRIPE, LLC` from `entity_search` vs `Stripe` as `Organization` from a query row).
   Hermes should track `(canonical_name, entity_type_hint, source_path)` and merge nodes in
   its *working graph*, while still calling Cala with the UUID it actually received.

### Live chain (reproducible; fixtures on disk)

This sequence was executed end-to-end on **2026-05-12** against `https://api.cala.ai`.
Manifest: `cala/fixtures/graph_expansion_chain.json`. Per-step JSON:
`graph_step02_stripe_introspection.json` … `graph_step10_wise_retrieve.json`.

| Step | Endpoint | What it adds to the graph |
|------|----------|---------------------------|
| 1 | `GET /v1/entities?name=Stripe&entity_types=Company` | Anchor company `8a8b44f3-8769-46aa-8af8-d96c55a031b5` (`STRIPE, LLC`) |
| 2 | `GET …/8a8b44f3…/introspection` | Valid edge keys for this company node |
| 3 | `POST …/8a8b44f3…` with `incoming: IS_CEO_OF, FOUNDED, IS_BOARD_MEMBER_OF, INVESTED_IN` | **Patrick Collison** `64fcbd4f-7e65-4dd1-a610-8efc1f88befb` (+ other edges) |
| 4 | `GET …/64fcbd4f…/introspection` | Person-level schema (`WORKS_AT`, `IS_CEO_OF`, `FOUNDED`, …) |
| 5 | `POST …/64fcbd4f…` with outgoing `WORKS_AT`, `IS_CEO_OF`, `FOUNDED` | **Arc Institute** `3d3522a4-a048-4d92-8eb0-b3438f819b8f`, **Auctomatic**, parallel Stripe-related company IDs |
| 6–7 | Same pattern on Arc | Confirms round-trip: org → incoming `WORKS_AT` → back to Patrick |
| 8 | `POST /v1/knowledge/query` (`companies.industry=financial technology.founded_year>=2010`, `return_entities: true`) | **20** typed rows + **30** UUIDs in `entities[]` (new frontier seeds in one slow call) |
| 9–10 | `introspection` + `retrieve` on **Wise** `f118df40-7adc-4f21-b6a9-c28271b11629` (picked from step 8) | Shows how **any** UUID from `knowledge_query.entities` deepens like a graph neighbor |

**Hermes invariant:** whenever you are unsure what to ask next, *default to expanding the
frontier*: pick an unvisited UUID → introspect → retrieve with one relationship family →
enqueue. Cala is the tool you call to **elaborate relationships between entities**; the
knowledge graph in your context window is whatever you have merged from those responses.

---

## 3. Endpoint Reference (with verified examples)

### 3.1 `POST /v1/knowledge/search` — Natural-Language Answer

**When to use:** open-ended research questions where you want a sourced narrative,
not a row set ("Who founded Stripe and what's their background?", "What regulations
affect EU fintechs?").

**Request body** (`SearchRequest`):

| Field             | Type   | Default | Notes                                                         |
|-------------------|--------|---------|---------------------------------------------------------------|
| `input`           | string | —       | Required. Natural language **or** Cala QL.                    |
| `explainability`  | bool   | `true`  | Returns step-by-step reasoning with KnowBit references.       |
| `return_entities` | bool   | `true`  | Returns entity mentions with UUIDs you can pivot on.          |

```bash
curl -X POST "$CALA_BASE/v1/knowledge/search" \
  -H "X-API-KEY: $CALA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "input": "What are the most well-funded AI startups in Spain?",
    "explainability": true,
    "return_entities": true
  }'
```

**Response** (`Answer`, see `cala/fixtures/01_knowledge_search.json`):

```jsonc
{
  "content": "**Multiverse Computing** ... €189M Series B ...",   // markdown
  "explainability": [
    { "content": "...", "references": ["<knowbit-uuid>", ...] }   // 5 steps
  ],
  "context": [
    { "id": "<knowbit-uuid>", "content": "...", "origins": [...] } // 10 KnowBits
  ],
  "entities": [
    { "id": "99edce80-...", "name": "Seedtag", "entity_type": "Company", "mentions": ["Seedtag"] }
    // 29 entity mentions in the verified test
  ]
}
```

Every `references[]` UUID inside `explainability` always exists in `context[].id` — you
can render footnotes deterministically.

---

### 3.2 `POST /v1/knowledge/query` — Structured Answer (Cala QL)

**When to use:** filtered list-building. You know the attributes you want to filter on
(industry, funding, year, role…). Returns JSON rows ready to drop into a table.

**Cala QL primer** (dot-notation, `=`, `>`, `<`, `>=`, `<=`):

| Pattern                                                   | Meaning                                              |
|-----------------------------------------------------------|------------------------------------------------------|
| `startups.location=Spain.funding>10M.funding<50M`         | Spanish startups with €10M–€50M funding              |
| `companies.industry=fintech.founded_year>=2020`           | Fintechs founded since 2020                          |
| `people.role=CEO.company.industry=AI`                     | CEOs of AI companies (relationship traversal via `.`) |

The `input` field also accepts plain English; the server picks the planner.

**Request body** (`QueryRequest`):

| Field             | Type   | Default | Notes                                            |
|-------------------|--------|---------|--------------------------------------------------|
| `input`           | string | —       | Required. Cala QL string or natural language.    |
| `return_entities` | bool   | `true`  | Returns the entity UUIDs of every row's subject. |

```bash
curl -X POST "$CALA_BASE/v1/knowledge/query" \
  -H "X-API-KEY: $CALA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"input": "startups.location=Spain.industry=AI.funding>5M"}'
```

**Response** (`QueryResponse`, see `cala/fixtures/02_knowledge_query.json`):

```jsonc
{
  "results": [
    {
      "startup": "Multiverse Computing",
      "location": "San Sebastián",
      "industry": "AI",
      "total_funding": "~€250M+",
      "key_round": "€189M Series B (June 2025)"
    }
    // 9 rows in test
  ],
  "entities": [ /* 14 EntityMention objects */ ]
}
```

> **Schema warning:** the row schema is **dynamic** — it's whatever the planner decides
> answers your input. Don't assume column names; iterate `Object.keys(results[0])`.

---

### 3.3 `GET /v1/entities` — Entity Search (fuzzy)

**When to use:** you have a name (full or partial) and want a UUID to drill into.
This is your gateway to the cheap, deterministic endpoints (3.4 and 3.5).

**Query params:**

| Param          | Type     | Default | Notes                                                                 |
|----------------|----------|---------|-----------------------------------------------------------------------|
| `name`         | string   | —       | **Required**, min length 1 (empty → 422).                             |
| `entity_types` | string[] | `[]`    | Filter by type — **see enum below**. Repeat the param for multiple.   |
| `limit`        | int      | `20`    | 1 ≤ limit ≤ 100.                                                      |

**Entity type enum** (full list from OpenAPI):

```
Entity, Animal, Award, Organization, Company, EducationalInstitution,
Person, Event, GPE, Country, CountryRegion, Industry, FinancialMetric,
Group, CorporateEvent, Facility, Location, Organism, Plant, Product,
Sanction, WorkOfArt, Law, Language, Exchange
```

`GPE` = Geo-Political Entity (cities, regions). Use `Country` only for sovereign states.

```bash
curl -G "$CALA_BASE/v1/entities" \
  -H "X-API-KEY: $CALA_API_KEY" \
  --data-urlencode "name=OpenAI" \
  --data-urlencode "entity_types=Company" \
  --data-urlencode "limit=3"
```

**Response** (`EntitySearchResponse`, see `cala/fixtures/03_entity_search.json`):

```jsonc
{
  "entities": [
    {
      "id": "0ac03ffd-ebde-413d-b9e2-c2cb2299b633",
      "name": "OpenAI",
      "entity_type": "Company",
      "description": "AI research and deployment company developing advanced AI models including ChatGPT."
    }
    // ... ordered by relevance
  ]
}
```

---

### 3.4 `POST /v1/entities/{entity_id}` — Retrieve Entity

**When to use:** you have a UUID (from 3.1, 3.2, or 3.3) and want the full profile.
**Always send a body** — see the quirks section.

**Path:** `entity_id` must be a valid UUID v4 (else 422).

**Request body** (`EntityQuery`, all fields optional):

| Field                    | Type            | Notes                                                                       |
|--------------------------|-----------------|-----------------------------------------------------------------------------|
| `properties`             | string[]        | Subset of property names from `introspection.properties`. Empty → defaults. |
| `relationships.outgoing` | object          | Map of `RELATION_NAME` → `{ limit, offset }`.                               |
| `relationships.incoming` | object          | Same shape, for inbound edges.                                              |
| `numerical_observations` | object          | Map of observation type → list of observation UUIDs to fetch time series.   |

**Cheap default call** (defaults, no relationships):

```bash
curl -X POST "$CALA_BASE/v1/entities/0ac03ffd-ebde-413d-b9e2-c2cb2299b633" \
  -H "X-API-KEY: $CALA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{}'
```

**Targeted call** — exactly the fields you want:

```bash
curl -X POST "$CALA_BASE/v1/entities/0ac03ffd-ebde-413d-b9e2-c2cb2299b633" \
  -H "X-API-KEY: $CALA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "properties": ["name", "aliases", "headquarters_address", "employee_count", "founding_date", "website"],
    "relationships": {
      "outgoing": {
        "OPERATES_IN_INDUSTRY": { "limit": 3 },
        "HAS_HEADQUARTERS_IN":  { "limit": 2 }
      },
      "incoming": {
        "IS_CEO_OF": { "limit": 2 },
        "FOUNDED":   { "limit": 5 }
      }
    }
  }'
```

**Response** (`GetEntityResponse`, see `cala/fixtures/05a_*` and `05b_*`):

```jsonc
{
  "id": "0ac03ffd-ebde-413d-b9e2-c2cb2299b633",
  "name": "OpenAI",
  "entity_type": "Company",
  "description": "AI research and deployment company developing advanced AI models including ChatGPT.",
  "properties": {
    "employee_count": {
      "value": 4500,
      "sources": [ { "name": "...", "document": "https://...", "date": "2026-04-22" } ]
    },
    "founding_date":  { "value": "2018-09-19", "sources": [ ... ] },
    "headquarters_address": { "value": "...", "sources": [ ... ] }
    // each property = { value, sources[] } — never trust `value` without inspecting `sources`.
  },
  "relationships": {
    "outgoing": {
      "OPERATES_IN_INDUSTRY": [ { "id":"...", "name":"...", "entity_type":"Industry", "properties":{...} } ]
    },
    "incoming": {
      "IS_CEO_OF": [ { "id":"...", "name":"Mira Murati", ...} ],
      "FOUNDED":   [ { "id":"...", "name":"Greg Brockman", ...} ]
    }
  },
  "numerical_observations": [ /* time-series, only when requested */ ]
}
```

Each related entity carries its own `id` — chain back to `retrieve_entity` to walk the graph.

---

### 3.5 `GET /v1/entities/{entity_id}/introspection` — Schema discovery

**When to use:** before crafting a `properties` / `relationships` body for 3.4.
The schema is **per-entity-type** (Company schema ≠ Person schema). Always introspect
once per type before building queries.

```bash
curl "$CALA_BASE/v1/entities/0ac03ffd-ebde-413d-b9e2-c2cb2299b633/introspection" \
  -H "X-API-KEY: $CALA_API_KEY"
```

**Response** (`IntrospectionResponse`, see `cala/fixtures/04_*` and `06_person_introspection.json`):

```jsonc
{
  "properties": [
    "legal_name", "id", "name", "aliases", "description",
    "registered_address", "employee_count", "headquarters_address",
    "lei", "website", "founding_date"
  ],
  "relationships": {
    "outgoing": [
      "OPERATES_IN_INDUSTRY", "IS_AFFILIATE_OF", "IS_REGISTERED_IN",
      "HAS_PRESENCE_IN", "IS_RELATED_PERSON_OF", "IS_DIRECT_OWNER_OF",
      "IS_BENEFICIARY_OWNER_OF", "HAS_HEADQUARTERS_IN", "IS_PROMOTER_OF",
      "IS_DIRECT_PARENT_OF", "PARTICIPATES_IN_CORPORATE_EVENT",
      "HAS_PRIVATE_FUND", "IS_INDIRECT_OWNER_OF", "IS_PREDECESSOR_OF"
    ],
    "incoming": [
      "IS_WHOLLY_OWNED_SUBSIDIARY_OF", "PUBLISHED_BY", "DESIGNED_BY",
      "WORKS_AT", "FOUNDED", "MANUFACTURED_BY", "IS_CEO_OF",
      "IS_BOARD_MEMBER_OF", "IS_INDIRECT_OWNER_OF", "IS_COO_OF",
      "IS_RELATED_PERSON_OF", "IS_DIRECT_OWNER_OF", "CREATED_BY",
      "IS_AFFILIATE_OF", "IS_BENEFICIARY_OWNER_OF", "IS_EXECUTIVE_OF",
      "OPERATED_BY", "INVESTED_IN", "IS_CTO_OF", "IS_MEMBER_OF",
      "IS_PREDECESSOR_OF"
    ]
  },
  "numerical_observations": {
    // Empty for OpenAI (private). For public companies (e.g. Apple) you get
    // FinancialMetric[] keyed entries — each with a stable observation UUID
    // you pass back into retrieve_entity.numerical_observations.
  }
}
```

**Schema-by-entity-type** — verified differences:

| Type    | #props | Outgoing rels | Incoming rels                                      |
|---------|--------|---------------|----------------------------------------------------|
| Company | 11     | 14            | 21 (CEO, COO, CTO, BOARD_MEMBER, FOUNDED, …)       |
| Person  | 9      | 9             | 2 (DESIGNED_BY, OPERATED_BY)                       |

For `Person`, useful fields beyond `name`:
`place_of_birth, birth_date, aliases, linkedin_url, imdb_url, personal_website`
and outgoing edges `WORKS_AT, IS_CEO_OF, FOUNDED, STUDIED_AT, RECEIVED_AWARD,
HAS_NATIONALITY, HAS_PRIVATE_FUND, IS_MEMBER_OF, SPOKE_AT`.

---

## 4. Canonical Workflow — "Profile Company X"

Cheap path (3 calls, < 3 s total — verified):

```bash
# Step 1: name → UUID
ID=$(curl -sG "$CALA_BASE/v1/entities" \
        -H "X-API-KEY: $CALA_API_KEY" \
        --data-urlencode "name=OpenAI" \
        --data-urlencode "entity_types=Company" \
        --data-urlencode "limit=1" \
      | jq -r '.entities[0].id')

# Step 2: discover schema
SCHEMA=$(curl -s "$CALA_BASE/v1/entities/$ID/introspection" \
          -H "X-API-KEY: $CALA_API_KEY")

# Step 3: targeted retrieval (only the fields you actually need)
curl -s -X POST "$CALA_BASE/v1/entities/$ID" \
  -H "X-API-KEY: $CALA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "properties": ["name","aliases","employee_count","headquarters_address","website"],
    "relationships": { "incoming": { "IS_CEO_OF": {"limit":3}, "FOUNDED": {"limit":10} } }
  }' | jq
```

For numerical time series (revenue, cash, etc.) on public entities, the third call
becomes:

```jsonc
{
  "properties": ["name"],
  "numerical_observations": { "FinancialMetric": ["1d3eae40-0ba8-5baf-9907-6a4823b067bb"] }
}
```

The observation UUIDs come from step 2's
`numerical_observations.FinancialMetric[].id`.

---

## 5. Errors & Status Codes (verified live)

| Status | Trigger                                        | Body shape                                                                |
|--------|------------------------------------------------|---------------------------------------------------------------------------|
| 401    | Missing or wrong `X-API-KEY` header            | empty                                                                     |
| 404    | Valid UUID, no such entity                     | `{ "response_type":"ERROR","error":{"error_type":"entity_not_found_error","message":"..." }}` |
| 422    | Bad UUID format / `name=""` / missing `input`  | FastAPI-style `{ "detail": [ { "type":"...", "loc":[...], "msg":"..." } ] }` |
| 429    | Rate limit                                     | `{ "error":"rate_limit_exceeded", "message":"Rate limit exceeded. Too many requests." }` |

**Hermes retry strategy:**

- 401 → don't retry, surface to operator.
- 404 → don't retry, fall back to `entity_search` with broader `entity_types`.
- 422 → don't retry, fix payload first.
- 429 → exponential backoff (start 2 s, cap 60 s, max 4 attempts).
- 5xx / network → retry with backoff, but *also* alarm if the call was a `/knowledge/*`
  one — those are expensive to repeat blindly.

---

## 6. Quirks & Gotchas (learned the hard way)

1. **`name` returns `null` in default `retrieve_entity` body.** The top-level `name`
   field is set, but `properties.name` is null unless you pass `"properties":["name", …]`
   explicitly. Use the top-level `name` for display; only request `properties.name` if
   you need the source provenance for the legal/registered name.

2. **Relationship results are not temporally filtered.** `IS_CEO_OF` returns *historical*
   CEOs (in our test, OpenAI's interim CEOs Emmett Shear and Mira Murati came back ahead
   of Sam Altman). Inspect `properties.valid_since` / `properties.valid_until` on each
   related entity before treating it as "current".

3. **Entity resolution is fuzzy.** `FOUNDED` for OpenAI returned "WIESSMANN, TREVOR, S"
   alongside Greg Brockman, Elon Musk, Andrej Karpathy, etc. Always cross-check
   `sources[]` and consider a follow-up `entity_search` to confirm identity before
   reporting.

4. **`/knowledge/*` is slow.** Plan for 45–55 s. Stream responses if your client supports
   it; otherwise raise client timeouts to ≥ 90 s and consider caching by `input` hash.

5. **Row schemas from `/knowledge/query` are non-deterministic.** Two calls with
   slightly different inputs can produce different column sets. Don't bake column
   names into downstream code — extract via `Object.keys`.

6. **Cala QL `>`/`<` operators want suffixes** like `10M`, `50M`, `1B` directly in the
   string — no spaces. Year filters are bare integers (`founded_year>=2020`).

7. **`numerical_observations` is empty for most private companies.** It's primarily
   populated for SEC-filing entities. Don't promise time series unless introspection
   shows them.

8. **`entity_types` enum is closed** — pass anything outside the list at section 3.3
   and you'll get a 422. Cache the enum (it's already in `cala/fixtures/`).

---

## 7. Re-run the Test Matrix

Everything above is reproducible with one command:

```bash
bash cala/test_endpoints.sh
```

The script writes fresh JSON into `cala/fixtures/` and exits non-zero if any endpoint
breaks contract. Run it after every Cala API release announcement.

**Graph-expansion walkthrough** (company → people → org → `knowledge_query` → new entity
deepening): see §2.5 and `cala/fixtures/graph_expansion_chain.json` plus the
`graph_step*.json` fixtures produced by that live run.

---

## 8. Reference Material

- Docs index (always start here): <https://docs.cala.ai/llms.txt>
- Live OpenAPI: <https://api.cala.ai/openapi.json>
- Console (rotate keys, view usage): <https://console.cala.ai/api-keys>
- MCP integration (zero-code agent hookup): <https://docs.cala.ai/integrations/mcp.md>

If you (Hermes) hit a question this manual doesn't answer, **fetch `llms.txt` first**
— it's the canonical pointer to every doc page in the workspace.
