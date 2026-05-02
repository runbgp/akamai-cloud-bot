import os
import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv
import asyncio
import random
import string
from typing import Dict, List, Optional, Any
import datetime

from akamai_api import AkamaiCloudAPI
from database import Database

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# Nudge configuration (overridable via env)
NUDGE_INTERVAL_DAYS = int(os.getenv("NUDGE_INTERVAL_DAYS", "7"))
NUDGE_GRACE_DAYS = int(os.getenv("NUDGE_GRACE_DAYS", "2"))
NUDGE_REMINDER_DAYS = int(os.getenv("NUDGE_REMINDER_DAYS", "1"))  # before grace ends
NUDGE_CHECK_HOURS = int(os.getenv("NUDGE_CHECK_HOURS", "1"))
# Hard ceiling on instance lifetime regardless of confirmations. 0 = disabled.
MAX_LIFETIME_DAYS = int(os.getenv("MAX_LIFETIME_DAYS", "0"))

ADMIN_USER_IDS = {
    s.strip() for s in os.getenv("ADMIN_USER_IDS", "").split(",") if s.strip()
}

# Initialize bot with intents
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Initialize Akamai Cloud API and Database
akamai_api = AkamaiCloudAPI()
db = Database()

# Cache for regions, images, and types
cache = {
    "regions": [],
    "images": [],
    "types": []
}

# Set on first nudge tick after startup so we extend grace by any downtime.
_downtime_extension_seconds: Optional[float] = None


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse_iso(value: Optional[str]) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        # `fromisoformat` handles both `+00:00` and naive ISO strings.
        dt = datetime.datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt
    except ValueError:
        return None


def is_admin(user_id: str) -> bool:
    return user_id in ADMIN_USER_IDS


@bot.event
async def on_ready():
    """Event triggered when the bot is ready."""
    print(f"{bot.user.name} is connected to Discord!")

    # Compute downtime so the first nudge tick can extend grace windows.
    global _downtime_extension_seconds
    meta = db.get_meta()
    last_seen = _parse_iso(meta.get("last_seen_at"))
    if last_seen is not None:
        gap = (_utcnow() - last_seen).total_seconds()
        # Ignore tiny gaps (normal restarts) — only extend for real outages.
        _downtime_extension_seconds = gap if gap > 300 else 0.0
        if _downtime_extension_seconds:
            print(
                f"Detected ~{int(_downtime_extension_seconds)}s of downtime; "
                "will extend pending nudge windows on next tick."
            )
    else:
        _downtime_extension_seconds = 0.0

    # Register persistent view so DM buttons survive restarts.
    bot.add_view(NudgeView())

    # Sync commands with Discord
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name="Akamai Cloud"
    ))

    await update_cache()

    if not auto_refresh_instances.is_running():
        auto_refresh_instances.start()
        print("Started automatic instance refresh task")
    if not check_nudges.is_running():
        check_nudges.start()
        print("Started nudge check task")
    if not heartbeat_meta.is_running():
        heartbeat_meta.start()


async def update_cache():
    """Update the cache with Akamai Cloud API data."""
    try:
        cache["regions"] = akamai_api.get_regions()
        cache["images"] = akamai_api.get_images()
        cache["types"] = akamai_api.get_instance_types()
        print("Cache updated successfully")
    except Exception as e:
        print(f"Failed to update cache: {e}")


@tasks.loop(minutes=1)
async def auto_refresh_instances():
    """Background task to automatically refresh all instances every minute."""
    try:
        all_instances = db.get_all_instances()
        if not all_instances:
            return

        print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Auto-refreshing {len(all_instances)} instances...")

        for user_id, instance_id in all_instances:
            try:
                updated_instance = akamai_api.get_instance(instance_id)
                db.update_instance(user_id, instance_id, updated_instance)
            except Exception as e:
                print(f"Failed to refresh instance {instance_id} for user {user_id}: {e}")

        print(f"[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Auto-refresh completed")
    except Exception as e:
        print(f"Error in auto_refresh_instances task: {e}")


@auto_refresh_instances.before_loop
async def before_auto_refresh():
    await bot.wait_until_ready()


@tasks.loop(minutes=5)
async def heartbeat_meta():
    """Persist last-seen time so we can detect downtime on next boot."""
    db.set_meta(last_seen_at=_utcnow().isoformat())


@heartbeat_meta.before_loop
async def before_heartbeat():
    await bot.wait_until_ready()


