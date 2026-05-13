# Two Telegram bots (jerme + Archap) on Hermes

Hermes binds **one `TELEGRAM_BOT_TOKEN` per profile** (`HERMES_HOME`). Two bots ⇒ **two named profiles**, each with its own gateway process and `.env`.

## 1. Profiles

```bash
hermes profile create jerme --clone      # or --no-skills / --clone-all as you prefer
hermes profile create archap --clone
```

Put tokens in **each profile’s** `~/.hermes/profiles/<name>/.env` (never commit tokens):

| Profile   | Bot        | Variable              |
|-----------|------------|------------------------|
| `jerme`   | jermeBot   | `TELEGRAM_BOT_TOKEN=…` |
| `archap`  | ArchapBot  | `TELEGRAM_BOT_TOKEN=…` (when you have it) |

Also set `TELEGRAM_ALLOWED_USERS` (and optionally `TELEGRAM_HOME_CHANNEL`) per profile — same pattern as `hermes gateway setup`.

**Allow “everyone”:** use `TELEGRAM_ALLOWED_USERS=*` and/or `TELEGRAM_ALLOW_ALL_USERS=true`. The literal string `everyone` is **not** matched to Telegram user IDs and will lock the bot down. If every allowlist env is empty, Hermes falls back to `GATEWAY_ALLOW_ALL_USERS` — see `gateway/run.py` `_is_user_authorized`.

**One command from this repo** (tokens only in the shell, never in git):

```bash
export JERME_TELEGRAM_BOT_TOKEN='…' ARCHAP_TELEGRAM_BOT_TOKEN='…'
export START_GATEWAYS=1   # optional: also start both gateways
bash /Users/pablote/Projects/growth_hacker_v7_2026/scripts/hermes_bootstrap_dual_telegram.sh
```

## 2. Personas (skills in this repo)

From the repo root:

```bash
bash scripts/install_hermes_skills.sh productivity
```

That installs:

- `productivity/jerme-board-operator` — Kanban + cron behaviour for jerme.
- `productivity/archap-librarian` — Librarian-only answers for Archap.

**Attach in config** (per profile `config.yaml`): add the skill names to whatever mechanism your build uses for default or gateway-loaded skills (e.g. `always_load`, gateway skill list, or SOUL preamble pointing to `/jerme-board-operator` / `/archap-librarian`). If unsure, invoke manually in chat once to verify.

## 2.5. Browser-capable Docker terminals

Profiles that run browser research from `terminal.backend: docker` need a Docker
image with `browser-use`, `profile-use`, and Chromium already installed. Build
and verify the repo image:

```bash
bash /Users/pablote/Projects/growth_hacker_v7_2026/scripts/hermes_browser_docker_smoke.sh
```

Then set browser-capable profiles, especially `jerme`, to:

```yaml
terminal:
  docker_image: growth-hacker/hermes-browser-use:latest
  docker_extra_args:
    - --shm-size=1g
  docker_volumes:
    - /Users/pablote/Projects:/Users/pablote/Projects
    - /Users/pablote/.hermes/cache/documents:/output
    - "/Users/pablote/Documents/Obsidian Vault:/Users/pablote/Documents/Obsidian Vault"
```

Keep the Obsidian mount at the same absolute path for every agent profile. The
space in `Obsidian Vault` means the YAML value must remain quoted.

## 3. Kanban + cron (jerme profile only)

The **dashboard Kanban uses the same `kanban.db` as the active `HERMES_HOME`.** Run the board, cron, and jerme Telegram **all on the `jerme` profile** (or keep everything on `default` and use Archap as the only separate profile).

1. **Auxiliary specifier** (for `hermes kanban specify`): in `~/.hermes/profiles/jerme/config.yaml`, set `auxiliary.triage_specifier` to a model that is good at JSON-ish specs (see upstream Hermes Kanban docs).
2. **Browser-capable Docker terminal** (for browser-use/profile-use research jobs): build `growth-hacker/hermes-browser-agent:2026.05` and mount the Obsidian vault at `/vault/obsidian`; see `hermes/DOCKER_BROWSER_AGENTS.md`.
3. **Gateway** (dispatcher picks up `ready` tasks with a valid assignee):

   ```bash
   hermes -p jerme gateway install   # or gateway start for foreground
   hermes -p jerme gateway status
   ```

4. **Ten-minute sweep** — either:
   - **Shell + specify (simple):** install `scripts/hermes_jerme_kanban_sweep.sh` from this repo and point cron at it (see skill `jerme-board-operator`), **or**
   - **Agent cron:** attach skill `jerme-board-operator` and create a job that loads `kanban` + `session_search` (+ file reads for `memories/*.md`):

   ```bash
   hermes -p jerme cron create "*/10 * * * *" \
     --name "kanban-triage-ready" \
     --skill productivity/jerme-board-operator \
     "Execute the jerme-board-operator SKILL: triage list → memory/session context → kanban specify per task (or --all) → confirm gateway is running for ready+assignee dispatch."
   ```

   If jobs need dangerous commands, relax cron approvals for that profile (e.g. `approvals.cron_mode: approve`) or keep the job to Kanban-safe tools only.

**Column truth (Hermes Kanban):** `hermes kanban specify` moves **`triage → todo`**; tasks with **no open parents** are then promoted to **`ready`** automatically. Parent-linked children stay in `todo` until parents are `done`.

## 4. Archap gateway (separate process)

When the second token exists:

```bash
hermes -p archap gateway install
hermes -p archap gateway start
```

Archap should **not** load the jerme Kanban skill if you want a hard boundary; load only `archap-librarian`.

## 5. Verify

```bash
hermes -p jerme gateway list
hermes -p archap gateway list
hermes -p jerme cron list
hermes -p jerme kanban stats
```

Official feature doc (upstream): Hermes user guide → **Kanban**, **Gateway / Telegram**, **Cron**.
