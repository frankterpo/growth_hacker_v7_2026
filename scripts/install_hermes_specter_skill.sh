#!/usr/bin/env bash
# Backwards-compatible alias: install just the hermes-specter-enrich skill.
# Forwards to the generic installer so adding new Luma/Cala/etc. skills is
# one rsync invocation away.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec bash "$ROOT/scripts/install_hermes_skills.sh" research/hermes-specter-enrich "$@"