@tasks.loop(hours=NUDGE_CHECK_HOURS)
async def check_nudges():
    """Walk every stored instance and send/reminder/delete as needed."""
    global _downtime_extension_seconds
    extension = datetime.timedelta(seconds=_downtime_extension_seconds or 0.0)
    # Only extend windows once per startup.
    _downtime_extension_seconds = 0.0

    now = _utcnow()
    interval = datetime.timedelta(days=NUDGE_INTERVAL_DAYS)
    grace = datetime.timedelta(days=NUDGE_GRACE_DAYS)
    reminder_lead = datetime.timedelta(days=NUDGE_REMINDER_DAYS)
    max_lifetime = (
        datetime.timedelta(days=MAX_LIFETIME_DAYS) if MAX_LIFETIME_DAYS > 0 else None
    )

    for user_id, instance in list(db.iter_all_full()):
        try:
            await _process_one_nudge(
                user_id, instance, now, interval, grace, reminder_lead,
                max_lifetime, extension,
            )
        except Exception as e:
            print(f"Nudge check failed for {user_id}/{instance.get('id')}: {e}")


@check_nudges.before_loop
async def before_check_nudges():
    await bot.wait_until_ready()
    # Stagger so we don't collide with auto_refresh on startup.
    await asyncio.sleep(30)


async def _process_one_nudge(
    user_id: str,
    instance: Dict[str, Any],
    now: datetime.datetime,
    interval: datetime.timedelta,
    grace: datetime.timedelta,
    reminder_lead: datetime.timedelta,
    max_lifetime: Optional[datetime.timedelta],
    extension: datetime.timedelta,
):
    instance_id = instance.get("id")
    nudge = instance.get("_nudge") or {}
    if nudge.get("exempt"):
        return

    last_confirmed = _parse_iso(nudge.get("last_confirmed_at")) or now
    nudge_sent = _parse_iso(nudge.get("nudge_sent_at"))

    # Hard lifetime cap.
    if max_lifetime is not None:
        created_at = _parse_iso(instance.get("created"))
        if created_at and (now - created_at) > max_lifetime:
            await _force_delete(
                user_id, instance,
                reason=f"reached the maximum lifetime of {MAX_LIFETIME_DAYS} days",
            )
            return

    # No active nudge — should we start one?
    if nudge_sent is None:
        if (now - last_confirmed) >= interval:
            await _send_nudge(user_id, instance, kind="initial")
        return

    # Active nudge: extend by detected downtime once, then evaluate.
    effective_sent = nudge_sent + extension
    elapsed = now - effective_sent

    if elapsed >= grace:
        await _force_delete(
            user_id, instance,
            reason=f"no confirmation within {NUDGE_GRACE_DAYS} days",
        )
        return

    if (
        nudge.get("reminder_sent_at") is None
        and elapsed >= (grace - reminder_lead)
    ):
        await _send_nudge(user_id, instance, kind="reminder")


def _instance_summary_lines(instance: Dict[str, Any]) -> str:
    region_id = instance.get("region", "Unknown")
    region_info = next((r for r in cache["regions"] if r.get("id") == region_id), None)
    region_label = region_info.get("label", "Unknown") if region_info else "Unknown"
    country_code = region_info.get("country", "") if region_info else ""
    flag_emoji = get_country_flag(country_code)
    ipv4 = instance.get("ipv4", []) or []
    ip_text = ipv4[0] if ipv4 else "n/a"
    return (
        f"**Label:** `{instance.get('label', 'Unknown')}`\n"
        f"**ID:** `{instance.get('id')}`\n"
        f"**Region:** {flag_emoji} `{region_id}` ({region_label})\n"
        f"**Type:** `{instance.get('type', 'Unknown')}`\n"
        f"**IPv4:** `{ip_text}`"
    )


async def _send_nudge(user_id: str, instance: Dict[str, Any], kind: str):
    """DM the owner with Keep / Delete buttons."""
    instance_id = instance.get("id")
    try:
        user = await bot.fetch_user(int(user_id))
    except Exception as e:
        print(f"Could not fetch user {user_id}: {e}")
        return

    deadline = _utcnow() + datetime.timedelta(days=NUDGE_GRACE_DAYS)
    if kind == "reminder":
        title = "⚠️ Final reminder: confirm your Akamai Cloud instance"
        intro = (
            f"This is your **final reminder**. If you don't confirm by "
            f"<t:{int(deadline.timestamp())}:F>, this VM will be deleted automatically."
        )
        color = discord.Color.orange()
    else:
        title = "Are you still using your Akamai Cloud instance?"
        intro = (
            f"It's been at least {NUDGE_INTERVAL_DAYS} days since you last confirmed this VM. "
            f"Confirm by <t:{int(deadline.timestamp())}:F> or it will be deleted automatically."
        )
        color = discord.Color.gold()

    embed = discord.Embed(title=title, description=intro, color=color)
    embed.add_field(name="Instance", value=_instance_summary_lines(instance), inline=False)
    embed.set_footer(text="Click 'Keep it' to extend, or 'Delete now' to release the resources.")

    view = NudgeView()
    try:
        dm = await user.create_dm()
        await dm.send(embed=embed, view=view)
    except discord.Forbidden:
        print(f"DM forbidden for user {user_id}; marking nudge as undeliverable.")
        db.update_nudge(user_id, instance_id, last_dm_failed=True)
        return
    except Exception as e:
        print(f"Failed to DM user {user_id}: {e}")
        return

    fields = {"last_dm_failed": False}
    if kind == "initial":
        fields.update({
            "nudge_sent_at": _utcnow().isoformat(),
            "reminder_sent_at": None,
            "nudge_count": (instance.get("_nudge") or {}).get("nudge_count", 0) + 1,
        })
    else:
        fields["reminder_sent_at"] = _utcnow().isoformat()
    db.update_nudge(user_id, instance_id, **fields)


