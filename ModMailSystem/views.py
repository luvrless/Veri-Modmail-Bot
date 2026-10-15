"""
Interactive Views, Modals, and UI Components for Enterprise ModMail System.
All interactive views use persistent custom_ids and timeout=None where appropriate,
ensuring that buttons, menus, and modals continue functioning across bot restarts.
"""

import discord
from discord import ui
import datetime
import asyncio
import re
import aiohttp
from typing import Optional, List, Dict, Any

import database
import transcripts

# ----------------- REUSABLE HELPERS -----------------

def create_embed(title: str, description: str, color=discord.Color.blurple(), thumbnail=None, fields=None) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.datetime.now(datetime.timezone.utc)
    )
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    if fields:
        for name, value, inline in fields:
            embed.add_field(name=name, value=value, inline=inline)
    embed.set_footer(text="Enterprise ModMail System")
    return embed

# ----------------- TICKET CHANNEL ACTION BAR (PERSISTENT) -----------------

class PersistentTicketActionBar(ui.View):
    """
    Persistent action bar embedded in every ticket channel.
    Survives bot restarts with timeout=None.
    """
    def __init__(self):
        super().__init__(timeout=None)

    @ui.button(label="Claim Ticket", style=discord.ButtonStyle.success, emoji="📌", custom_id="ticket_action_claim")
    async def claim_button(self, interaction: discord.Interaction, button: ui.Button):
        await handle_claim_interaction(interaction)

    @ui.button(label="Unclaim", style=discord.ButtonStyle.secondary, emoji="🔓", custom_id="ticket_action_unclaim")
    async def unclaim_button(self, interaction: discord.Interaction, button: ui.Button):
        await handle_unclaim_interaction(interaction)

    @ui.button(label="Priority", style=discord.ButtonStyle.primary, emoji="⚡", custom_id="ticket_action_priority")
    async def priority_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(
            "Select the new priority level for this ticket:",
            view=PrioritySelectView(),
            ephemeral=True
        )

    @ui.button(label="Add Note", style=discord.ButtonStyle.primary, emoji="📝", custom_id="ticket_action_note")
    async def note_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(TicketNoteModal())

    @ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket_action_close")
    async def close_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(TicketCloseModal())


async def handle_claim_interaction(interaction: discord.Interaction):
    ticket = database.get_ticket_by_channel(interaction.channel.id)
    if not ticket:
        await interaction.response.send_message("❌ This is not an active ticket channel.", ephemeral=True)
        return

    # Check staff permission
    config = database.get_guild_config(interaction.guild.id)
    staff_role_id = config.get("staff_role_id")
    is_staff = interaction.user.guild_permissions.administrator or (
        staff_role_id and any(r.id == staff_role_id for r in interaction.user.roles)
    )
    if not is_staff:
        await interaction.response.send_message("❌ You do not have permission to claim tickets.", ephemeral=True)
        return

    current_claim = ticket.get("claimed_by_id")
    if current_claim:
        if current_claim == interaction.user.id:
            await interaction.response.send_message("⚠️ You already have this ticket claimed.", ephemeral=True)
            return
        claimed_name = ticket.get("claimed_by_name", "another staff member")
        await interaction.response.send_message(f"⚠️ This ticket is already claimed by **{claimed_name}**. Use `/transfer` or unclaim first.", ephemeral=True)
        return

    # Update claim
    database.update_ticket(
        ticket["ticket_id"],
        claimed_by_id=interaction.user.id,
        claimed_by_name=interaction.user.display_name,
        status="claimed"
    )
    database.add_ticket_audit(
        ticket["ticket_id"],
        "CLAIMED",
        interaction.user.id,
        interaction.user.display_name,
        f"Ticket claimed by {interaction.user.display_name}"
    )

    # Update channel topic
    try:
        current_topic = interaction.channel.topic or ""
        base_topic = current_topic.split(" | Claimed:")[0]
        new_topic = f"{base_topic} | Claimed: {interaction.user.display_name} | Priority: {str(ticket.get('priority', 'normal')).upper()}"
        await interaction.channel.edit(topic=new_topic[:1024])
    except Exception:
        pass

    embed = create_embed(
        title="📌 Ticket Claimed",
        description=f"{interaction.user.mention} has claimed this ticket. Other staff please consult them before replying.",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)


