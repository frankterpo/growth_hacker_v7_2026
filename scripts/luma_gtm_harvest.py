#!/usr/bin/env python3
"""
Luma GTM harvest (AI + Tech) — public api2.luma.com only, stdlib, no Ollama.

Discovered from luma.com Next bundles (2026-05):
  GET https://api2.luma.com/discover/bootstrap-page?featured_place_api_id=...
  GET https://api2.luma.com/discover/get-paginated-events?...

Pagination uses pagination_limit + pagination_cursor (NOT ?cursor=).
next_cursor from the JSON is passed as pagination_cursor on the next request.

Outputs JSONL suitable for Hermes + browser-use follow-ups (e.g. guest list
requires auth — GET /event/get-guest-list returns 401 when logged out).

Usage:
  python3 scripts/luma_gtm_harvest.py \\
    --featured-place-api-id discplace-QCcNk3HXowOR97j \\
    --out-dir .gstack/luma/out/europe-smoke \\
    --max-places 2 --max-pages 1 --sleep 0.2
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

API_BASE = "https://api2.luma.com"
UA = "Mozilla/5.0 (compatible; luma-gtm-harvest/1.0; +https://v7labs.com)"

# Fixed discovery categories for the “AI + Tech” slice (from bootstrap-page).
GTM_CATEGORIES: tuple[dict[str, str], ...] = (
    {"discover_category_api_id": "cat-ai", "label": "ai"},
    {"discover_category_api_id": "cat-tech", "label": "tech"},
)


@dataclass(frozen=True)
class PlaceRow:
    api_id: str
    name: str
    slug: str
    latitude: float
    longitude: float


def _http_json(url: str, *, timeout: float = 45.0) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/json"},
        method="GET",
    )
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise SystemExit(f"HTTP {e.code} for {url}\n{e.read(800).decode('utf-8', errors='replace')}") from e
    return json.loads(raw)


def fetch_bootstrap(featured_place_api_id: str) -> dict[str, Any]:
    q = urllib.parse.urlencode({"featured_place_api_id": featured_place_api_id})
    return _http_json(f"{API_BASE}/discover/bootstrap-page?{q}")


def places_from_bootstrap(data: dict[str, Any]) -> list[PlaceRow]:
    rows: list[PlaceRow] = []
    for item in data.get("places") or []:
        p = item.get("place") if isinstance(item, dict) else None
        if not isinstance(p, dict):
            continue
        coord = p.get("coordinate")
        if not isinstance(coord, dict):
            continue
        lat, lon = coord.get("latitude"), coord.get("longitude")
        api_id = p.get("api_id")
        if not isinstance(api_id, str) or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
            continue
        rows.append(
            PlaceRow(
                api_id=api_id,
                name=str(p.get("name") or api_id),
                slug=str(p.get("slug") or ""),
                latitude=float(lat),
                longitude=float(lon),
            )
        )
    return rows


def iter_event_pages(
    *,
    discover_category_api_id: str,
    latitude: float,
    longitude: float,
    pagination_limit: int,
    max_pages: int | None,
    sleep_s: float,
) -> Iterator[dict[str, Any]]:
    """Yield one API response page at a time (each page == one HTTP GET)."""
    cursor: str | None = None
    pages = 0
    while True:
        q: dict[str, Any] = {
            "discover_category_api_id": discover_category_api_id,
            "latitude": latitude,
            "longitude": longitude,
            "pagination_limit": pagination_limit,
        }
        if cursor:
            q["pagination_cursor"] = cursor
        url = f"{API_BASE}/discover/get-paginated-events?{urllib.parse.urlencode(q)}"
        payload = _http_json(url)
        yield payload
        pages += 1
        if max_pages is not None and pages >= max_pages:
            break
        if not payload.get("has_more"):
            break
        nxt = payload.get("next_cursor")
        if not isinstance(nxt, str) or not nxt:
            break
        cursor = nxt
        if sleep_s:
            time.sleep(sleep_s)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__ or "")
    ap.add_argument(
        "--featured-place-api-id",
        action="append",
        default=[],
        help="Region hub id (repeatable), e.g. discplace-QCcNk3HXowOR97j (Europe).",
    )
    ap.add_argument("--out-dir", required=True, help="Directory for manifest + events.jsonl")
    ap.add_argument("--pagination-limit", type=int, default=50, help="Page size (maps to pagination_limit).")
    ap.add_argument("--max-places", type=int, default=0, help="Cap cities per bootstrap (0 = all).")
    ap.add_argument("--max-pages", type=int, default=0, help="Cap pages per city×category (0 = until has_more false).")
    ap.add_argument("--sleep", type=float, default=0.15, help="Delay between paginated requests (seconds).")
    args = ap.parse_args()

    if not args.featured_place_api_id:
        raise SystemExit("Provide at least one --featured-place-api-id")

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jsonl_path = out / "events.jsonl"

    seen_events: set[str] = set()
    stats: dict[str, Any] = {
        "featured_place_api_ids": list(args.featured_place_api_id),
        "categories": [c["discover_category_api_id"] for c in GTM_CATEGORIES],
        "pagination_limit": args.pagination_limit,
        "places_scanned": 0,
        "requests": 0,
        "rows_written": 0,
        "dedup_skipped": 0,
    }

    max_pages = args.max_pages or None

    with jsonl_path.open("w", encoding="utf-8") as sink:
        for fpid in args.featured_place_api_id:
            boot = fetch_bootstrap(fpid)
            stats["requests"] += 1
            places = places_from_bootstrap(boot)
            if args.max_places:
                places = places[: max(0, args.max_places)]

            for place in places:
                stats["places_scanned"] += 1
                for cat in GTM_CATEGORIES:
                    cid = cat["discover_category_api_id"]
                    for payload in iter_event_pages(
                        discover_category_api_id=cid,
                        latitude=place.latitude,
                        longitude=place.longitude,
                        pagination_limit=max(1, min(100, args.pagination_limit)),
                        max_pages=max_pages,
                        sleep_s=args.sleep,
                    ):
                        stats["requests"] += 1
                        entries = payload.get("entries") or []
                        if not isinstance(entries, list):
                            continue
                        for entry in entries:
                            if not isinstance(entry, dict):
                                continue
                            eid = entry.get("api_id")
                            if not isinstance(eid, str):
                                continue
                            if eid in seen_events:
                                stats["dedup_skipped"] += 1
                                continue
                            seen_events.add(eid)

                            ev = entry.get("event") if isinstance(entry.get("event"), dict) else {}
                            record = {
                                "featured_place_api_id": fpid,
                                "discover_category_api_id": cid,
                                "discover_category_label": cat["label"],
                                "place_api_id": place.api_id,
                                "place_name": place.name,
                                "place_slug": place.slug,
                                "event_api_id": eid,
                                "event_name": ev.get("name"),
                                "event_url_slug": ev.get("url"),
                                "public_event_url": f"https://luma.com/{ev.get('url')}" if ev.get("url") else None,
                                "start_at": entry.get("start_at") or ev.get("start_at"),
                                "guest_count": entry.get("guest_count"),
                                "hosts": entry.get("hosts"),
                                "featured_guests": entry.get("featured_guests"),
                                "calendar": entry.get("calendar"),
                            }
                            sink.write(json.dumps(record, ensure_ascii=False) + "\n")
                            stats["rows_written"] += 1

                    if args.sleep:
                        time.sleep(args.sleep)

    (out / "manifest.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "out_dir": str(out), **stats}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
