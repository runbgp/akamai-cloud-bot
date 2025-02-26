import os
import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import asyncio
import random
import string
from typing import Dict, List, Optional, Any

from akamai_api import AkamaiCloudAPI
from database import Database

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

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

@bot.event
async def on_ready():
    """Event triggered when the bot is ready."""
    print(f"{bot.user.name} is connected to Discord!")
    
    # Sync commands with Discord
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    
    # Set bot activity
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching, 
        name="Akamai Cloud"
    ))
    
    # Cache Akamai Cloud data
    await update_cache()

async def update_cache():
    """Update the cache with Akamai Cloud data."""
    try:
        cache["regions"] = akamai_api.get_regions()
        cache["images"] = akamai_api.get_images()
        cache["types"] = akamai_api.get_instance_types()
        print("Cache updated successfully")
    except Exception as e:
        print(f"Failed to update cache: {e}")

def generate_password(length=16):
    """Generate a secure random password."""
    chars = string.ascii_letters + string.digits + "!@#$%^&*()_-+=<>?"
    return ''.join(random.choice(chars) for _ in range(length))

class RegionSelect(discord.ui.Select):
    """Dropdown for selecting an Akamai Cloud region."""
    
    def __init__(self, regions):
        if not regions:
            super().__init__(placeholder="No regions available", options=[
                discord.SelectOption(label="No regions available", value="none")
            ])
            return
            
        # Define priority regions (common US, EU, and Asia regions)
        priority_regions = [
            "us-east", "us-central", "us-west", "us-southeast", "us-iad",
            "eu-west", "eu-central", "ap-northeast", "ap-southeast", "ap-south"
        ]
        
        # Sort regions by priority
        sorted_regions = []
        
        # First add priority regions in the specified order
        for region_id in priority_regions:
            region = next((r for r in regions if r.get("id") == region_id), None)
            if region:
                sorted_regions.append(region)
        
        # Then add remaining regions
        for region in regions:
            if region.get("id") not in priority_regions:
                sorted_regions.append(region)
        
        # Create options from our sorted list
        options = [
            discord.SelectOption(
                label=region.get("id", "unknown"),
                description=region.get("country", "")
            )
            for region in sorted_regions[:25]  # Discord limits to 25 options
        ]
        
        # If no valid options were created, provide a default
        if not options:
            options = [discord.SelectOption(label="No regions available", value="none")]
            
        super().__init__(placeholder="Select a region", options=options)

class ImageSelect(discord.ui.Select):
    """Dropdown for selecting an Akamai Cloud image (OS)."""
    
    def __init__(self, images):
        # Check if images is None or empty
        if not images:
            super().__init__(placeholder="No images available", options=[
                discord.SelectOption(label="No images available", value="none")
            ])
            return
            
        # Filter to only public images and non-deprecated images
        public_images = [img for img in images if img.get("is_public", False) and not img.get("deprecated", True)]
        
        # Define priority distributions to show first
        priority_vendors = ["Ubuntu", "Debian", "CentOS", "AlmaLinux", "Fedora"]
        
        # Find Ubuntu 24.04 LTS image
        ubuntu_24_04_id = "linode/ubuntu24.04"
        ubuntu_24_04_image = next((img for img in public_images if img.get("id") == ubuntu_24_04_id), None)
        
        # Create a list to hold our final options
        final_images = []
        
        # Add Ubuntu 24.04 first if it exists
        if ubuntu_24_04_image:
            final_images.append(ubuntu_24_04_image)
            
        # Add other priority distributions
        for vendor in priority_vendors:
            vendor_images = [img for img in public_images 
                            if img.get("vendor") == vendor 
                            and img.get("id") != ubuntu_24_04_id]  # Skip Ubuntu 24.04 as it's already added
            
            # Sort by newest first (assuming newer versions have higher numbers)
            vendor_images.sort(key=lambda x: x.get("label", ""), reverse=True)
            
            # Add up to 3 images per vendor
            final_images.extend(vendor_images[:3])
            
            # Stop if we're approaching the limit
            if len(final_images) >= 20:
                break
                
        # Create options from our filtered list
        options = [
            discord.SelectOption(
                label=f"{img.get('vendor', 'Unknown')} - {img.get('label', 'Unknown')}"[:100],
                value=img.get("id", "unknown"),
                description=(img.get("description", "") or "")[:100],
                default=(img.get("id") == ubuntu_24_04_id)
            )
            for img in final_images[:25] if img  # Discord limits to 25 options
        ]
        
        # If no valid options were created, provide a default
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
            
        # Define priority instance types (common sizes from smallest to largest)
        priority_types = [
            "g6-nanode-1",    # Smallest/cheapest
            "g6-standard-1",  # Small standard
            "g6-standard-2",  # Medium standard
            "g6-standard-4",  # Larger standard
            "g6-standard-8",  # Largest standard we'll show
            "g6-dedicated-8"  # One dedicated option
        ]
        
        # Sort types by priority
        sorted_types = []
        
        # First add priority types in the specified order
        for type_id in priority_types:
            instance_type = next((t for t in types if t.get("id") == type_id), None)
            if instance_type:
                sorted_types.append(instance_type)
        
        # Create options from our sorted list
        options = [
            discord.SelectOption(
                label=f"{t.get('label', 'Unknown')} ({t.get('id', 'unknown')})",
                value=t.get("id", "unknown"),
                description=f"${t.get('price', {}).get('monthly', 0)}/mo - {t.get('memory', 0)/1024}GB RAM, {t.get('vcpus', 0)} vCPUs"[:100],
                default=(t.get("id") == "g6-nanode-1")  # Set g6-nanode-1 as default
            )
            for t in sorted_types if t  # Only use our priority list
        ]
        
        # If no valid options were created, provide a default
        if not options:
            options = [discord.SelectOption(label="No instance types available", value="none")]
            
        super().__init__(placeholder="Select an instance type", options=options)