async def handle_unclaim_interaction(interaction: discord.Interaction):
    ticket = database.get_ticket_by_channel(interaction.channel.id)
    if not ticket:
        await interaction.response.send_message("❌ This is not an active ticket channel.", ephemeral=True)
        return

    config = database.get_guild_config(interaction.guild.id)
    staff_role_id = config.get("staff_role_id")
    is_staff = interaction.user.guild_permissions.administrator or (
        staff_role_id and any(r.id == staff_role_id for r in interaction.user.roles)
    )
    if not is_staff:
        await interaction.response.send_message("❌ You do not have permission to manage claims.", ephemeral=True)
        return

    if not ticket.get("claimed_by_id"):
        await interaction.response.send_message("⚠️ This ticket is not currently claimed.", ephemeral=True)
        return

    # If claimed by someone else and caller is not admin
    if ticket.get("claimed_by_id") != interaction.user.id and not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message("❌ Only the staff member who claimed this ticket or an administrator can unclaim it.", ephemeral=True)
        return

    database.update_ticket(
        ticket["ticket_id"],
        claimed_by_id=None,
        claimed_by_name=None,
        status="open"
    )
    database.add_ticket_audit(
        ticket["ticket_id"],
        "UNCLAIMED",
        interaction.user.id,
        interaction.user.display_name,
        f"Claim released by {interaction.user.display_name}"
    )

    try:
        current_topic = interaction.channel.topic or ""
        base_topic = current_topic.split(" | Claimed:")[0]
        await interaction.channel.edit(topic=base_topic[:1024])
    except Exception:
        pass

    embed = create_embed(
        title="🔓 Ticket Unclaimed",
        description=f"{interaction.user.mention} has released the claim. The ticket is now open for any staff member to assist.",
        color=discord.Color.orange()
    )
    await interaction.response.send_message(embed=embed)

# ----------------- PRIORITY SELECT VIEW -----------------

class PrioritySelectView(ui.View):
    def __init__(self):
        super().__init__(timeout=60)

    @ui.select(
        placeholder="Select priority level...",
        options=[
            discord.SelectOption(label="Low Priority", value="low", emoji="🟢", description="Non-urgent inquiries or questions"),
            discord.SelectOption(label="Normal Priority", value="normal", emoji="🔵", description="Standard support requests"),
            discord.SelectOption(label="High Priority", value="high", emoji="🟡", description="Time-sensitive issues requiring attention"),
            discord.SelectOption(label="Urgent Priority", value="urgent", emoji="🔴", description="Critical bugs, safety, or exploits")
        ]
    )
    async def select_callback(self, interaction: discord.Interaction, select: ui.Select):
        level = select.values[0]
        ticket = database.get_ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("❌ Active ticket not found.", ephemeral=True)
            return

        database.update_ticket(ticket["ticket_id"], priority=level)
        database.add_ticket_audit(
            ticket["ticket_id"],
            "PRIORITY_CHANGED",
            interaction.user.id,
            interaction.user.display_name,
            f"Priority set to {level.upper()}"
        )

        colors = {
            "low": discord.Color.green(),
            "normal": discord.Color.blue(),
            "high": discord.Color.gold(),
            "urgent": discord.Color.red()
        }

        embed = create_embed(
            title="⚡ Priority Updated",
            description=f"Ticket priority was changed to **{level.upper()}** by {interaction.user.mention}.",
            color=colors.get(level, discord.Color.blurple())
        )
        await interaction.response.send_message(embed=embed)

# ----------------- TICKET NOTE MODAL -----------------

