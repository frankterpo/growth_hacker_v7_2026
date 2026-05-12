#!/usr/bin/env python3
"""
Luma GTM — layer 2 hydrator.

Takes the JSONL written by `luma_gtm_harvest.py` and N+1 fetches
`GET https://api2.luma.com/event/get?event_api_id=...` for each unique
event_api_id, projecting to a slim "GTM record" optimised for prospecting:

  - full host roster (often 2-3x larger than the discover row)
  - organising `calendar` (org slug, website, linkedin/twitter/instagram)
  - `featured_infos` (Luma's sponsor strip)
  - `featured_guests` (public spotlight users)
  - `categories` (event taxonomy)
  - `sessions` (multi-track agenda)
  - `description_mirror.content` (full body)
  - location facets, ticket pricing, capacity / sold-out

No auth needed. Resumable (skips event_api_ids already present in
the output file). Public api2.luma.com only.

Usage:
  python3 scripts/luma_hydrate_events.py \\
    --in  .gstack/luma/out/europe-smoke/events.jsonl \\
    --out .gstack/luma/out/europe-smoke/events_hydrated.jsonl \\
    --workers 4 --sleep 0.15

For the guest list (logged-in only, returns 401 from this script),
see scripts/LUMA_GUESTLIST_BROWSER_USE.md.
"""

from __future__ import annotations

import argparse
import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

API_BASE = "https://api2.luma.com"
UA = "Mozilla/5.0 (compatible; luma-gtm-hydrate/1.0; +https://v7labs.com)"

_SSL_CTX = ssl.create_default_context()


