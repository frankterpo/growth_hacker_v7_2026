#!/usr/bin/env python3
"""
Luma GTM — week-over-week diff on event_api_id.

Inputs are two JSONL files (typically `events.jsonl` from two harvest runs,
or two `events_hydrated.jsonl` files if you want full payload diff). Output:

  new_events.jsonl   — event_api_ids present in --new but not in --prev
  gone_events.jsonl  — event_api_ids present in --prev but not in --new
                       (cancelled, ended, deleted, or rolled off the window)

Hermes cron calls this between layer 1 and the scorer so it can report
"15 new events this week" and target the scorer at the delta if desired.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterable


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


def _index(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in _read_jsonl(path):
        eid = row.get("event_api_id")
        if isinstance(eid, str) and eid not in out:
            out[eid] = row
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__ or "")
    ap.add_argument("--prev", required=True, help="Previous run's events.jsonl (e.g. last week)")
    ap.add_argument("--new", required=True, help="Current run's events.jsonl (e.g. this week)")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    prev_path = Path(args.prev)
    new_path = Path(args.new)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if not new_path.exists():
        raise SystemExit(f"--new not found: {new_path}")

    prev_idx: dict[str, dict[str, Any]] = _index(prev_path) if prev_path.exists() else {}
    new_idx = _index(new_path)

    new_ids = sorted(set(new_idx) - set(prev_idx))
    gone_ids = sorted(set(prev_idx) - set(new_idx))

    (out_dir / "new_events.jsonl").write_text(
        "".join(json.dumps(new_idx[i], ensure_ascii=False) + "\n" for i in new_ids),
        encoding="utf-8",
    )
    (out_dir / "gone_events.jsonl").write_text(
        "".join(json.dumps(prev_idx[i], ensure_ascii=False) + "\n" for i in gone_ids),
        encoding="utf-8",
    )

    summary = {
        "prev_path": str(prev_path),
        "new_path": str(new_path),
        "prev_count": len(prev_idx),
        "new_count": len(new_idx),
        "added": len(new_ids),
        "removed": len(gone_ids),
        "out_dir": str(out_dir),
    }
    (out_dir / "diff_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
