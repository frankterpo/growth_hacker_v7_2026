#!/usr/bin/env python3
"""
Luma event lookup — answers "is X person/company at any Luma event?" by
searching the harvested + hydrated event corpus on disk. Read-only.

Looks in (in order of preference):
  --in <path>                                 explicit JSONL
  .gstack/luma/runs/<latest>/events_hydrated.jsonl
  .gstack/luma/runs/<latest>/events.jsonl

Match surface for each event row:
  - event.name, event.url_slug, event.description (Pretext flattened)
  - hosts[].{name, username, bio_short, linkedin_handle, twitter_handle, website}
  - featured_guests[].{name, username, bio_short, linkedin_handle, website}
  - featured_infos[].{name, name_raw}         # sponsors / strip
  - calendar.{name, website, linkedin_url, twitter_handle, instagram_handle}
  - sessions[].{name, description}            # multi-track agenda

Match rules: case-insensitive substring on any of the configured needles.
For names, also tries a `tidy` form (lowercased, hyphens/underscores → spaces).
`--linkedin /in/<slug>` matches that exact handle anywhere in the event row.
`--domain example.com` matches against calendar.website + host websites.

CLI:
  python3 scripts/luma_event_lookup.py --name "Browser Use" --name "Magnus Müller" \\
      --linkedin /in/gregorzunic --linkedin /in/manuelsuess \\
      --domain browser-use.com --json

Outputs JSON: { query, source_file, events_scanned, matches: [...], summary: {...} }
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# input discovery
# ---------------------------------------------------------------------------

def _latest_run(root: Path) -> Path | None:
    if not root.exists():
        return None
    runs = sorted([p for p in root.iterdir() if p.is_dir()])
    return runs[-1] if runs else None


def _discover_input(explicit: str | None, run_root: Path) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.exists():
            raise SystemExit(f"--in not found: {p}")
        return p
    latest = _latest_run(run_root)
    if not latest:
        raise SystemExit(f"No Luma runs under {run_root} — run scripts/luma_weekly.sh first.")
    for candidate in ("events_hydrated.jsonl", "events.jsonl"):
        c = latest / candidate
        if c.exists():
            return c
    raise SystemExit(f"No events.jsonl in latest run {latest}.")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _flatten_pretext(blob: Any) -> str:
    if not blob:
        return ""
    if isinstance(blob, str):
        try:
            blob = json.loads(blob)
        except json.JSONDecodeError:
            return blob
    out: list[str] = []

    def walk(n: Any) -> None:
        if isinstance(n, dict):
            t = n.get("text")
            if isinstance(t, str):
                out.append(t)
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for x in n:
                walk(x)

    walk(blob)
    return " ".join(out)


def _norm(s: str | None) -> str:
    if not isinstance(s, str):
        return ""
    return s.lower().strip()


def _tidy(s: str | None) -> str:
    n = _norm(s)
    return n.replace("-", " ").replace("_", " ")


def _norm_linkedin(s: str | None) -> str:
    n = _norm(s)
    for marker in ("/in/", "/company/", "linkedin.com/in/", "linkedin.com/company/"):
        if marker in n:
            n = n.rsplit(marker, 1)[-1]
            break
    return n.strip("/")


def _norm_domain(s: str | None) -> str:
    n = _norm(s)
    for prefix in ("https://", "http://"):
        if n.startswith(prefix):
            n = n[len(prefix):]
    if n.startswith("www."):
        n = n[4:]
    return n.split("/", 1)[0].strip()


# ---------------------------------------------------------------------------
# event traversal
# ---------------------------------------------------------------------------

def _person_records(row: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    for kind in ("hosts", "featured_guests"):
        for p in (row.get(kind) or []):
            if isinstance(p, dict):
                yield kind.rstrip("s"), p


def _calendar(row: dict[str, Any]) -> dict[str, Any]:
    cal = row.get("calendar")
    return cal if isinstance(cal, dict) else {}


def _featured_infos(row: dict[str, Any]) -> list[dict[str, Any]]:
    fi = row.get("featured_infos")
    return [x for x in (fi or []) if isinstance(x, dict)]


def _haystack_text(row: dict[str, Any]) -> str:
    ev = row.get("event") or {}
    cal = _calendar(row)
    parts: list[str] = [
        ev.get("name") or "",
        ev.get("url_slug") or "",
        cal.get("name") or "",
        cal.get("description_short") or "",
        cal.get("website") or "",
        _flatten_pretext(row.get("description")),
    ]
    for p_kind, p in _person_records(row):
        parts.extend([
            p.get("name") or "",
            p.get("username") or "",
            p.get("bio_short") or "",
            p.get("linkedin_handle") or "",
            p.get("twitter_handle") or "",
            p.get("website") or "",
        ])
    for fi in _featured_infos(row):
        parts.extend([fi.get("name") or "", fi.get("name_raw") or ""])
    for s in (row.get("sessions") or []):
        if isinstance(s, dict):
            parts.extend([s.get("name") or "", s.get("description") or ""])
    return " || ".join(p for p in parts if p)


def _match_row(
    row: dict[str, Any],
    *,
    name_needles: list[str],
    linkedin_needles: list[str],
    domain_needles: list[str],
) -> dict[str, Any] | None:
    haystack = _norm(_haystack_text(row))
    tidy_haystack = _tidy(_haystack_text(row))
    hits: list[dict[str, Any]] = []

    for needle in name_needles:
        n = _norm(needle)
        t = _tidy(needle)
        if not n:
            continue
        if n in haystack or t in tidy_haystack:
            # locate where it hit (most informative)
            where = _locate_name(row, n, t)
            hits.append({"kind": "name", "needle": needle, "where": where})

    for li in linkedin_needles:
        slug = _norm_linkedin(li)
        if not slug:
            continue
        if _has_linkedin(row, slug):
            hits.append({"kind": "linkedin", "needle": li, "slug": slug,
                         "where": _locate_linkedin(row, slug)})

    for d in domain_needles:
        dn = _norm_domain(d)
        if not dn:
            continue
        if _has_domain(row, dn):
            hits.append({"kind": "domain", "needle": d, "where": _locate_domain(row, dn)})

    if not hits:
        return None
    ev = row.get("event") or {}
    cal = _calendar(row)
    return {
        "event_name": ev.get("name"),
        "event_api_id": ev.get("api_id"),
        "start_at": ev.get("start_at"),
        "url": ev.get("url"),
        "calendar": {"name": cal.get("name"), "api_id": cal.get("api_id"),
                     "website": cal.get("website")},
        "show_guest_list": ev.get("show_guest_list"),
        "guest_count": ev.get("guest_count"),
        "matches": hits,
    }


def _locate_name(row: dict[str, Any], norm_needle: str, tidy_needle: str) -> dict[str, Any]:
    ev = row.get("event") or {}
    if norm_needle in _norm(ev.get("name")) or tidy_needle in _tidy(ev.get("name")):
        return {"field": "event.name", "value": ev.get("name")}
    cal = _calendar(row)
    if norm_needle in _norm(cal.get("name")) or tidy_needle in _tidy(cal.get("name")):
        return {"field": "calendar.name", "value": cal.get("name")}
    for kind, p in _person_records(row):
        if norm_needle in _norm(p.get("name")) or tidy_needle in _tidy(p.get("name")):
            return {"field": f"{kind}.name", "person": p.get("name"),
                    "linkedin": p.get("linkedin_handle")}
        if norm_needle in _norm(p.get("bio_short")):
            return {"field": f"{kind}.bio_short", "person": p.get("name"),
                    "snippet": (p.get("bio_short") or "")[:160]}
    for fi in _featured_infos(row):
        for f in ("name", "name_raw"):
            v = fi.get(f)
            if v and (norm_needle in _norm(v) or tidy_needle in _tidy(v)):
                return {"field": f"featured_infos.{f}", "value": v}
    desc = _flatten_pretext(row.get("description"))
    if norm_needle in _norm(desc):
        idx = _norm(desc).find(norm_needle)
        return {"field": "description", "snippet": desc[max(0, idx - 60): idx + 120]}
    return {"field": "unlocated"}


def _has_linkedin(row: dict[str, Any], slug: str) -> bool:
    if not slug:
        return False
    cal = _calendar(row)
    if _norm_linkedin(cal.get("linkedin_url")) == slug:
        return True
    for _, p in _person_records(row):
        if _norm_linkedin(p.get("linkedin_handle")) == slug:
            return True
    return slug in _norm(_haystack_text(row))


def _locate_linkedin(row: dict[str, Any], slug: str) -> dict[str, Any]:
    cal = _calendar(row)
    if _norm_linkedin(cal.get("linkedin_url")) == slug:
        return {"field": "calendar.linkedin_url", "value": cal.get("linkedin_url")}
    for kind, p in _person_records(row):
        if _norm_linkedin(p.get("linkedin_handle")) == slug:
            return {"field": f"{kind}.linkedin_handle", "person": p.get("name"),
                    "handle": p.get("linkedin_handle")}
    return {"field": "description_or_bio"}


def _has_domain(row: dict[str, Any], dn: str) -> bool:
    if not dn:
        return False
    cal = _calendar(row)
    if dn in _norm_domain(cal.get("website")):
        return True
    for _, p in _person_records(row):
        if dn in _norm_domain(p.get("website")):
            return True
    return False


def _locate_domain(row: dict[str, Any], dn: str) -> dict[str, Any]:
    cal = _calendar(row)
    if dn in _norm_domain(cal.get("website")):
        return {"field": "calendar.website", "value": cal.get("website")}
    for kind, p in _person_records(row):
        if dn in _norm_domain(p.get("website")):
            return {"field": f"{kind}.website", "person": p.get("name"),
                    "website": p.get("website")}
    return {"field": "unlocated"}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__ or "",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="in_path", default=None,
                    help="Explicit JSONL path. Otherwise picks latest run.")
    ap.add_argument("--run-root",
                    default=str(Path(__file__).resolve().parents[1] / ".gstack" / "luma" / "runs"),
                    help="Run dir root (default: <repo>/.gstack/luma/runs, "
                         "resolved relative to this script so cwd doesn't matter).")
    ap.add_argument("--name", action="append", default=[],
                    help="Name needle, can repeat. Case-insensitive substring match.")
    ap.add_argument("--linkedin", action="append", default=[],
                    help="LinkedIn handle (e.g. /in/gregorzunic). Can repeat.")
    ap.add_argument("--domain", action="append", default=[],
                    help="Domain (e.g. browser-use.com). Can repeat.")
    ap.add_argument("--json", action="store_true",
                    help="Emit a single JSON document (good for piping/Hermes).")
    ap.add_argument("--limit", type=int, default=50,
                    help="Max matches to return (default 50, 0 = unlimited).")
    args = ap.parse_args(argv)

    if not (args.name or args.linkedin or args.domain):
        print("Provide at least one of --name / --linkedin / --domain", file=sys.stderr)
        return 2

    in_path = _discover_input(args.in_path, Path(args.run_root))
    matches: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    scanned = 0

    with in_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            scanned += 1
            ev_id = (row.get("event") or {}).get("api_id")
            if isinstance(ev_id, str) and ev_id in seen_ids:
                continue
            m = _match_row(
                row,
                name_needles=args.name,
                linkedin_needles=args.linkedin,
                domain_needles=args.domain,
            )
            if m:
                matches.append(m)
                if isinstance(ev_id, str):
                    seen_ids.add(ev_id)
                if args.limit and len(matches) >= args.limit:
                    break

    summary = {
        "events_scanned": scanned,
        "matches_returned": len(matches),
        "any_guest_list_open": sum(1 for m in matches if m.get("show_guest_list")),
        "calendar_breakdown": {},
    }
    cal_counts: dict[str, int] = {}
    for m in matches:
        cn = (m.get("calendar") or {}).get("name") or "(unknown)"
        cal_counts[cn] = cal_counts.get(cn, 0) + 1
    summary["calendar_breakdown"] = dict(sorted(cal_counts.items(), key=lambda kv: -kv[1])[:20])

    report = {
        "query": {"name": args.name, "linkedin": args.linkedin, "domain": args.domain},
        "source_file": str(in_path),
        "summary": summary,
        "matches": matches,
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    # Human-readable
    print(f"Source: {in_path}")
    print(f"Scanned: {scanned} events; matched: {len(matches)}")
    if not matches:
        print("No matches.")
        return 0
    for m in matches:
        print(f"\n• {m['event_name']}  ({m['start_at']})")
        print(f"    event_id: {m['event_api_id']}   url: {m['url']}")
        cal = m["calendar"] or {}
        print(f"    calendar: {cal.get('name')}  ({cal.get('website') or '—'})")
        print(f"    show_guest_list: {m.get('show_guest_list')}  guest_count: {m.get('guest_count')}")
        for hit in m["matches"][:6]:
            where = hit.get("where") or {}
            print(f"    └ {hit['kind']:8}  needle={hit.get('needle')!r}  "
                  f"at {where.get('field','?')}  {where.get('person') or where.get('value') or where.get('snippet') or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
