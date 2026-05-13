#!/usr/bin/env python3
"""
Luma guest list harvest — Layer 3.

Reads an `events_scored.jsonl` (or `events_hydrated.jsonl`), filters to events
with `event.show_guest_list == true` AND `event.guest_count >= --min-guests`
AND optionally `topic_score >= --min-score`, then for each event hits
`api2.luma.com/event/get-guest-list` with the cookies captured by
`scripts/luma_login_harvest.py`. Writes one JSONL line per guest. Resumable.

Run prerequisites:
  1. python3 scripts/luma_login_harvest.py       # one-time, captures cookies
  2. python3 scripts/luma_weekly.sh              # produces events_scored.jsonl

Usage:
  python3 scripts/luma_guestlist_harvest.py \\
    --in  .gstack/luma/runs/2026-W19-final/leads/events_scored.jsonl \\
    --out .gstack/luma/runs/2026-W19-final/leads/guests.jsonl \\
    --min-guests 25 --min-score 4 --max-events 50 --sleep 1.0

Output schema (one JSON object per guest, dedup-safe on event+user):
  event_api_id, event_name, calendar_api_id, calendar_name,
  guest_api_id, user_api_id, name, first_name, last_name,
  email_obfuscated, approval_status, registered_at, approved_at,
  linkedin_handle, twitter_handle, instagram_handle, website,
  bio_short, avatar_url, ticket_type_api_id, role
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
from pathlib import Path
from typing import Any, Iterable


REPO = Path(__file__).resolve().parents[1]
DEFAULT_SESSION = REPO / ".gstack" / "luma" / "session"
UA = "luma-guestlist-harvest/1.0"
_SSL_CTX = ssl.create_default_context()


def _read_cookie_header(session_dir: Path) -> str:
    header_path = session_dir / "cookie_header.txt"
    if header_path.exists():
        h = header_path.read_text(encoding="utf-8").strip()
        if h:
            return h
    cookies_path = session_dir / "cookies.json"
    if not cookies_path.exists():
        raise SystemExit(
            f"No Luma session cookies at {session_dir}. "
            "Run `python3 scripts/luma_login_harvest.py` first."
        )
    data = json.loads(cookies_path.read_text(encoding="utf-8"))
    items: list[dict[str, Any]] = []
    if isinstance(data, list):
        items = [c for c in data if isinstance(c, dict)]
    elif isinstance(data, dict) and isinstance(data.get("cookies"), list):
        items = [c for c in data["cookies"] if isinstance(c, dict)]
    parts: list[str] = []
    for c in items:
        n, v = c.get("name"), c.get("value")
        dom = str(c.get("domain") or "").lower()
        if isinstance(n, str) and isinstance(v, str) and ("lu.ma" in dom or "luma.com" in dom):
            parts.append(f"{n}={v}")
    if not parts:
        raise SystemExit("Cookies file has no lu.ma cookies — re-run luma_login_harvest.py")
    return "; ".join(parts)


def _http_get_json(url: str, *, cookie_header: str, timeout: float = 25.0) -> tuple[int, Any]:
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Cookie": cookie_header,
            "Accept": "application/json",
            "User-Agent": UA,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.getcode(), json.loads(body) if body else None


def _read_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _shrink_guest(g: dict[str, Any], event_meta: dict[str, Any]) -> dict[str, Any]:
    u = g.get("user") if isinstance(g.get("user"), dict) else g
    return {
        **event_meta,
        "guest_api_id": g.get("api_id"),
        "user_api_id": u.get("api_id") if isinstance(u, dict) else None,
        "name": u.get("name") if isinstance(u, dict) else g.get("name"),
        "first_name": u.get("first_name") if isinstance(u, dict) else None,
        "last_name": u.get("last_name") if isinstance(u, dict) else None,
        "email_obfuscated": g.get("email") or u.get("email") if isinstance(u, dict) else g.get("email"),
        "approval_status": g.get("approval_status"),
        "registered_at": g.get("registered_at") or g.get("created_at"),
        "approved_at": g.get("approved_at"),
        "linkedin_handle": (u.get("linkedin_handle") if isinstance(u, dict) else None) or g.get("linkedin_handle"),
        "twitter_handle": (u.get("twitter_handle") if isinstance(u, dict) else None) or g.get("twitter_handle"),
        "instagram_handle": (u.get("instagram_handle") if isinstance(u, dict) else None),
        "website": (u.get("website") if isinstance(u, dict) else None),
        "bio_short": (u.get("bio_short") if isinstance(u, dict) else None) or g.get("bio_short"),
        "avatar_url": (u.get("avatar_url") if isinstance(u, dict) else None) or g.get("avatar_url"),
        "ticket_type_api_id": g.get("ticket_type_api_id") or (g.get("ticket_type") or {}).get("api_id"),
        "role": g.get("role"),
    }


def _eligible(row: dict[str, Any], *, min_guests: int, min_score: int) -> tuple[bool, str]:
    ev = row.get("event") or {}
    if not ev.get("show_guest_list"):
        return False, "guest_list_closed"
    gc = ev.get("guest_count") or 0
    if isinstance(gc, int) and gc < min_guests:
        return False, f"guest_count<{min_guests}"
    score = row.get("topic_score") or 0
    if isinstance(score, (int, float)) and score < min_score:
        return False, f"topic_score<{min_score}"
    return True, "ok"


def harvest_one(
    event_api_id: str,
    *,
    cookie_header: str,
    page_size: int,
    sleep_s: float,
    event_meta: dict[str, Any],
) -> dict[str, Any]:
    cursor = None
    guests_emitted = 0
    pages = 0
    while True:
        qs: dict[str, Any] = {"event_api_id": event_api_id, "pagination_limit": page_size}
        if cursor:
            qs["pagination_cursor"] = cursor
        url = f"https://api2.luma.com/event/get-guest-list?{urllib.parse.urlencode(qs)}"
        try:
            status, body = _http_get_json(url, cookie_header=cookie_header)
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return {"event_api_id": event_api_id, "status": "session_expired",
                        "guests_emitted": guests_emitted, "pages": pages, "http": 401}
            if e.code == 403:
                return {"event_api_id": event_api_id, "status": "forbidden_or_private",
                        "guests_emitted": guests_emitted, "pages": pages, "http": 403}
            return {"event_api_id": event_api_id, "status": "http_error",
                    "guests_emitted": guests_emitted, "pages": pages, "http": e.code,
                    "msg": e.reason or str(e)[:200]}
        except Exception as e:  # noqa: BLE001
            return {"event_api_id": event_api_id, "status": "error",
                    "guests_emitted": guests_emitted, "pages": pages, "msg": str(e)[:200]}
        pages += 1
        entries = (body or {}).get("entries") or []
        for g in entries:
            if isinstance(g, dict):
                row = _shrink_guest(g, event_meta)
                yield row  # noqa: B901 — generator-style emit via outer wrapper
                guests_emitted += 1
        if not (body or {}).get("has_more"):
            break
        cursor = (body or {}).get("next_cursor")
        if not cursor:
            break
        if sleep_s:
            time.sleep(sleep_s)
    return {"event_api_id": event_api_id, "status": "ok",
            "guests_emitted": guests_emitted, "pages": pages}


def _harvest_drive(
    event_api_id: str,
    *,
    cookie_header: str,
    page_size: int,
    sleep_s: float,
    event_meta: dict[str, Any],
    sink,
    seen_keys: set[str],
) -> dict[str, Any]:
    """Wraps `harvest_one` so we can collect both rows and the final outcome
    dict that the generator returns via StopIteration.value."""
    gen = harvest_one(
        event_api_id,
        cookie_header=cookie_header,
        page_size=page_size,
        sleep_s=sleep_s,
        event_meta=event_meta,
    )
    outcome: dict[str, Any]
    try:
        while True:
            try:
                row = next(gen)
            except StopIteration as stop:
                outcome = stop.value if isinstance(stop.value, dict) else {
                    "event_api_id": event_api_id, "status": "ok"}
                break
            key = f"{row.get('event_api_id')}|{row.get('user_api_id') or row.get('guest_api_id')}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            sink.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        outcome = {"event_api_id": event_api_id, "status": "error", "msg": str(e)[:200]}
    return outcome


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__ or "",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="in_path", required=True,
                    help="events_scored.jsonl or events_hydrated.jsonl")
    ap.add_argument("--out", dest="out_path", required=True,
                    help="Output JSONL (one guest per line). Resumable.")
    ap.add_argument("--report", default=None,
                    help="Optional per-event outcome JSONL (status, http, guests_emitted).")
    ap.add_argument("--session-dir", default=str(DEFAULT_SESSION))
    ap.add_argument("--page-size", type=int, default=100)
    ap.add_argument("--sleep", type=float, default=1.0)
    ap.add_argument("--min-guests", type=int, default=25)
    ap.add_argument("--min-score", type=int, default=0,
                    help="Minimum topic_score (when input is events_scored.jsonl).")
    ap.add_argument("--max-events", type=int, default=0,
                    help="Cap events processed this run (0 = all).")
    args = ap.parse_args()

    in_path = Path(args.in_path)
    out_path = Path(args.out_path)
    if not in_path.exists():
        raise SystemExit(f"Input not found: {in_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    cookie_header = _read_cookie_header(Path(args.session_dir))

    seen_event_ids: set[str] = set()
    if out_path.exists():
        for row in _read_jsonl(out_path):
            eid = row.get("event_api_id")
            if isinstance(eid, str):
                seen_event_ids.add(eid)
    seen_keys: set[str] = set()  # in-run dedup for guests across pagination

    rows_in = list(_read_jsonl(in_path))
    queue: list[dict[str, Any]] = []
    reasons: dict[str, int] = {}
    for row in rows_in:
        ev = row.get("event") or {}
        eid = ev.get("api_id")
        if not isinstance(eid, str):
            reasons["no_event_id"] = reasons.get("no_event_id", 0) + 1
            continue
        if eid in seen_event_ids:
            reasons["already_done"] = reasons.get("already_done", 0) + 1
            continue
        ok, why = _eligible(row, min_guests=args.min_guests, min_score=args.min_score)
        if not ok:
            reasons[why] = reasons.get(why, 0) + 1
            continue
        queue.append(row)
    if args.max_events:
        queue = queue[: args.max_events]

    print(f"input events: {len(rows_in)}  eligible queue: {len(queue)}  "
          f"filter_reasons: {reasons}", file=sys.stderr)

    if not queue:
        print(json.dumps({"ok": True, "queue_size": 0, "filter_reasons": reasons,
                          "session_dir": args.session_dir}, indent=2))
        return 0

    outcomes: list[dict[str, Any]] = []
    with out_path.open("a", encoding="utf-8") as sink:
        for row in queue:
            ev = row.get("event") or {}
            cal = row.get("calendar") or {}
            meta = {
                "event_api_id": ev.get("api_id"),
                "event_name": ev.get("name"),
                "event_url": ev.get("url"),
                "event_start_at": ev.get("start_at"),
                "guest_count_advertised": ev.get("guest_count"),
                "calendar_api_id": cal.get("api_id"),
                "calendar_name": cal.get("name"),
                "calendar_website": cal.get("website"),
                "topic_score": row.get("topic_score"),
                "topics": row.get("topics"),
            }
            outcome = _harvest_drive(
                ev["api_id"],
                cookie_header=cookie_header,
                page_size=args.page_size,
                sleep_s=args.sleep,
                event_meta=meta,
                sink=sink,
                seen_keys=seen_keys,
            )
            outcomes.append(outcome)
            print(f"  · {ev['api_id']}  {outcome.get('status')}  "
                  f"guests={outcome.get('guests_emitted')}  pages={outcome.get('pages')}",
                  file=sys.stderr)
            if outcome.get("status") == "session_expired":
                print("  ** session expired — re-run scripts/luma_login_harvest.py and resume.",
                      file=sys.stderr)
                break
            if args.sleep:
                time.sleep(args.sleep)

    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        with Path(args.report).open("a", encoding="utf-8") as rh:
            for o in outcomes:
                rh.write(json.dumps(o, ensure_ascii=False) + "\n")

    status_counts: dict[str, int] = {}
    for o in outcomes:
        s = o.get("status", "unknown")
        status_counts[s] = status_counts.get(s, 0) + 1
    print(json.dumps({
        "ok": True,
        "events_processed": len(outcomes),
        "guests_written_this_run": len(seen_keys),
        "status_counts": status_counts,
        "filter_reasons": reasons,
        "in": str(in_path),
        "out": str(out_path),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
