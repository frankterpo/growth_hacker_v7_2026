#!/usr/bin/env bash
# jermeBot helper: optional 10-minute cron — memory-free path uses only the
# built-in triage specifier. For memory/session grounding, use an agent cron
# with skill productivity/jerme-board-operator (see hermes/TELEGRAM_DUAL_BOTS_SETUP.md).
set -euo pipefail

: "${HERMES_HOME:=$HOME/.hermes}"
export HERMES_HOME

if ! command -v hermes >/dev/null 2>&1; then
  echo "hermes: command not found" >&2
  exit 1
fi

hermes kanban specify --all
