#!/usr/bin/env python3
"""
Specter — name/LinkedIn entity lookup for the unified graph enricher.

Honest stance: Specter has **no public REST API** that we control. Reaching it
programmatically requires (a) an authenticated browser session captured by
`specter_harvest.py` and (b) a search-endpoint URL template observed once in
the Network panel of that session. This module is the **runtime adapter** that
turns those two artifacts into a callable `lookup_person()` / `lookup_org()`.

Three readiness states, all reported truthfully — never as `no_match`:

  ready          — authenticated cookies + URL template configured;
                   real HTTP lookups will run.
  partial_ready  — session looks authenticated but URL templates not configured
                   (Hermes hasn't observed the endpoint yet).
  not_ready      — no `__session` / `__client_uat*` cookie on tryspecter.com,
                   or no cookies file at all.

When not ready, every lookup returns `{"status": "unavailable", "reason": ...}`
so the unified enricher can keep "disregard" semantics honest:

  disregard fires only when EVERY enabled provider explicitly says no_match.
  If Specter is unavailable, the row is `needs_review` instead — never silently
  dropped because of infrastructure state.

Env-driven configuration (operator fills in `.env` once endpoints are
discovered — see scripts/SPECTER_LOOKUP.md for the runbook):

  SPECTER_PERSON_SEARCH_URL_TEMPLATE   e.g. "https://app.tryspecter.com/api/talent/search?q={name}&limit=5"
  SPECTER_COMPANY_SEARCH_URL_TEMPLATE  e.g. "https://app.tryspecter.com/api/companies/search?q={name}&limit=5"
  SPECTER_RESPONSE_RESULTS_PATH        dot-path inside the JSON body (default: "results")
                                       supports e.g. "data.results" or "" (root array)
  SPECTER_RESPONSE_ID_FIELD            default "id"
  SPECTER_RESPONSE_NAME_FIELD          default "name"
  SPECTER_RESPONSE_LINKEDIN_FIELD      default "linkedin_url"
  SPECTER_RESPONSE_WEBSITE_FIELD       default "website"     (org only)
  SPECTER_RESPONSE_DOMAIN_FIELD        default "domain"      (org only)
  SPECTER_SESSION_DIR                  default ".gstack/specter"
  SPECTER_MIN_NAME_SIM                 default 0.55
  SPECTER_TIMEOUT_S                    default 25

CLI:
  python3 scripts/specter_lookup.py --readiness
  python3 scripts/specter_lookup.py --kind person --name "Aadam Sumer" --linkedin /in/aadamsumer
  python3 scripts/specter_lookup.py --kind org --name "Angels Den"
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Literal


_DEFAULT_SESSION_DIR = Path(".gstack/specter")
_UA = "specter-lookup/1.0"
_SSL_CTX = ssl.create_default_context()


# ---------------------------------------------------------------------------
# readiness
# ---------------------------------------------------------------------------

def _load_cookies(session_dir: Path) -> list[dict[str, Any]]:
    cookies_path = session_dir / "cookies.export.json"
    if not cookies_path.exists():
        return []
    try:
        data = json.loads(cookies_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, list):
        return [c for c in data if isinstance(c, dict)]
    if isinstance(data, dict) and isinstance(data.get("cookies"), list):
        return [c for c in data["cookies"] if isinstance(c, dict)]
    return []


def _cookie_header(cookies: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for c in cookies:
        n, v = c.get("name"), c.get("value")
        if isinstance(n, str) and isinstance(v, str):
            parts.append(f"{n}={v}")
    return "; ".join(parts)


def _bearer(session_dir: Path) -> str | None:
    p = session_dir / "clerk.jwt"
    if not p.exists():
        return None
    raw = p.read_text(encoding="utf-8").strip()
    return raw or None


def readiness(session_dir: Path | None = None) -> dict[str, Any]:
    """Inspect on-disk session + env config; never throws."""
    sd = session_dir or Path(os.environ.get("SPECTER_SESSION_DIR", str(_DEFAULT_SESSION_DIR)))
    cookies = _load_cookies(sd)
    has_session_cookie = any(
        (c.get("name") == "__session" and "tryspecter.com" in (c.get("domain") or ""))
        for c in cookies
    )
    has_uat = any(
        (str(c.get("name") or "").startswith("__client_uat")
         and "tryspecter.com" in (c.get("domain") or "").lower())
        for c in cookies
    )
    has_bearer = bool(_bearer(sd))
    person_tpl = os.environ.get("SPECTER_PERSON_SEARCH_URL_TEMPLATE", "").strip()
    org_tpl = os.environ.get("SPECTER_COMPANY_SEARCH_URL_TEMPLATE", "").strip()

    if has_session_cookie or has_uat or has_bearer:
        if person_tpl or org_tpl:
            state = "ready"
            reason = "authenticated_session_and_endpoint_template_present"
        else:
            state = "partial_ready"
            reason = "authenticated_session_but_no_endpoint_template"
    else:
        state = "not_ready"
        reason = "no_authenticated_specter_cookies_on_disk"

    return {
        "state": state,
        "reason": reason,
        "session_dir": str(sd),
        "cookies_total": len(cookies),
        "has_session_cookie": has_session_cookie,
        "has_client_uat": has_uat,
        "has_clerk_jwt": has_bearer,
        "person_template_configured": bool(person_tpl),
        "company_template_configured": bool(org_tpl),
    }


def is_session_ready(session_dir: Path | None = None) -> bool:
    return readiness(session_dir)["state"] == "ready"


# ---------------------------------------------------------------------------
# HTTP + response parsing
# ---------------------------------------------------------------------------

def _http_get_json(url: str, *, cookie_header: str, bearer: str | None, timeout: float) -> tuple[int, Any]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": _UA,
    }
    if cookie_header:
        headers["Cookie"] = cookie_header
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, method="GET", headers=headers)
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
        status = getattr(resp, "status", None) or resp.getcode()
        body = resp.read()
    try:
        return status, json.loads(body.decode("utf-8", errors="replace"))
    except Exception:
        return status, None


def _dig(obj: Any, dotted: str) -> Any:
    if not dotted:
        return obj
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def _coerce_list(node: Any) -> list[dict[str, Any]]:
    if isinstance(node, list):
        return [n for n in node if isinstance(n, dict)]
    return []


def _normalise_handle(handle: str | None) -> str | None:
    if not isinstance(handle, str) or not handle:
        return None
    h = handle.strip().lower()
    for marker in ("/in/", "/company/", "linkedin.com/in/", "linkedin.com/company/"):
        if marker in h:
            h = h.rsplit(marker, 1)[-1]
            break
    return h.strip("/") or None


def _name_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------

def _disabled(reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason}


def _verify_linkedin(candidate: dict[str, Any], linkedin_hint: str | None,
                     li_field: str) -> tuple[str, str | None, str | None]:
    """Returns (verdict, got_handle, reason). Verdict mirrors Cala's ladder."""
    if not linkedin_hint:
        return "no_hint", None, None
    wanted = _normalise_handle(linkedin_hint)
    got = _normalise_handle(candidate.get(li_field) if isinstance(candidate, dict) else None)
    if got is None:
        return "absent", None, "specter_has_no_linkedin_for_candidate"
    if wanted == got:
        return "verified", got, None
    return "mismatch", got, f"wanted={wanted} got={got}"


