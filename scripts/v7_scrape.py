#!/usr/bin/env python3
"""
Stage 1 of the V7 -> Cala/Specter -> Hermes pipeline.

Scrapes https://www.v7labs.com/customer-stories deterministically with stdlib only
(no Framer JSON guessing, no headless browser). Emits one record per customer story:

    {
      "slug":          "abyss-solutions",
      "brand_guess":   "Abyss Solutions",
      "story_url":     "https://www.v7labs.com/customer-stories/abyss-solutions",
      "story_title":   "How Abyss uses V7 Darwin to advance critical ...",
      "logo_alt":      "Abyss Logo"
    }

Slug -> canonical domain resolution is intentionally deferred to Stage 2 (Cala
entity_search) so this script stays offline-friendly and re-runnable.

Output: .gstack/v7/clients.json (overwrites)
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

INDEX_URL = "https://www.v7labs.com/customer-stories"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/138.0.0.0 Safari/537.36"
)

SLUG_RE = re.compile(r"/customer-stories/([a-z0-9][a-z0-9-]+)(?:[\"'/?#]|$)")
TITLE_RE = re.compile(r"<title>([^<]+)</title>", re.IGNORECASE)
LOGO_ALT_RE = re.compile(r'alt="([^"]{1,80}[Ll]ogo)"')

# Slugs that are not real customers (V7 service pages that happen to live under
# /customer-stories/). Add new ones here as you encounter them.
NON_CUSTOMER_SLUGS = {
    "scaling-automotive-ai-with-complete-data-annotation-services",
}


def fetch(url: str, *, timeout: float = 20.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def humanize_slug(slug: str) -> str:
    parts = slug.split("-")
    fixed = []
    for p in parts:
        if p.lower() in {"ai", "ml", "ar", "vr", "ui", "ux", "api", "sdk", "ip"}:
            fixed.append(p.upper())
        elif p.lower() == "mtc":
            fixed.append("MTC")
        else:
            fixed.append(p.capitalize())
    return " ".join(fixed)


def extract_slugs(index_html: str) -> list[str]:
    raw = sorted(set(SLUG_RE.findall(index_html)))
    return [s for s in raw if s not in NON_CUSTOMER_SLUGS]


def enrich_story(slug: str) -> dict[str, str | None]:
    story_url = f"https://www.v7labs.com/customer-stories/{slug}"
    rec: dict[str, str | None] = {
        "slug": slug,
        "brand_guess": humanize_slug(slug),
        "story_url": story_url,
        "story_title": None,
        "logo_alt": None,
    }
    try:
        page = fetch(story_url)
    except (urllib.error.URLError, TimeoutError) as e:
        rec["error"] = f"fetch_failed: {e}"
        return rec

    title_match = TITLE_RE.search(page)
    if title_match:
        rec["story_title"] = html.unescape(title_match.group(1)).strip()

    logo_match = LOGO_ALT_RE.search(page)
    if logo_match:
        rec["logo_alt"] = logo_match.group(1).strip()

    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__ or "")
    ap.add_argument("--out", default=".gstack/v7/clients.json")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--delay", type=float, default=0.0, help="Per-request delay (s).")
    args = ap.parse_args()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"GET {INDEX_URL}", file=sys.stderr)
    index_html = fetch(INDEX_URL)
    slugs = extract_slugs(index_html)
    print(f"  found {len(slugs)} customer slugs", file=sys.stderr)

    records: list[dict[str, str | None]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futs = {pool.submit(enrich_story, s): s for s in slugs}
        for fut in as_completed(futs):
            rec = fut.result()
            slug = rec.get("slug") or "?"
            ok = "ok" if rec.get("story_title") else "no-title"
            print(f"  [{ok}] {slug}", file=sys.stderr)
            records.append(rec)
            if args.delay:
                time.sleep(args.delay)

    records.sort(key=lambda r: r.get("slug") or "")
    out_path.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out_path} ({len(records)} records)", file=sys.stderr)
    print(json.dumps({"count": len(records), "out": str(out_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
