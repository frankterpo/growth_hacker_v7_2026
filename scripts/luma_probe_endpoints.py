#!/usr/bin/env python3
"""One-off: probe api2.luma.com discover paths (edit PATHS as needed)."""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

UA = "Mozilla/5.0 (compatible; luma-probe/1.0)"

PATHS = [
    "discover/bootstrap-page?featured_place_api_id=discplace-QCcNk3HXowOR97j",
    "discover/category-page?category_api_id=cat-ai&featured_place_api_id=discplace-QCcNk3HXowOR97j",
    "discover/category-page?category_api_id=cat-tech&featured_place_api_id=discplace-QCcNk3HXowOR97j",
    "discover/place-events-page?place_api_id=discplace-FC4SDMUVXiFtMOr",
    "discover/events-page?place_api_id=discplace-FC4SDMUVXiFtMOr",
    "discover/place-page?place_api_id=discplace-FC4SDMUVXiFtMOr",
    "discover/place?place_api_id=discplace-FC4SDMUVXiFtMOr",
    "discover/feed-page?featured_place_api_id=discplace-QCcNk3HXowOR97j",
    "discover/feed?featured_place_api_id=discplace-QCcNk3HXowOR97j",
]


def main() -> None:
    ctx = ssl.create_default_context()
    for path in PATHS:
        url = f"https://api2.luma.com/{path}"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        try:
            with urllib.request.urlopen(req, timeout=25, context=ctx) as resp:
                body = resp.read(1200)
                code = resp.getcode()
        except urllib.error.HTTPError as e:
            code = e.code
            body = e.read(400)
        except Exception as e:  # noqa: BLE001
            print(f"ERR {path} :: {e}")
            continue
        head = body.decode("utf-8", errors="replace").replace("\n", " ")[:200]
        print(f"{code:3d}  {path}")
        print(f"      {head}")
        print()


if __name__ == "__main__":
    main()
