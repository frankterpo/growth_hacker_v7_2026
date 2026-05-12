#!/usr/bin/env python3
"""
Specter session harvest via Browser Use CLI:

1) Opens Specter (feed URL -> Clerk login), performs in-page login (no element indices).
2) Exports cookies for curl-style replay.
3) Records Performance API resource URLs (endpoint inventory).
4) Attempts to capture a Clerk JWT via `window.Clerk.session.getToken()` (async) when Clerk is exposed.
5) HTTP-probes a small set of candidate API URLs using the exported cookie header (read-only).

Requires: `browser-use` on PATH or `SPECTER_BROWSER_USE_BIN`.

Run (bash — copy/paste safe):

  ./scripts/run_specter_harvest.sh --probe-limit 12

Or zsh/bash without inline comments on the same line as `export`:

  export SPECTER_BROWSER_USE_BIN="/Users/pablote/.browser-use-env/bin/browser-use"
  python3 scripts/specter_harvest.py --probe-limit 12

Secrets: read from `.env` in repo root (or `--env-file`). Writes under `.gstack/specter/` by default.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip("'").strip('"')
        out[k] = v
    return out


def _browser_use_bin() -> str:
    return os.environ.get("SPECTER_BROWSER_USE_BIN") or shutil_which("browser-use")


def shutil_which(name: str) -> str:
    import shutil

    p = shutil.which(name)
    if not p:
        raise SystemExit(f"Missing `{name}` on PATH (set SPECTER_BROWSER_USE_BIN).")
    return p


def _run(
    bu: str,
    args: list[str],
    *,
    timeout: int = 120,
    silent: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [bu, *args],
        cwd=str(_repo_root()),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _eval(bu: str, js: str, *, timeout: int = 120) -> str:
    p = _run(bu, ["eval", js], timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(p.stderr or p.stdout or f"eval failed rc={p.returncode}")
    # CLI prints: result: <payload>
    out = (p.stdout or "").strip()
    if out.startswith("result:"):
        return out[len("result:") :].strip()
    return out


def _js_string(s: str) -> str:
    return json.dumps(s, ensure_ascii=False)


def _login_js(email: str, password: str, *, stage: str) -> str:
    e = _js_string(email)
    p = _js_string(password)
    if stage == "click_login":
        return f"""(() => {{
  const buttons = [...document.querySelectorAll('button')];
  const login = buttons.find(b => (b.textContent || '').trim() === 'Login');
  if (login) login.click();
  return JSON.stringify({{clicked: !!login, href: location.href}});
}})()"""
    if stage == "fill":
        return f"""(() => {{
  const email = {e};
  const password = {p};
  const el = document.getElementById('identifier-field');
  const pw = document.getElementById('password-field');
  if (!el || !pw) return JSON.stringify({{ok:false, reason:'missing_fields', href: location.href}});
  el.focus();
  el.value = email;
  el.dispatchEvent(new Event('input', {{ bubbles: true }}));
  el.dispatchEvent(new Event('change', {{ bubbles: true }}));
  pw.focus();
  pw.value = password;
  pw.dispatchEvent(new Event('input', {{ bubbles: true }}));
  pw.dispatchEvent(new Event('change', {{ bubbles: true }}));
  const cont = [...document.querySelectorAll('button')].find(b => /^continue$/i.test((b.textContent || '').trim()));
  if (cont) cont.click();
  return JSON.stringify({{ok:true, href: location.href, clickedContinue: !!cont}});
}})()"""
    raise ValueError(stage)


def _clerk_present_js() -> str:
    return """(() => JSON.stringify({ clerk: !!window.Clerk }))()"""


def _has_login_fields_js() -> str:
    return """(() => JSON.stringify({
  has: !!(document.getElementById('identifier-field') && document.getElementById('password-field')),
  href: location.href
}))()"""


def _cookie_has_session(cookies_path: Path) -> bool:
    data = json.loads(cookies_path.read_text(encoding="utf-8"))
    items: list[dict[str, Any]] = []
    if isinstance(data, list):
        items = [c for c in data if isinstance(c, dict)]
    elif isinstance(data, dict) and isinstance(data.get("cookies"), list):
        items = [c for c in data["cookies"] if isinstance(c, dict)]
    for c in items:
        name = str(c.get("name") or "")
        domain = str(c.get("domain") or "")
        if name == "__session" and "tryspecter.com" in domain:
            return True
    return False


def _cookie_has_tryspecter_uat(cookies_path: Path) -> bool:
    data = json.loads(cookies_path.read_text(encoding="utf-8"))
    items: list[dict[str, Any]] = []
    if isinstance(data, list):
        items = [c for c in data if isinstance(c, dict)]
    elif isinstance(data, dict) and isinstance(data.get("cookies"), list):
        items = [c for c in data["cookies"] if isinstance(c, dict)]
    for c in items:
        name = str(c.get("name") or "")
        domain = str(c.get("domain") or "").lower()
        if name.startswith("__client_uat") and "tryspecter.com" in domain:
            return True
    return False


def _wait_body_contains_any(bu: str, needles: list[str], *, timeout_s: float = 300.0, poll_s: float = 3.0) -> None:
    """Poll via `eval` until any marker substring appears in `document.body.innerText`."""
    deadline = time.time() + timeout_s
    needles_js = json.dumps(needles)
    js = f"""(() => {{
  const t = (document.body && document.body.innerText) || '';
  const needles = {needles_js};
  const hit = needles.find(n => t.includes(n)) || null;
  return JSON.stringify({{ has: !!hit, hit }});
}})()"""
    last = ""
    while time.time() < deadline:
        last = _eval(bu, js, timeout=90).strip()
        try:
            payload = json.loads(last)
        except Exception:
            time.sleep(poll_s)
            continue
        if isinstance(payload, dict) and payload.get("has"):
            return
        time.sleep(poll_s)
    raise SystemExit(f"Timed out waiting for feed UI markers {needles!r}. Last probe: {last[:240]}")


def _cookies_export(bu: str, out_file: Path) -> None:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    p = _run(bu, ["cookies", "export", str(out_file)], timeout=60)
    if p.returncode != 0:
        raise RuntimeError(p.stderr or p.stdout or f"cookies export failed rc={p.returncode}")
    if not out_file.exists() or out_file.stat().st_size < 10:
        raise RuntimeError("cookies export produced an empty/missing file")


def _cookies_to_header(cookies_path: Path) -> str:
    data = json.loads(cookies_path.read_text(encoding="utf-8"))
    parts: list[str] = []
    # browser-use export is typically a JSON list of cookie objects.
    if isinstance(data, list):
        for c in data:
            if not isinstance(c, dict):
                continue
            name = c.get("name")
            value = c.get("value")
            if isinstance(name, str) and isinstance(value, str):
                parts.append(f"{name}={value}")
    elif isinstance(data, dict) and "cookies" in data:
        for c in data.get("cookies") or []:
            if isinstance(c, dict):
                name = c.get("name")
                value = c.get("value")
                if isinstance(name, str) and isinstance(value, str):
                    parts.append(f"{name}={value}")
    return "; ".join(parts)


def _resource_inventory_js() -> str:
    return r"""(() => {
  const names = performance.getEntriesByType('resource').map(e => e.name);
  const uniq = [...new Set(names)];
  return JSON.stringify({ count: uniq.length, urls: uniq });
})()"""


def _clerk_token_probe_js(template: str | None) -> str:
    opts = json.dumps({"template": template}) if template else "{}"
    return f"""(async () => {{
  try {{
    const clerk = window.Clerk;
    if (!clerk?.session?.getToken) return JSON.stringify({{ ok:false, reason:'no_clerk_getToken' }});
    const token = await clerk.session.getToken({opts});
    if (!token) return JSON.stringify({{ ok:false, reason:'empty_token' }});
    return JSON.stringify({{ ok:true, len: token.length, prefix: token.slice(0, 12), token }});
  }} catch (e) {{
    return JSON.stringify({{ ok:false, reason: String(e && e.message ? e.message : e) }});
  }}
}})()"""


def _pick_probe_urls(urls: list[str], *, limit: int) -> list[str]:
    needles = (
        "railway",
        "tryrailway",
        "up.railway",
        "specter",
        "tryspecter",
        "/api/",
        "graphql",
        "clerk",
    )
    scored: list[tuple[int, str]] = []
    for u in urls:
        low = u.lower()
        if not low.startswith("http"):
            continue
        if any(x in low for x in ("posthog", "sentry", "google-analytics", "doubleclick", "facebook.net")):
            continue
        score = sum(1 for n in needles if n in low)
        if score:
            scored.append((score, u))
    scored.sort(key=lambda t: (-t[0], len(t[1])))
    out: list[str] = []
    for _, u in scored:
        if u not in out:
            out.append(u)
        if len(out) >= limit:
            break
    return out


def _http_probe(
    url: str,
    cookie_header: str,
    *,
    bearer: str | None = None,
    timeout_s: float = 20.0,
) -> dict[str, Any]:
    headers: dict[str, str] = {
        "Cookie": cookie_header,
        "User-Agent": "specter-harvest/1.0 (+local research)",
        "Accept": "application/json, text/plain, */*",
    }
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(
        url,
        method="GET",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = resp.read(8000)
            ctype = resp.headers.get("Content-Type", "")
            snippet = body.decode("utf-8", errors="replace")
            keys: list[str] | None = None
            if "application/json" in ctype.lower():
                try:
                    parsed = json.loads(snippet)
                    if isinstance(parsed, dict):
                        keys = sorted(list(parsed.keys()))[:40]
                except Exception:
                    keys = None
            return {
                "ok": True,
                "status": getattr(resp, "status", None) or resp.getcode(),
                "content_type": ctype,
                "json_keys": keys,
                "body_preview": snippet[:240].replace("\n", " "),
            }
    except Exception as e:
        return {"ok": False, "error": str(e)[:240]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=str(_repo_root() / ".env"))
    ap.add_argument("--out-dir", default=str(_repo_root() / ".gstack" / "specter"))
    ap.add_argument("--probe-limit", type=int, default=8)
    ap.add_argument("--clerk-template", default="", help="Optional Clerk JWT template name for getToken().")
    ap.add_argument(
        "--skip-feed-wait",
        action="store_true",
        help="Skip waiting for feed UI markers (still attempts login). Useful when debugging auth/MFA/captcha drift.",
    )
    args = ap.parse_args()

    repo = _repo_root()
    env_path = Path(args.env_file)
    env = _load_dotenv(env_path)
    email = env.get("SPECTER_LOGIN_EMAIL") or env.get("SPECTER_EMAIL")
    password = env.get("SPECTER_LOGIN_PASSWORD") or env.get("SPECTER_PASSWORD")
    start_url = env.get("SPECTER_COMPANY_FEED_URL") or env.get("SPECTER_LOGIN_URL")
    if not email or not password or not start_url:
        raise SystemExit("Missing SPECTER_LOGIN_EMAIL/SPECTER_LOGIN_PASSWORD and SPECTER_COMPANY_FEED_URL (or SPECTER_LOGIN_URL) in env file.")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    bu = _browser_use_bin()

    # Fresh session
    _run(bu, ["close"], timeout=60)

    p_open = _run(bu, ["open", start_url], timeout=180)
    if p_open.returncode != 0:
        raise SystemExit((p_open.stderr or p_open.stdout or "open failed").strip())

    time.sleep(2.0)

    # Specter often hydrates a minimal shell first; Clerk mounts later, then "Login" reveals email/password fields.
    st: dict[str, Any] = {}
    for _ in range(45):
        st = json.loads(_eval(bu, _clerk_present_js(), timeout=30))
        if isinstance(st, dict) and st.get("clerk"):
            break
        time.sleep(1.0)
    else:
        raise SystemExit(f"Timed out waiting for Clerk to mount: {json.dumps(st)}")

    last_click = json.loads(_eval(bu, _login_js(email, password, stage="click_login"), timeout=60))
    time.sleep(1.5)

    fields: dict[str, Any] = {}
    for _ in range(45):
        fields = json.loads(_eval(bu, _has_login_fields_js(), timeout=60))
        if isinstance(fields, dict) and fields.get("has"):
            break
        time.sleep(1.0)
    else:
        # Second click can help if the first one raced the modal mount.
        last_click = json.loads(_eval(bu, _login_js(email, password, stage="click_login"), timeout=60))
        time.sleep(1.5)
        for _ in range(20):
            fields = json.loads(_eval(bu, _has_login_fields_js(), timeout=60))
            if isinstance(fields, dict) and fields.get("has"):
                break
            time.sleep(1.0)
        if not (isinstance(fields, dict) and fields.get("has")):
            raise SystemExit(f"Timed out waiting for Clerk fields: {json.dumps(fields)}")

    time.sleep(0.5)

    fill_res = json.loads(_eval(bu, _login_js(email, password, stage="fill")))
    if not fill_res.get("ok"):
        json.loads(_eval(bu, _login_js(email, password, stage="click_login")))
        time.sleep(1.2)
        fill_res = json.loads(_eval(bu, _login_js(email, password, stage="fill")))
    if not fill_res.get("ok"):
        raise SystemExit(f"Login form not ready: {json.dumps(fill_res)}")

    time.sleep(5.0)

    # Wait for authenticated app shell (table UI is a strong signal).
    if not args.skip_feed_wait:
        _wait_body_contains_any(
            bu,
            ["50 rows/page", "Push to CRM", "Recommended Searches", "Add Filter", "Company DB"],
            timeout_s=360.0,
        )
        time.sleep(2.0)

        # Ensure we're on the feed route (SPA can retain older paths briefly).
        feed_url = env.get("SPECTER_COMPANY_FEED_URL") or ""
        if feed_url:
            p_nav = _run(bu, ["open", feed_url], timeout=180)
            if p_nav.returncode != 0:
                raise SystemExit((p_nav.stderr or p_nav.stdout or "feed navigation failed").strip())
            time.sleep(2.0)
            _wait_body_contains_any(
                bu,
                ["50 rows/page", "Push to CRM", "Recommended Searches", "Add Filter", "Company DB"],
                timeout_s=360.0,
            )
            time.sleep(1.5)
    else:
        time.sleep(2.0)

    href_obj = json.loads(_eval(bu, """(() => JSON.stringify({ href: location.href }))()""", timeout=60))
    href = href_obj.get("href", "") if isinstance(href_obj, dict) else ""
    if not args.skip_feed_wait and "app.tryspecter.com" not in href:
        raise SystemExit(f"Expected Specter app URL after login, got: {href!r}")

    cookies_path = out_dir / "cookies.export.json"
    _cookies_export(bu, cookies_path)
    try:
        cookies_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass

    if not args.skip_feed_wait and not (_cookie_has_session(cookies_path) or _cookie_has_tryspecter_uat(cookies_path)):
        raise SystemExit(
            "Missing expected Clerk auth cookies after export "
            "(`__session` on `tryspecter.com` and/or `__client_uat*` on `tryspecter.com`). "
            "This usually means sign-in did not complete (MFA/captcha/wrong password/UI drift)."
        )
    if not args.skip_feed_wait and not _cookie_has_session(cookies_path):
        (out_dir / "WARN_no_httponly_session_cookie.txt").write_text(
            "No `__session` cookie was exported. Some API replays still work via `__client_uat*`, "
            "but you may need a Bearer JWT from Clerk for Railway calls.\n",
            encoding="utf-8",
        )

    resources = json.loads(_eval(bu, _resource_inventory_js(), timeout=120))
    (out_dir / "resource_urls.json").write_text(json.dumps(resources, indent=2), encoding="utf-8")

    clerk_meta_path = out_dir / "clerk_token_meta.json"
    clerk_token_path = out_dir / "clerk.jwt"
    clerk_template = (args.clerk_template or env.get("SPECTER_CLERK_JWT_TEMPLATE") or "").strip() or None
    clerk_raw = _eval(bu, _clerk_token_probe_js(clerk_template), timeout=120).strip()
    clerk_probe: dict[str, Any]
    try:
        parsed = json.loads(clerk_raw)
        clerk_probe = parsed if isinstance(parsed, dict) else {"raw": parsed}
    except Exception:
        clerk_probe = {"ok": False, "reason": "non_json_clerk_probe", "raw_head": clerk_raw[:200]}
    (out_dir / "clerk_token_raw.txt").write_text(clerk_raw[:2000], encoding="utf-8")

    meta_for_disk: dict[str, Any]
    if isinstance(clerk_probe, dict) and clerk_probe.get("ok") and isinstance(clerk_probe.get("token"), str):
        token = clerk_probe["token"]
        clerk_token_path.write_text(token + "\n", encoding="utf-8")
        try:
            clerk_token_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
        except Exception:
            pass
        meta_for_disk = {k: v for k, v in clerk_probe.items() if k != "token"}
        meta_for_disk["wrote_token_file"] = str(clerk_token_path)
    else:
        meta_for_disk = clerk_probe if isinstance(clerk_probe, dict) else {"raw": clerk_probe}
    clerk_meta_path.write_text(json.dumps(meta_for_disk, indent=2), encoding="utf-8")

    urls = resources.get("urls") if isinstance(resources, dict) else None
    if not isinstance(urls, list):
        urls = []

    cookie_header = _cookies_to_header(cookies_path)
    (out_dir / "cookie_header.len.txt").write_text(str(len(cookie_header)), encoding="utf-8")

    probe_urls = _pick_probe_urls([u for u in urls if isinstance(u, str)], limit=int(args.probe_limit))
    bearer: str | None = None
    if clerk_token_path.exists():
        bearer = clerk_token_path.read_text(encoding="utf-8").strip() or None

    probe_out: dict[str, Any] = {"probed": [], "note": "GET probes only; some APIs require POST/GraphQL."}
    for u in probe_urls:
        probe_out["probed"].append({"url": u, "mode": "cookie", "result": _http_probe(u, cookie_header)})
        if bearer:
            probe_out["probed"].append(
                {"url": u, "mode": "cookie_plus_bearer", "result": _http_probe(u, cookie_header, bearer=bearer)}
            )
    (out_dir / "probe_results.json").write_text(json.dumps(probe_out, indent=2), encoding="utf-8")

    summary = {
        "out_dir": str(out_dir),
        "cookies_export": str(cookies_path),
        "resource_count": int(resources.get("count") or 0),
        "probe_candidates": probe_urls,
        "skip_feed_wait": bool(args.skip_feed_wait),
        "clerk_fields": fields,
        "clerk_mount": st,
        "click_login": last_click,
        "fill": fill_res,
    }
    (out_dir / "harvest_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # Best-effort: close browser (keep optional)
    _run(bu, ["close"], timeout=60)

    print(json.dumps({"ok": True, **summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
