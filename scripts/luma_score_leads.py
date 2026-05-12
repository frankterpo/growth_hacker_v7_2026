#!/usr/bin/env python3
"""
Luma GTM — layer 2b: topical lead extraction.

Reads `events_hydrated.jsonl` (produced by luma_hydrate_events.py) and writes
three JSONL files biased for B2B / institutional GTM:

  events_scored.jsonl  — every event, plus `topics` array and `topic_score`
  leads_orgs.jsonl     — unique organising calendars, ranked by topic match
  leads_people.jsonl   — unique hosts + featured_guests, ranked by topic match

Topics are pure regex buckets — no LLM. Loaded from a JSON topic pack:

  scripts/luma_topic_packs/institutional.json   (default — private markets,
                                                 allocators, asset mgmt, …)
  scripts/luma_topic_packs/fintech.json         (payments, embedded fin, …)

Resolution: `--topic-pack institutional` is shorthand for
`scripts/luma_topic_packs/institutional.json`. A full path also works.

Usage:
  python3 scripts/luma_score_leads.py \\
    --in  .gstack/luma/out/europe/events_hydrated.jsonl \\
    --out-dir .gstack/luma/out/europe/leads \\
    --topic-pack institutional
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

_REPO_PACKS = Path(__file__).resolve().parent / "luma_topic_packs"


def _load_topic_pack(spec: str) -> dict[str, Any]:
    """Resolve `--topic-pack` to a dict; accept name or full path."""
    candidate = Path(spec)
    if not candidate.exists() and not candidate.is_absolute() and "/" not in spec:
        candidate = _REPO_PACKS / f"{spec}.json"
    if not candidate.exists():
        raise SystemExit(f"Topic pack not found: {spec} (tried {candidate})")
    pack = json.loads(candidate.read_text(encoding="utf-8"))
    topics = pack.get("topics")
    if not isinstance(topics, dict) or not topics:
        raise SystemExit(f"Topic pack {candidate} has no 'topics' map")
    return pack


def _compile_pack(pack: dict[str, Any]) -> dict[str, tuple[int, list[re.Pattern[str]]]]:
    out: dict[str, tuple[int, list[re.Pattern[str]]]] = {}
    for bucket, cfg in (pack.get("topics") or {}).items():
        if not isinstance(cfg, dict):
            continue
        weight = int(cfg.get("weight", 1))
        patterns = [str(p) for p in (cfg.get("patterns") or []) if isinstance(p, str)]
        if not patterns:
            continue
        out[bucket] = (weight, [re.compile(p, re.IGNORECASE) for p in patterns])
    if not out:
        raise SystemExit("No usable topics in pack after compile")
    return out


def _flatten_pretext(blob: Any) -> str:
    """Luma `description` is either plain text or a Pretext/tiptap JSON string."""
    if not blob:
        return ""
    if isinstance(blob, str):
        try:
            parsed = json.loads(blob)
        except json.JSONDecodeError:
            return blob
        blob = parsed
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


def _event_haystack(row: dict[str, Any]) -> str:
    ev = row.get("event") or {}
    cal = row.get("calendar") or {}
    parts: list[str] = [
        ev.get("name") or "",
        cal.get("name") or "",
        cal.get("description_short") or "",
        _flatten_pretext(row.get("description")),
    ]
    for cat in row.get("categories") or []:
        if isinstance(cat, dict):
            parts.append(cat.get("name") or "")
    for fi in row.get("featured_infos") or []:
        if isinstance(fi, dict):
            parts.append(fi.get("name") or "")
            parts.append(fi.get("name_raw") or "")
    for h in row.get("hosts") or []:
        if isinstance(h, dict):
            parts.append(h.get("bio_short") or "")
    return "  ||  ".join(p for p in parts if p)


def _score_text(text: str, compiled: dict[str, tuple[int, list[re.Pattern[str]]]]) -> tuple[list[str], int, dict[str, int]]:
    """Return (matched_buckets, total_weighted_score, per_bucket_hits)."""
    from math import log
    hits: dict[str, int] = {}
    total = 0
    for bucket, (weight, patterns) in compiled.items():
        bucket_hits = sum(len(p.findall(text)) for p in patterns)
        if bucket_hits:
            hits[bucket] = bucket_hits
            # log-dampened: 1 hit = weight, 5 hits ≈ 1.7×weight
            total += int(round(weight * (1 + log(bucket_hits))))
    return sorted(hits.keys()), total, hits


def _ranking_key(row: dict[str, Any]) -> tuple[int, int]:
    return (
        -int(row.get("topic_score") or 0),
        -int(row.get("guest_count") or 0),
    )


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__ or "")
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument(
        "--topic-pack",
        default="institutional",
        help="Topic pack name (resolves to scripts/luma_topic_packs/<name>.json) "
             "or a full path to a JSON pack.",
    )
    ap.add_argument(
        "--min-score",
        type=int,
        default=1,
        help="Drop events whose topic_score is below this (leads_* files only).",
    )
    args = ap.parse_args()

    in_path = Path(args.in_path)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pack = _load_topic_pack(args.topic_pack)
    compiled = _compile_pack(pack)
    print(
        f"topic pack: {pack.get('name', '?')}  buckets={list(compiled)}",
        file=sys.stderr,
    )

    scored: list[dict[str, Any]] = []
    for row in _read_jsonl(in_path):
        topics, score, hits = _score_text(_event_haystack(row), compiled)
        scored_row = {
            **row,
            "topics": topics,
            "topic_score": score,
            "topic_hits": hits,
        }
        scored.append(scored_row)

    scored.sort(key=_ranking_key)
    (out_dir / "events_scored.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in scored) + ("\n" if scored else ""),
        encoding="utf-8",
    )

    # ---- org-level aggregation (organising calendar = "org") ----
    org_acc: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"events": [], "topic_score": 0, "topics": set()}
    )
    for r in scored:
        cal = r.get("calendar") or {}
        cid = cal.get("api_id")
        if not isinstance(cid, str):
            continue
        bucket = org_acc[cid]
        # snapshot org fields from first event we see; later events refresh non-null fields
        for k in (
            "api_id", "name", "slug", "website", "linkedin_handle",
            "twitter_handle", "instagram_handle", "geo_city", "geo_country",
            "luma_plan", "luma_plus_active", "is_personal", "avatar_url",
        ):
            v = cal.get(k)
            if v is not None and bucket.get(k) in (None, ""):
                bucket[k] = v
        bucket["topic_score"] += int(r.get("topic_score") or 0)
        bucket["topics"].update(r.get("topics") or [])
        bucket["events"].append({
            "event_api_id": r.get("event_api_id"),
            "name": (r.get("event") or {}).get("name"),
            "url_slug": (r.get("event") or {}).get("url_slug"),
            "start_at": (r.get("event") or {}).get("start_at"),
            "topics": r.get("topics") or [],
            "topic_score": r.get("topic_score") or 0,
            "guest_count": r.get("guest_count"),
        })

    org_rows: list[dict[str, Any]] = []
    for cid, b in org_acc.items():
        if b["topic_score"] < args.min_score:
            continue
        org_rows.append({
            **{k: v for k, v in b.items() if k not in ("events", "topics", "topic_score")},
            "topics": sorted(b["topics"]),
            "topic_score": b["topic_score"],
            "event_count": len(b["events"]),
            "events": sorted(b["events"], key=lambda e: -(e.get("topic_score") or 0)),
        })
    org_rows.sort(key=lambda r: (-(r["topic_score"]), -(r["event_count"])))
    (out_dir / "leads_orgs.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in org_rows) + ("\n" if org_rows else ""),
        encoding="utf-8",
    )

    # ---- people-level aggregation (hosts ∪ featured_guests) ----
    ppl_acc: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"events": [], "topic_score": 0, "topics": set(), "roles": set()}
    )
    for r in scored:
        topics = r.get("topics") or []
        score = int(r.get("topic_score") or 0)
        if score < args.min_score:
            continue
        ev_meta = {
            "event_api_id": r.get("event_api_id"),
            "name": (r.get("event") or {}).get("name"),
            "url_slug": (r.get("event") or {}).get("url_slug"),
            "start_at": (r.get("event") or {}).get("start_at"),
            "city": (r.get("event") or {}).get("city"),
            "country": (r.get("event") or {}).get("country"),
            "topics": topics,
            "topic_score": score,
        }
        for role, group in (("host", r.get("hosts") or []), ("featured_guest", r.get("featured_guests") or [])):
            for p in group:
                if not isinstance(p, dict):
                    continue
                pid = p.get("api_id")
                if not isinstance(pid, str):
                    continue
                b = ppl_acc[pid]
                for k in (
                    "api_id", "name", "first_name", "last_name", "username",
                    "linkedin_handle", "twitter_handle", "instagram_handle",
                    "website", "bio_short", "timezone", "avatar_url", "is_verified",
                ):
                    v = p.get(k)
                    if v is not None and b.get(k) in (None, ""):
                        b[k] = v
                b["topic_score"] += score
                b["topics"].update(topics)
                b["roles"].add(role)
                b["events"].append(ev_meta)

    ppl_rows: list[dict[str, Any]] = []
    for pid, b in ppl_acc.items():
        # require either a LinkedIn handle OR a website to be worth Hermes-pushing
        if not (b.get("linkedin_handle") or b.get("website") or b.get("twitter_handle")):
            continue
        ppl_rows.append({
            **{k: v for k, v in b.items() if k not in ("events", "topics", "topic_score", "roles")},
            "topics": sorted(b["topics"]),
            "roles": sorted(b["roles"]),
            "topic_score": b["topic_score"],
            "event_count": len({e["event_api_id"] for e in b["events"]}),
            "events": b["events"],
        })
    ppl_rows.sort(key=lambda r: (-(r["topic_score"]), -(r["event_count"])))
    (out_dir / "leads_people.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in ppl_rows) + ("\n" if ppl_rows else ""),
        encoding="utf-8",
    )

    summary = {
        "in": str(in_path),
        "out_dir": str(out_dir),
        "topic_pack": pack.get("name"),
        "topic_pack_buckets": list(compiled),
        "events_scored": len(scored),
        "events_with_topics": sum(1 for r in scored if r["topics"]),
        "leads_orgs": len(org_rows),
        "leads_people": len(ppl_rows),
        "top_topics": _topic_histogram(scored),
    }
    (out_dir / "score_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


def _topic_histogram(rows: list[dict[str, Any]]) -> dict[str, int]:
    h: dict[str, int] = defaultdict(int)
    for r in rows:
        for t in r.get("topics") or []:
            h[t] += 1
    return dict(sorted(h.items(), key=lambda kv: -kv[1]))


if __name__ == "__main__":
    raise SystemExit(main())