class TicketNoteModal(ui.Modal, title="Add Internal Staff Note"):
    note_content = ui.TextInput(
        label="Internal Note",
        style=discord.TextStyle.paragraph,
        placeholder="This note is only visible to staff and transcripts, NOT to the client...",
        required=True,
        max_length=1500
    )

    async def on_submit(self, interaction: discord.Interaction):
        ticket = database.get_ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("❌ Active ticket not found.", ephemeral=True)
            return

        content = self.note_content.value.strip()
        database.add_ticket_note(
            ticket["ticket_id"],
            interaction.user.id,
            interaction.user.display_name,
            content
        )
        database.record_ticket_message(
            ticket["ticket_id"],
            interaction.id,
            interaction.user.id,
            interaction.user.display_name,
            "staff",
            content,
            is_internal=True
        )

        embed = create_embed(
            title="📝 Internal Note Added",
            description=content,
            color=discord.Color.gold()
        )
        embed.set_author(name=f"Note by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

# ----------------- TICKET CLOSE MODAL -----------------

class TicketCloseModal(ui.Modal, title="Close ModMail Ticket"):
    reason = ui.TextInput(
        label="Close Reason",
        placeholder="Reason for closing this ticket (e.g. Issue resolved, inactive)...",
        style=discord.TextStyle.paragraph,
        required=False,
        default="Issue resolved by support staff",
        max_length=500
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        await execute_ticket_close(
            interaction.channel,
            interaction.guild,
            interaction.user,
            reason=self.reason.value.strip() or "Issue resolved"
        )


async def execute_ticket_close(channel: discord.TextChannel, guild: discord.Guild, closer: discord.User, reason: str = "Issue resolved"):
    ticket = database.get_ticket_by_channel(channel.id)
    if not ticket:
        try:
            await channel.send("❌ Error: Active ticket state not found.")
        except Exception:
            pass
        return

    # Update database
    database.close_ticket(
        ticket["ticket_id"],
        closer.id,
        closer.display_name,
        reason=reason
    )

    # Fetch all ticket details, messages, notes, and audits
    messages = database.get_ticket_messages(ticket["ticket_id"], include_internal=True)
    notes = database.get_ticket_notes(ticket["ticket_id"])
    audit_logs = database.get_ticket_audit(ticket["ticket_id"])
    config = database.get_guild_config(guild.id)

    # Build transcripts
    transcript_files = transcripts.save_and_build_transcripts(
        guild_name=guild.name,
        guild_id=guild.id,
        ticket_data=ticket,
        messages=messages,
        notes=notes,
        audit_logs=audit_logs,
        closed_by_name=closer.display_name,
        closed_by_id=closer.id
    )

    # Send to logs channel
    logs_channel_id = config.get("logs_channel_id")
    if logs_channel_id:
        logs_channel = guild.get_channel(logs_channel_id)
        if logs_channel:
            try:
                embed_log = create_embed(
                    title=f"💾 Ticket #{ticket.get('ticket_number', 1)} Closed",
                    description=f"Ticket for **{ticket.get('user_name')}** (`{ticket.get('user_id')}`) was closed by {closer.mention}.",
                    color=discord.Color.red(),
                    fields=[
                        ("Close Reason", reason, False),
                        ("Priority", str(ticket.get("priority", "normal")).upper(), True),
                        ("Claimed By", ticket.get("claimed_by_name") or "Unclaimed", True),
                        ("Total Messages", str(len(messages)), True)
                    ]
                )
                await logs_channel.send(
                    embed=embed_log,
                    files=[transcript_files["html_file"], transcript_files["txt_file"]]
                )
            except Exception as e:
                print(f"Error sending to logs channel: {e}")

    # Send to user DMs
    user = guild.get_member(ticket["user_id"])
    if not user:
        try:
            client = getattr(guild, "_state", None) and guild._state._get_client()
            if client:
                user = await client.fetch_user(ticket["user_id"])
        except Exception:
            pass

    if user:
        try:
            embed_user = create_embed(
                title=f"💾 Ticket Closed - {guild.name}",
                description=(
                    f"Your support ticket #{ticket.get('ticket_number', 1)} in **{guild.name}** has been closed.\n\n"
                    f"**Reason:** {reason}\n"
                    "A full transcript of your conversation is attached below for your records."
                ),
                color=discord.Color.red()
            )
            # Check survey config
            survey_view = None
            if config.get("survey_enabled", True):
                survey_view = PersistentSatisfactionSurveyView(ticket["ticket_id"])

            await user.send(embed=embed_user, file=transcript_files["client_html_file"], view=survey_view)
        except Exception as e:
            print(f"Could not DM user: {e}")

    # Notify in channel, wait 5 seconds, delete channel
    try:
        await channel.send("🔒 **Ticket closed!** Transcripts saved. Channel deleting in 5 seconds...")
        await asyncio.sleep(5)
        await channel.delete()
    except Exception:
        pass

# ----------------- SATISFACTION SURVEY VIEW (PERSISTENT) -----------------

class PersistentSatisfactionSurveyView(ui.View):
    """
    Survey view presented to users in DMs after a ticket is closed.
    Collects 1-5 star ratings and optional written feedback.
    """
    def __init__(self, ticket_id: Optional[int] = None):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id

    @ui.button(label="1", style=discord.ButtonStyle.secondary, emoji="⭐", custom_id="csat_rate_1")
    async def rate_1(self, interaction: discord.Interaction, button: ui.Button):
        await self.process_rating(interaction, 1)

    @ui.button(label="2", style=discord.ButtonStyle.secondary, emoji="⭐", custom_id="csat_rate_2")
    async def rate_2(self, interaction: discord.Interaction, button: ui.Button):
        await self.process_rating(interaction, 2)

    @ui.button(label="3", style=discord.ButtonStyle.secondary, emoji="⭐", custom_id="csat_rate_3")
    async def rate_3(self, interaction: discord.Interaction, button: ui.Button):
        await self.process_rating(interaction, 3)

    @ui.button(label="4", style=discord.ButtonStyle.primary, emoji="⭐", custom_id="csat_rate_4")
    async def rate_4(self, interaction: discord.Interaction, button: ui.Button):
        await self.process_rating(interaction, 4)

    @ui.button(label="5", style=discord.ButtonStyle.success, emoji="⭐", custom_id="csat_rate_5")
    async def rate_5(self, interaction: discord.Interaction, button: ui.Button):
        await self.process_rating(interaction, 5)

    async def process_rating(self, interaction: discord.Interaction, rating: int):
        # Disable buttons
        for child in self.children:
            child.disabled = True

        # Resolve ticket_id from message or instance
        t_id = self.ticket_id
        if not t_id:
            # Fallback: get user's most recent closed ticket
            conn = database.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT ticket_id FROM tickets WHERE user_id = ? AND status = 'closed' ORDER BY ticket_id DESC LIMIT 1", (interaction.user.id,))
            row = cursor.fetchone()
            conn.close()
            if row:
                t_id = row[0]

        if t_id:
            database.record_feedback(t_id, rating)

        await interaction.response.send_modal(CSATFeedbackModal(t_id, rating, self))


class CSATFeedbackModal(ui.Modal, title="Support Feedback"):
    feedback = ui.TextInput(
        label="Additional Comments (Optional)",
        style=discord.TextStyle.paragraph,
        placeholder="How was your experience? How can we improve?",
        required=False,
        max_length=500
    )

    def __init__(self, ticket_id: Optional[int], rating: int, original_view: ui.View):
        super().__init__()
        self.ticket_id = ticket_id
        self.rating = rating
        self.original_view = original_view

    async def on_submit(self, interaction: discord.Interaction):
        fb_text = self.feedback.value.strip() or None
        if self.ticket_id:
            database.record_feedback(self.ticket_id, self.rating, fb_text)

        try:
            await interaction.message.edit(view=self.original_view)
        except Exception:
            pass

        embed = create_embed(
            title="💖 Thank You for Your Feedback!",
            description=f"Your rating of **{self.rating}/5 stars** has been submitted.\nWe appreciate your help in improving our support experience!",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

# ----------------- PAGINATION VIEW -----------------

class PaginationView(ui.View):
    def __init__(self, pages: List[discord.Embed], author_id: int):
        super().__init__(timeout=120)
        self.pages = pages
        self.author_id = author_id
        self.current = 0
        self.update_buttons()

    def update_buttons(self):
        self.btn_first.disabled = (self.current == 0)
        self.btn_prev.disabled = (self.current == 0)
        self.btn_next.disabled = (self.current == len(self.pages) - 1)
        self.btn_last.disabled = (self.current == len(self.pages) - 1)

    @ui.button(label="<<", style=discord.ButtonStyle.secondary)
    async def btn_first(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ This is not your menu.", ephemeral=True)
            return
        self.current = 0
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current], view=self)

    @ui.button(label="<", style=discord.ButtonStyle.primary)
    async def btn_prev(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ This is not your menu.", ephemeral=True)
            return
        self.current = max(0, self.current - 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current], view=self)

    @ui.button(label=">", style=discord.ButtonStyle.primary)
    async def btn_next(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ This is not your menu.", ephemeral=True)
            return
        self.current = min(len(self.pages) - 1, self.current + 1)
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current], view=self)

    @ui.button(label=">>", style=discord.ButtonStyle.secondary)
    async def btn_last(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("❌ This is not your menu.", ephemeral=True)
            return
        self.current = len(self.pages) - 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current], view=self)

