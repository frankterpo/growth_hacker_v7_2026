#!/usr/bin/env bash
# Build and verify the browser-capable Docker image used by Hermes research agents.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${HERMES_BROWSER_IMAGE:-growth-hacker/hermes-browser-agent:2026.05}"
VAULT="${OBSIDIAN_VAULT:-/Users/pablote/Documents/Obsidian Vault}"
DOCKERFILE="$ROOT/hermes/docker/browser-agent.Dockerfile"

usage() {
  cat <<'EOF'
Usage:
  scripts/hermes_browser_agent_image.sh build
  scripts/hermes_browser_agent_image.sh verify
  scripts/hermes_browser_agent_image.sh smoke
  scripts/hermes_browser_agent_image.sh tunnel-smoke
  scripts/hermes_browser_agent_image.sh config-snippet

Environment:
  HERMES_BROWSER_IMAGE   Docker image tag (default: growth-hacker/hermes-browser-agent:2026.05)
  OBSIDIAN_VAULT         Host vault path (default: /Users/pablote/Documents/Obsidian Vault)

Notes:
  - No secrets are baked into the image.
  - API keys should stay in Hermes profile .env files and be forwarded by Hermes only when needed.
  - The vault is mounted at /vault/obsidian inside the container.
EOF
}

docker_run_base=(
  docker run --rm
  --shm-size=2g
  --add-host=host.docker.internal:host-gateway
  -e "OBSIDIAN_VAULT=/vault/obsidian"
  -e "BROWSER_USE_HEADLESS=true"
  -e "PROFILE_USE_HOME=/browser-profiles"
  -v "$ROOT:$ROOT"
  -v "$ROOT:/workspace"
  -v "$VAULT:/vault/obsidian"
  -w "$ROOT"
)

case "${1:-}" in
  build)
    docker build -f "$DOCKERFILE" -t "$IMAGE" "$ROOT"
    ;;
  verify)
    "${docker_run_base[@]}" "$IMAGE" bash -lc \
      'set -euo pipefail
       test -d "$OBSIDIAN_VAULT"
       python --version
       node --version
       cloudflared --version
       browser-use --help >/dev/null
       profile-use --help >/dev/null
       browser-use doctor
       python - <<'"'"'PY'"'"'
import importlib.metadata as metadata
for name in ("browser-use", "playwright"):
    print(f"{name}: {metadata.version(name)}")
print("python packages: ok")
PY
       python - <<'"'"'PY'"'"'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
    page = browser.new_page()
    page.goto("data:text/html,<title>hermes-browser-agent</title>")
    assert page.title() == "hermes-browser-agent"
    browser.close()
print("playwright chromium: ok")
PY'
    ;;
  smoke)
    "${docker_run_base[@]}" "$IMAGE" bash -lc \
      'set -euo pipefail
       browser-use --session hermes-smoke --json open https://example.com >/tmp/browser-open.json
       browser-use --session hermes-smoke --json state
       browser-use --session hermes-smoke close || true'
    ;;
  tunnel-smoke)
    "${docker_run_base[@]}" "$IMAGE" bash -lc \
      'set -euo pipefail
       python -m http.server 8765 --bind 127.0.0.1 >/tmp/hermes-origin.log 2>&1 &
       origin_pid=$!
       timeout 25s cloudflared tunnel --url http://127.0.0.1:8765 --no-autoupdate > /tmp/cloudflared.log 2>&1 &
       tunnel_pid=$!
       cleanup() {
         kill "$tunnel_pid" "$origin_pid" >/dev/null 2>&1 || true
         wait "$tunnel_pid" >/dev/null 2>&1 || true
         wait "$origin_pid" >/dev/null 2>&1 || true
       }
       trap cleanup EXIT
       for _ in $(seq 1 20); do
         url="$(python - <<'"'"'PY'"'"'
import re
from pathlib import Path

match = re.search(r"https://\S*trycloudflare\.com", Path("/tmp/cloudflared.log").read_text(errors="ignore"))
print(match.group(0) if match else "")
PY
)"
         if [ -n "$url" ]; then
           echo "$url"
           exit 0
         fi
         sleep 1
       done
       python - <<'"'"'PY'"'"'
from pathlib import Path

print(Path("/tmp/cloudflared.log").read_text(errors="ignore"))
PY
       echo "cloudflared tunnel did not publish a trycloudflare.com URL" >&2
       exit 1'
    ;;
  config-snippet)
    cat <<EOF
terminal:
  backend: docker
  docker_image: $IMAGE
  docker_volumes:
    - /Users/pablote/Projects:/Users/pablote/Projects
    - /Users/pablote/.hermes/cache/documents:/output
    - "$VAULT:/vault/obsidian"
  docker_extra_args:
    - --shm-size=2g
    - --add-host=host.docker.internal:host-gateway
  docker_env:
    OBSIDIAN_VAULT: "/vault/obsidian"
    BROWSER_USE_HEADLESS: "true"
    PROFILE_USE_HOME: "/browser-profiles"
    ANONYMIZED_TELEMETRY: "false"
EOF
    ;;
  *)
    usage
    exit 2
    ;;
esac