class InstanceCreationView(discord.ui.View):
    """View for creating an Akamai Cloud instance."""
    
    def __init__(self, user_id: str):
        super().__init__(timeout=300)  # 5 minute timeout
        self.user_id = user_id
        self.region = None
        self.image = "linode/ubuntu24.04"  # Default to Ubuntu 24.04 LTS
        self.type = "g6-nanode-1"  # Default to the smallest instance type
        
        # Add selects
        self.region_select = RegionSelect(cache["regions"])
        self.image_select = ImageSelect(cache["images"])
        self.type_select = TypeSelect(cache["types"])
        
        self.add_item(self.region_select)
        self.add_item(self.image_select)
        self.add_item(self.type_select)
        
        # Set callbacks
        self.region_select.callback = self.region_callback
        self.image_select.callback = self.image_callback
        self.type_select.callback = self.type_callback
    
    async def region_callback(self, interaction: discord.Interaction):
        """Callback for region selection."""
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This is not your menu!", ephemeral=True)
            return
        
        self.region = self.region_select.values[0]
        await interaction.response.defer()
    
    async def image_callback(self, interaction: discord.Interaction):
        """Callback for image selection."""
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This is not your menu!", ephemeral=True)
            return
        
        self.image = self.image_select.values[0]
        await interaction.response.defer()
    
    async def type_callback(self, interaction: discord.Interaction):
        """Callback for type selection."""
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This is not your menu!", ephemeral=True)
            return
        
        self.type = self.type_select.values[0]
        await interaction.response.defer()
    
    @discord.ui.button(label="Create Instance", style=discord.ButtonStyle.green)
    async def create_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Button to create the Akamai Cloud instance."""
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This is not your menu!", ephemeral=True)
            return
        
        if not self.region:
            await interaction.response.send_message("Please select a region first!", ephemeral=True)
            return
        
        await interaction.response.defer(thinking=True)
        
        # Generate a unique label for the instance
        label = f"akamai-{interaction.user.name}-{random.randint(1000, 9999)}"
        
        # Generate a secure root password
        root_pass = generate_password()
        
        try:
            # Create the instance
            instance = akamai_api.create_instance(
                label=label,
                region=self.region,
                image=self.image,
                root_pass=root_pass,
                type=self.type
            )
            
            # Wait a moment for IP addresses to be assigned
            await asyncio.sleep(5)
            
            # Get the updated instance details with IP addresses
            updated_instance = akamai_api.get_instance(instance['id'])
            
            # Store the instance in the database
            db.add_instance(str(interaction.user.id), updated_instance)
            
            # Send the instance details and root password via DM
            try:
                dm_channel = await interaction.user.create_dm()
                
                # Format IP addresses
                ipv4_addresses = updated_instance.get('ipv4', [])
                ipv6_address = updated_instance.get('ipv6', '')
                
                # Format the IP addresses with backticks
                if ipv4_addresses:
                    ipv4_text = "\n".join([f"- `{ip}`" for ip in ipv4_addresses])
                else:
                    ipv4_text = "None assigned yet"
                
                ipv6_text = f"`{ipv6_address}`" if ipv6_address else "None assigned yet"
                
                await dm_channel.send(
                    f"**Your Akamai Cloud instance has been created!**\n\n"
                    f"**Instance ID:** `{instance['id']}`\n"
                    f"**Label:** `{instance['label']}`\n"
                    f"**Region:** `{instance['region']}`\n"
                    f"**Image:** `{instance['image']}`\n"
                    f"**Type:** `{instance['type']}`\n"
                    f"**IPv4 Addresses:**\n{ipv4_text}\n"
                    f"**IPv6 Address:** {ipv6_text}\n"
                    f"**Root Password:** `{root_pass}`\n\n"
                    f"**IMPORTANT:** Please save this information, especially the root password. "
                    f"For security reasons, this is the only time you'll receive the password."
                )
                
                # Send a confirmation message in the channel
                embed = discord.Embed(
                    title="Akamai Cloud Instance Created",
                    description=f"Your Akamai Cloud instance has been created successfully! Check your DMs for details.",
                    color=discord.Color.green()
                )
                embed.add_field(name="Instance ID", value=f"`{instance['id']}`")
                embed.add_field(name="Label", value=f"`{instance['label']}`")
                embed.add_field(name="Region", value=f"`{instance['region']}`")
                
                # Add IP information to the public embed as well
                if ipv4_addresses:
                    embed.add_field(name="IPv4", value=f"`{ipv4_addresses[0]}`", inline=False)
                
                await interaction.followup.send(embed=embed)
                
            except discord.Forbidden:
                # If DM fails, send a message in the channel
                await interaction.followup.send(
                    "Your Akamai Cloud instance has been created, but I couldn't send you a DM with the details. "
                    "Please enable DMs from server members to receive your root password."
                )
        
        except Exception as e:
            await interaction.followup.send(f"Failed to create Akamai Cloud instance: {str(e)}")
        
        # Disable all components
        for child in self.children:
            child.disabled = True
        
        await interaction.edit_original_response(view=self)

class InstanceDeleteView(discord.ui.View):
    """View for confirming Akamai Cloud instance deletion."""
    
    def __init__(self, user_id: str, instance_id: int):
        super().__init__(timeout=60)  # 1 minute timeout
        self.user_id = user_id
        self.instance_id = instance_id
    
    @discord.ui.button(label="Confirm Delete", style=discord.ButtonStyle.danger)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Button to confirm deletion."""
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This is not your menu!", ephemeral=True)
            return
        
        await interaction.response.defer(thinking=True)
        
        try:
            # Delete the instance
            success = akamai_api.delete_instance(self.instance_id)
            
            if success:
                # Remove from database
                db.remove_instance(self.user_id, self.instance_id)
                
                await interaction.followup.send(f"Akamai Cloud instance {self.instance_id} has been deleted successfully.")
            else:
                await interaction.followup.send(f"Failed to delete Akamai Cloud instance {self.instance_id}.")
        
        except Exception as e:
            await interaction.followup.send(f"Error deleting Akamai Cloud instance: {str(e)}")
        
        # Disable all components
        for child in self.children:
            child.disabled = True
        
        await interaction.edit_original_response(view=self)
    
    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """Button to cancel deletion."""
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This is not your menu!", ephemeral=True)
            return
        
        await interaction.response.send_message("Deletion cancelled.", ephemeral=True)
        
        # Disable all components
        for child in self.children:
            child.disabled = True
        
        await interaction.edit_original_response(view=self)