# ----------------- ADVANCED SETUP DASHBOARD & SELECTS -----------------

class CategorySelect(ui.ChannelSelect):
    def __init__(self, view_holder):
        super().__init__(
            placeholder="Select Modmail Category...",
            channel_types=[discord.ChannelType.category],
            min_values=1,
            max_values=1,
            row=0
        )
        self.view_holder = view_holder

    async def callback(self, interaction: discord.Interaction):
        selected_category = self.values[0]
        self.view_holder.config["category_id"] = selected_category.id
        database.save_guild_config(self.view_holder.guild_id, self.view_holder.config)
        await interaction.response.defer()
        await self.view_holder.update_dashboard(interaction)

class RoleSelect(ui.RoleSelect):
    def __init__(self, view_holder):
        super().__init__(
            placeholder="Select Staff/Support Role...",
            min_values=1,
            max_values=1,
            row=1
        )
        self.view_holder = view_holder

    async def callback(self, interaction: discord.Interaction):
        selected_role = self.values[0]
        self.view_holder.config["staff_role_id"] = selected_role.id
        database.save_guild_config(self.view_holder.guild_id, self.view_holder.config)
        await interaction.response.defer()
        await self.view_holder.update_dashboard(interaction)

class LogsChannelSelect(ui.ChannelSelect):
    def __init__(self, view_holder):
        super().__init__(
            placeholder="Select Logs Channel...",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
            row=2
        )
        self.view_holder = view_holder

    async def callback(self, interaction: discord.Interaction):
        selected_channel = self.values[0]
        self.view_holder.config["logs_channel_id"] = selected_channel.id
        database.save_guild_config(self.view_holder.guild_id, self.view_holder.config)
        await interaction.response.defer()
        await self.view_holder.update_dashboard(interaction)

