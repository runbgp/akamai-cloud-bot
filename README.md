# Akamai Cloud Bot

A Discord bot that allows users to create, check the status of, and delete Akamai Cloud instances using the Akamai Cloud API.

## Features

- Create Akamai Cloud instances with custom configurations
- Check the status of your Akamai Cloud instances
- Delete your Akamai Cloud instances
- User-specific instance tracking
- Automatic instance status refresh every minute
- Weekly "still using this VM?" nudges with auto-deletion if the user doesn't confirm

## Setup

1. Clone this repository
2. Install uv (Python package manager):
   ```
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
3. Install dependencies:
   ```
   uv sync
   ```
4. Create a `.env` file with the following variables:
   ```
   DISCORD_TOKEN=your_discord_bot_token
   AKAMAI_API_TOKEN=your_akamai_cloud_api_token
   ```
5. Run the bot:
   ```
   uv run akamai_cloud_bot.py
   ```

## Commands

- `/create-instance` - Create a new Akamai Cloud instance
- `/list-instances` - List your Akamai Cloud instances (auto-refreshed every minute)
- `/delete-instance` - Delete an Akamai Cloud instance
- `/reboot-instance` - Reboot an Akamai Cloud instance
- `/keep-instance` - Confirm you're still using a VM (resets the auto-cleanup clock; fallback for missed DM nudges)

Admin-only (requires the user's Discord ID in `ADMIN_USER_IDS`):

- `/admin-extend <user_id> <instance_id> <days>` - Push the next usage check out
- `/admin-exempt <user_id> <instance_id> <true|false>` - Toggle a VM's exemption from auto-cleanup
- `/admin-list-all` - List every tracked instance and its nudge state

## Auto-cleanup

Every `NUDGE_INTERVAL_DAYS` (default 7) the bot DMs each VM owner asking
whether they're still using the instance. The DM has **Keep it** and **Delete now**
buttons. If no confirmation arrives within `NUDGE_GRACE_DAYS` (default 2 — with one
reminder DM 1 day before the deadline), the VM is deleted automatically.

If the bot was offline when a grace window would have expired, the window is
extended by the duration of the outage so users aren't punished for downtime.
Tuning happens via `NUDGE_INTERVAL_DAYS`, `NUDGE_GRACE_DAYS`, `NUDGE_REMINDER_DAYS`,
`NUDGE_CHECK_HOURS`, and (optionally) a hard `MAX_LIFETIME_DAYS` cap.