async def _force_delete(user_id: str, instance: Dict[str, Any], reason: str):
    instance_id = instance.get("id")
    label = instance.get("label", "Unknown")
    try:
        akamai_api.delete_instance(instance_id)
    except Exception as e:
        print(f"Failed to delete instance {instance_id} for user {user_id}: {e}")
        return

    db.remove_instance(user_id, instance_id)
    print(f"Deleted instance {instance_id} ({label}) for {user_id}: {reason}")

    try:
        user = await bot.fetch_user(int(user_id))
        dm = await user.create_dm()
        await dm.send(
            f"Your Akamai Cloud instance `{label}` (`{instance_id}`) has been deleted: {reason}.\n"
            f"You can create a new one anytime with `/create-instance`."
        )
    except Exception as e:
        print(f"Could not notify user {user_id} about deletion: {e}")


def generate_password(length=16):
    """Generate a secure random password."""
    chars = string.ascii_letters + string.digits + "!@#$%^&*()_-+=<>?"
    return ''.join(random.choice(chars) for _ in range(length))


def get_country_flag(country_code):
    """Convert a country code to a flag emoji."""
    if not country_code or len(country_code) != 2:
        return "🌐"

    country_code = country_code.upper()
    first_letter = ord(country_code[0]) - ord('A') + ord('🇦')
    second_letter = ord(country_code[1]) - ord('A') + ord('🇦')
    return chr(first_letter) + chr(second_letter)


class NudgeView(discord.ui.View):
    """Persistent view for the weekly Keep/Delete nudge.

    Buttons use stable custom_ids so they keep working after the bot restarts.
    The view itself carries no per-instance state — it looks up which instance
    the click belongs to via the DM recipient and the button id suffix.
    """

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Keep it",
        style=discord.ButtonStyle.success,
        custom_id="nudge:keep",
    )
    async def keep_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle(interaction, keep=True)

    @discord.ui.button(
        label="Delete now",
        style=discord.ButtonStyle.danger,
        custom_id="nudge:delete",
    )
    async def delete_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle(interaction, keep=False)

    async def _handle(self, interaction: discord.Interaction, keep: bool):
        user_id = str(interaction.user.id)
        instances = db.get_user_instances(user_id)
        # Find an instance with an active nudge — there's almost always at most one
        # at a time per user, but be defensive if multiple are pending.
        pending = [
            inst for inst in instances
            if (inst.get("_nudge") or {}).get("nudge_sent_at") is not None
        ]

        if not pending:
            await interaction.response.send_message(
                "No pending confirmation found for your account. You're all set.",
                ephemeral=True,
            )
            return

        # If multiple are pending, prefer the oldest nudge (closest to deletion).
        pending.sort(key=lambda i: (i.get("_nudge") or {}).get("nudge_sent_at") or "")
        target = pending[0]
        instance_id = target.get("id")
        label = target.get("label", "Unknown")

        if keep:
            db.update_nudge(
                user_id, instance_id,
                last_confirmed_at=_utcnow().isoformat(),
                nudge_sent_at=None,
                reminder_sent_at=None,
            )
            await interaction.response.send_message(
                f"Got it — keeping `{label}` (`{instance_id}`). "
                f"You'll be checked in with again in {NUDGE_INTERVAL_DAYS} days.",
                ephemeral=True,
            )
        else:
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                akamai_api.delete_instance(instance_id)
                db.remove_instance(user_id, instance_id)
                await interaction.followup.send(
                    f"Deleted `{label}` (`{instance_id}`).",
                    ephemeral=True,
                )
            except Exception as e:
                await interaction.followup.send(
                    f"Failed to delete `{label}` (`{instance_id}`): {e}",
                    ephemeral=True,
                )


