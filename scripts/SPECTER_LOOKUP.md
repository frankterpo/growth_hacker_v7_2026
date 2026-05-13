# Specter lookup — one-time discovery + cron handoff

**Hermes (terminal):** install the Specter skill into `~/.hermes` from this repo:

```bash
bash scripts/install_hermes_specter_skill.sh
```

Canonical skill files live in **`hermes/skills/research/hermes-specter-enrich/`** (source); the script rsyncs them to **`~/.hermes/skills/research/hermes-specter-enrich/`**. Re-run after edits. See also **`cala/AGENTS.md`** §0.1c operator skills.

`scripts/specter_lookup.py` is the runtime adapter that lets the unified
graph enricher (`luma_graph_enrich.py`) ask Specter "do you know this
person / org?" using the **same authenticated browser session** that a
human operator would use, without re-driving the UI on every cron tick.

Two artifacts make it work:

1. **An authenticated Specter session on disk** — captured by
   `scripts/specter_harvest.py` via `browser-use`. Writes cookies + a
   Clerk JWT (when issuable) to `.gstack/specter/`.
2. **A search-endpoint URL template** — observed once in the DevTools
   Network panel during step 3 below, then stored in `.env`.

Until both exist, every call returns
`{"status":"unavailable", "reason":"..."}` and the unified verdict for any
Cala-only miss becomes `needs_review` instead of `disregard`. That is
intentional: we never drop a lead because of infrastructure state.

## Readiness states

```bash
python3 scripts/specter_lookup.py --readiness
```

| state | meaning | what to do |
|---|---|---|
| `ready` | session cookies present **and** URL template configured | nothing — cron will use the live HTTP path |
| `partial_ready` | session looks authenticated, but no URL template in env | do step 3 (discover the endpoint) |
| `not_ready` | no Specter session cookies on disk | do step 1 (run `specter_harvest.py`) |

## Step 1 — capture an authenticated session

Make sure `.env` has:

```bash
SPECTER_LOGIN_EMAIL=...
SPECTER_LOGIN_PASSWORD=...
SPECTER_COMPANY_FEED_URL=https://app.tryspecter.com/signals/company/feed
SPECTER_BROWSER_USE_BIN=/Users/<you>/.browser-use-env/bin/browser-use   # or rely on PATH
```

Then:

```bash
./scripts/run_specter_harvest.sh --probe-limit 12
# or:
python3 scripts/specter_harvest.py --probe-limit 12
```

The script logs in, exports cookies, captures a Clerk JWT when possible,
and writes everything under `.gstack/specter/`. A successful run leaves
you with **at least one** of `__session` (HTTP-only) or `__client_uat*`
(public) cookies on the `tryspecter.com` domain. If MFA/captcha drift
appears, sign in once manually in `browser-use` and re-run — the export
is idempotent.

Validate:

```bash
python3 scripts/specter_lookup.py --readiness
# expect: "state": "partial_ready"
```

## Step 2 — keep the session alive

Specter's `__session` cookie expires. Schedule
`scripts/run_specter_harvest.sh` to run **every 24h** before the weekly
graph enricher cron tick. If it fails (login UI drift, MFA), the
readiness check will flip to `not_ready` and the weekly cron will route
the affected rows to `needs_review` rather than fabricate a `disregard`.

## Railway JSON API (high-signal, POST-heavy)

Specter’s SPA talks to a separate JSON host on Railway (example base:
`https://specter-api-prod.up.railway.app`). Many routes are **POST-only**:
a browser-style **GET** often returns **405** with `{"detail":"Method Not Allowed"}`.
An unauthenticated **POST** typically returns **401** with
`{"detail":"Invalid or expired token"}` — so replay needs a **valid Clerk JWT**
(Authorization: `Bearer …`) and/or the same **browser cookie jar** the app uses.

Example (discover payloads from DevTools → Network when you trigger the UI):

- `POST https://specter-api-prod.up.railway.app/private/users/company-connections`

After `specter_harvest.py` runs, check `.gstack/specter/probe_results.json`: it now
includes **POST** probes for paths listed in `SPECTER_RAILWAY_POST_PATHS` (comma‑separated)
against `SPECTER_RAILWAY_API_BASE` (defaults to the prod host above). Tune paths as you
map more screens (products, traction, news, investor profiles, etc.).

