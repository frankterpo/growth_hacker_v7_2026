#!/usr/bin/env bash
# Compatibility wrapper for the canonical browser-agent image helper.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export HERMES_BROWSER_IMAGE="${HERMES_BROWSER_IMAGE:-growth-hacker/hermes-browser-agent:2026.05}"
export OBSIDIAN_VAULT="${OBSIDIAN_VAULT:-${OBSIDIAN_VAULT_PATH:-/Users/pablote/Documents/Obsidian Vault}}"

bash "$ROOT/scripts/hermes_browser_agent_image.sh" build
bash "$ROOT/scripts/hermes_browser_agent_image.sh" verify
bash "$ROOT/scripts/hermes_browser_agent_image.sh" tunnel-smoke
