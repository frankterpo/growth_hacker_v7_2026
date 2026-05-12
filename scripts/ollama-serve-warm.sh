#!/usr/bin/env bash
# Start Ollama with settings that reduce "first message takes forever" / reload churn.
# Per Ollama FAQ: https://docs.ollama.com/faq#how-do-i-keep-a-model-loaded-in-memory-or-make-it-unload-immediately
#
# Usage:
#   chmod +x scripts/ollama-serve-warm.sh
#   ./scripts/ollama-serve-warm.sh
#
# For Ollama.app on macOS, use `launchctl setenv` for the same vars, then restart the app.

set -euo pipefail

export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:--1}"
export OLLAMA_FLASH_ATTENTION="${OLLAMA_FLASH_ATTENTION:-1}"
# Hermes + tools prefill can exceed 8k/16k; server default must not truncate (see Ollama context-length FAQ).
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-32768}"

exec ollama serve "$@"
