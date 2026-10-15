"""
Enterprise Discord ModMail System - Main Runner
A production-ready, enterprise-grade support platform for Discord.
Supports unlimited servers with independent per-server configurations,
persistent UI views across restarts, and full backward compatibility.
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import json
import os
import datetime
import aiohttp
import asyncio
import re
from typing import Optional, Dict, Any, List

import database
import views
import transcripts

# Ensure base directories exist
os.makedirs("servers", exist_ok=True)
os.makedirs("transcripts", exist_ok=True)

# ----------------- BACKWARD-COMPATIBLE HELPERS -----------------

ACTIVE_TICKETS_FILE = "active_tickets.json"

def load_active_tickets():
    """Backward-compatible ticket loader."""
    if os.path.exists(ACTIVE_TICKETS_FILE):
        try:
            with open(ACTIVE_TICKETS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"by_user": {}, "by_channel": {}}

def save_active_tickets(tickets):
    """Backward-compatible ticket saver."""
    with open(ACTIVE_TICKETS_FILE, "w", encoding="utf-8") as f:
        json.dump(tickets, f, indent=4)

def load_guild_config(guild_id: int):
    """Backward-compatible guild configuration loader."""
    return database.get_guild_config(guild_id)

def save_guild_config(guild_id: int, config: dict):
    """Backward-compatible guild configuration saver."""
    database.save_guild_config(guild_id, config)

def create_embed(title, description, color=discord.Color.blurple(), thumbnail=None, fields=None):
    """Backward-compatible embed utility."""
    return views.create_embed(title, description, color, thumbnail, fields)

# ----------------- BOT INITIALIZATION -----------------

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True
intents.dm_messages = True

class ModMailBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)
        self.active_tickets = load_active_tickets()
        self.claim_locks: Dict[int, asyncio.Lock] = {}

    def get_ticket_lock(self, ticket_id: int) -> asyncio.Lock:
        if ticket_id not in self.claim_locks:
            self.claim_locks[ticket_id] = asyncio.Lock()
        return self.claim_locks[ticket_id]

    async def setup_hook(self):
        # Initialize SQLite persistence & sync from files
        database.init_db()
        print("Database initialized & state synchronized.")

        # Register persistent views to keep buttons active across restarts
        self.add_view(views.PersistentTicketActionBar())
        self.add_view(views.PersistentSatisfactionSurveyView())
        print("Persistent interactive views registered.")

        # Load extension cogs
        await self.load_extension("commands_ticket")
        await self.load_extension("commands_staff")
        await self.load_extension("commands_admin")
        print("All enterprise command suites loaded.")

        # Global command sync
        try:
            synced = await self.tree.sync()
            count_str = f" ({len(synced)} commands)" if synced is not None else ""
            print(f"Application commands synced globally{count_str}.")
        except Exception as e:
            print(f"Error syncing application commands: {e}")

    @tasks.loop(minutes=5.0)
    async def inactivity_checker(self):
        """Per-server inactivity audit and automated closure."""
        await self.wait_until_ready()
        for guild in self.guilds:
            try:
                config = database.get_guild_config(guild.id)
                if not config.get("enabled"):
                    continue

                # 1. Inactivity Warnings
                warn_hrs = config.get("warning_hours", 24)
                if warn_hrs > 0:
                    tickets_to_warn = database.get_inactive_tickets_to_warn(guild.id, warn_hrs)
                    for t in tickets_to_warn:
                        channel = guild.get_channel(t["channel_id"])
                        if channel:
                            embed_warn = views.create_embed(
                                title="⚠️ Ticket Inactivity Warning",
                                description=(
                                    f"This ticket has been inactive for over **{warn_hrs} hours**.\n"
                                    "If you still need assistance, please send a message to keep this ticket open. "
                                    f"Otherwise, it will be automatically closed after **{config.get('inactivity_hours', 48)} hours** of total inactivity."
                                ),
                                color=discord.Color.gold()
                            )
                            await channel.send(embed=embed_warn)
                        database.update_ticket(t["ticket_id"], inactivity_warned=1)

                # 2. Inactivity Auto-Close
                close_hrs = config.get("inactivity_hours", 48)
                if close_hrs > 0:
                    tickets_to_close = database.get_inactive_tickets_to_close(guild.id, close_hrs)
                    for t in tickets_to_close:
                        channel = guild.get_channel(t["channel_id"])
                        if channel:
                            await views.execute_ticket_close(
                                channel=channel,
                                guild=guild,
                                closer=self.user,
                                reason=f"Auto-closed due to {close_hrs} hours of inactivity"
                            )
            except Exception as e:
                print(f"Error in inactivity loop for guild {guild.id}: {e}")

bot = ModMailBot()

@bot.event
async def on_ready():
    print(f"ModMail Bot connected as {bot.user} (ID: {bot.user.id}) across {len(bot.guilds)} server(s).")
    
    # Start background inactivity task once connected
    if not bot.inactivity_checker.is_running():
        bot.inactivity_checker.start()
        print("Inactivity background monitor started.")

    await bot.change_presence(
        status=discord.Status.online,
        activity=discord.Activity(type=discord.ActivityType.watching, name="Direct Messages | ModMail")
    )

# ----------------- CHAT MIRRORING & ROUTING -----------------

@bot.event
async def on_message(message: discord.Message):
    # Ignore messages sent by bots
    if message.author.bot:
        return

    # 1. Handle DM Messages (Client -> Support Channel)
    if isinstance(message.channel, discord.DMChannel):
        active_ticket = database.get_active_ticket_by_user(message.author.id)

        if active_ticket:
            guild = bot.get_guild(active_ticket["guild_id"])
            if not guild:
                return

            channel = guild.get_channel(active_ticket["channel_id"])
            if not channel:
                # Channel deleted manually, mark closed in DB and clean state
                database.close_ticket(active_ticket["ticket_id"], bot.user.id, "System", "Channel manually deleted")
                return

            # Check client content
            content = message.content or ""
            mirror_content = f"Client said: {content}" if content else "Client said: *[Sent attachments only]*"

            files = []
            att_urls = []
            if message.attachments:
                for attachment in message.attachments:
                    try:
                        att_urls.append(attachment.url)
                        file_bytes = await attachment.read()
                        from io import BytesIO
                        files.append(discord.File(BytesIO(file_bytes), filename=attachment.filename))
                    except Exception:
                        pass

            # Send to support ticket channel
            await channel.send(content=mirror_content, files=files)

            # Record in database
            database.record_ticket_message(
                ticket_id=active_ticket["ticket_id"],
                message_id=message.id,
                sender_id=message.author.id,
                sender_name=str(message.author),
                sender_type="client",
                content=content,
                attachments=att_urls
            )

            # React in DM to confirm delivery
            try:
                await message.add_reaction("✅")
            except Exception:
                pass
        else:
            # No active ticket: check common guilds configured with Modmail
            common_guilds = []
            for guild in bot.guilds:
                member = guild.get_member(message.author.id)
                if member:
                    cfg = database.get_guild_config(guild.id)
                    if cfg.get("setup_completed") and cfg.get("enabled"):
                        common_guilds.append(guild)

            if not common_guilds:
                await message.channel.send("❌ Hello! You do not share any servers where Modmail is currently enabled.")
                return

            embed = views.create_embed(
                title="✉️ ModMail Support Request",
                description="You are contacting server support. Would you like to create a support ticket?",
                color=discord.Color.blurple()
            )
            await message.channel.send(embed=embed, view=views.DMGreetingView(common_guilds, bot_instance=bot))

    # 2. Handle Support Channel Messages (Staff -> User DM)
    elif isinstance(message.channel, discord.TextChannel):
        ticket = database.get_ticket_by_channel(message.channel.id)

        if ticket:
            content = message.content or ""

            # Check for commands or prefixes
            if content.startswith("/") or content.startswith("!"):
                # Process bot prefix commands (!r, !ar, !n, !close, etc.)
                await bot.process_commands(message)
                return

            # Check for internal notes shorthand (starting with #)
            if content.startswith("#"):
                note_text = content[1:].strip()
                if note_text:
                    database.add_ticket_note(ticket["ticket_id"], message.author.id, message.author.display_name, note_text)
                    database.record_ticket_message(
                        ticket["ticket_id"], message.id, message.author.id, message.author.display_name,
                        "staff", note_text, is_internal=True
                    )
                    await message.add_reaction("📝")
                return

            # Check claim lock
            claimed_by_id = ticket.get("claimed_by_id")
            if claimed_by_id and claimed_by_id != message.author.id:
                # If author is not the claimer and not administrator, prevent accidental collision
                if not message.author.guild_permissions.administrator and message.guild.owner_id != message.author.id:
                    claimer_name = ticket.get("claimed_by_name") or f"<@{claimed_by_id}>"
                    warn = await message.channel.send(
                        f"⚠️ {message.author.mention}, this ticket is currently claimed by **{claimer_name}**. "
                        "To prevent message collisions, your message was NOT forwarded to the user. "
                        "Use `/claim` or ask the claimer to `/transfer` it to you, or prefix with `#` to add an internal note."
                    )
                    await asyncio.sleep(8)
                    try:
                        await warn.delete()
                    except Exception:
                        pass
                    return

            # Forward message to user DM
            user_id = ticket["user_id"]
            user = message.guild.get_member(user_id)
            if not user:
                try:
                    user = await bot.fetch_user(user_id)
                except Exception:
                    pass

            if not user:
                await message.channel.send("❌ Error: Could not locate the client user in Discord.")
                return

            # Check if server defaults to anonymous replies
            config = database.get_guild_config(message.guild.id)
            is_anon = config.get("anon_replies_default", False)

            author_name = "Server Support Team" if is_anon else f"{message.author.display_name} (Support)"
            author_icon = message.guild.icon.url if is_anon and message.guild.icon else message.author.display_avatar.url

            embed_reply = views.create_embed(
                title=f"✉️ Reply from {message.guild.name} Support",
                description=content,
                color=discord.Color.blue()
            )
            embed_reply.set_author(name=author_name, icon_url=author_icon)

            files = []
            att_urls = []
            if message.attachments:
                for attachment in message.attachments:
                    try:
                        att_urls.append(attachment.url)
                        file_bytes = await attachment.read()
                        from io import BytesIO
                        files.append(discord.File(BytesIO(file_bytes), filename=attachment.filename))
                    except Exception:
                        pass

            try:
                await user.send(embed=embed_reply, files=files)
                database.record_ticket_message(
                    ticket_id=ticket["ticket_id"],
                    message_id=message.id,
                    sender_id=message.author.id,
                    sender_name="Anonymous Staff" if is_anon else message.author.display_name,
                    sender_type="staff",
                    content=content,
                    attachments=att_urls
                )
                await message.add_reaction("✉️")
            except discord.Forbidden:
                await message.channel.send("❌ Error: Client has Direct Messages disabled or has blocked the bot.")
            except Exception as e:
                await message.channel.send(f"❌ Error sending message to client: {str(e)}")

        else:
            await bot.process_commands(message)
    else:
        await bot.process_commands(message)

# ----------------- MAIN RUNNER -----------------

if __name__ == "__main__":
    token_path = "Token.txt"
    if not os.path.exists(token_path):
        print(f"Error: Token file '{token_path}' not found.")
        with open(token_path, "w", encoding="utf-8") as f:
            f.write("INSERT_TOKEN_HERE")
        print("Please place your bot token in Token.txt and run again.")
    else:
        with open(token_path, "r", encoding="utf-8") as f:
            token = f.read().strip()
        if token == "INSERT_TOKEN_HERE" or not token:
            print("Error: Please specify a valid bot token in Token.txt.")
        else:
            try:
                bot.run(token)
            except Exception as e:
                print(f"Error running bot: {e}")