```bash
SPECTER_RAILWAY_API_BASE=https://specter-api-prod.up.railway.app
SPECTER_RAILWAY_POST_PATHS=/private/users/company-connections,/private/another/path
```

## Step 3 — discover the search endpoint (one time)

This is the part that has to be done with eyes on a real session because
Specter's data API isn't published anywhere.

1. In `browser-use` (or any Chromium with the captured cookies), open
   `https://app.tryspecter.com/signals/company/feed`.
2. Open DevTools → Network → filter on `Fetch/XHR`.
3. Type a known **company name** into Specter's search bar — e.g.
   `NayaOne` — and let the autocomplete fire.
4. Find the request whose response contains an array of company-shaped
   objects (name, linkedin_url, website). Right-click → Copy → Copy URL.
5. Replace the search term in the URL with the placeholder `{name}`
   (or `{query}` — both are accepted).
6. Repeat for **person** search (e.g. type a known person name into the
   talent / people search).

Drop both into `.env`:

```bash
SPECTER_COMPANY_SEARCH_URL_TEMPLATE=https://app.tryspecter.com/api/...?q={name}&limit=5
SPECTER_PERSON_SEARCH_URL_TEMPLATE=https://app.tryspecter.com/api/...?q={name}&limit=5
```

If the JSON response wraps the array (e.g. `{"data":{"results":[...]}}`),
tell the lookup module the path with dot-notation:

```bash
SPECTER_RESPONSE_RESULTS_PATH=data.results       # default: "results"
```

And if Specter uses non-standard field names on each result object:

```bash
SPECTER_RESPONSE_ID_FIELD=id                     # default: "id"
SPECTER_RESPONSE_NAME_FIELD=name                 # default: "name"
SPECTER_RESPONSE_LINKEDIN_FIELD=linkedin_url     # default: "linkedin_url"
SPECTER_RESPONSE_WEBSITE_FIELD=website           # default: "website"  (org)
SPECTER_RESPONSE_DOMAIN_FIELD=domain             # default: "domain"   (org)
```

Validate:

```bash
python3 scripts/specter_lookup.py --readiness
# expect: "state": "ready"

python3 scripts/specter_lookup.py --kind org --name "NayaOne"
# expect a status=ok payload (or status=no_match, but NOT status=unavailable)
```

## Step 4 — flip the cron to use it

```bash
# Default cron line
LUMA_ENABLE_GRAPH=1 scripts/luma_weekly.sh

# Surgical: disable Specter temporarily without code change
LUMA_ENABLE_GRAPH=1 LUMA_DISABLE_SPECTER=1 scripts/luma_weekly.sh

# Surgical: Specter-only (skip Cala spend)
LUMA_ENABLE_GRAPH=1 LUMA_DISABLE_CALA=1 scripts/luma_weekly.sh
```

## Honest-confidence behaviour you can rely on

- **`graph_match.verdict == "disregard"`** means **every enabled provider
  explicitly returned `no_match`**. If you see it, both Cala and Specter
  ran and neither found the entity. It is the user's "drop it" verdict.
- **`graph_match.verdict == "needs_review"`** with
  `providers.specter == "unavailable"` means **we couldn't ask Specter**.
  Treat as a retry queue, not a discard.
- **`specter.status == "rejected"`** with `reason == "linkedin_mismatch"`
  means the same as Cala's `rejected`: Specter returned a candidate but
  its `linkedin_url` doesn't match the Luma row's `linkedin_handle`. The
  unified verdict downgrades to `rejected` only when no provider passed.

## Re-discovery triggers

Re-run step 3 whenever you see:

- `partial_ready` in readiness, after a successful step 1.
- A sudden spike of `specter.status == "error"` with HTTP `404` / `400`
  in the per-run output (Specter changed an internal URL).
- An auth wall (`http_401_session_likely_expired`) that persists after a
  fresh `specter_harvest.py` run — the search endpoint may need an
  additional header (e.g. CSRF) that we'd capture here.
