# Akamai Cloud Bot

A Discord bot that allows users to create, check the status of, and delete Akamai Cloud instances using the Akamai Cloud API.

## Features

- Create Akamai Cloud instances with custom configurations
- Check the status of your Akamai Cloud instances
- Delete your Akamai Cloud instances
- User-specific instance tracking
- Automatic instance status refresh every minute

## Setup

1. Clone this repository
2. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Create a `.env` file with the following variables:
   ```
   DISCORD_TOKEN=your_discord_bot_token
   AKAMAI_API_TOKEN=your_akamai_cloud_api_token
   ```
4. Run the bot:
   ```
   python3 akamai_cloud_bot.py
   ```

## Commands

- `/create-instance` - Create a new Akamai Cloud instance
- `/list-instances` - List your Akamai Cloud instances (auto-refreshed every minute)
- `/delete-instance` - Delete an Akamai Cloud instance
- `/reboot-instance` - Reboot an Akamai Cloud instance
