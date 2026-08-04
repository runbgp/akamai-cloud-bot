# CLAUDE.md

When you change code in this repository, use these instructions.

## Commands

- Install or synchronize dependencies: `uv sync`
- Run the bot: `uv run akamai_cloud_bot.py`
- Add a dependency: `uv add <pkg>`
- Remove a dependency: `uv remove <pkg>`
- Start the container: `docker compose up -d`

Do not edit `pyproject.toml` directly to change dependencies. Commit `uv.lock` with dependency changes.

The container command uses `ghcr.io/runbgp/akamai-cloud-bot:latest`. It mounts `.env` and `akamai_instances.json`.

This repository has no test suite, linter configuration, or continuous integration. Use a test Discord server to make sure that changes work.

A manual test requires a real Akamai Cloud API token.

## Required environment

`akamai_cloud_bot.py` and `akamai_api.py` load `.env` through `python-dotenv`.

- `DISCORD_TOKEN` is the bot token. If this value is absent, the process stops at startup.
- `AKAMAI_API_TOKEN` is the Linode API token. If this value is absent, `AkamaiCloudAPI.__init__` raises `ValueError`.
- `ADMIN_USER_IDS` contains the Discord user IDs that can run `/admin-*` commands. Separate multiple IDs with commas.
- `NUDGE_INTERVAL_DAYS` sets the interval between usage checks. The default is 7 days.
- `NUDGE_GRACE_DAYS` sets the confirmation period. The default is 2 days.
- `NUDGE_REMINDER_DAYS` sets the time between the reminder and deletion. The default is 1 day.
- `NUDGE_CHECK_HOURS` sets the interval for the cleanup task. The default is 1 hour.
- `REFRESH_INTERVAL_MINUTES` sets the interval for `auto_refresh_instances`. The default is 10 minutes.
- `MAX_LIFETIME_DAYS` sets an optional instance age limit. A value of `0` disables this limit.

`/list-instances` refreshes instance data immediately. It does not wait for `auto_refresh_instances`.

## Architecture

This repository has three Python modules and no package structure.

### `akamai_cloud_bot.py`

This module contains the Discord interface and all slash commands. It also defines the views and selection menus for the create process.

Each nudge message has a `discord.ui.View` with **Keep it** and **Delete now** controls. The `on_ready` hook initializes the bot.

Three background tasks refresh instances, process usage checks, and record a heartbeat. These tasks are `auto_refresh_instances`, `check_nudges`, and `heartbeat_meta`.

`_import_instance_for_user` serves both import commands. If any user tracks an instance, this function rejects the import.

### `akamai_api.py`

This module calls `https://api.linode.com/v4`. Akamai Cloud is the current name for Linode, but the API still uses Linode identifiers.

Examples include `linode/ubuntu24.04`, `g6-nanode-1`, and `us-iad`.

All API calls use synchronous `requests` methods. `delete_instance` returns whether the response status is 200.

The other request methods call `raise_for_status()`. Their exceptions pass to the Discord command handlers.

### `database.py`

This module stores data in `akamai_instances.json`. The current layout is `{"_meta": {...}, "users": {user_id: [instance, ...]}}`.

The first read converts a legacy file that has user IDs at the top level. Each write operation loads and rewrites the full file.

The class has no external file lock or formal schema. Its methods enforce the data structure.

Each instance has a `_nudge` object. This object stores confirmation times, reminders, exemptions, counts, and direct-message failures.

During a field replacement, `update_instance` keeps the `_nudge` object. `_meta.last_seen_at` records a heartbeat every 5 minutes.

The nudge task uses the heartbeat to detect downtime. `docker-compose.yml` mounts the ignored database file so that container restarts keep the data.

## Important data flows

### Selection data

The module-level `cache` in `akamai_cloud_bot.py` stores regions, images, and instance types. `on_ready` fills this cache through `update_cache()`.

If the cache is empty, `/create-instance` refreshes it. No scheduled task refreshes this cache.

As a result, a long-running bot can show old selection data until it restarts.

### Instance refresh

`auto_refresh_instances` synchronizes each tracked instance at the `REFRESH_INTERVAL_MINUTES` interval. This task keeps inactive data current.

`/list-instances` calls `_refresh_instances_for_user` before it displays data. `update_instance` keeps the `_nudge` object during both refresh paths.

Do not write instance data outside the database methods.

### Automatic cleanup

`check_nudges` examines each instance every `NUDGE_CHECK_HOURS`. It sends a direct message after `NUDGE_INTERVAL_DAYS` without confirmation.

The owner then has `NUDGE_GRACE_DAYS` to confirm use. The bot sends one reminder `NUDGE_REMINDER_DAYS` before deletion.

After startup, the first cleanup pass adds detected downtime to an active grace period. It ignores downtime of less than 5 minutes.

If `MAX_LIFETIME_DAYS` is more than `0`, the bot deletes older instances without regard to confirmations.

### Nudge controls

`KeepInstanceButton` and `DeleteInstanceButton` are `discord.ui.DynamicItem` classes. `on_ready` registers these classes once.

Each direct message has a new `discord.ui.View(timeout=None)`. The button `custom_id` contains the instance ID.

Discord resolves a click to the correct instance after a bot restart. Before you change the regular-expression template, review active messages.

### Database concurrency

Public methods that change data are asynchronous. An `asyncio.Lock` serializes these changes.

Read methods are synchronous. A single read is atomic on the single-threaded event loop.

For an atomic read-and-write sequence, use `async with db.transaction():`. The lock is not reentrant.

Inside a transaction, call the private `_locked` methods. Do not call a public method that changes data.

Use `import_instance` as the reference for an atomic add with a uniqueness check.

### Blocking API calls

Synchronous `requests` calls run on the event loop. Each request blocks the bot until the request ends.

This design is sufficient for the current load. Do not assume that it supports a large request volume.

### Root passwords

The create process generates a root password through `generate_password()`. It sends the password once in a direct message.

The bot does not store the password. If Discord rejects the message, the instance remains active but the password is lost.

Preserve this behavior unless the task changes the security model.

### Selection limits

`RegionSelect`, `ImageSelect`, and `TypeSelect` contain priority lists. The default image is `linode/ubuntu24.04`.

The default type is `g6-nanode-1`. Discord limits each selection menu to 25 entries.

The priority lists determine which entries users can select.

## Conventions

- Use Python 3.14 or a later version. `pyproject.toml` contains the minimum version.
- Keep the exact dependency versions in `pyproject.toml`.
- Convert Discord user IDs to strings with `str(interaction.user.id)`.
- Keep Linode instance IDs as integers.
- Do not mix Discord user IDs and Linode instance IDs in `database.py`.
- Register slash commands with `@bot.tree.command(...)`.
- Reconnect the bot after you add a command. `bot.tree.sync()` runs during `on_ready`.