class RegionSelect(discord.ui.Select):
    """Dropdown for selecting an Akamai Cloud region."""

    def __init__(self, regions):
        if not regions:
            super().__init__(placeholder="No regions available", options=[
                discord.SelectOption(label="No regions available", value="none")
            ])
            return

        priority_regions = [
            "us-iad", "us-ord", "us-sea", "us-lax", "us-mia",
            "gb-lon", "de-fra-2", "se-sto", "nl-ams", "fr-par"
        ]

        sorted_regions = []
        for region_id in priority_regions:
            region = next((r for r in regions if r.get("id") == region_id), None)
            if region:
                sorted_regions.append(region)

        for region in regions:
            if region.get("id") not in priority_regions:
                sorted_regions.append(region)

        options = []
        for region in sorted_regions[:25]:
            region_id = region.get("id", "unknown")
            region_label = region.get("label", "Unknown")
            country_code = region.get("country", "")
            flag_emoji = get_country_flag(country_code)
            options.append(
                discord.SelectOption(
                    label=f"{flag_emoji} {region_id}",
                    description=region_label,
                    value=region_id
                )
            )

        if not options:
            options = [discord.SelectOption(label="No regions available", value="none")]

        super().__init__(placeholder="Select a region", options=options)


class ImageSelect(discord.ui.Select):
    """Dropdown for selecting an Akamai Cloud instance image (OS)."""

    def __init__(self, images):
        if not images:
            super().__init__(placeholder="No images available", options=[
                discord.SelectOption(label="No images available", value="none")
            ])
            return

        public_images = [img for img in images if img.get("is_public", False) and not img.get("deprecated", True)]

        priority_vendors = ["Ubuntu", "Debian", "AlmaLinux", "Fedora", "Alpine"]

        ubuntu_24_04_id = "linode/ubuntu24.04"
        ubuntu_24_04_image = next((img for img in public_images if img.get("id") == ubuntu_24_04_id), None)

        final_images = []
        if ubuntu_24_04_image:
            final_images.append(ubuntu_24_04_image)

        for vendor in priority_vendors:
            vendor_images = [img for img in public_images
                            if img.get("vendor") == vendor
                            and img.get("id") != ubuntu_24_04_id]
            vendor_images.sort(key=lambda x: x.get("label", ""), reverse=True)
            final_images.extend(vendor_images[:3])
            if len(final_images) >= 20:
                break

        options = [
            discord.SelectOption(
                label=f"{img.get('vendor', 'Unknown')} - {img.get('label', 'Unknown')}"[:100],
                value=img.get("id", "unknown"),
                description=(img.get("description", "") or "")[:100],
                default=(img.get("id") == ubuntu_24_04_id)
            )
            for img in final_images[:25] if img
        ]

        if not options:
            options = [discord.SelectOption(label="No valid images found", value="none")]

        super().__init__(placeholder="Select an operating system", options=options)


class TypeSelect(discord.ui.Select):
    """Dropdown for selecting an Akamai Cloud instance type."""

    def __init__(self, types):
        if not types:
            super().__init__(placeholder="No instance types available", options=[
                discord.SelectOption(label="No instance types available", value="none")
            ])
            return

        priority_types = [
            "g6-nanode-1",
            "g6-standard-1",
            "g6-standard-2",
            "g6-standard-4"
        ]

        sorted_types = []
        for type_id in priority_types:
            instance_type = next((t for t in types if t.get("id") == type_id), None)
            if instance_type:
                sorted_types.append(instance_type)

        options = [
            discord.SelectOption(
                label=f"{t.get('label', 'Unknown')} ({t.get('id', 'unknown')})",
                value=t.get("id", "unknown"),
                description=f"${t.get('price', {}).get('monthly', 0)}/mo - {t.get('memory', 0)/1024}GB RAM, {t.get('vcpus', 0)} vCPUs"[:100],
                default=(t.get("id") == "g6-nanode-1")
            )
            for t in sorted_types if t
        ]

        if not options:
            options = [discord.SelectOption(label="No instance types available", value="none")]

        super().__init__(placeholder="Select an instance type", options=options)


