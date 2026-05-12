---
name: local-ollama-hermes
description: Keep Ollama models warm and Hermes fast on a free local stack (macOS-friendly). Use when tuning Ollama + Hermes for latency, RAM, and no cloud quota.
---

# Local Ollama + Hermes — speed & “always ready”

## Does Docker keep models warm?

**No.** Docker is for **repeatable deploy / isolation / same image everywhere**. Warmth is controlled by **Ollama’s unload timer** (`keep_alive`), not by containers. On **Mac**, Docker often runs **CPU-only**, so it can be **slower** than the **native Ollama app** (Metal).

## Keep models loaded (the real fix)

Ollama **unloads** weights after idle unless you extend **`keep_alive`**.

1. **Server-wide (recommended)** — Ollama’s default is **~5 minutes** in RAM, then unload. Override with **`OLLAMA_KEEP_ALIVE`** (same values as API `keep_alive`: duration string, seconds, **`0`** unload immediately, **negative** = stay loaded). Example:

   ```bash
   export OLLAMA_KEEP_ALIVE=-1
   # or: export OLLAMA_KEEP_ALIVE=24h
   ollama serve
   ```

   **macOS Ollama.app:** use **`launchctl setenv`** then restart the app (see [Ollama FAQ — configure server](https://docs.ollama.com/faq#how-do-i-configure-ollama-server)).

2. **Preload once** (official pattern) — empty request loads weights without a long chat:

   ```bash
   curl -s http://127.0.0.1:11434/api/generate -d '{"model":"YOUR_MODEL"}'
   # or: ollama run YOUR_MODEL ""
   ```

   See [Ollama FAQ — preload](https://docs.ollama.com/faq#how-can-i-preload-a-model-into-ollama-to-get-faster-response-times).

3. **Per-request** — API `keep_alive` overrides `OLLAMA_KEEP_ALIVE` for that call (same FAQ).

4. **Heartbeat (optional)** — if something still evicts RAM, ping every few minutes with a tiny `generate` (same host/port as preload).

## Hermes: fewer tokens before the first reply

- **Smaller tool surface** — e.g. `hermes-acp` instead of full `hermes-cli` (smaller tool JSON → faster prefill).
- **Cap `model.ollama_num_ctx`** — e.g. `8192`–`16384` unless you truly need long context (smaller KV → faster prefill on CPU).
- **`model.context_length` ≥ 65536** — Hermes enforces a **64K minimum** for the *declared* window; keep **`ollama_num_ctx`** as the **real** Ollama cap.
- **Warm once per model** — `ollama run YOUR_MODEL "hi"` then leave Ollama running; first Hermes turn is much cheaper.

## Hardware reality (free ≠ instant)

- **First load** after cold boot or unload = **RAM I/O + graph init** — unavoidable.
- **`ollama ps`** — if you see **`100% CPU`** and no GPU path, expect **slow** tokens vs **Metal**. On Apple Silicon, install/run **native arm64 Ollama** (not Rosetta); prefer **Ollama.app** so Metal is used.
- **Flash Attention** — on the Ollama server, `OLLAMA_FLASH_ATTENTION=1` can reduce memory pressure at longer contexts ([Ollama FAQ](https://docs.ollama.com/faq)).

## Hermes + local Ollama (fast path)

- **Hermes `model`**: `provider: custom`, `base_url: http://127.0.0.1:11434/v1`, `api_key: none`, **`model.ollama_num_ctx`** modest (e.g. `8192`) — smaller KV → faster prefill.
- **Hermes `model.context_length`**: must be **≥ 65536** (Hermes minimum); that is **not** the same as Ollama’s real `num_ctx` (use `ollama_num_ctx`).
- **Toolsets**: prefer **`hermes-acp`** over **`hermes-cli`** for fewer tool-schema tokens before first reply.
- **`OLLAMA_API_KEY` in `~/.hermes/.env`**: only for **Ollama Cloud** (`ollama-cloud` / `https://ollama.com/v1`). It does **not** change local `127.0.0.1` speed.

## Repo helper: warm `ollama serve`

From the project root:

```bash
chmod +x scripts/ollama-serve-warm.sh
./scripts/ollama-serve-warm.sh
```

This sets **`OLLAMA_KEEP_ALIVE`** (default **`-1`**) and **`OLLAMA_FLASH_ATTENTION=1`** then runs `ollama serve`. **Stop any other Ollama** on `:11434` first.

**Preload** after the server is up ([FAQ](https://docs.ollama.com/faq#how-can-i-preload-a-model-into-ollama-to-get-faster-response-times)):

```bash
curl -s http://127.0.0.1:11434/api/generate -d '{"model":"hermes-llama3.2-3b-fast:latest"}' >/dev/null
```
- **Model size** — `3B` interactive, `7B` quality; pick one default and switch with `hermes model` when needed.

## Optional skill catalogs (ideas, not requirements)

- **Hugging Face Hub** — discover GGUFs / quantizations; Ollama **`ollama pull`** is still the simplest local path.
- **Community skill repos** (e.g. aggregators) — good for *workflows*; **latency** is still Ollama + prompt + tool schemas.

## One-line health check

```bash
curl -s http://127.0.0.1:11434/api/version && ollama ps
```
