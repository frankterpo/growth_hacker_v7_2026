#!/usr/bin/env python3
"""
Luma GTM — layer 2c (unified): graph enrichment from Cala + Specter.

Replaces single-provider `luma_cala_enrich.py` for cron use. Calls BOTH
providers per row, attaches both result blocks, and computes a merged
`graph_match` verdict that the user can filter on.

Verdict ladder (highest first):
  verified        any provider returned confidence "verified" (LinkedIn-confirmed)
  strong          any provider returned "exact_name_no_li_hint" / "absent_strong_name"
  weak            any provider returned "weak"
  rejected        at least one provider explicitly REJECTED (LinkedIn mismatch)
                  and no other provider passed
  disregard       EVERY enabled provider explicitly returned "no_match"
                  (this is the user's "if not in Cala AND not in Specter, drop")
  needs_review    at least one provider was unavailable / errored and the rest
                  weren't strong enough to settle it
  error           every enabled provider returned an error
  skipped         no usable name on the input row

Honest contract: `disregard` does NOT fire when Specter is `unavailable`
(no session / no endpoint configured) — that becomes `needs_review` instead,
so we never drop leads because of infrastructure gaps.

Inputs/outputs (resumable — rows already present in --out are skipped):

  --in    .gstack/luma/runs/<RUN>/leads/leads_people.jsonl
  --out   .gstack/luma/runs/<RUN>/leads/leads_people_graph.jsonl
  --kind  person | org

Each output row is the original lead row plus:

  "cala":         { ... (luma_cala_enrich shape) ... } | {"status":"disabled", ...}
  "specter":      { ... (specter_lookup shape) ... }   | {"status":"disabled", ...}
  "graph_match":  { "verdict": ..., "best_provider": ..., "best_confidence": ...,
                    "providers": {"cala": <status>, "specter": <status>},
                    "reason": "..." }

CLI also writes a summary JSON to stdout with verdict counts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable, Literal

# Local providers
sys.path.insert(0, str(Path(__file__).resolve().parent))
import luma_cala_enrich as cala_provider  # noqa: E402
import specter_lookup as specter_provider  # noqa: E402


# ---------------------------------------------------------------------------
# verdict merge
# ---------------------------------------------------------------------------

# Order matters: the FIRST hit in this list among any provider sets the verdict.
_POSITIVE_LADDER = ("verified", "absent_strong_name", "exact_name_no_li_hint", "weak")
_POSITIVE_VERDICT = {
    "verified": "verified",
    "absent_strong_name": "strong",
    "exact_name_no_li_hint": "strong",
    "weak": "weak",
}


def merge_verdict(
    cala: dict[str, Any] | None,
    specter: dict[str, Any] | None,
) -> dict[str, Any]:
    cala = cala or {"status": "disabled"}
    specter = specter or {"status": "disabled"}
    statuses = {"cala": cala.get("status"), "specter": specter.get("status")}
    confs = {"cala": cala.get("confidence"), "specter": specter.get("confidence")}

    # 1) Best positive match wins.
    for conf in _POSITIVE_LADDER:
        for prov in ("cala", "specter"):
            if statuses[prov] == "ok" and confs[prov] == conf:
                return {
                    "verdict": _POSITIVE_VERDICT[conf],
                    "best_provider": prov,
                    "best_confidence": conf,
                    "providers": statuses,
                    "reason": f"{prov}_{conf}",
                }

    # 2) Negative space — count by category.
    enabled = [s for s in statuses.values() if s != "disabled"]
    no_match_count = sum(1 for s in enabled if s == "no_match")
    rejected_count = sum(1 for s in enabled if s == "rejected")
    unavailable_count = sum(1 for s in enabled if s == "unavailable")
    error_count = sum(1 for s in enabled if s == "error")
    skipped_count = sum(1 for s in enabled if s == "skipped")
    enabled_count = len(enabled)

    # All enabled providers said "no_match" → the user's "disregard" rule.
    if enabled_count > 0 and no_match_count == enabled_count:
        return {
            "verdict": "disregard",
            "best_provider": None,
            "best_confidence": None,
            "providers": statuses,
            "reason": "all_enabled_providers_returned_no_match",
        }

    # At least one rejected and nothing positive cleared it → rejected.
    if rejected_count > 0:
        return {
            "verdict": "rejected",
            "best_provider": None,
            "best_confidence": None,
            "providers": statuses,
            "reason": "at_least_one_provider_rejected_linkedin",
        }

    # Mix of no_match + unavailable → can't disregard honestly.
    if unavailable_count > 0:
        return {
            "verdict": "needs_review",
            "best_provider": None,
            "best_confidence": None,
            "providers": statuses,
            "reason": "provider_unavailable_blocks_honest_disregard",
        }

    if error_count > 0 and error_count == enabled_count:
        return {
            "verdict": "error",
            "best_provider": None,
            "best_confidence": None,
            "providers": statuses,
            "reason": "all_enabled_providers_errored",
        }

    if skipped_count == enabled_count and enabled_count > 0:
        return {
            "verdict": "skipped",
            "best_provider": None,
            "best_confidence": None,
            "providers": statuses,
            "reason": "no_usable_input",
        }

    # Both providers disabled (someone ran --disable-cala --disable-specter)
    if enabled_count == 0:
        return {
            "verdict": "disabled",
            "best_provider": None,
            "best_confidence": None,
            "providers": statuses,
            "reason": "no_providers_enabled",
        }

    return {
        "verdict": "needs_review",
        "best_provider": None,
        "best_confidence": None,
        "providers": statuses,
        "reason": "mixed_states_no_clear_signal",
    }


# ---------------------------------------------------------------------------
# per-row enrichment
# ---------------------------------------------------------------------------

def _disabled() -> dict[str, Any]:
    return {"status": "disabled"}


def enrich_row(
    row: dict[str, Any],
    *,
    kind: Literal["person", "org"],
    cala_api_key: str | None,
    cala_min_name_sim: float,
    specter_min_name_sim: float,
    enable_cala: bool,
    enable_specter: bool,
) -> dict[str, Any]:
    name_for_lookup = row.get("name") or " ".join(
        x for x in (row.get("first_name"), row.get("last_name")) if x
    )

    cala_result: dict[str, Any]
    if enable_cala and cala_api_key:
        try:
            cala_out = cala_provider.enrich_one(
                row,
                kind=kind,
                api_key=cala_api_key,
                min_name_sim=cala_min_name_sim,
            )
            cala_result = cala_out.get("cala") or {"status": "error", "msg": "missing_cala_block"}
        except Exception as e:  # noqa: BLE001
            cala_result = {"status": "error", "stage": "provider", "msg": str(e)[:240]}
    else:
        cala_result = _disabled()

    specter_result: dict[str, Any]
    if enable_specter:
        try:
            specter_result = specter_provider.lookup(
                kind=kind,
                name=name_for_lookup,
                linkedin_handle=row.get("linkedin_handle"),
                domain=row.get("domain") or row.get("website"),
                min_name_sim=specter_min_name_sim,
            )
        except Exception as e:  # noqa: BLE001
            specter_result = {"status": "error", "stage": "provider", "msg": str(e)[:240]}
    else:
        specter_result = _disabled()

    graph_match = merge_verdict(cala_result, specter_result)
    return {
        **row,
        "cala": cala_result,
        "specter": specter_result,
        "graph_match": graph_match,
    }


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------

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


def _already_done(out_path: Path) -> set[str]:
    if not out_path.exists():
        return set()
    seen: set[str] = set()
    for row in _read_jsonl(out_path):
        rid = row.get("api_id")
        if isinstance(rid, str):
            seen.add(rid)
    return seen


def _load_cala_key(env_file: Path | None, required: bool) -> str | None:
    try:
        return cala_provider._load_api_key(env_file)  # noqa: SLF001
    except SystemExit:
        if required:
            raise
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__ or "", formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--out", dest="out_path", required=True)
    ap.add_argument("--kind", choices=("person", "org"), required=True)
    ap.add_argument("--workers", type=int, default=3,
                    help="Concurrent rows (each row makes 1 Cala + 1 Specter call).")
    ap.add_argument("--sleep", type=float, default=0.25,
                    help="Inter-future sleep to keep API spend polite.")
    ap.add_argument("--max", type=int, default=0, help="Cap rows enriched this run (0 = all).")
    ap.add_argument("--disable-cala", action="store_true",
                    help="Skip Cala lookups (record cala.status=disabled).")
    ap.add_argument("--disable-specter", action="store_true",
                    help="Skip Specter lookups (record specter.status=disabled). "
                         "Useful for cron-only-Cala mode while session ops are still being set up.")
    ap.add_argument("--cala-min-name-sim", type=float, default=0.55)
    ap.add_argument("--specter-min-name-sim", type=float, default=0.55)
    ap.add_argument("--env-file", default=str(Path(__file__).resolve().parents[1] / ".env"))
    args = ap.parse_args(argv)

    in_path = Path(args.in_path)
    out_path = Path(args.out_path)
    if not in_path.exists():
        raise SystemExit(f"Input not found: {in_path}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    enable_cala = not args.disable_cala
    enable_specter = not args.disable_specter

    cala_api_key = _load_cala_key(Path(args.env_file), required=enable_cala) if enable_cala else None
    specter_state = specter_provider.readiness().get("state") if enable_specter else "disabled_by_flag"

    rows = list(_read_jsonl(in_path))
    already = _already_done(out_path)

    def _eligible(r: dict[str, Any]) -> bool:
        if not isinstance(r.get("api_id"), str) or r["api_id"] in already:
            return False
        if args.kind == "org" and r.get("is_personal") is True:
            return False
        return True

    todo = [r for r in rows if _eligible(r)]
    skipped_personal = sum(
        1 for r in rows if args.kind == "org" and r.get("is_personal") is True
    )
    if args.max:
        todo = todo[: args.max]

    print(
        f"input rows: {len(rows)}  already enriched: {len(already)}  "
        f"skipped_personal: {skipped_personal}  this run: {len(todo)}",
        file=sys.stderr,
    )
    print(
        f"cala_enabled: {enable_cala}  specter_enabled: {enable_specter}  "
        f"specter_state: {specter_state}",
        file=sys.stderr,
    )

    verdict_counts: dict[str, int] = {}
    written = 0
    with out_path.open("a", encoding="utf-8") as sink, ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futs = {
            pool.submit(
                enrich_row,
                r,
                kind=args.kind,
                cala_api_key=cala_api_key,
                cala_min_name_sim=args.cala_min_name_sim,
                specter_min_name_sim=args.specter_min_name_sim,
                enable_cala=enable_cala,
                enable_specter=enable_specter,
            ): r
            for r in todo
        }
        for fut in as_completed(futs):
            result = fut.result()
            verdict = (result.get("graph_match") or {}).get("verdict", "unknown")
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
            sink.write(json.dumps(result, ensure_ascii=False) + "\n")
            written += 1
            if args.sleep:
                time.sleep(args.sleep)

    summary = {
        "ok": True,
        "kind": args.kind,
        "in": str(in_path),
        "out": str(out_path),
        "providers": {"cala": enable_cala, "specter": enable_specter},
        "specter_readiness": specter_state,
        "input_rows": len(rows),
        "previously_enriched": len(already),
        "skipped_personal": skipped_personal,
        "newly_written": written,
        "total_in_out_file": len(already) + written,
        "verdict_counts": dict(sorted(verdict_counts.items(), key=lambda kv: -kv[1])),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
