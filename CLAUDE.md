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
- `ADMIN_USER_IDS` — comma-separated Discord user IDs allowed to run `/admin-*` commands (optional)
- `NUDGE_INTERVAL_DAYS` / `NUDGE_GRACE_DAYS` / `NUDGE_REMINDER_DAYS` / `NUDGE_CHECK_HOURS` — auto-cleanup tuning (defaults 7 / 2 / 1 / 1)
- `REFRESH_INTERVAL_MINUTES` — backstop interval for `auto_refresh_instances` (default 10). `/list-instances` refreshes inline regardless
- `MAX_LIFETIME_DAYS` — optional hard ceiling on instance age regardless of confirmations; `0` disables (default)

## Architecture

Three modules, no package structure:

- `akamai_cloud_bot.py` — Discord layer. Defines slash commands (`/create-instance`, `/list-instances`, `/delete-instance`, `/reboot-instance`, `/keep-instance`, `/import-instance`, and admin-only `/admin-extend` / `/admin-exempt` / `/admin-list-all` / `/admin-import-instance`), the `discord.ui.View` + `Select` widgets used for the create flow, the persistent `NudgeView` for weekly Keep/Delete confirmations, the `on_ready` startup hook, and three background tasks: `auto_refresh_instances`, `check_nudges`, and `heartbeat_meta`. `_import_instance_for_user` is the shared path for both import commands; it refuses if any user already tracks the instance to avoid double-counting against the nudge loop.
- `akamai_api.py` — Thin REST wrapper around `https://api.linode.com/v4`. **Important: "Akamai Cloud" is the Linode rebrand — this code talks to the Linode API, and image/region/type IDs use Linode naming (`linode/ubuntu24.04`, `g6-nanode-1`, `us-iad`).** Every method uses `requests` synchronously and calls `raise_for_status()` — exceptions bubble up to the Discord command handlers, which catch and surface them via `interaction.followup.send`.
- `database.py` — JSON-file persistence (`akamai_instances.json`). The current layout is `{"_meta": {...}, "users": {user_id: [instance, ...]}}`; legacy flat-dict files are migrated transparently on first read. Every read/write loads and rewrites the entire file; there is no locking, no async, and no schema beyond what the code enforces. Each instance gets a `_nudge` sub-dict (`last_confirmed_at`, `nudge_sent_at`, `reminder_sent_at`, `nudge_count`, `exempt`, `last_dm_failed`) that `update_instance` preserves across `auto_refresh_instances` overwrites. `_meta.last_seen_at` is bumped every 5 minutes by `heartbeat_meta` so the nudge loop can extend grace windows by detected downtime on the next startup. The file is `.gitignore`d but mounted as a volume in `docker-compose.yml` so it survives container restarts.

### Data flow worth knowing before editing

- A module-level `cache` dict in `akamai_cloud_bot.py` holds regions/images/instance types. It is populated once in `on_ready` via `update_cache()` and refreshed lazily inside `/create-instance` only if empty — there is no scheduled cache refresh, so a long-running bot will see stale lists until restart.
- `auto_refresh_instances` (`@tasks.loop(minutes=REFRESH_INTERVAL_MINUTES)`, default 10) is a low-frequency backstop that resyncs every tracked instance from Linode. The hot path for fresh data is now `_refresh_instances_for_user`, called inline by `/list-instances`, so this loop exists mostly to keep idle state warm. `update_instance` merges Linode fields over the stored dict but preserves the `_nudge` block, so don't rewrite the JSON outside the database helpers.
- `check_nudges` (`@tasks.loop(hours=NUDGE_CHECK_HOURS)`) walks every instance and DMs the owner if they haven't confirmed in `NUDGE_INTERVAL_DAYS`. After the initial DM there's a `NUDGE_GRACE_DAYS` window with one reminder DM `NUDGE_REMINDER_DAYS` before deletion. On the first tick after startup the loop extends the grace window by the detected downtime (`now - _meta.last_seen_at`, ignoring gaps under 5 minutes) so users aren't punished for outages. The loop reads `MAX_LIFETIME_DAYS` (default 0/disabled) before nudging — if set, instances older than that are deleted regardless of confirmation status.
- Nudge buttons are per-instance dynamic items (`KeepInstanceButton` / `DeleteInstanceButton`, `discord.ui.DynamicItem` subclasses) registered once via `bot.add_dynamic_items(...)` in `on_ready`. Each nudge DM gets a fresh `discord.ui.View(timeout=None)` with the instance_id encoded in the button's `custom_id` (`nudge:keep:<id>` / `nudge:delete:<id>`). Clicks resolve to the exact VM the message is about even after a bot restart. Don't change the regex template without thinking through in-flight DMs.
- `Database` mutating methods are async and serialize through an `asyncio.Lock`. Read methods are sync (single-call snapshots are atomic on a single-threaded event loop). For multi-step "read, decide, write" operations that need to be atomic against concurrent writers, use `async with db.transaction():` — but note the lock is **not reentrant**, so don't call other public mutating methods from inside a transaction; use the private `_locked` helpers if you need to compose. `import_instance` is the canonical example of an atomic uniqueness-checked add.
- The synchronous `requests` calls run on the asyncio event loop. They block the bot during each call. Acceptable at current scale; do not assume it scales.
- The instance create flow generates a root password via `generate_password()`, sends it to the user **once via DM**, and never persists it. If the DM fails (`discord.Forbidden`), the password is lost and only a generic message is sent in-channel — the instance still exists but is unreachable. Preserve this behavior unless explicitly changing the security model.
- The `RegionSelect`, `ImageSelect`, and `TypeSelect` classes hard-code priority lists (`priority_regions`, `priority_vendors`, `priority_types`) and a default image (`linode/ubuntu24.04`) and type (`g6-nanode-1`). Discord caps dropdowns at 25 options, so these lists determine what users actually see.

## Conventions

- Python 3.14+ (see `pyproject.toml`); pinned exact versions for `discord.py`, `python-dotenv`, `requests`.
- All user-facing IDs are stringified Discord user IDs (`str(interaction.user.id)`). Instance IDs from the Linode API are ints. Don't mix them when touching `database.py`.
- Slash commands are registered via `@bot.tree.command(...)` and synced in `on_ready`. New commands won't appear in Discord until the bot reconnects and `bot.tree.sync()` runs.
