"""
Administrative, Diagnostics, Analytics, and Core Configuration Commands.
Maintains 100% backward compatibility with /system, /quit, /on, /off, /bl, /ubl, /cmds
while adding enterprise management capabilities.
"""

import discord
from discord import app_commands
from discord.ext import commands
import datetime
import time
import os
try:
    import psutil
except ImportError:
    psutil = None
from typing import Optional, Literal

import database
import views

START_TIME = time.time()

def is_admin_check(interaction_or_ctx) -> bool:
    guild = interaction_or_ctx.guild
    user = interaction_or_ctx.user if hasattr(interaction_or_ctx, "user") else interaction_or_ctx.author
    if not guild or not user:
        return False
    return user.guild_permissions.administrator or guild.owner_id == user.id

class AdminCommands(commands.Cog, name="Administration & Analytics"):
    def __init__(self, bot):
        self.bot = bot

    # ----------------- SYSTEM SETUP DASHBOARD -----------------

    @app_commands.command(name="system", description="Set up or edit the ModMail system settings for this server")
    @app_commands.default_permissions(administrator=True)
    async def slash_system(self, interaction: discord.Interaction):
        if not is_admin_check(interaction):
            await interaction.response.send_message("❌ Only server administrators can configure the ModMail system.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True, manage_messages=True),
            interaction.user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }

        try:
            setup_channel = await guild.create_text_channel(
                name=f"modmail-setup-{interaction.user.name.lower()}",
                overwrites=overwrites,
                topic="Setup channel for Enterprise ModMail bot"
            )
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to create setup channel: {str(e)}", ephemeral=True)
            return

        await interaction.followup.send(f"⚙️ Setup channel created: <#{setup_channel.id}>. Please head there to configure the bot.", ephemeral=True)

        dashboard_view = views.SetupDashboardView(guild.id, setup_channel, interaction.user, self.bot)
        await dashboard_view.update_dashboard(setup_channel)

    # ----------------- QUIT / CLOSE PRESERVED -----------------

    @app_commands.command(name="quit", description="Close the current modmail ticket and save the transcript")
    async def slash_quit(self, interaction: discord.Interaction):
        ticket = database.get_ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("❌ This command can only be used in active modmail ticket channels.", ephemeral=True)
            return

        await interaction.response.defer()
        await views.execute_ticket_close(
            interaction.channel,
            interaction.guild,
            interaction.user,
            reason="Ticket closed via /quit"
        )

    # ----------------- ON / OFF PRESERVED -----------------

    @app_commands.command(name="on", description="Enable the ModMail system for this server")
    @app_commands.default_permissions(administrator=True)
    async def slash_on(self, interaction: discord.Interaction):
        config = database.get_guild_config(interaction.guild.id)
        if not config.get("setup_completed"):
            await interaction.response.send_message("❌ Please complete the system configuration first using `/system`.", ephemeral=True)
            return

        config["enabled"] = True
        database.save_guild_config(interaction.guild.id, config)

        embed = views.create_embed(
            title="🚦 ModMail Status",
            description="🟢 **Modmail system has been enabled.** Users can now contact staff via DM.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="off", description="Disable the ModMail system for this server")
    @app_commands.default_permissions(administrator=True)
    async def slash_off(self, interaction: discord.Interaction):
        config = database.get_guild_config(interaction.guild.id)
        config["enabled"] = False
        database.save_guild_config(interaction.guild.id, config)

        embed = views.create_embed(
            title="🚦 ModMail Status",
            description="🔴 **Modmail system has been disabled.** DM support is now offline.",
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)

    # ----------------- BLACKLIST / UNBLACKLIST PRESERVED -----------------

    @app_commands.command(name="bl", description="Blacklist a user or role from opening modmail tickets")
    @app_commands.describe(
        target_user="User to blacklist",
        target_role="Role to blacklist",
        target_id="Specific ID to blacklist (useful if user/role is not in server)"
    )
    @app_commands.default_permissions(administrator=True)
    async def slash_blacklist(self, interaction: discord.Interaction, target_user: discord.User = None, target_role: discord.Role = None, target_id: str = None):
        config = database.get_guild_config(interaction.guild.id)

        if not target_user and not target_role and not target_id:
            await interaction.response.send_message("❌ You must specify a user, a role, or an ID to blacklist.", ephemeral=True)
            return

        added = []
        if target_user:
            if target_user.id not in config["blacklist_users"]:
                config["blacklist_users"].append(target_user.id)
                added.append(f"User: **{target_user}** (ID: `{target_user.id}`)")

        if target_role:
            if target_role.id not in config["blacklist_roles"]:
                config["blacklist_roles"].append(target_role.id)
                added.append(f"Role: **{target_role.name}** (ID: `{target_role.id}`)")

        if target_id:
            try:
                val_id = int(target_id)
                item = interaction.guild.get_role(val_id)
                if item:
                    if val_id not in config["blacklist_roles"]:
                        config["blacklist_roles"].append(val_id)
                        added.append(f"Role ID: **{item.name}** (ID: `{val_id}`)")
                else:
                    if val_id not in config["blacklist_users"]:
                        config["blacklist_users"].append(val_id)
                        added.append(f"User ID: `{val_id}`")
            except ValueError:
                await interaction.response.send_message("❌ Provided ID must be a numeric string.", ephemeral=True)
                return

        if not added:
            await interaction.response.send_message("⚠️ The specified targets are already blacklisted.", ephemeral=True)
            return

        database.save_guild_config(interaction.guild.id, config)

        embed = views.create_embed(
            title="🚫 Blacklist Updated",
            description="The following targets have been blacklisted:\n" + "\n".join(added),
            color=discord.Color.red()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="ubl", description="Unblacklist a user or role from opening modmail tickets")
    @app_commands.describe(
        target_user="User to unblacklist",
        target_role="Role to unblacklist",
        target_id="Specific ID to unblacklist"
    )
    @app_commands.default_permissions(administrator=True)
    async def slash_unblacklist(self, interaction: discord.Interaction, target_user: discord.User = None, target_role: discord.Role = None, target_id: str = None):
        config = database.get_guild_config(interaction.guild.id)

        if not target_user and not target_role and not target_id:
            await interaction.response.send_message("❌ You must specify a user, a role, or an ID to unblacklist.", ephemeral=True)
            return

        removed = []
        if target_user:
            if target_user.id in config["blacklist_users"]:
                config["blacklist_users"].remove(target_user.id)
                removed.append(f"User: **{target_user}** (ID: `{target_user.id}`)")

        if target_role:
            if target_role.id in config["blacklist_roles"]:
                config["blacklist_roles"].remove(target_role.id)
                removed.append(f"Role: **{target_role.name}** (ID: `{target_role.id}`)")

        if target_id:
            try:
                val_id = int(target_id)
                if val_id in config["blacklist_users"]:
                    config["blacklist_users"].remove(val_id)
                    removed.append(f"User ID: `{val_id}`")
                elif val_id in config["blacklist_roles"]:
                    config["blacklist_roles"].remove(val_id)
                    role_obj = interaction.guild.get_role(val_id)
                    role_name = role_obj.name if role_obj else "Unknown"
                    removed.append(f"Role ID: **{role_name}** (ID: `{val_id}`)")
            except ValueError:
                await interaction.response.send_message("❌ Provided ID must be a numeric string.", ephemeral=True)
                return

        if not removed:
            await interaction.response.send_message("⚠️ The specified targets were not found in the blacklist.", ephemeral=True)
            return

        database.save_guild_config(interaction.guild.id, config)

        embed = views.create_embed(
            title="🔓 Blacklist Updated",
            description="The following targets have been unblacklisted:\n" + "\n".join(removed),
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="blacklist_list", description="List all blacklisted users and roles in this server")
    @app_commands.default_permissions(administrator=True)
    async def slash_blacklist_list(self, interaction: discord.Interaction):
        config = database.get_guild_config(interaction.guild.id)
        bl_users = config.get("blacklist_users", [])
        bl_roles = config.get("blacklist_roles", [])

        if not bl_users and not bl_roles:
            await interaction.response.send_message("✅ No users or roles are currently blacklisted.", ephemeral=True)
            return

        user_lines = [f"• <@{uid}> (`{uid}`)" for uid in bl_users] or ["None"]
        role_lines = [f"• <@&{rid}> (`{rid}`)" for rid in bl_roles] or ["None"]

        fields = [
            (f"Blacklisted Users ({len(bl_users)})", "\n".join(user_lines[:15]), False),
            (f"Blacklisted Roles ({len(bl_roles)})", "\n".join(role_lines[:15]), False)
        ]

        embed = views.create_embed(
            title=f"🚫 ModMail Blacklist - {interaction.guild.name}",
            description="Users and roles barred from opening tickets:",
            color=discord.Color.red(),
            fields=fields
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ----------------- SERVER CONFIG & STATS -----------------

    @app_commands.command(name="config", description="View the current ModMail configuration for this server")
    @app_commands.default_permissions(administrator=True)
    async def slash_config(self, interaction: discord.Interaction):
        config = database.get_guild_config(interaction.guild.id)

        cat_str = f"<#{config.get('category_id')}>" if config.get("category_id") else "❌ Not set"
        role_str = f"<@&{config.get('staff_role_id')}>" if config.get("staff_role_id") else "❌ Not set"
        logs_str = f"<#{config.get('logs_channel_id')}>" if config.get("logs_channel_id") else "❌ Not set"

        fields = [
            ("Category", cat_str, True),
            ("Staff Role", role_str, True),
            ("Logs Channel", logs_str, True),
            ("System Status", "🟢 Enabled" if config.get("enabled") else "🔴 Disabled", True),
            ("Auto-Assign", "🟢 Enabled" if config.get("auto_assign") else "⚪ Disabled", True),
            ("CSAT Surveys", "🟢 Enabled" if config.get("survey_enabled", True) else "⚪ Disabled", True),
            ("Inactivity Warning", f"`{config.get('warning_hours', 24)}h`", True),
            ("Inactivity Auto-Close", f"`{config.get('inactivity_hours', 48)}h`", True),
            ("Blacklisted Count", f"{len(config.get('blacklist_users', []))} Users / {len(config.get('blacklist_roles', []))} Roles", True)
        ]

        embed = views.create_embed(
            title=f"⚙️ ModMail Configuration - {interaction.guild.name}",
            description="Use `/system` to launch the interactive configuration wizard.",
            color=discord.Color.purple(),
            fields=fields
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="stats", description="View comprehensive server support analytics and metrics")
    async def slash_stats(self, interaction: discord.Interaction):
        st = database.get_guild_stats(interaction.guild.id)

        resp_sec = st["avg_first_response_sec"]
        res_sec = st["avg_resolution_sec"]
        resp_fmt = f"{resp_sec // 60}m {resp_sec % 60}s" if resp_sec else "N/A"
        res_fmt = f"{res_sec // 3600}h {(res_sec % 3600) // 60}m" if res_sec else "N/A"

        rating_stars = f"⭐ {st['avg_rating']}/5.0" if st['rated_count'] > 0 else "No ratings yet"

        fields = [
            ("Total Tickets", str(st["total_tickets"]), True),
            ("Currently Open", str(st["open_tickets"]), True),
            ("Closed Cases", str(st["closed_tickets"]), True),
            ("Avg First Response", resp_fmt, True),
            ("Avg Resolution Time", res_fmt, True),
            ("Customer Satisfaction", f"{rating_stars} ({st['rated_count']} reviews)", True)
        ]

        embed = views.create_embed(
            title=f"📈 Support Platform Analytics - {interaction.guild.name}",
            description="Server-wide support performance metrics:",
            color=discord.Color.blurple(),
            fields=fields
        )
        await interaction.response.send_message(embed=embed)

    # ----------------- CASE LOOKUP & SEARCH -----------------

    @app_commands.command(name="userinfo", description="Lookup previous support cases and ticket history for a user")
    @app_commands.describe(user="User to search history for")
    async def slash_userinfo(self, interaction: discord.Interaction, user: discord.User):
        history = database.get_user_history(interaction.guild.id, user.id)
        config = database.get_guild_config(interaction.guild.id)
        is_bl = user.id in config.get("blacklist_users", [])

        if not history:
            bl_text = "🚫 Blacklisted" if is_bl else "🟢 Clear"
            embed = views.create_embed(
                title=f"👤 Client History - {user}",
                description=f"This user has no past ticket records in **{interaction.guild.name}**.\nBlacklist status: **{bl_text}**.",
                color=discord.Color.blue(),
                thumbnail=user.display_avatar.url
            )
            await interaction.response.send_message(embed=embed)
            return

        fields = []
        for h in history[:10]:
            t_num = h.get("ticket_number") or h["ticket_id"]
            status = h["status"].upper()
            opened = h["opened_at"][:10]
            rating = f"⭐ {h['rating']}/5" if h.get("rating") else "Unrated"
            fields.append((
                f"Ticket #{t_num} [{status}] - {opened}",
                f"Priority: `{h.get('priority', 'normal')}` | Rating: {rating}\nReason: {h.get('close_reason') or 'None'}",
                False
            ))

        bl_text = "🚫 **BLACKLISTED**" if is_bl else "🟢 Normal"
        embed = views.create_embed(
            title=f"👤 Client History - {user}",
            description=f"Showing past cases for **{user}** (`{user.id}`).\nAccount Standing: {bl_text} | Total Tickets: **{len(history)}**",
            color=discord.Color.blurple(),
            thumbnail=user.display_avatar.url,
            fields=fields
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="search", description="Search past ticket records by client name, ID, or subject keywords")
    @app_commands.describe(query="Keyword, user ID, or client name to search for")
    async def slash_search(self, interaction: discord.Interaction, query: str):
        results = database.search_tickets(interaction.guild.id, query.strip())
        if not results:
            await interaction.response.send_message(f"🔍 No tickets matching `{query}` were found.", ephemeral=True)
            return

        fields = []
        for r in results[:10]:
            t_num = r.get("ticket_number") or r["ticket_id"]
            fields.append((
                f"Ticket #{t_num} - {r['user_name']} [{r['status'].upper()}]",
                f"Subject: {r.get('subject') or 'No subject'}\nOpened: {r['opened_at'][:10]} | Priority: `{r.get('priority')}`",
                False
            ))

        embed = views.create_embed(
            title=f"🔍 Ticket Search Results: \"{query}\"",
            description=f"Found **{len(results)}** matching case(s):",
            color=discord.Color.blue(),
            fields=fields
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ----------------- DIAGNOSTICS & SYSTEM HEALTH -----------------

    @app_commands.command(name="diagnostics", description="Check bot health, memory, uptime, latency, and database status")
    async def slash_diagnostics(self, interaction: discord.Interaction):
        uptime_sec = int(time.time() - START_TIME)
        days = uptime_sec // 86400
        hours = (uptime_sec % 86400) // 3600
        minutes = (uptime_sec % 3600) // 60
        seconds = uptime_sec % 60
        uptime_str = f"{days}d {hours}h {minutes}m {seconds}s"

        # Memory usage
        try:
            process = psutil.Process()
            mem_info = process.memory_info()
            mem_mb = round(mem_info.rss / (1024 * 1024), 2)
        except Exception:
            mem_mb = "N/A"

        # Latency
        latency_ms = round(self.bot.latency * 1000, 1)

        # Active tickets count
        active_tickets = database.get_all_active_tickets()
        guild_active = [t for t in active_tickets if t["guild_id"] == interaction.guild.id]

        fields = [
            ("Uptime", uptime_str, True),
            ("WebSocket Latency", f"`{latency_ms} ms`", True),
            ("RAM Usage", f"`{mem_mb} MB`", True),
            ("Total Servers", f"`{len(self.bot.guilds)}`", True),
            ("Global Open Tickets", f"`{len(active_tickets)}`", True),
            ("This Server Open Tickets", f"`{len(guild_active)}`", True),
            ("Database Engine", "SQLite3 WAL Mode (Operational)", True),
            ("Persistent Views", "Active & Registered", True)
        ]

        embed = views.create_embed(
            title="🩺 ModMail System Diagnostics",
            description="System operational telemetry and resource consumption:",
            color=discord.Color.green(),
            fields=fields
        )
        await interaction.response.send_message(embed=embed)

    # ----------------- CMDS / HELP PRESERVED & EXPANDED -----------------

    @app_commands.command(name="cmds", description="List all available Enterprise ModMail commands")
    async def slash_cmds(self, interaction: discord.Interaction):
        categories = {
            "🛡️ Core Administration": [
                ("`/system`", "Interactive server setup dashboard wizard."),
                ("`/on`", "Enable modmail system for this server."),
                ("`/off`", "Disable modmail system for this server."),
                ("`/config`", "View current server settings and thresholds."),
                ("`/diagnostics`", "Bot health, uptime, latency, and memory.")
            ],
            "🚫 Blacklist Controls": [
                ("`/bl [user] [role] [id]`", "Blacklist target from opening tickets."),
                ("`/ubl [user] [role] [id]`", "Remove target from blacklist."),
                ("`/blacklist_list`", "List all blacklisted users and roles.")
            ],
            "📂 Ticket & Case Management": [
                ("`/claim`", "Claim the current ticket to handle it."),
                ("`/unclaim`", "Release your claim on the current ticket."),
                ("`/assign <staff>`", "Assign ticket to specific staff member."),
                ("`/transfer <staff> [note]`", "Transfer ticket with handover notes."),
                ("`/priority <level>`", "Set priority: Low, Normal, High, Urgent."),
                ("`/ticketinfo`", "View ticket details, metadata, and lifecycle."),
                ("`/rename <name>`", "Rename the ticket channel safely."),
                ("`/topic <topic>`", "Update channel topic."),
                ("`/tag <add|remove|list>`", "Add, remove, or view ticket tags."),
                ("`/add <user>`", "Add user/staff to ticket channel."),
                ("`/remove <user>`", "Remove user/staff from ticket channel."),
                ("`/escalate [role]`", "Escalate ticket to senior management."),
                ("`/close [reason] [delay]`", "Close ticket with countdown & reason."),
                ("`/reopen <ticket_id>`", "Reopen a closed ticket in a new channel."),
                ("`/quit`", "Close ticket and save transcripts.")
            ],
            "📝 Communication & Notes": [
                ("`/reply <message>` (or `!r`)", "Send official staff reply to client."),
                ("`/anonreply <message>` (or `!ar`)", "Send anonymous staff reply to client."),
                ("`/note <content>` (or `!n`)", "Add private staff note (hidden from client)."),
                ("`/notes`", "View all internal notes for this ticket."),
                ("`/snippet send <name>`", "Send a canned FAQ snippet to client."),
                ("`/snippet add <name>`", "Create a new canned response snippet.")
            ],
            "📊 Analytics & Staff Performance": [
                ("`/stats`", "Server-wide ticket analytics and CSAT rating."),
                ("`/leaderboard`", "Staff resolution and performance rankings."),
                ("`/staffstats [staff]`", "Individual staff metrics and response times."),
                ("`/userinfo <user>`", "Client past ticket history and standing."),
                ("`/search <query>`", "Search tickets by user, ID, or keywords."),
                ("`/duty <status>`", "Set duty status: on_duty, off_duty, on_break."),
                ("`/stafflist`", "View currently on-duty staff and workloads.")
            ]
        }

        embeds = []
        for cat_name, cmd_list in categories.items():
            fields = [(name, desc, False) for name, desc in cmd_list]
            e = views.create_embed(
                title=f"📋 ModMail Command Suite - {cat_name}",
                description="Comprehensive list of Enterprise ModMail commands:",
                color=discord.Color.blurple(),
                fields=fields
            )
            embeds.append(e)

        if len(embeds) > 1:
            view = views.PaginationView(embeds, interaction.user.id)
            await interaction.response.send_message(embed=embeds[0], view=view)
        else:
            await interaction.response.send_message(embed=embeds[0])


async def setup(bot):
    await bot.add_cog(AdminCommands(bot))