@bot.tree.command(name="create-instance", description="Create a new Akamai Cloud instance")
async def create_instance(interaction: discord.Interaction):
    """Command to create a new Akamai Cloud instance."""
    await interaction.response.defer(thinking=True)
    
    # Check if cache is empty
    if not cache["regions"] or not cache["images"] or not cache["types"]:
        await update_cache()
    
    view = InstanceCreationView(str(interaction.user.id))
    
    # Create an embed with instructions and default selections
    embed = discord.Embed(
        title="Create Akamai Cloud Instance",
        description="Please configure your Akamai Cloud instance by selecting options from the dropdowns below.",
        color=discord.Color.blue()
    )
    
    # Add information about defaults
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
    
    await interaction.followup.send(embed=embed, view=view)

@bot.tree.command(name="list-instances", description="List your Akamai Cloud instances")
async def list_instances(interaction: discord.Interaction):
    """Command to list a user's Akamai Cloud instances."""
    await interaction.response.defer(thinking=True)
    
    user_id = str(interaction.user.id)
    instances = db.get_user_instances(user_id)
    
    if not instances:
        await interaction.followup.send("You don't have any Akamai Cloud instances.")
        return
    
    # Create an embed for each instance
    embeds = []
    for instance in instances:
        embed = discord.Embed(
            title=f"Akamai Cloud: {instance.get('label', 'Unknown')}",
            description=f"ID: `{instance.get('id', 'Unknown')}`",
            color=discord.Color.blue()
        )
        
        # Add instance details
        embed.add_field(name="Status", value=f"`{instance.get('status', 'Unknown')}`")
        embed.add_field(name="Region", value=f"`{instance.get('region', 'Unknown')}`")
        embed.add_field(name="Type", value=f"`{instance.get('type', 'Unknown')}`")
        
        # Add IP addresses if available
        ipv4 = instance.get('ipv4', [])
        if ipv4:
            formatted_ips = "\n".join([f"`{ip}`" for ip in ipv4])
            embed.add_field(name="IPv4", value=formatted_ips, inline=False)
        
        embeds.append(embed)
    
    # Send the embeds
    await interaction.followup.send(embeds=embeds[:10])  # Discord limits to 10 embeds per message

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
    
    # Create confirmation view
    view = InstanceDeleteView(user_id, instance_id)
    
    # Create embed
    embed = discord.Embed(
        title="Confirm Deletion",
        description=f"Are you sure you want to delete Akamai Cloud instance {instance_id} ({instance.get('label', 'Unknown')})?\n\n"
                    f"**This action cannot be undone!**",
        color=discord.Color.red()
    )
    
    await interaction.followup.send(embed=embed, view=view)

