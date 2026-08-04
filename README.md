# Akamai Cloud Bot

Akamai Cloud Bot manages Akamai Cloud instances through Discord and the Linode API.

## Features

- Creates instances with a selected region, image, and type.
- Lists, reboots, imports, and deletes instances.
- Tracks the instances for each Discord user.
- Refreshes instance data from Linode.
- Sends regular usage checks and deletes unconfirmed instances.

## Set up the bot

1. Clone this repository.
2. Install `uv`.

   ```
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```

3. Install the dependencies.

   ```
   uv sync
   ```

4. Create a `.env` file with these required values.

   ```
   DISCORD_TOKEN=your_discord_bot_token
   AKAMAI_API_TOKEN=your_akamai_cloud_api_token
   ```

5. Run the bot.

   ```
   uv run akamai_cloud_bot.py
   ```

## Commands

| Command | Function |
|---|---|
| `/create-instance` | Creates an Akamai Cloud instance. |
| `/list-instances` | Lists your instances and refreshes their data. |
| `/delete-instance` | Deletes an instance. |
| `/reboot-instance` | Reboots an instance. |
| `/keep-instance` | Confirms that you still use an instance and resets its cleanup timer. |
| `/import-instance <instance_id>` | Adds an existing instance that you own to your account. |

The following commands require the Discord user ID in `ADMIN_USER_IDS`:

| Command | Function |
|---|---|
| `/admin-extend <user_id> <instance_id> <days>` | Delays the next usage check. |
| `/admin-exempt <user_id> <instance_id> <true\|false>` | Enables or disables automatic cleanup for an instance. |
| `/admin-list-all` | Lists all tracked instances and their cleanup status. |
| `/admin-import-instance <user_id> <instance_id>` | Adds an existing instance to another user account. |

## Automatic cleanup

The bot sends each owner a direct message every `NUDGE_INTERVAL_DAYS`. The default interval is 7 days.

Each message has **Keep it** and **Delete now** buttons. The bot deletes the instance after `NUDGE_GRACE_DAYS` without confirmation.

The default grace period is 2 days. The bot sends one reminder `NUDGE_REMINDER_DAYS` before the deadline.

If the bot was offline, it extends each pending grace period by the outage duration. This extension prevents deletion during an outage.

The following variables control automatic cleanup:

- `NUDGE_INTERVAL_DAYS`
- `NUDGE_GRACE_DAYS`
- `NUDGE_REMINDER_DAYS`
- `NUDGE_CHECK_HOURS`
- `MAX_LIFETIME_DAYS`

`MAX_LIFETIME_DAYS` sets an optional maximum instance age. A value of `0` disables this limit.
