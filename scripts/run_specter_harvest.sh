#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# zsh: put comments on their own line — inline `# ...` after export breaks if `interactivecomments` is off.
bu="$(command -v browser-use 2>/dev/null || true)"
if [[ -n "${SPECTER_BROWSER_USE_BIN:-}" ]]; then
  :
elif [[ -n "$bu" ]]; then
  export SPECTER_BROWSER_USE_BIN="$bu"
fi
exec python3 scripts/specter_harvest.py "$@"