@bot.tree.command(name="refresh-instance", description="Refresh an Akamai Cloud instance's status")
@app_commands.describe(instance_id="The ID of the Akamai Cloud instance to refresh")
async def refresh_instance(interaction: discord.Interaction, instance_id: int):
    """Command to refresh an Akamai Cloud instance's status."""
    await interaction.response.defer(thinking=True)
    
    user_id = str(interaction.user.id)
    instance = db.get_instance(user_id, instance_id)
    
    if not instance:
        await interaction.followup.send(f"You don't have an Akamai Cloud instance with ID {instance_id}.")
        return
    
    try:
        # Get the latest instance data
        updated_instance = akamai_api.get_instance(instance_id)
        
        # Update the database
        db.update_instance(user_id, instance_id, updated_instance)
        
        # Create embed
        embed = discord.Embed(
            title=f"Akamai Cloud: {updated_instance.get('label', 'Unknown')}",
            description=f"ID: {updated_instance.get('id', 'Unknown')}",
            color=discord.Color.green()
        )
        
        # Add instance details
        embed.add_field(name="Status", value=updated_instance.get('status', 'Unknown'))
        embed.add_field(name="Region", value=updated_instance.get('region', 'Unknown'))
        embed.add_field(name="Type", value=updated_instance.get('type', 'Unknown'))
        
        # Add IP addresses if available
        ipv4 = updated_instance.get('ipv4', [])
        if ipv4:
            embed.add_field(name="IPv4", value="\n".join(ipv4), inline=False)
        
        await interaction.followup.send(embed=embed)
    
    except Exception as e:
        await interaction.followup.send(f"Failed to refresh Akamai Cloud instance: {str(e)}")

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
        # Reboot the instance
        akamai_api.reboot_instance(instance_id)
        
        await interaction.followup.send(f"Akamai Cloud instance {instance_id} is being rebooted. This may take a few minutes.")
    
    except Exception as e:
        await interaction.followup.send(f"Failed to reboot Akamai Cloud instance: {str(e)}")

# Add aliases for backward compatibility
@bot.tree.command(name="create-linode", description="Create a new Akamai Cloud instance")
async def create_linode(interaction: discord.Interaction):
    """Alias for create-instance command."""
    await create_instance(interaction)

@bot.tree.command(name="list-linodes", description="List your Akamai Cloud instances")
async def list_linodes(interaction: discord.Interaction):
    """Alias for list-instances command."""
    await list_instances(interaction)

@bot.tree.command(name="delete-linode", description="Delete an Akamai Cloud instance")
@app_commands.describe(instance_id="The ID of the Akamai Cloud instance to delete")
async def delete_linode(interaction: discord.Interaction, instance_id: int):
    """Alias for delete-instance command."""
    await delete_instance(interaction, instance_id)

@bot.tree.command(name="refresh-linode", description="Refresh an Akamai Cloud instance's status")
@app_commands.describe(instance_id="The ID of the Akamai Cloud instance to refresh")
async def refresh_linode(interaction: discord.Interaction, instance_id: int):
    """Alias for refresh-instance command."""
    await refresh_instance(interaction, instance_id)

@bot.tree.command(name="reboot-linode", description="Reboot an Akamai Cloud instance")
@app_commands.describe(instance_id="The ID of the Akamai Cloud instance to reboot")
async def reboot_linode(interaction: discord.Interaction, instance_id: int):
    """Alias for reboot-instance command."""
    await reboot_instance(interaction, instance_id)

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("Error: DISCORD_TOKEN not found in environment variables")
        exit(1)
    
    print("Starting Akamai Cloud Bot...")
    bot.run(DISCORD_TOKEN) 