class IdentityModal(ui.Modal, title="Bot Profile Configuration"):
    bot_name = ui.TextInput(
        label="Bot Name (Username)",
        placeholder="Enter new username...",
        required=False,
        max_length=32
    )
    bot_activity = ui.TextInput(
        label="Bot Activity Status Text",
        placeholder="e.g. DM me for help!",
        required=False,
        max_length=128
    )
    bot_status = ui.TextInput(
        label="Bot Status (online / idle / dnd)",
        placeholder="online, idle, or dnd",
        default="online",
        required=False,
        max_length=10
    )

    def __init__(self, view_holder, bot_instance):
        super().__init__()
        self.view_holder = view_holder
        self.bot = bot_instance
        self.bot_name.default = self.bot.user.name
        self.bot_activity.default = self.view_holder.activity_text
        self.bot_status.default = self.view_holder.status_str

    async def on_submit(self, interaction: discord.Interaction):
        new_name = self.bot_name.value.strip()
        new_activity = self.bot_activity.value.strip()
        new_status_str = self.bot_status.value.strip().lower()

        status_map = {
            "online": discord.Status.online,
            "idle": discord.Status.idle,
            "dnd": discord.Status.dnd,
            "do not disturb": discord.Status.dnd
        }
        selected_status = status_map.get(new_status_str, discord.Status.online)

        await interaction.response.defer(ephemeral=True)

        try:
            if new_name and new_name != self.bot.user.name:
                try:
                    await self.bot.user.edit(username=new_name)
                    await interaction.followup.send(f"✅ Bot username updated to **{new_name}**.", ephemeral=True)
                except discord.HTTPException as e:
                    await interaction.followup.send(f"⚠️ Failed to update username: {e.text}. Discord limits changes to 2 per hour.", ephemeral=True)

            self.view_holder.activity_text = new_activity
            self.view_holder.status_str = new_status_str
            await self.bot.change_presence(
                status=selected_status,
                activity=discord.Activity(type=discord.ActivityType.watching, name=new_activity) if new_activity else None
            )
            await interaction.followup.send("✅ Bot status and presence updated.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Error updating profile: {str(e)}", ephemeral=True)

        await self.view_holder.update_dashboard(interaction)

class InactivityConfigModal(ui.Modal, title="Inactivity Settings"):
    warn_hours = ui.TextInput(
        label="Warning Timeout (Hours)",
        placeholder="Send warning after X hours of inactivity (e.g. 24, 0 to disable)",
        default="24",
        required=True,
        max_length=4
    )
    close_hours = ui.TextInput(
        label="Auto-Close Timeout (Hours)",
        placeholder="Auto close after X hours of inactivity (e.g. 48, 0 to disable)",
        default="48",
        required=True,
        max_length=4
    )

    def __init__(self, view_holder):
        super().__init__()
        self.view_holder = view_holder
        self.warn_hours.default = str(self.view_holder.config.get("warning_hours", 24))
        self.close_hours.default = str(self.view_holder.config.get("inactivity_hours", 48))

    async def on_submit(self, interaction: discord.Interaction):
        try:
            w = int(self.warn_hours.value.strip())
            c = int(self.close_hours.value.strip())
            self.view_holder.config["warning_hours"] = max(0, w)
            self.view_holder.config["inactivity_hours"] = max(0, c)
            database.save_guild_config(self.view_holder.guild_id, self.view_holder.config)
            await interaction.response.send_message(f"✅ Inactivity timeouts updated: Warning after **{w}h**, auto-close after **{c}h**.", ephemeral=True)
            await self.view_holder.update_dashboard(interaction)
        except ValueError:
            await interaction.response.send_message("❌ Please provide valid numeric hours.", ephemeral=True)