class InstanceCreationView(discord.ui.View):
    """View for creating an Akamai Cloud instance."""

    def __init__(self, user_id: str):
        super().__init__(timeout=300)
        self.user_id = user_id
        self.region = None
        self.image = "linode/ubuntu24.04"
        self.type = "g6-nanode-1"

        self.region_select = RegionSelect(cache["regions"])
        self.image_select = ImageSelect(cache["images"])
        self.type_select = TypeSelect(cache["types"])

        self.add_item(self.region_select)
        self.add_item(self.image_select)
        self.add_item(self.type_select)

        self.region_select.callback = self.region_callback
        self.image_select.callback = self.image_callback
        self.type_select.callback = self.type_callback

    async def region_callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This is not your menu!", ephemeral=True)
            return
        self.region = self.region_select.values[0]
        await interaction.response.defer()

    async def image_callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This is not your menu!", ephemeral=True)
            return
        self.image = self.image_select.values[0]
        await interaction.response.defer()

    async def type_callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This is not your menu!", ephemeral=True)
            return
        self.type = self.type_select.values[0]
        await interaction.response.defer()

    @discord.ui.button(label="Create Instance", style=discord.ButtonStyle.green)
    async def create_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This is not your menu!", ephemeral=True)
            return

        if not self.region:
            await interaction.response.send_message("Please select a region first!", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        label = f"akamai-{interaction.user.name}-{random.randint(1000, 9999)}"
        root_pass = generate_password()

        try:
            instance = akamai_api.create_instance(
                label=label,
                region=self.region,
                image=self.image,
                root_pass=root_pass,
                type=self.type
            )

            await asyncio.sleep(5)
            updated_instance = akamai_api.get_instance(instance['id'])
            db.add_instance(str(interaction.user.id), updated_instance)

            try:
                dm_channel = await interaction.user.create_dm()

                ipv4_addresses = updated_instance.get('ipv4', [])
                ipv6_address = updated_instance.get('ipv6', '')

                if ipv4_addresses:
                    ipv4_text = "\n".join([f"- `{ip}`" for ip in ipv4_addresses])
                else:
                    ipv4_text = "None assigned yet"

                ipv6_text = f"`{ipv6_address}`" if ipv6_address else "None assigned yet"

                region_id = instance.get('region', 'Unknown')
                region_info = next((r for r in cache["regions"] if r.get("id") == region_id), None)
                region_label = region_info.get("label", "Unknown") if region_info else "Unknown"
                country_code = region_info.get("country", "") if region_info else ""
                flag_emoji = get_country_flag(country_code)

                await dm_channel.send(
                    f"**Your Akamai Cloud instance has been created!**\n\n"
                    f"**Instance ID:** `{instance['id']}`\n"
                    f"**Label:** `{instance['label']}`\n"
                    f"**Region:** {flag_emoji} `{region_id}` ({region_label})\n"
                    f"**Image:** `{instance['image']}`\n"
                    f"**Type:** `{instance['type']}`\n"
                    f"**IPv4 Address:** {ipv4_text}\n"
                    f"**IPv6 Address:** {ipv6_text}\n"
                    f"**Root Password:** `{root_pass}`\n\n"
                    f"**IMPORTANT:** Please save this information, especially the root password. "
                    f"For security reasons, this is the only time you'll receive the password.\n\n"
                    f"**Heads up:** every {NUDGE_INTERVAL_DAYS} days I'll DM you to confirm "
                    f"you're still using this VM. If you don't reply within {NUDGE_GRACE_DAYS} days, "
                    f"it'll be deleted automatically. Use `/keep-instance` if you ever miss the DM."
                )

                embed = discord.Embed(
                    title="Akamai Cloud Instance Created",
                    description=f"Your Akamai Cloud instance has been created successfully! Check your DMs for details.",
                    color=discord.Color.green()
                )
                embed.add_field(name="Instance ID", value=f"`{instance['id']}`")
                embed.add_field(name="Label", value=f"`{instance['label']}`")
                embed.add_field(name="Region", value=f"{flag_emoji} `{region_id}` ({region_label})")

                if ipv4_addresses:
                    embed.add_field(name="IPv4", value=f"`{ipv4_addresses[0]}`", inline=False)

                await interaction.followup.send(embed=embed)

            except discord.Forbidden:
                await interaction.followup.send(
                    "Your Akamai Cloud instance has been created, but I couldn't send you a DM with the details. "
                    "Please enable DMs from server members to receive your root password."
                )

        except Exception as e:
            await interaction.followup.send(f"Failed to create Akamai Cloud instance: {str(e)}")

        for child in self.children:
            child.disabled = True

        await interaction.edit_original_response(view=self)


class InstanceDeleteView(discord.ui.View):
    """View for confirming Akamai Cloud instance deletion."""

    def __init__(self, user_id: str, instance_id: int):
        super().__init__(timeout=60)
        self.user_id = user_id
        self.instance_id = instance_id

    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This is not your menu!", ephemeral=True)
            return

        await interaction.response.defer(thinking=True)

        try:
            success = akamai_api.delete_instance(self.instance_id)
            if success:
                db.remove_instance(self.user_id, self.instance_id)
                await interaction.followup.send(f"Akamai Cloud instance {self.instance_id} has been deleted successfully.")
            else:
                await interaction.followup.send(f"Failed to delete Akamai Cloud instance {self.instance_id}.")

        except Exception as e:
            await interaction.followup.send(f"Error deleting Akamai Cloud instance: {str(e)}")

        for child in self.children:
            child.disabled = True

        await interaction.edit_original_response(view=self)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This is not your menu!", ephemeral=True)
            return

        await interaction.response.send_message("Deletion cancelled.", ephemeral=True)

        for child in self.children:
            child.disabled = True

        await interaction.edit_original_response(view=self)


def _nudge_status_text(instance: Dict[str, Any]) -> str:
    """Human-readable line for /list-instances showing where the VM stands."""
    nudge = instance.get("_nudge") or {}
    if nudge.get("exempt"):
        return "✅ Exempt from auto-cleanup."

    last_confirmed = _parse_iso(nudge.get("last_confirmed_at"))
    nudge_sent = _parse_iso(nudge.get("nudge_sent_at"))

    if nudge_sent is not None:
        deadline = nudge_sent + datetime.timedelta(days=NUDGE_GRACE_DAYS)
        return (
            f"⚠️ Confirm by <t:{int(deadline.timestamp())}:F> or this VM will be deleted. "
            f"Use `/keep-instance {instance.get('id')}`."
        )
    if last_confirmed is not None:
        next_check = last_confirmed + datetime.timedelta(days=NUDGE_INTERVAL_DAYS)
        return f"Next usage check: <t:{int(next_check.timestamp())}:R>."
    return "Next usage check pending."


@bot.tree.command(name="create-instance", description="Create a new Akamai Cloud instance")
async def create_instance(interaction: discord.Interaction):
    """Command to create a new Akamai Cloud instance."""
    await interaction.response.defer(thinking=True)

    if not cache["regions"] or not cache["images"] or not cache["types"]:
        await update_cache()

    view = InstanceCreationView(str(interaction.user.id))

    embed = discord.Embed(
        title="Create an Akamai Cloud Instance",
        description="Please configure your Akamai Cloud instance by selecting options from the dropdowns below.",
        color=discord.Color.blue()
    )

    embed.add_field(
        name="Default Selections",
        value="• **Image:** Ubuntu 24.04 LTS (pre-selected)\n• **Type:** Nanode 1GB (pre-selected)\n• **Region:** Please select a region",
        inline=False
    )

    embed.add_field(
        name="Instructions",
        value="1. Select a region (required)\n2. You can change the image and instance type if needed\n3. Click 'Create Instance' when ready",
        inline=False
    )

    embed.add_field(
        name="Auto-cleanup",
        value=(
            f"I'll DM you every {NUDGE_INTERVAL_DAYS} days to confirm you're still using this VM. "
            f"If you don't reply within {NUDGE_GRACE_DAYS} days, it'll be deleted automatically."
        ),
        inline=False,
    )

    await interaction.followup.send(embed=embed, view=view)


@bot.tree.command(name="list-instances", description="List your Akamai Cloud instances (auto-refreshed every minute)")
async def list_instances(interaction: discord.Interaction):
    """Command to list a user's Akamai Cloud instances."""
    await interaction.response.defer(thinking=True)

    user_id = str(interaction.user.id)
    instances = db.get_user_instances(user_id)

    if not instances:
        await interaction.followup.send("You don't have any Akamai Cloud instances.")
        return

    embeds = []
    for instance in instances:
        embed = discord.Embed(
            title=f"Akamai Cloud: {instance.get('label', 'Unknown')}",
            description=f"ID: `{instance.get('id', 'Unknown')}`",
            color=discord.Color.blue()
        )

        region_id = instance.get('region', 'Unknown')
        region_info = next((r for r in cache["regions"] if r.get("id") == region_id), None)
        region_label = region_info.get("label", "Unknown") if region_info else "Unknown"
        country_code = region_info.get("country", "") if region_info else ""
        flag_emoji = get_country_flag(country_code)

        embed.add_field(name="Status", value=f"`{instance.get('status', 'Unknown')}`")
        embed.add_field(name="Region", value=f"{flag_emoji} `{region_id}` ({region_label})")
        embed.add_field(name="Type", value=f"`{instance.get('type', 'Unknown')}`")

        ipv4 = instance.get('ipv4', [])
        if ipv4:
            formatted_ips = "\n".join([f"`{ip}`" for ip in ipv4])
            embed.add_field(name="IPv4", value=formatted_ips, inline=False)

        embed.add_field(name="Usage check", value=_nudge_status_text(instance), inline=False)

        embed.set_footer(text="Instances are automatically refreshed every minute")
        embeds.append(embed)

    await interaction.followup.send(embeds=embeds[:10])


@bot.tree.command(name="delete-instance", description="Delete an Akamai Cloud instance")
@app_commands.describe(instance_id="The ID of the Akamai Cloud instance to delete")
async def delete_instance(interaction: discord.Interaction, instance_id: int):
    """Command to delete an Akamai Cloud instance."""
    await interaction.response.defer(thinking=True)

    user_id = str(interaction.user.id)
    instance = db.get_instance(user_id, instance_id)

    if not instance:
        await interaction.followup.send(f"You don't have an Akamai Cloud instance with ID {instance_id}.")
        return

    view = InstanceDeleteView(user_id, instance_id)

    embed = discord.Embed(
        title="Confirm Deletion",
        description=f"Are you sure you want to delete Akamai Cloud instance {instance_id} ({instance.get('label', 'Unknown')})?\n\n"
                    f"**This action cannot be undone!**",
        color=discord.Color.red()
    )

    await interaction.followup.send(embed=embed, view=view)


@bot.tree.command(name="reboot-instance", description="Reboot an Akamai Cloud instance")
@app_commands.describe(instance_id="The ID of the Akamai Cloud instance to reboot")
async def reboot_instance(interaction: discord.Interaction, instance_id: int):
    """Command to reboot an Akamai Cloud instance."""
    await interaction.response.defer(thinking=True)

    user_id = str(interaction.user.id)
    instance = db.get_instance(user_id, instance_id)

    if not instance:
        await interaction.followup.send(f"You don't have an Akamai Cloud instance with ID {instance_id}.")
        return

    try:
        akamai_api.reboot_instance(instance_id)
        await interaction.followup.send(f"Akamai Cloud instance {instance_id} is being rebooted. This may take a few minutes.")

    except Exception as e:
        await interaction.followup.send(f"Failed to reboot Akamai Cloud instance: {str(e)}")


def _find_owner_of_instance(instance_id: int) -> Optional[str]:
    """Return the Discord user_id that owns a tracked instance, or None."""
    for user_id, instance in db.iter_all_full():
        if instance.get("id") == instance_id:
            return user_id
    return None


async def _import_instance_for_user(user_id: str, instance_id: int) -> Dict[str, Any]:
    """Fetch the live Linode instance and add it to the DB under user_id.

    Raises ValueError if it's already tracked (by anyone) or doesn't exist.
    """
    existing_owner = _find_owner_of_instance(instance_id)
    if existing_owner is not None:
        raise ValueError(
            f"Instance {instance_id} is already tracked under user {existing_owner}."
        )

    # Will raise on 404/etc. — caller surfaces the message.
    live = akamai_api.get_instance(instance_id)
    db.add_instance(user_id, live)
    return live


def _import_success_embed(user_id: str, instance: Dict[str, Any]) -> discord.Embed:
    region_id = instance.get("region", "Unknown")
    region_info = next((r for r in cache["regions"] if r.get("id") == region_id), None)
    region_label = region_info.get("label", "Unknown") if region_info else "Unknown"
    country_code = region_info.get("country", "") if region_info else ""
    flag_emoji = get_country_flag(country_code)
    next_check = _utcnow() + datetime.timedelta(days=NUDGE_INTERVAL_DAYS)

    embed = discord.Embed(
        title="Akamai Cloud Instance Imported",
        description=(
            f"Now tracking `{instance.get('label', 'Unknown')}` "
            f"(`{instance.get('id')}`) for <@{user_id}>."
        ),
        color=discord.Color.green(),
    )
    embed.add_field(name="Status", value=f"`{instance.get('status', 'Unknown')}`")
    embed.add_field(name="Region", value=f"{flag_emoji} `{region_id}` ({region_label})")
    embed.add_field(name="Type", value=f"`{instance.get('type', 'Unknown')}`")

    ipv4 = instance.get("ipv4", []) or []
    if ipv4:
        embed.add_field(name="IPv4", value="\n".join(f"`{ip}`" for ip in ipv4), inline=False)

    embed.add_field(
        name="Auto-cleanup",
        value=(
            f"Next usage check: <t:{int(next_check.timestamp())}:R>. "
            f"Use `/keep-instance {instance.get('id')}` to confirm anytime."
        ),
        inline=False,
    )
    return embed


@bot.tree.command(
    name="import-instance",
    description="Track an existing Akamai Cloud VM you own (e.g. created manually)",
)
@app_commands.describe(
    instance_id="The ID of the Akamai Cloud instance to track under your account",
)
async def import_instance(interaction: discord.Interaction, instance_id: int):
    await interaction.response.defer(thinking=True, ephemeral=True)
    user_id = str(interaction.user.id)

    try:
        instance = await _import_instance_for_user(user_id, instance_id)
    except ValueError as e:
        await interaction.followup.send(str(e), ephemeral=True)
        return
    except Exception as e:
        await interaction.followup.send(
            f"Couldn't import instance {instance_id}: {e}", ephemeral=True
        )
        return

    await interaction.followup.send(
        embed=_import_success_embed(user_id, instance), ephemeral=True
    )


@bot.tree.command(
    name="keep-instance",
    description="Confirm you're still using a VM (resets the auto-cleanup clock)",
)
@app_commands.describe(instance_id="The ID of the Akamai Cloud instance to confirm")
async def keep_instance(interaction: discord.Interaction, instance_id: int):
    user_id = str(interaction.user.id)
    instance = db.get_instance(user_id, instance_id)
    if not instance:
        await interaction.response.send_message(
            f"You don't have an Akamai Cloud instance with ID {instance_id}.",
            ephemeral=True,
        )
        return

    db.update_nudge(
        user_id, instance_id,
        last_confirmed_at=_utcnow().isoformat(),
        nudge_sent_at=None,
        reminder_sent_at=None,
        last_dm_failed=False,
    )
    next_check = _utcnow() + datetime.timedelta(days=NUDGE_INTERVAL_DAYS)
    await interaction.response.send_message(
        f"Confirmed `{instance.get('label', 'Unknown')}` (`{instance_id}`). "
        f"I'll check in again <t:{int(next_check.timestamp())}:R>.",
        ephemeral=True,
    )


# ----- admin commands -----


@bot.tree.command(
    name="admin-extend",
    description="(admin) Push an instance's next usage check out by N days",
)
@app_commands.describe(
    user_id="Discord user ID owning the instance",
    instance_id="Akamai Cloud instance ID",
    days="Number of days to extend (default 7)",
)
async def admin_extend(
    interaction: discord.Interaction,
    user_id: str,
    instance_id: int,
    days: int = 7,
):
    if not is_admin(str(interaction.user.id)):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return

    instance = db.get_instance(user_id, instance_id)
    if not instance:
        await interaction.response.send_message(
            f"No instance {instance_id} for user {user_id}.", ephemeral=True
        )
        return

    new_confirmed = _utcnow() + datetime.timedelta(days=days - NUDGE_INTERVAL_DAYS)
    db.update_nudge(
        user_id, instance_id,
        last_confirmed_at=new_confirmed.isoformat(),
        nudge_sent_at=None,
        reminder_sent_at=None,
    )
    next_check = _utcnow() + datetime.timedelta(days=days)
    await interaction.response.send_message(
        f"Extended `{instance.get('label')}` (`{instance_id}`). "
        f"Next check: <t:{int(next_check.timestamp())}:R>.",
        ephemeral=True,
    )


@bot.tree.command(
    name="admin-exempt",
    description="(admin) Toggle a VM's exemption from auto-cleanup",
)
@app_commands.describe(
    user_id="Discord user ID owning the instance",
    instance_id="Akamai Cloud instance ID",
    exempt="True to exempt, False to re-enable nudges",
)
async def admin_exempt(
    interaction: discord.Interaction,
    user_id: str,
    instance_id: int,
    exempt: bool,
):
    if not is_admin(str(interaction.user.id)):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return

    instance = db.get_instance(user_id, instance_id)
    if not instance:
        await interaction.response.send_message(
            f"No instance {instance_id} for user {user_id}.", ephemeral=True
        )
        return

    db.update_nudge(user_id, instance_id, exempt=exempt)
    state = "exempt" if exempt else "not exempt"
    await interaction.response.send_message(
        f"`{instance.get('label')}` (`{instance_id}`) is now **{state}** from auto-cleanup.",
        ephemeral=True,
    )


@bot.tree.command(
    name="admin-list-all",
    description="(admin) List every tracked instance and its nudge status",
)
async def admin_list_all(interaction: discord.Interaction):
    if not is_admin(str(interaction.user.id)):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return

    lines = []
    for user_id, instance in db.iter_all_full():
        nudge = instance.get("_nudge") or {}
        flags = []
        if nudge.get("exempt"):
            flags.append("exempt")
        if nudge.get("last_dm_failed"):
            flags.append("dm-failed")
        if nudge.get("nudge_sent_at"):
            flags.append("nudge-pending")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        lines.append(
            f"- `{instance.get('id')}` `{instance.get('label')}` "
            f"owner=`{user_id}`{flag_str}"
        )

    if not lines:
        await interaction.response.send_message("No tracked instances.", ephemeral=True)
        return

    body = "\n".join(lines)
    if len(body) > 1900:
        body = body[:1900] + "\n…(truncated)"
    await interaction.response.send_message(body, ephemeral=True)


@bot.tree.command(
    name="admin-import-instance",
    description="(admin) Track an existing Akamai Cloud VM under another user's account",
)
@app_commands.describe(
    user_id="Discord user ID to associate the instance with",
    instance_id="Akamai Cloud instance ID",
)
async def admin_import_instance(
    interaction: discord.Interaction,
    user_id: str,
    instance_id: int,
):
    if not is_admin(str(interaction.user.id)):
        await interaction.response.send_message("Not authorized.", ephemeral=True)
        return

    await interaction.response.defer(thinking=True, ephemeral=True)
    try:
        instance = await _import_instance_for_user(user_id, instance_id)
    except ValueError as e:
        await interaction.followup.send(str(e), ephemeral=True)
        return
    except Exception as e:
        await interaction.followup.send(
            f"Couldn't import instance {instance_id}: {e}", ephemeral=True
        )
        return

    await interaction.followup.send(
        embed=_import_success_embed(user_id, instance), ephemeral=True
    )


if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("Error: DISCORD_TOKEN not found in environment variables")
        exit(1)

    print("Starting Akamai Cloud Bot...")

    try:
        bot.run(DISCORD_TOKEN)
    except KeyboardInterrupt:
        print("Shutting down Akamai Cloud Bot...")
        if auto_refresh_instances.is_running():
            auto_refresh_instances.cancel()
            print("Stopped automatic instance refresh task")
        if check_nudges.is_running():
            check_nudges.cancel()
        if heartbeat_meta.is_running():
            heartbeat_meta.cancel()
    finally:
        print("Bot has been shut down")
