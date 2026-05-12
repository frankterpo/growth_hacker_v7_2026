# Luma layer 3 — guest list (auth-gated, browser-use recipe)

`/event/get-guest-list` is **the** GTM payload: every approved guest, not just
the 8-10 `featured_guests`. It returns **401** to anonymous requests. This
recipe is the deterministic browser-use script Hermes should run after
`luma_gtm_harvest.py` + `luma_hydrate_events.py`.

## Why browser-use and not stdlib

Luma's session is **Centrifugo-backed cookies** (`luma-auth-token` +
`luma_csrf` family) set after the email-link / Google sign-in flow. There is
no public token API. The only durable path is:

1. Sign in once in browser-use (cookies persist in its profile).
2. Reuse those cookies for every subsequent **plain `GET`** call to
   `api2.luma.com`.

You do NOT need to drive the UI of each event page — once cookies are
attached, `/event/get-guest-list` works as a normal JSON GET.

## Endpoint contract (verified anonymous: 401)

```
GET https://api2.luma.com/event/get-guest-list?event_api_id=evt-...&pagination_limit=100
```

Pagination follows the same convention as `discover/get-paginated-events`:

| key | meaning |
|-----|--------|
| `pagination_limit` | page size (50–100 ok) |
| `pagination_cursor` | echo back `next_cursor` from prior response |
| response `has_more` | continue while true |

Visibility flag on each event: `event.show_guest_list`. If `false`, expect
403 even when authenticated — skip those.

## browser-use sketch (Hermes-side)

```python
# pseudo: replace with your browser-use SDK shape
async def harvest_guest_lists(event_ids: list[str], *, page_size: int = 100):
    await browser.goto("https://luma.com/signin")
    # one-time human-in-the-loop or stored cookies; skip if profile is warm
    if not await is_signed_in():
        await browser.email_signin("hermes@v7labs.com")
        await browser.wait_for_signed_in()

    # cookie jar is shared with fetch() through browser context
    out = []
    for eid in event_ids:
        cursor = None
        while True:
            qs = {"event_api_id": eid, "pagination_limit": page_size}
            if cursor:
                qs["pagination_cursor"] = cursor
            res = await browser.request_json(
                "GET", f"https://api2.luma.com/event/get-guest-list?{urlencode(qs)}"
            )
            if res.status == 401:
                raise RuntimeError("session dropped — re-auth required")
            if res.status == 403:
                break  # show_guest_list=false on this event
            body = res.json
            for g in body.get("entries", []):
                out.append({"event_api_id": eid, **g})
            if not body.get("has_more"):
                break
            cursor = body.get("next_cursor")
    return out
```

## Recommended pipeline order (Hermes cron)

```
1. luma_gtm_harvest.py            — bootstrap + discover, public, free
2. luma_hydrate_events.py         — /event/get fan-out, public, free
3. THIS RECIPE (browser-use)      — guest list for events where:
                                    event.show_guest_list == true
                                    AND guest_count >= MIN_THRESHOLD (e.g. 25)
4. Push to HubSpot via MCP        — orgs (calendar), hosts, featured_infos,
                                    plus guest_list rows from step 3
```

## Rate-limit + stealth notes

- Stick to `~1 req/s` on guest-list. Discover is more permissive but be polite.
- Reuse one signed-in browser-use session across the whole run — re-auth
  triggers email-link friction.
- Luma fingerprints UA strings; let browser-use send its real Chromium UA,
  do NOT override.
- A `pagination_limit` >100 returns 422.

## Output schema (one row per guest)

Minimum projection to keep in JSONL:

```
event_api_id, api_id (guest), user_api_id, name, first_name, last_name,
email_obfuscated, approval_status, registered_at, approved_at,
linkedin_handle, twitter_handle, instagram_handle, website,
bio_short, avatar_url, ticket_type_api_id, role
```

`email_obfuscated` is the format Luma serves on guest-list APIs (e.g.
`j***@v7labs.com`). Full emails require **event-host privilege** on each
calendar — Hermes does not get those by default. Do not pretend you can.

## Failure modes & how to surface them

| HTTP / state | Meaning | Action |
|--------------|---------|--------|
| 401 | session cookies dropped | re-auth, retry once |
| 403 | `show_guest_list=false` OR rate-limited | skip event, continue |
| 422 | bad pagination_limit | clamp to 100 |
| empty `entries` and `has_more=false` from page 1 | private event | skip |
| `event-deleted` on /event/get earlier | event removed mid-run | drop from queue |
