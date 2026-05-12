#!/usr/bin/env python3
"""
Luma session harvest — opens lu.ma in browser-use, lets the operator sign in
ONCE manually (email-link or Google), then exports the authenticated cookie
jar to `.gstack/luma/session/cookies.json` for later replay by
`luma_guestlist_harvest.py`.

Why semi-manual: lu.ma sign-in is email-link by default. There's no clean way
to automate the email side without inbox API access. The session lasts weeks
once captured, so a 30-second human step is cheaper than maintaining IMAP
plumbing.

Run (interactive):
  python3 scripts/luma_login_harvest.py

Run (already-signed-in, just refresh export):
  python3 scripts/luma_login_harvest.py --skip-signin-wait

Outputs:
  .gstack/luma/session/cookies.json           browser-use export
  .gstack/luma/session/cookie_header.txt      ready-to-replay Cookie header
  .gstack/luma/session/session_meta.json      whoami probe result
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO / ".gstack" / "luma" / "session"


def _browser_use_bin() -> str:
    return os.environ.get("LUMA_BROWSER_USE_BIN") \
        or os.environ.get("SPECTER_BROWSER_USE_BIN") \
        or shutil.which("browser-use") \
        or (lambda: (_ for _ in ()).throw(SystemExit(
            "browser-use not on PATH. Set LUMA_BROWSER_USE_BIN or SPECTER_BROWSER_USE_BIN.")))()


def _run(bu: str, args: list[str], *, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run([bu, *args], cwd=str(REPO), text=True, capture_output=True,
                          timeout=timeout, check=False)


def _eval(bu: str, js: str, *, timeout: int = 60) -> str:
    p = _run(bu, ["eval", js], timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(p.stderr or p.stdout or f"eval rc={p.returncode}")
    out = (p.stdout or "").strip()
    return out[len("result:"):].strip() if out.startswith("result:") else out


def _cookies_export(bu: str, out_file: Path) -> None:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    p = _run(bu, ["cookies", "export", str(out_file)], timeout=60)
    if p.returncode != 0:
        raise RuntimeError(p.stderr or p.stdout or "cookies export failed")
    if not out_file.exists() or out_file.stat().st_size < 10:
        raise RuntimeError("cookies export produced empty/missing file")


def _cookies_to_header(cookies_path: Path) -> tuple[str, dict[str, Any]]:
    data = json.loads(cookies_path.read_text(encoding="utf-8"))
    items: list[dict[str, Any]] = []
    if isinstance(data, list):
        items = [c for c in data if isinstance(c, dict)]
    elif isinstance(data, dict) and isinstance(data.get("cookies"), list):
        items = [c for c in data["cookies"] if isinstance(c, dict)]
    parts: list[str] = []
    relevant: dict[str, str] = {}
    for c in items:
        name, value = c.get("name"), c.get("value")
        domain = str(c.get("domain") or "").lower()
        if not (isinstance(name, str) and isinstance(value, str)):
            continue
        if "lu.ma" in domain or "luma.com" in domain:
            parts.append(f"{name}={value}")
            relevant[name] = (c.get("domain") or "")
    return "; ".join(parts), relevant


def _whoami(cookie_header: str, *, timeout: float = 12.0) -> dict[str, Any]:
    """Probe an authenticated endpoint. lu.ma's `/user/get-self` returns 200
    when signed in, 401 when not. Cheap, no side effects."""
    req = urllib.request.Request(
        "https://api2.luma.com/user/get-self",
        headers={
            "Cookie": cookie_header,
            "Accept": "application/json",
            "User-Agent": "luma-session-harvest/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(2048).decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body)
            except Exception:
                parsed = None
            return {"signed_in": True, "status": resp.getcode(),
                    "user_keys": sorted(list(parsed.keys()))[:20] if isinstance(parsed, dict) else None,
                    "preview": body[:240]}
    except urllib.error.HTTPError as e:
        return {"signed_in": False, "status": e.code, "reason": e.reason}
    except Exception as e:  # noqa: BLE001
        return {"signed_in": False, "error": str(e)[:200]}


def _is_signed_in_js() -> str:
    return r"""(() => JSON.stringify({
  href: location.href,
  has_session: !!document.cookie.match(/luma-auth-token|luma-session/i),
  has_signin_button: !!document.querySelector('a[href="/signin"], a[href*="signin"]'),
  has_signout: !!document.querySelector('button[aria-label*="Sign out" i], button:has(svg)')
}))()"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__ or "",
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--start-url", default="https://lu.ma/signin")
    ap.add_argument("--skip-signin-wait", action="store_true",
                    help="Don't prompt for sign-in; just open the page and immediately export "
                         "cookies. Useful when browser-use already holds a warm session.")
    ap.add_argument("--wait-timeout-s", type=int, default=300,
                    help="Max seconds to wait for the operator to sign in (default 5 min).")
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    bu = _browser_use_bin()

    print(f"==> browser-use bin: {bu}", file=sys.stderr)
    print(f"==> opening {args.start_url}", file=sys.stderr)
    p_open = _run(bu, ["open", args.start_url], timeout=120)
    if p_open.returncode != 0:
        raise SystemExit((p_open.stderr or p_open.stdout or "open failed").strip())

    time.sleep(2.0)

    if not args.skip_signin_wait:
        print(
            "\n"
            "============================================================\n"
            "  ACTION NEEDED: sign in to lu.ma in the browser-use window.\n"
            "    1) Enter your email (or click 'Sign in with Google')\n"
            "    2) Click the magic-link sent to your inbox\n"
            "    3) Wait until you see the lu.ma home / events page\n"
            f"  This script will poll every 5s for up to {args.wait_timeout_s}s.\n"
            "============================================================\n",
            file=sys.stderr,
        )
        deadline = time.time() + args.wait_timeout_s
        signed_in = False
        while time.time() < deadline:
            try:
                state = json.loads(_eval(bu, _is_signed_in_js(), timeout=30))
            except Exception as e:  # noqa: BLE001
                print(f"  (probe error: {e})", file=sys.stderr)
                time.sleep(5)
                continue
            href = state.get("href", "") if isinstance(state, dict) else ""
            if isinstance(state, dict) and (state.get("has_session") or
                                            ("/signin" not in href and href.startswith("https://lu.ma/"))):
                signed_in = True
                print(f"  detected signed-in state at {href}", file=sys.stderr)
                break
            print(f"  waiting... current: {href}", file=sys.stderr)
            time.sleep(5)
        if not signed_in:
            print("  WARNING: sign-in not detected within timeout. Exporting anyway.",
                  file=sys.stderr)

    cookies_path = out_dir / "cookies.json"
    _cookies_export(bu, cookies_path)
    try:
        cookies_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass

    cookie_header, relevant = _cookies_to_header(cookies_path)
    (out_dir / "cookie_header.txt").write_text(cookie_header, encoding="utf-8")
    try:
        (out_dir / "cookie_header.txt").chmod(stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass

    meta = _whoami(cookie_header) if cookie_header else {"signed_in": False, "reason": "no_lu.ma_cookies"}
    meta["cookies_path"] = str(cookies_path)
    meta["cookies_relevant_to_luma"] = relevant
    (out_dir / "session_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(json.dumps({"ok": True, **meta}, indent=2))
    return 0 if meta.get("signed_in") else 2


if __name__ == "__main__":
    raise SystemExit(main())