class SetupDashboardView(ui.View):
    def __init__(self, guild_id: int, setup_channel: discord.TextChannel, author: discord.User, bot_instance):
        super().__init__(timeout=600)
        self.guild_id = guild_id
        self.setup_channel = setup_channel
        self.author = author
        self.bot = bot_instance
        self.config = database.get_guild_config(guild_id)
        self.activity_text = "Direct Messages"
        self.status_str = "online"

        self.add_item(CategorySelect(self))
        self.add_item(RoleSelect(self))
        self.add_item(LogsChannelSelect(self))

    async def update_dashboard(self, interaction_or_ctx):
        category_mention = f"<#{self.config['category_id']}>" if self.config.get("category_id") else "❌ Not Set"
        role_mention = f"<@&{self.config['staff_role_id']}>" if self.config.get("staff_role_id") else "❌ Not Set"
        logs_mention = f"<#{self.config['logs_channel_id']}>" if self.config.get("logs_channel_id") else "❌ Not Set"
        enabled_status = "🟢 Enabled" if self.config.get("enabled") else "🔴 Disabled"
        auto_assign_status = "🟢 Enabled" if self.config.get("auto_assign") else "⚪ Disabled"
        survey_status = "🟢 Enabled" if self.config.get("survey_enabled", True) else "⚪ Disabled"

        desc = (
            f"Welcome to the Enterprise ModMail Setup page for **{interaction_or_ctx.guild.name}**!\n"
            "Configure each setting below using the menus and buttons.\n\n"
            "### ⚙️ Core Configurations\n"
            f"**📂 Category:** {category_mention}\n"
            f"**🛡️ Staff Role:** {role_mention}\n"
            f"**📝 Logs Channel:** {logs_mention}\n"
            f"**🚦 System Status:** {enabled_status}\n\n"
            "### ⚡ Advanced Automation\n"
            f"**🤖 Auto-Assignment:** {auto_assign_status}\n"
            f"**⭐ CSAT Surveys:** {survey_status}\n"
            f"**⏱️ Inactivity Warning:** `{self.config.get('warning_hours', 24)}h`\n"
            f"**🔒 Inactivity Auto-Close:** `{self.config.get('inactivity_hours', 48)}h`\n\n"
            "### 🤖 Bot Persona Settings\n"
            f"**Bot Name:** `{self.bot.user.name}`\n"
            f"**Bot Status:** `{self.status_str.upper()}`\n"
            f"**Bot Activity:** `{self.activity_text}`\n"
        )

        embed = create_embed(
            title="🛡️ Enterprise ModMail Configuration Dashboard",
            description=desc,
            color=discord.Color.purple(),
            thumbnail=self.bot.user.avatar.url if self.bot.user.avatar else None
        )

        if isinstance(interaction_or_ctx, discord.Interaction):
            await interaction_or_ctx.message.edit(embed=embed, view=self)
        else:
            self.message = await interaction_or_ctx.send(embed=embed, view=self)

    @ui.button(label="Identity Settings", style=discord.ButtonStyle.primary, custom_id="setup_identity", row=3)
    async def configure_identity(self, interaction: discord.Interaction, button: ui.Button):
        modal = IdentityModal(self, self.bot)
        await interaction.response.send_modal(modal)

    @ui.button(label="Change PFP", style=discord.ButtonStyle.primary, custom_id="setup_pfp", row=3)
    async def change_pfp(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(
            "📷 Please upload/attach an image or send a URL in this channel. I will set it as my profile picture. (Expires in 60s)",
            ephemeral=True
        )

        def check(m):
            return m.author.id == self.author.id and m.channel.id == self.setup_channel.id

        try:
            msg = await self.bot.wait_for("message", check=check, timeout=60.0)
            url = None
            if msg.attachments:
                url = msg.attachments[0].url
            else:
                match = re.search(r'(https?://[^\s]+)', msg.content)
                if match:
                    url = match.group(1)

            if not url:
                await self.setup_channel.send("❌ No valid image attachment or URL found. Action cancelled.", delete_after=10)
                return

            await self.setup_channel.send("⏳ Downloading image and updating PFP...", delete_after=5)

            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        await self.setup_channel.send("❌ Failed to download the image. Please try another URL.", delete_after=10)
                        return
                    img_bytes = await response.read()

            await self.bot.user.edit(avatar=img_bytes)
            await self.setup_channel.send("✅ Profile picture updated successfully!", delete_after=10)
            await self.update_dashboard(interaction)

        except asyncio.TimeoutError:
            await self.setup_channel.send("⏳ Timeout. PFP configuration closed.", delete_after=10)
        except discord.HTTPException as e:
            await self.setup_channel.send(f"⚠️ Discord HTTP error: {e.text}. You may be rate limited.", delete_after=15)
        except Exception as e:
            await self.setup_channel.send(f"❌ Error updating PFP: {str(e)}", delete_after=10)

    @ui.button(label="Toggle Auto-Assign", style=discord.ButtonStyle.secondary, custom_id="setup_auto_assign", row=3)
    async def toggle_auto_assign(self, interaction: discord.Interaction, button: ui.Button):
        self.config["auto_assign"] = not self.config.get("auto_assign", False)
        database.save_guild_config(self.guild_id, self.config)
        await interaction.response.defer()
        await self.update_dashboard(interaction)

    @ui.button(label="Toggle Surveys", style=discord.ButtonStyle.secondary, custom_id="setup_surveys", row=3)
    async def toggle_surveys(self, interaction: discord.Interaction, button: ui.Button):
        self.config["survey_enabled"] = not self.config.get("survey_enabled", True)
        database.save_guild_config(self.guild_id, self.config)
        await interaction.response.defer()
        await self.update_dashboard(interaction)

    @ui.button(label="Inactivity Settings", style=discord.ButtonStyle.secondary, custom_id="setup_inactivity", row=4)
    async def config_inactivity(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(InactivityConfigModal(self))

    @ui.button(label="Toggle System ON/OFF", style=discord.ButtonStyle.success, custom_id="setup_toggle", row=4)
    async def toggle_system(self, interaction: discord.Interaction, button: ui.Button):
        self.config["enabled"] = not self.config.get("enabled", False)
        database.save_guild_config(self.guild_id, self.config)
        await interaction.response.defer()
        await self.update_dashboard(interaction)

    @ui.button(label="Save & Exit Setup", style=discord.ButtonStyle.danger, custom_id="setup_exit", row=4)
    async def save_and_exit(self, interaction: discord.Interaction, button: ui.Button):
        if not self.config.get("category_id") or not self.config.get("staff_role_id"):
            await interaction.response.send_message(
                "❌ You must select a Category and Staff Role before finishing setup!",
                ephemeral=True
            )
            return

        self.config["setup_completed"] = True
        database.save_guild_config(self.guild_id, self.config)

        await interaction.response.send_message("⚙️ Settings saved. Closing setup channel in 5 seconds...", ephemeral=True)
        await asyncio.sleep(5)
        try:
            await self.setup_channel.delete()
        except Exception:
            pass

# ----------------- TICKET FLOW VIEWS & MODALS -----------------

class UserGuildSelect(ui.Select):
    def __init__(self, common_guilds: List[discord.Guild], greeting_view, greeting_interaction, bot_instance):
        self.greeting_view = greeting_view
        self.greeting_interaction = greeting_interaction
        self.bot = bot_instance
        options = [
            discord.SelectOption(label=g.name[:100], value=str(g.id), description=f"Server ID: {g.id}")
            for g in common_guilds[:25]
        ]
        super().__init__(
            placeholder="Choose the server you want to contact...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        guild_id = int(self.values[0])
        guild = self.bot.get_guild(guild_id)
        if not guild:
            await interaction.response.send_message("❌ Server not found.", ephemeral=True)
            return

        config = database.get_guild_config(guild_id)
        if not config or not config.get("setup_completed") or not config.get("enabled"):
            await interaction.response.send_message("❌ Modmail is currently disabled or not configured in that server.", ephemeral=True)
            return

        # Check blacklist
        if interaction.user.id in config.get("blacklist_users", []):
            await interaction.response.send_message("❌ You are blacklisted from opening tickets in this server.", ephemeral=True)
            return

        member = guild.get_member(interaction.user.id)
        if member:
            for role in member.roles:
                if role.id in config.get("blacklist_roles", []):
                    await interaction.response.send_message("❌ You have a role that is blacklisted from opening tickets in this server.", ephemeral=True)
                    return

        # Check max open tickets per user
        active_t = database.get_active_ticket_by_user(interaction.user.id, guild_id=guild_id)
        if active_t:
            await interaction.response.send_message("⚠️ You already have an active ticket open in this server. Please finish that conversation first.", ephemeral=True)
            return

        modal = OpenTicketModal(
            guild=guild,
            greeting_view=self.greeting_view,
            greeting_interaction=self.greeting_interaction,
            select_view=self.view,
            select_interaction=interaction,
            bot_instance=self.bot
        )
        await interaction.response.send_modal(modal)


class OpenTicketModal(ui.Modal, title="Open ModMail Ticket"):
    category = ui.TextInput(
        label="Department / Category",
        placeholder="e.g. General Support, Billing, Technical, Report...",
        default="General Support",
        required=False,
        max_length=50
    )
    reason = ui.TextInput(
        label="Reason for opening ticket",
        style=discord.TextStyle.paragraph,
        placeholder="Provide as much detail as possible...",
        required=True,
        max_length=1000
    )

    def __init__(self, guild: discord.Guild, greeting_view, greeting_interaction, bot_instance, select_view=None, select_interaction=None):
        super().__init__()
        self.guild = guild
        self.greeting_view = greeting_view
        self.greeting_interaction = greeting_interaction
        self.bot = bot_instance
        self.select_view = select_view
        self.select_interaction = select_interaction

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        config = database.get_guild_config(self.guild.id)
        category_id = config.get("category_id")
        staff_role_id = config.get("staff_role_id")

        category_channel = self.guild.get_channel(category_id)
        staff_role = self.guild.get_role(staff_role_id)

        if not category_channel or not staff_role:
            await interaction.followup.send("❌ Error: The server has an invalid configuration (missing Category or Staff Role). Please notify staff.", ephemeral=True)
            return

        # Define channel overwrites
        overwrites = {
            self.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            self.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True, manage_messages=True, embed_links=True, attach_files=True),
            staff_role: discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True, attach_files=True)
        }

        clean_name = re.sub(r'[^a-zA-Z0-9-]', '', interaction.user.name.lower()) or "user"
        channel_name = f"modmail-{clean_name}"

        try:
            ticket_channel = await self.guild.create_text_channel(
                name=channel_name,
                category=category_channel,
                overwrites=overwrites,
                topic=f"Modmail Ticket for {interaction.user} (ID: {interaction.user.id})"
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to create ticket channel: {str(e)}", ephemeral=True)
            return

        cat_tag = self.category.value.strip() or "General"
        ticket = database.create_ticket(
            guild_id=self.guild.id,
            channel_id=ticket_channel.id,
            user_id=interaction.user.id,
            user_name=str(interaction.user),
            subject=self.reason.value.strip(),
            priority="normal",
            category_tag=cat_tag,
            user_discriminator=interaction.user.discriminator
        )

        # Smart Auto-Assignment
        assigned_staff_member = None
        if config.get("auto_assign"):
            on_duty_ids = database.get_all_on_duty_staff(self.guild.id)
            staff_candidates = [m.id for m in staff_role.members]
            if on_duty_ids:
                staff_candidates = [uid for uid in staff_candidates if uid in on_duty_ids]

            best_staff_id = database.get_least_busy_staff(self.guild.id, staff_candidates)
            if best_staff_id:
                assigned_staff_member = self.guild.get_member(best_staff_id)
                if assigned_staff_member:
                    database.update_ticket(
                        ticket["ticket_id"],
                        claimed_by_id=assigned_staff_member.id,
                        claimed_by_name=assigned_staff_member.display_name,
                        status="claimed"
                    )
                    database.add_ticket_audit(
                        ticket["ticket_id"],
                        "ASSIGNED",
                        self.bot.user.id,
                        "System Auto-Router",
                        f"Automatically routed to {assigned_staff_member.display_name}"
                    )

        # Record initial reason as client message
        database.record_ticket_message(
            ticket["ticket_id"],
            interaction.id,
            interaction.user.id,
            str(interaction.user),
            "client",
            f"**Ticket Opened**\nCategory: {cat_tag}\nReason: {self.reason.value}"
        )

        # Send staff-facing panel with persistent action bar
        embed_staff = create_embed(
            title=f"📂 ModMail Ticket #{ticket['ticket_number']}",
            description=f"Ticket opened by **{interaction.user}** (`{interaction.user.id}`).",
            color=discord.Color.orange(),
            thumbnail=interaction.user.display_avatar.url,
            fields=[
                ("Client Info", f"Username: **{interaction.user}**\nAccount Created: <t:{int(interaction.user.created_at.timestamp())}:R>", True),
                ("Category", f"`{cat_tag}`", True),
                ("Assigned To", f"{assigned_staff_member.mention}" if assigned_staff_member else "Unassigned", True),
                ("Reason / Details", self.reason.value, False)
            ]
        )

        action_bar = PersistentTicketActionBar()
        await ticket_channel.send(embed=embed_staff, view=action_bar)
        await ticket_channel.send(f"<@&{staff_role.id}> a new support ticket has been opened.")

        # Update greeting views
        if self.greeting_view:
            for child in self.greeting_view.children:
                child.disabled = True
            try:
                await self.greeting_interaction.message.edit(view=self.greeting_view)
            except Exception:
                pass

        if self.select_view and self.select_interaction:
            for child in self.select_view.children:
                child.disabled = True
            try:
                await self.select_interaction.message.edit(view=self.select_view)
            except Exception:
                pass

        # Send DM confirmation
        embed_user = create_embed(
            title=f"✅ Support Ticket #{ticket['ticket_number']} Opened",
            description=(
                f"Your support ticket in **{self.guild.name}** is now active!\n"
                "Our support staff have been notified. Any direct messages or media you send here will be forwarded directly to your ticket channel.\n\n"
                f"**Category:** {cat_tag}\n"
                f"**Reason:** {self.reason.value}"
            ),
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed_user, ephemeral=True)


class DMGreetingView(ui.View):
    def __init__(self, common_guilds: List[discord.Guild], bot_instance=None):
        super().__init__(timeout=180)
        self.common_guilds = common_guilds
        self.bot = bot_instance

    @ui.button(label="Create Ticket", style=discord.ButtonStyle.success, custom_id="dm_create")
    async def create_ticket(self, interaction: discord.Interaction, button: ui.Button):
        if not self.common_guilds:
            await interaction.response.send_message("❌ No available servers found.", ephemeral=True)
            return

        if len(self.common_guilds) == 1:
            guild = self.common_guilds[0]
            config = database.get_guild_config(guild.id)
            if not config or not config.get("setup_completed") or not config.get("enabled"):
                await interaction.response.send_message("❌ Modmail is currently disabled or not configured in this server.", ephemeral=True)
                return

            if interaction.user.id in config.get("blacklist_users", []):
                await interaction.response.send_message("❌ You are blacklisted from opening tickets in this server.", ephemeral=True)
                return

            member = guild.get_member(interaction.user.id)
            if member:
                for r in member.roles:
                    if r.id in config.get("blacklist_roles", []):
                        await interaction.response.send_message("❌ You have a role that is blacklisted from opening tickets.", ephemeral=True)
                        return

            active_t = database.get_active_ticket_by_user(interaction.user.id, guild_id=guild.id)
            if active_t:
                await interaction.response.send_message("⚠️ You already have an active ticket in this server. Please send messages directly here to reply to it.", ephemeral=True)
                return

            modal = OpenTicketModal(
                guild=guild,
                greeting_view=self,
                greeting_interaction=interaction,
                bot_instance=self.bot
            )
            await interaction.response.send_modal(modal)
        else:
            select_view = ui.View(timeout=120)
            select_view.add_item(UserGuildSelect(self.common_guilds, greeting_view=self, greeting_interaction=interaction, bot_instance=self.bot))
            await interaction.response.send_message(
                "Please select the server you wish to open a ticket in:",
                view=select_view,
                ephemeral=True
            )

    @ui.button(label="Cancel", style=discord.ButtonStyle.danger, custom_id="dm_cancel")
    async def cancel_modmail(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message("❌ Modmail ticket request cancelled.", ephemeral=True)
        for child in self.children:
            child.disabled = True
        try:
            await interaction.message.edit(view=self)
        except Exception:
            pass
