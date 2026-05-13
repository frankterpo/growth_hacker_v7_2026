# Browser-capable Hermes research agents

This repo uses a dedicated Docker image for Hermes profiles that need browser
automation, authenticated research, and Obsidian CRM handoffs.

Use this file and `scripts/hermes_browser_agent_image.sh` for the current build
and verification path.

The image installs:

- `browser-use`
- `profile-use`
- Playwright Chromium and browser system dependencies
- Python 3.11 and Node 20 from the existing Hermes base image

No secrets are baked into the image. Keep API keys in the Hermes profile `.env`
or another approved secret source.

## Build

```bash
cd /Users/pablote/Projects/growth_hacker_v7_2026
bash scripts/hermes_browser_agent_image.sh build
```

Historical local image tag:

```text
growth-hacker/hermes-browser-agent:local
```

Default helper image tag:

```text
growth-hacker/hermes-browser-agent:2026.05
```

Override it with:

```bash
HERMES_BROWSER_IMAGE=your-tag bash scripts/hermes_browser_agent_image.sh build
```

## Verify

```bash
cd /Users/pablote/Projects/growth_hacker_v7_2026
bash scripts/hermes_browser_agent_image.sh verify
```

The verifier checks:

- the Obsidian vault mount exists in the container at `/vault/obsidian`
- `browser-use --help` works
- `browser-use doctor` can run
- `profile-use --help` works
- `browser-use` and `playwright` are installed as Python packages
- Playwright can launch headless Chromium inside Docker
- Python and Node are available

Optional browser smoke test:

```bash
bash scripts/hermes_browser_agent_image.sh smoke
```

This opens `https://example.com` in a container browser session and reads state.

## Hermes profile config

Use this image for browser-capable research profiles such as `jerme`. Archap can
stay on the lighter base image unless it needs browser tools.

```yaml
terminal:
  backend: docker
  docker_image: growth-hacker/hermes-browser-agent:2026.05
  docker_volumes:
    - /Users/pablote/Projects:/Users/pablote/Projects
    - /Users/pablote/.hermes/cache/documents:/output
    - "/Users/pablote/Documents/Obsidian Vault:/vault/obsidian"
  docker_extra_args:
    - --shm-size=2g
    - --add-host=host.docker.internal:host-gateway
  docker_env:
    OBSIDIAN_VAULT: "/vault/obsidian"
    BROWSER_USE_HEADLESS: "true"
    PROFILE_USE_HOME: "/browser-profiles"
    ANONYMIZED_TELEMETRY: "false"
```

For `jerme`, edit:

```text
/Users/pablote/.hermes/profiles/jerme/config.yaml
```

Keep `terminal.backend: docker`; do not switch research agents back to a host
terminal just to access browser-use.

## Scaling pattern

Use a profile per agent and the same image tag for all browser-capable research
agents. Mount shared read/write surfaces explicitly:

- repo: `/Users/pablote/Projects:/Users/pablote/Projects`
- output: `/Users/pablote/.hermes/cache/documents:/output`
- vault: `/Users/pablote/Documents/Obsidian Vault:/vault/obsidian`

Set `docker_extra_args: ["--shm-size=2g", "--add-host=host.docker.internal:host-gateway"]`
for Chromium stability and host-service access under Docker Desktop.

If an agent should not write the vault, omit the vault mount or mount it
read-only after validating Hermes supports Docker mount suffixes in
`docker_volumes`.

## CRM contract

Browser research agents still do not own the Obsidian schema. `jerme` produces
structured, sourced handoffs. `Archap` owns markdown materialization. Both must
follow:

```text
hermes/skills/productivity/obsidian-crm-data-contract/SKILL.md
```

That contract defines the field names, association rules, provenance block, and
handoff shape.

## Failure modes

- `browser-use` missing in Hermes: the profile is not using this image, or the
  image build failed.
- Vault not visible in Docker: the profile is missing the vault entry under
  `terminal.docker_volumes`, or `OBSIDIAN_VAULT` is not set to `/vault/obsidian`.
- `browser-use doctor` reports missing `cloudflared`: only browser-use tunnels
  need it. Local Chromium use can still work.
- Browser launch fails on macOS Docker Desktop: rebuild the image and rerun
  `verify`; Docker Desktop must have enough memory for Chromium.
- API key missing: do not bake it into the image. Add it to the Hermes profile
  environment and forward only the keys required by that profile.

## Browser boundary

The image is designed for headless browser automation from Hermes Docker
terminals. Manual sign-in flows can still require a visible host browser or a
remote browser/CDP service. If a login cannot be completed headlessly, capture
the session on the host or in a remote browser first, then run Docker replay jobs
against the mounted repo state (`.gstack/luma/`, `.gstack/specter/`, etc.).

For host browser services on Docker Desktop, target `host.docker.internal` from
inside the container.