def _pick_best(results: list[dict[str, Any]], *, name: str, name_field: str,
               min_name_sim: float) -> tuple[dict[str, Any] | None, float, int]:
    if not results:
        return None, 0.0, 0
    scored = [(r, _name_similarity(name, str(r.get(name_field) or ""))) for r in results]
    scored.sort(key=lambda t: -t[1])
    best, sim = scored[0]
    if sim < min_name_sim:
        return None, sim, len(results)
    return best, sim, len(results)


def lookup(
    *,
    kind: Literal["person", "org"],
    name: str,
    linkedin_handle: str | None = None,
    domain: str | None = None,
    session_dir: Path | None = None,
    min_name_sim: float | None = None,
    timeout_s: float | None = None,
) -> dict[str, Any]:
    """Perform a single Specter lookup. Stable shape:

    OK match:
      {"status":"ok", "confidence":"verified|exact_name_no_li_hint|absent_strong_name|weak",
       "name_similarity":0.0-1.0, "linkedin_verdict":"verified|absent|no_hint",
       "specter_id":..., "name":..., "linkedin_url":..., "raw_candidate":{...}}

    Rejected (LinkedIn handle disagreed):
      {"status":"rejected", "reason":"linkedin_mismatch", "detail":"wanted=x got=y",
       "candidate_name":..., "name_similarity":...}

    No match (Specter returned candidates but none cleared min_name_sim, or
    Specter returned zero):
      {"status":"no_match", "candidates_examined":N, "best_name_similarity":...}

    Unavailable (infrastructure — DO NOT treat as no_match downstream):
      {"status":"unavailable", "reason":"no_session"|"no_endpoint_configured"|...}

    Error (HTTP/JSON layer):
      {"status":"error", "stage":"search", "http":<code>, "msg":...}
    """
    if not isinstance(name, str) or not name.strip():
        return {"status": "skipped", "reason": "no_name"}

    sd = session_dir or Path(os.environ.get("SPECTER_SESSION_DIR", str(_DEFAULT_SESSION_DIR)))
    rd = readiness(sd)
    if rd["state"] == "not_ready":
        return _disabled("no_authenticated_specter_session")

    if kind == "person":
        template = os.environ.get("SPECTER_PERSON_SEARCH_URL_TEMPLATE", "").strip()
    else:
        template = os.environ.get("SPECTER_COMPANY_SEARCH_URL_TEMPLATE", "").strip()
    if not template:
        return _disabled("no_endpoint_template_configured")
    if "{name}" not in template and "{query}" not in template:
        return _disabled("endpoint_template_missing_placeholder")

    name_field = os.environ.get("SPECTER_RESPONSE_NAME_FIELD", "name")
    li_field = os.environ.get("SPECTER_RESPONSE_LINKEDIN_FIELD", "linkedin_url")
    id_field = os.environ.get("SPECTER_RESPONSE_ID_FIELD", "id")
    results_path = os.environ.get("SPECTER_RESPONSE_RESULTS_PATH", "results")
    min_sim = (
        float(min_name_sim) if min_name_sim is not None
        else float(os.environ.get("SPECTER_MIN_NAME_SIM", "0.55"))
    )
    timeout = (
        float(timeout_s) if timeout_s is not None
        else float(os.environ.get("SPECTER_TIMEOUT_S", "25"))
    )

    cookies = _load_cookies(sd)
    ch = _cookie_header(cookies)
    bearer = _bearer(sd)

    encoded = urllib.parse.quote(name)
    url = template.replace("{name}", encoded).replace("{query}", encoded)

    try:
        status, body = _http_get_json(url, cookie_header=ch, bearer=bearer, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return _disabled(f"http_{e.code}_session_likely_expired")
        return {"status": "error", "stage": "search", "http": e.code, "msg": e.reason or str(e)[:200]}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "stage": "search", "msg": str(e)[:240]}

    if status == 401 or status == 403:
        return _disabled(f"http_{status}_session_likely_expired")

    results = _coerce_list(_dig(body, results_path))
    pick, sim, examined = _pick_best(results, name=name, name_field=name_field, min_name_sim=min_sim)
    if not pick:
        return {
            "status": "no_match",
            "candidates_examined": examined,
            "best_name_similarity": round(sim, 2),
        }

    li_verdict, got_handle, li_detail = _verify_linkedin(pick, linkedin_handle, li_field=li_field)
    if li_verdict == "mismatch":
        return {
            "status": "rejected",
            "reason": "linkedin_mismatch",
            "detail": li_detail,
            "candidate_name": pick.get(name_field),
            "name_similarity": round(sim, 2),
        }

    if li_verdict == "verified":
        confidence = "verified"
    elif li_verdict == "absent" and sim >= 0.90:
        confidence = "absent_strong_name"
    elif li_verdict == "no_hint" and sim >= 0.99:
        confidence = "exact_name_no_li_hint"
    else:
        confidence = "weak"

    payload = {
        "status": "ok",
        "confidence": confidence,
        "name_similarity": round(sim, 2),
        "linkedin_verdict": li_verdict,
        "specter_id": pick.get(id_field),
        "name": pick.get(name_field),
        "linkedin_url": pick.get(li_field),
    }
    if kind == "org":
        for k_env, k_out in (
            ("SPECTER_RESPONSE_WEBSITE_FIELD", "website"),
            ("SPECTER_RESPONSE_DOMAIN_FIELD", "domain"),
        ):
            field = os.environ.get(k_env, k_out)
            val = pick.get(field)
            if val:
                payload[k_out] = val
    payload["raw_candidate_keys"] = sorted(list(pick.keys()))[:30]
    return payload