def _fetch_event(event_api_id: str, *, timeout: float = 30.0) -> dict[str, Any]:
    q = urllib.parse.urlencode({"event_api_id": event_api_id})
    req = urllib.request.Request(
        f"{API_BASE}/event/get?{q}",
        headers={"User-Agent": UA, "Accept": "application/json"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _slim_host(h: dict[str, Any]) -> dict[str, Any]:
    return {k: h.get(k) for k in (
        "api_id", "name", "first_name", "last_name", "username",
        "website", "linkedin_handle", "twitter_handle", "instagram_handle",
        "tiktok_handle", "youtube_handle", "bio_short", "is_verified",
        "timezone", "avatar_url",
    )}


def _slim_calendar(c: dict[str, Any]) -> dict[str, Any]:
    """Calendars in Luma == organising entity. The GTM gold record."""
    return {k: c.get(k) for k in (
        "api_id", "name", "slug", "description_short",
        "website", "linkedin_handle", "twitter_handle",
        "instagram_handle", "tiktok_handle", "youtube_handle",
        "is_personal", "personal_user_api_id",
        "luma_plan", "luma_plus_active",
        "geo_city", "geo_country", "geo_region", "city",
        "verified_at", "avatar_url", "social_image_url",
    )}


def _slim_category(cat: dict[str, Any]) -> dict[str, Any]:
    return {k: cat.get(k) for k in ("api_id", "slug", "name", "subscriber_count")}


def _slim_event_shell(ev: dict[str, Any]) -> dict[str, Any]:
    geo = ev.get("geo_address_info") or {}
    return {
        "api_id": ev.get("api_id"),
        "name": ev.get("name"),
        "url_slug": ev.get("url"),
        "start_at": ev.get("start_at"),
        "end_at": ev.get("end_at"),
        "timezone": ev.get("timezone"),
        "event_type": ev.get("event_type"),
        "location_type": ev.get("location_type"),
        "visibility": ev.get("visibility"),
        "show_guest_list": ev.get("show_guest_list"),
        "calendar_api_id": ev.get("calendar_api_id"),
        "city": geo.get("city"),
        "region": geo.get("region"),
        "country": geo.get("country"),
        "country_code": geo.get("country_code"),
        "full_address": geo.get("full_address"),
        "coordinate": ev.get("coordinate"),
    }


def _slim_ticket_info(ti: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(ti, dict):
        return None
    return {k: ti.get(k) for k in (
        "is_free", "is_sold_out", "is_near_capacity",
        "price", "max_price", "spots_remaining", "require_approval",
    )}


def _slim_featured_info(fi: dict[str, Any]) -> dict[str, Any]:
    return {k: fi.get(k) for k in ("api_id", "name", "name_raw", "type", "path", "avatar_url")}


def _slim_session(s: dict[str, Any]) -> dict[str, Any]:
    return {k: s.get(k) for k in ("api_id", "name", "start_at", "end_at", "location")}


def _slim_description(dm: dict[str, Any] | None) -> str | None:
    if not isinstance(dm, dict):
        return None
    content = dm.get("content")
    if isinstance(content, str):
        return content
    # Some payloads return a Pretext-style block; serialise compactly.
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


def hydrate(event_api_id: str, provenance: dict[str, Any]) -> dict[str, Any]:
    d = _fetch_event(event_api_id)
    ev = d.get("event") or {}
    hosts = [_slim_host(h) for h in (d.get("hosts") or []) if isinstance(h, dict)]
    featured_guests = [_slim_host(h) for h in (d.get("featured_guests") or []) if isinstance(h, dict)]
    featured_infos = [_slim_featured_info(fi) for fi in (d.get("featured_infos") or []) if isinstance(fi, dict)]
    sessions = [_slim_session(s) for s in (d.get("sessions") or []) if isinstance(s, dict)]
    categories = [_slim_category(c) for c in (d.get("categories") or []) if isinstance(c, dict)]
    return {
        **provenance,
        "event_api_id": event_api_id,
        "event": _slim_event_shell(ev),
        "guest_count": d.get("guest_count"),
        "ticket_count": d.get("ticket_count"),
        "registration_availability": d.get("registration_availability"),
        "ticket_info": _slim_ticket_info(d.get("ticket_info")),
        "calendar": _slim_calendar(d.get("calendar") or {}),
        "hosts": hosts,
        "featured_guests": featured_guests,
        "featured_infos": featured_infos,
        "sessions": sessions,
        "categories": categories,
        "description": _slim_description(d.get("description_mirror")),
        "_fetched_at": int(time.time()),
    }


def _read_discover_rows(in_path: Path) -> dict[str, dict[str, Any]]:
    """
    Returns {event_api_id: provenance_dict} keyed by first occurrence.
    Provenance = lightweight discover context (which city/category surfaced it).
    """
    out: dict[str, dict[str, Any]] = {}
    with in_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            eid = row.get("event_api_id")
            if not isinstance(eid, str) or eid in out:
                continue
            out[eid] = {
                "via_featured_place_api_id": row.get("featured_place_api_id"),
                "via_discover_category_api_id": row.get("discover_category_api_id"),
                "via_discover_category_label": row.get("discover_category_label"),
                "via_place_api_id": row.get("place_api_id"),
                "via_place_name": row.get("place_name"),
                "via_place_slug": row.get("place_slug"),
            }
    return out


def _read_already_hydrated(out_path: Path) -> set[str]:
    if not out_path.exists():
        return set()
    seen: set[str] = set()
    with out_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            eid = row.get("event_api_id")
            if isinstance(eid, str):
                seen.add(eid)
    return seen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__ or "")
    ap.add_argument("--in", dest="in_path", required=True, help="JSONL written by luma_gtm_harvest.py")
    ap.add_argument("--out", dest="out_path", required=True, help="JSONL hydrated output (resumable)")
    ap.add_argument("--workers", type=int, default=4, help="Concurrent /event/get fetches")
    ap.add_argument("--sleep", type=float, default=0.1, help="Per-future delay (seconds)")
    ap.add_argument("--max-events", type=int, default=0, help="Cap total events hydrated this run (0 = all)")
    args = ap.parse_args()

    in_path = Path(args.in_path)
    out_path = Path(args.out_path)
    if not in_path.exists():
        raise SystemExit(f"Input not found: {in_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    discovered = _read_discover_rows(in_path)
    already = _read_already_hydrated(out_path)
    todo = [eid for eid in discovered if eid not in already]
    if args.max_events:
        todo = todo[: args.max_events]

    print(
        f"discover events: {len(discovered)}  "
        f"already hydrated: {len(already)}  "
        f"this run: {len(todo)}",
        file=sys.stderr,
    )
    if not todo:
        print(json.dumps({"ok": True, "new": 0, "total": len(already)}))
        return 0

    written = 0
    errors: list[dict[str, Any]] = []
    with out_path.open("a", encoding="utf-8") as sink, ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futs = {pool.submit(hydrate, eid, discovered[eid]): eid for eid in todo}
        for fut in as_completed(futs):
            eid = futs[fut]
            try:
                rec = fut.result()
            except urllib.error.HTTPError as e:
                errors.append({"event_api_id": eid, "status": e.code, "msg": e.reason})
                print(f"  HTTP {e.code} {eid}", file=sys.stderr)
                continue
            except Exception as e:  # noqa: BLE001
                errors.append({"event_api_id": eid, "msg": str(e)[:200]})
                print(f"  ERR {eid}: {e}", file=sys.stderr)
                continue
            sink.write(json.dumps(rec, ensure_ascii=False) + "\n")
            written += 1
            if args.sleep:
                time.sleep(args.sleep)

    if errors:
        (out_path.parent / "hydrate_errors.json").write_text(
            json.dumps(errors, indent=2), encoding="utf-8"
        )

    print(json.dumps({
        "ok": True,
        "new": written,
        "errors": len(errors),
        "total_in_out_file": len(already) + written,
        "out": str(out_path),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
