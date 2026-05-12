#!/usr/bin/env python3
"""
Luma GTM — layer 2c: Cala graph enrichment.

Reads `leads_people.jsonl` or `leads_orgs.jsonl` from luma_score_leads.py
and resolves each row against Cala's REST graph:

  1. GET  /v1/entities?name=...&entity_types=Person     (or Company)
  2. Disambiguate via linkedin_url ↔ linkedin_handle when possible.
  3. POST /v1/entities/{id} with a small property + relationship body.

Output is the original row + a `cala` block. Resumable (skips rows that
already have a `cala.entity_id`). Stdlib only — uses CALA_API_KEY from .env
or environment.

Person enrichment pulls:
  - properties: name, description, linkedin_url, personal_website, aliases
  - outgoing: WORKS_AT (current employer), FOUNDED, HAS_NATIONALITY

Org enrichment pulls:
  - properties: name, aliases, description, website, headquarters_address,
                employee_count, founding_date
  - outgoing: HAS_HEADQUARTERS_IN, IS_REGISTERED_IN
  - incoming: IS_CEO_OF, IS_CFO_OF, IS_CTO_OF (when populated)

Usage:
  python3 scripts/luma_cala_enrich.py \\
    --in  .gstack/luma/runs/2026-W19/leads/leads_people.jsonl \\
    --out .gstack/luma/runs/2026-W19/leads/leads_people_cala.jsonl \\
    --kind person --workers 3 --sleep 0.4

  python3 scripts/luma_cala_enrich.py \\
    --in  .gstack/luma/runs/2026-W19/leads/leads_orgs.jsonl \\
    --out .gstack/luma/runs/2026-W19/leads/leads_orgs_cala.jsonl \\
    --kind org
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable, Literal

CALA_BASE = os.environ.get("CALA_BASE", "https://api.cala.ai")
UA = "luma-cala-enrich/1.0"
_SSL_CTX = ssl.create_default_context()

PERSON_PROPS = ["name", "description", "linkedin_url", "personal_website", "aliases"]
PERSON_OUT_REL: dict[str, dict[str, int]] = {
    "WORKS_AT": {"limit": 3},
    "FOUNDED": {"limit": 3},
    "HAS_NATIONALITY": {"limit": 1},
}

ORG_PROPS = ["name", "aliases", "description", "website",
             "headquarters_address", "employee_count", "founding_date"]
ORG_OUT_REL: dict[str, dict[str, int]] = {
    "HAS_HEADQUARTERS_IN": {"limit": 1},
    "IS_REGISTERED_IN": {"limit": 1},
}
ORG_IN_REL: dict[str, dict[str, int]] = {
    "IS_CEO_OF": {"limit": 1},
    "IS_CFO_OF": {"limit": 1},
    "IS_CTO_OF": {"limit": 1},
}


def _load_api_key(env_path: Path | None) -> str:
    key = os.environ.get("CALA_API_KEY")
    if key:
        return key
    if env_path and env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() == "CALA_API_KEY":
                return v.strip().strip('"').strip("'")
    raise SystemExit("CALA_API_KEY not found in env or .env file")


def _cala_get(path: str, params: dict[str, Any], *, api_key: str, timeout: float = 25.0) -> dict[str, Any]:
    url = f"{CALA_BASE}{path}?{urllib.parse.urlencode(params, doseq=True)}"
    req = urllib.request.Request(
        url,
        headers={"X-API-KEY": api_key, "Accept": "application/json", "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _cala_post(path: str, body: dict[str, Any], *, api_key: str, timeout: float = 25.0) -> dict[str, Any]:
    req = urllib.request.Request(
        f"{CALA_BASE}{path}",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={
            "X-API-KEY": api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": UA,
        },
    )
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _normalise_handle(handle: str | None) -> str | None:
    """Luma stores `/in/<slug>` or `/company/<slug>`; Cala stores full URLs.
    Reduce both to a comparable suffix (slug only, lowercased)."""
    if not isinstance(handle, str) or not handle:
        return None
    h = handle.strip().lower()
    for marker in ("/in/", "/company/", "linkedin.com/in/", "linkedin.com/company/"):
        if marker in h:
            h = h.rsplit(marker, 1)[-1]
            break
    return h.strip("/")


def _slug_from_url(url: str | None) -> str | None:
    return _normalise_handle(url)


def _name_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _pick_match(
    candidates: list[dict[str, Any]],
    *,
    desired_type: Literal["Person", "Company", "Organization"],
    name_hint: str,
    min_name_sim: float,
) -> tuple[dict[str, Any] | None, float]:
    """Return (best_candidate, name_similarity). Filters by entity_type and
    requires `min_name_sim` similarity between the search query and Cala's
    returned name — anything below that is treated as no match."""
    if desired_type == "Person":
        typed = [c for c in candidates if (c or {}).get("entity_type") == "Person"]
    else:
        typed = [c for c in candidates if (c or {}).get("entity_type") in {"Company", "Organization"}]
    if not typed:
        return None, 0.0
    scored = [(c, _name_similarity(name_hint, c.get("name") or "")) for c in typed]
    scored.sort(key=lambda t: -t[1])
    best, sim = scored[0]
    if sim < min_name_sim:
        return None, sim
    return best, sim


def _shrink_person(profile: dict[str, Any]) -> dict[str, Any]:
    props = profile.get("properties") or {}
    out = (profile.get("relationships") or {}).get("outgoing") or {}

    def _value(k: str) -> Any:
        node = props.get(k)
        return node.get("value") if isinstance(node, dict) else None

    works_at = [
        {"entity_id": e.get("id"), "name": e.get("name")}
        for e in (out.get("WORKS_AT") or [])
    ]
    founded = [
        {"entity_id": e.get("id"), "name": e.get("name")}
        for e in (out.get("FOUNDED") or [])
    ]
    nationality = [e.get("name") for e in (out.get("HAS_NATIONALITY") or [])]
    return {
        "entity_id": profile.get("id"),
        "entity_type": profile.get("entity_type"),
        "name": profile.get("name"),
        "description": profile.get("description") or _value("description"),
        "linkedin_url": _value("linkedin_url"),
        "personal_website": _value("personal_website"),
        "aliases": _value("aliases"),
        "works_at": works_at,
        "founded": founded,
        "nationality": nationality,
    }


def _shrink_org(profile: dict[str, Any]) -> dict[str, Any]:
    props = profile.get("properties") or {}
    out = (profile.get("relationships") or {}).get("outgoing") or {}
    inc = (profile.get("relationships") or {}).get("incoming") or {}

    def _value(k: str) -> Any:
        node = props.get(k)
        return node.get("value") if isinstance(node, dict) else None

    return {
        "entity_id": profile.get("id"),
        "entity_type": profile.get("entity_type"),
        "name": profile.get("name") or _value("name"),
        "description": profile.get("description") or _value("description"),
        "aliases": _value("aliases"),
        "website": _value("website"),
        "headquarters_address": _value("headquarters_address"),
        "employee_count": _value("employee_count"),
        "founding_date": _value("founding_date"),
        "headquarters_country": next(
            (e.get("name") for e in (out.get("HAS_HEADQUARTERS_IN") or [])), None
        ),
        "registered_in": next(
            (e.get("name") for e in (out.get("IS_REGISTERED_IN") or [])), None
        ),
        "ceo": next(
            ({"entity_id": e.get("id"), "name": e.get("name")} for e in (inc.get("IS_CEO_OF") or [])),
            None,
        ),
        "cfo": next(
            ({"entity_id": e.get("id"), "name": e.get("name")} for e in (inc.get("IS_CFO_OF") or [])),
            None,
        ),
        "cto": next(
            ({"entity_id": e.get("id"), "name": e.get("name")} for e in (inc.get("IS_CTO_OF") or [])),
            None,
        ),
    }


def _verify_linkedin(shrunk: dict[str, Any], linkedin_hint: str | None) -> tuple[str, str | None]:
    """Returns (verdict, reason). Verdict: 'verified' | 'mismatch' | 'absent' | 'no_hint'."""
    if not linkedin_hint:
        return "no_hint", None
    wanted = _normalise_handle(linkedin_hint)
    got = _slug_from_url(shrunk.get("linkedin_url") if isinstance(shrunk, dict) else None)
    if got is None:
        return "absent", "cala_has_no_linkedin"
    if wanted == got:
        return "verified", None
    return "mismatch", f"wanted={wanted} got={got}"


def enrich_one(
    row: dict[str, Any],
    *,
    kind: Literal["person", "org"],
    api_key: str,
    min_name_sim: float,
) -> dict[str, Any]:
    if kind == "person":
        name = row.get("name") or " ".join(
            x for x in (row.get("first_name"), row.get("last_name")) if x
        )
        linkedin_hint = row.get("linkedin_handle")
        desired = "Person"
        entity_filter = ["Person"]
    else:
        name = row.get("name")
        linkedin_hint = row.get("linkedin_handle")
        desired = "Company"
        entity_filter = ["Company", "Organization"]

    if not isinstance(name, str) or not name.strip():
        return {**row, "cala": {"status": "skipped", "reason": "no_name"}}

    try:
        search = _cala_get(
            "/v1/entities",
            {"name": name, "entity_types": entity_filter, "limit": 5},
            api_key=api_key,
        )
    except urllib.error.HTTPError as e:
        return {**row, "cala": {"status": "error", "stage": "search", "http": e.code, "msg": e.reason}}
    except Exception as e:  # noqa: BLE001
        return {**row, "cala": {"status": "error", "stage": "search", "msg": str(e)[:200]}}

    candidates = search.get("entities") or []
    pick, name_sim = _pick_match(
        candidates, desired_type=desired, name_hint=name, min_name_sim=min_name_sim,
    )
    if not pick or not isinstance(pick.get("id"), str):
        return {
            **row,
            "cala": {
                "status": "no_match",
                "candidates_examined": len(candidates),
                "best_name_similarity": round(name_sim, 2),
            },
        }

    body = (
        {"properties": PERSON_PROPS, "relationships": {"outgoing": PERSON_OUT_REL}}
        if kind == "person"
        else {
            "properties": ORG_PROPS,
            "relationships": {"outgoing": ORG_OUT_REL, "incoming": ORG_IN_REL},
        }
    )
    try:
        profile = _cala_post(f"/v1/entities/{pick['id']}", body, api_key=api_key)
    except urllib.error.HTTPError as e:
        return {**row, "cala": {"status": "error", "stage": "retrieve", "http": e.code, "msg": e.reason}}
    except Exception as e:  # noqa: BLE001
        return {**row, "cala": {"status": "error", "stage": "retrieve", "msg": str(e)[:200]}}

    shrunk = _shrink_person(profile) if kind == "person" else _shrink_org(profile)

    li_verdict, li_reason = _verify_linkedin(shrunk, linkedin_hint)
    # Confidence ladder:
    #   verified              — linkedin handle in Cala matches Luma hint (HIGH)
    #   mismatch              — linkedin handle disagrees, REJECT this match
    #   exact_name_no_li_hint — no hint, but exact case-insensitive name match
    #   absent_strong_name    — hint exists, Cala has none, name sim >= 0.9
    #   weak                  — anything else above the floor; downstream should
    #                           treat as a suggestion not a fact.
    if li_verdict == "mismatch":
        return {
            **row,
            "cala": {
                "status": "rejected",
                "reason": "linkedin_mismatch",
                "detail": li_reason,
                "candidate_name": shrunk.get("name"),
                "candidate_entity_id": shrunk.get("entity_id"),
                "name_similarity": round(name_sim, 2),
            },
        }
    if li_verdict == "verified":
        confidence = "verified"
    elif li_verdict == "absent" and name_sim >= 0.90:
        confidence = "absent_strong_name"
    elif li_verdict == "no_hint" and name_sim >= 0.99:
        confidence = "exact_name_no_li_hint"
    else:
        confidence = "weak"

    return {
        **row,
        "cala": {
            "status": "ok",
            "confidence": confidence,
            "name_similarity": round(name_sim, 2),
            "linkedin_verdict": li_verdict,
            **shrunk,
        },
    }


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


def _read_already_done(out_path: Path) -> set[str]:
    if not out_path.exists():
        return set()
    seen: set[str] = set()
    for row in _read_jsonl(out_path):
        rid = row.get("api_id")  # both leads_people.jsonl and leads_orgs.jsonl use api_id
        if isinstance(rid, str):
            seen.add(rid)
    return seen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__ or "")
    ap.add_argument("--in", dest="in_path", required=True)
    ap.add_argument("--out", dest="out_path", required=True)
    ap.add_argument("--kind", choices=("person", "org"), required=True)
    ap.add_argument("--workers", type=int, default=3, help="Concurrent Cala lookups (be polite)")
    ap.add_argument("--sleep", type=float, default=0.35, help="Delay between completed futures (seconds)")
    ap.add_argument("--max", type=int, default=0, help="Cap rows enriched this run (0 = all)")
    ap.add_argument(
        "--min-name-sim",
        type=float,
        default=0.55,
        help="Minimum SequenceMatcher ratio between query name and Cala name to consider a match "
             "(default 0.55). Below this we record 'no_match' rather than promoting noise.",
    )
    ap.add_argument("--env-file", default=str(Path(__file__).resolve().parents[1] / ".env"))
    args = ap.parse_args()

    in_path = Path(args.in_path)
    out_path = Path(args.out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not in_path.exists():
        raise SystemExit(f"Input not found: {in_path}")

    api_key = _load_api_key(Path(args.env_file))
    rows = list(_read_jsonl(in_path))
    already = _read_already_done(out_path)

    def _eligible(r: dict[str, Any]) -> bool:
        if not isinstance(r.get("api_id"), str) or r["api_id"] in already:
            return False
        # Skip personal-org Luma calendars — they're noise and trigger
        # generic-noun false matches in Cala ("Personal" → "Work Personal OÜ").
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

    written = 0
    errors = 0
    with out_path.open("a", encoding="utf-8") as sink, ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futs = {
            pool.submit(enrich_one, r, kind=args.kind, api_key=api_key, min_name_sim=args.min_name_sim): r
            for r in todo
        }
        verdict_counts: dict[str, int] = {}
        for fut in as_completed(futs):
            result = fut.result()
            cala = result.get("cala") or {}
            status = cala.get("status")
            conf = cala.get("confidence", "")
            label = f"{status}/{conf}" if status == "ok" else status
            verdict_counts[label] = verdict_counts.get(label, 0) + 1
            if status not in ("ok",):
                errors += 1
            sink.write(json.dumps(result, ensure_ascii=False) + "\n")
            written += 1
            if args.sleep:
                time.sleep(args.sleep)

    summary = {
        "ok": True,
        "kind": args.kind,
        "min_name_sim": args.min_name_sim,
        "in": str(in_path),
        "out": str(out_path),
        "input_rows": len(rows),
        "previously_enriched": len(already),
        "skipped_personal": skipped_personal,
        "newly_written": written,
        "errors_or_misses": errors,
        "total_in_out_file": len(already) + written,
        "verdict_counts": dict(sorted(verdict_counts.items(), key=lambda kv: -kv[1])),
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