def lookup_person(name: str, linkedin_handle: str | None = None, **kw: Any) -> dict[str, Any]:
    return lookup(kind="person", name=name, linkedin_handle=linkedin_handle, **kw)


def lookup_org(name: str, linkedin_handle: str | None = None,
               domain: str | None = None, **kw: Any) -> dict[str, Any]:
    return lookup(kind="org", name=name, linkedin_handle=linkedin_handle, domain=domain, **kw)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__ or "", formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--readiness", action="store_true", help="Probe session + env and print state.")
    ap.add_argument("--kind", choices=("person", "org"))
    ap.add_argument("--name")
    ap.add_argument("--linkedin", default=None)
    ap.add_argument("--domain", default=None)
    ap.add_argument("--session-dir", default=None)
    ap.add_argument("--min-name-sim", type=float, default=None)
    args = ap.parse_args(argv)

    sd = Path(args.session_dir) if args.session_dir else None

    if args.readiness or not args.name:
        out = readiness(sd)
        print(json.dumps(out, indent=2))
        return 0 if out["state"] in ("ready", "partial_ready") else 0

    if not args.kind:
        print("--kind person|org required when --name is given", file=sys.stderr)
        return 2

    res = lookup(
        kind=args.kind,
        name=args.name,
        linkedin_handle=args.linkedin,
        domain=args.domain,
        session_dir=sd,
        min_name_sim=args.min_name_sim,
    )
    print(json.dumps(res, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
