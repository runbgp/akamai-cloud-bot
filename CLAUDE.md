# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

- Install/sync deps: `uv sync`
- Run the bot: `uv run akamai_cloud_bot.py`
- Add/remove deps: `uv add <pkg>` / `uv remove <pkg>` (never edit `pyproject.toml` by hand for deps; `uv.lock` is committed)
- Container: `docker compose up -d` (pulls `ghcr.io/runbgp/akamai-cloud-bot:latest`; mounts `.env` and `akamai_instances.json`)

There is no test suite, linter config, or CI in this repo — verify changes by running the bot against a test Discord server with a real Akamai/Linode API token.

## Required environment

`.env` (loaded via `python-dotenv` in `akamai_cloud_bot.py` and `akamai_api.py`):

- `DISCORD_TOKEN` — bot token; without it the process exits at startup
- `AKAMAI_API_TOKEN` — sent as `Authorization: Bearer …` to the Linode API; `AkamaiCloudAPI.__init__` raises `ValueError` if missing

## Architecture

Three modules, no package structure:

- `akamai_cloud_bot.py` — Discord layer. Defines slash commands (`/create-instance`, `/list-instances`, `/delete-instance`, `/reboot-instance`), the `discord.ui.View` + `Select` widgets used for the create flow, the `on_ready` startup hook, and the `auto_refresh_instances` background task.
- `akamai_api.py` — Thin REST wrapper around `https://api.linode.com/v4`. **Important: "Akamai Cloud" is the Linode rebrand — this code talks to the Linode API, and image/region/type IDs use Linode naming (`linode/ubuntu24.04`, `g6-nanode-1`, `us-iad`).** Every method uses `requests` synchronously and calls `raise_for_status()` — exceptions bubble up to the Discord command handlers, which catch and surface them via `interaction.followup.send`.
- `database.py` — JSON-file persistence (`akamai_instances.json`) keyed by Discord user ID. Every read/write loads and rewrites the entire file; there is no locking, no async, and no schema. The file is `.gitignore`d but mounted as a volume in `docker-compose.yml` so it survives container restarts.

### Data flow worth knowing before editing

- A module-level `cache` dict in `akamai_cloud_bot.py` holds regions/images/instance types. It is populated once in `on_ready` via `update_cache()` and refreshed lazily inside `/create-instance` only if empty — there is no scheduled cache refresh, so a long-running bot will see stale lists until restart.
- `auto_refresh_instances` (`@tasks.loop(minutes=1)`) iterates every stored `(user_id, instance_id)` and calls `akamai_api.get_instance` per row, then `db.update_instance`. This is O(N) HTTP calls per minute against the Linode API — keep that in mind before adding instances or shortening the interval.
- The synchronous `requests` calls run on the asyncio event loop. They block the bot during each call. Acceptable at current scale; do not assume it scales.
- The instance create flow generates a root password via `generate_password()`, sends it to the user **once via DM**, and never persists it. If the DM fails (`discord.Forbidden`), the password is lost and only a generic message is sent in-channel — the instance still exists but is unreachable. Preserve this behavior unless explicitly changing the security model.
- The `RegionSelect`, `ImageSelect`, and `TypeSelect` classes hard-code priority lists (`priority_regions`, `priority_vendors`, `priority_types`) and a default image (`linode/ubuntu24.04`) and type (`g6-nanode-1`). Discord caps dropdowns at 25 options, so these lists determine what users actually see.

## Conventions

- Python 3.13+ (see `pyproject.toml`); pinned exact versions for `discord.py`, `python-dotenv`, `requests`.
- All user-facing IDs are stringified Discord user IDs (`str(interaction.user.id)`). Instance IDs from the Linode API are ints. Don't mix them when touching `database.py`.
- Slash commands are registered via `@bot.tree.command(...)` and synced in `on_ready`. New commands won't appear in Discord until the bot reconnects and `bot.tree.sync()` runs.
