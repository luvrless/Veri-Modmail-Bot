"""
Staff Productivity, Availability, Canned Snippets, and Leaderboard Commands.
Enterprise ModMail System.
"""

import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional, Literal

import database
import views

def is_staff_check(interaction_or_ctx) -> bool:
    guild = interaction_or_ctx.guild
    user = interaction_or_ctx.user if hasattr(interaction_or_ctx, "user") else interaction_or_ctx.author
    if not guild or not user:
        return False
    if user.guild_permissions.administrator or guild.owner_id == user.id:
        return True
    config = database.get_guild_config(guild.id)
    staff_role_id = config.get("staff_role_id")
    if staff_role_id and any(r.id == staff_role_id for r in user.roles):
        return True
    return False

class StaffCommands(commands.Cog, name="Staff Operations"):
    def __init__(self, bot):
        self.bot = bot

    # ----------------- DUTY & AVAILABILITY -----------------

    @app_commands.command(name="duty", description="Set your staff availability status (affects ticket auto-routing)")
    @app_commands.describe(status="Your availability status")
    async def slash_duty(self, interaction: discord.Interaction, status: Literal["on_duty", "off_duty", "on_break"]):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Staff permission required.", ephemeral=True)
            return

        database.set_staff_duty(interaction.guild.id, interaction.user.id, status)
        icons = {
            "on_duty": "🟢 On Duty (Available for ticket routing)",
            "off_duty": "🔴 Off Duty (Will not receive auto-assigned tickets)",
            "on_break": "🟡 On Break (Temporarily unavailable)"
        }
        await interaction.response.send_message(f"✅ Your availability status is now: **{icons[status]}**.", ephemeral=True)

    @app_commands.command(name="stafflist", description="View active staff, their duty status, and ticket workloads")
    async def slash_stafflist(self, interaction: discord.Interaction):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Staff permission required.", ephemeral=True)
            return

        config = database.get_guild_config(interaction.guild.id)
        staff_role_id = config.get("staff_role_id")
        if not staff_role_id:
            await interaction.response.send_message("⚠️ Staff role not configured for this server.", ephemeral=True)
            return

        staff_role = interaction.guild.get_role(staff_role_id)
        if not staff_role:
            await interaction.response.send_message("⚠️ Staff role not found.", ephemeral=True)
            return

        members = [m for m in staff_role.members if not m.bot]
        if not members:
            await interaction.response.send_message("No staff members found with the staff role.", ephemeral=True)
            return

        fields = []
        for m in members[:25]:
            duty = database.get_staff_duty(interaction.guild.id, m.id)
            active_load = database.get_staff_active_ticket_count(interaction.guild.id, m.id)
            duty_badge = "🟢 On Duty" if duty == "on_duty" else ("🟡 Break" if duty == "on_break" else "🔴 Off Duty")
            fields.append((
                f"{m.display_name}",
                f"Status: **{duty_badge}**\nActive Claimed Tickets: **{active_load}**",
                True
            ))

        embed = views.create_embed(
            title=f"🛡️ Staff Roster & Workload - {interaction.guild.name}",
            description=f"Showing **{len(fields)}** staff members.",
            color=discord.Color.blue(),
            fields=fields
        )
        await interaction.response.send_message(embed=embed)

    # ----------------- LEADERBOARD & INDIVIDUAL STATS -----------------

    @app_commands.command(name="leaderboard", description="View the staff support resolution and performance leaderboard")
    async def slash_leaderboard(self, interaction: discord.Interaction):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Staff permission required.", ephemeral=True)
            return

        lb_rows = database.get_staff_leaderboard(interaction.guild.id, limit=10)
        if not lb_rows:
            await interaction.response.send_message("📊 No staff resolution records available yet.", ephemeral=True)
            return

        fields = []
        medals = ["🥇", "🥈", "🥉"]
        for idx, row in enumerate(lb_rows):
            medal = medals[idx] if idx < 3 else f"#{idx+1}"
            claimed = row["total_claimed"] or 0
            closed = row["total_closed"] or 0
            resp = int(row["avg_resp"] or 0)
            rating = round(row["avg_rating"] or 0.0, 1)

            resp_str = f"{resp // 60}m {resp % 60}s" if resp else "N/A"
            rating_str = f"⭐ {rating}/5" if rating > 0 else "N/A"

            staff_name = row["claimed_by_name"] or f"Staff {row['claimed_by_id']}"
            fields.append((
                f"{medal} {staff_name}",
                f"Resolved: **{closed}** | Claimed: **{claimed}** | Avg Resp: **{resp_str}** | Rating: **{rating_str}**",
                False
            ))

        embed = views.create_embed(
            title=f"🏆 Staff Support Leaderboard - {interaction.guild.name}",
            description="Top performing support agents based on resolved cases and ratings:",
            color=discord.Color.gold(),
            fields=fields
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="staffstats", description="View individual performance metrics and response times")
    @app_commands.describe(staff="Staff member to inspect (defaults to you)")
    async def slash_staffstats(self, interaction: discord.Interaction, staff: Optional[discord.Member] = None):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Staff permission required.", ephemeral=True)
            return

        target = staff or interaction.user
        s = database.get_staff_stats(interaction.guild.id, target.id)

        resp_sec = int(s.get("avg_resp") or 0)
        res_sec = int(s.get("avg_res") or 0)

        resp_str = f"{resp_sec // 60}m {resp_sec % 60}s" if resp_sec else "N/A"
        res_str = f"{res_sec // 3600}h {(res_sec % 3600) // 60}m" if res_sec else "N/A"
        rating = round(s.get("avg_rating") or 0.0, 1)
        rating_str = f"⭐ {rating}/5 ({s.get('rated_count', 0)} reviews)" if rating > 0 else "No ratings yet"

        fields = [
            ("Total Claimed", str(s.get("total_claimed", 0)), True),
            ("Total Resolved", str(s.get("total_closed", 0)), True),
            ("Customer Rating", rating_str, True),
            ("Avg First Response", resp_str, True),
            ("Avg Resolution Time", res_str, True),
            ("Current Duty", database.get_staff_duty(interaction.guild.id, target.id).upper(), True)
        ]

        embed = views.create_embed(
            title=f"📊 Staff Performance - {target.display_name}",
            description="Comprehensive analytics profile:",
            color=discord.Color.blurple(),
            thumbnail=target.display_avatar.url,
            fields=fields
        )
        await interaction.response.send_message(embed=embed)

    # ----------------- CANNED SNIPPETS / FAQS -----------------

    snippet_group = app_commands.Group(name="snippet", description="Manage and send canned FAQ templates")

    @snippet_group.command(name="add", description="Save a new canned response snippet")
    @app_commands.describe(name="Shortcut name (e.g. refund, rules, verify)", content="The full canned response text")
    async def snippet_add(self, interaction: discord.Interaction, name: str, content: str):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
            return

        clean_name = name.strip().lower()
        database.add_snippet(interaction.guild.id, clean_name, content.strip(), interaction.user.id)
        await interaction.response.send_message(f"✅ Snippet `{clean_name}` saved successfully! Use `/snippet send {clean_name}` to use it.")

    @snippet_group.command(name="send", description="Send a saved snippet into the ticket and deliver to the client")
    @app_commands.describe(name="Snippet shortcut name")
    async def snippet_send(self, interaction: discord.Interaction, name: str):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
            return

        clean_name = name.strip().lower()
        snip = database.get_snippet(interaction.guild.id, clean_name)
        if not snip:
            await interaction.response.send_message(f"❌ Snippet `{clean_name}` was not found. Use `/snippet list`.", ephemeral=True)
            return

        ticket = database.get_ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("❌ Active ticket not found.", ephemeral=True)
            return

        user_id = ticket["user_id"]
        user = interaction.guild.get_member(user_id)
        if not user:
            try:
                user = await self.bot.fetch_user(user_id)
            except Exception:
                pass

        if not user:
            await interaction.response.send_message("❌ Client user not found.", ephemeral=True)
            return

        # Send to user DMs
        embed_reply = views.create_embed(
            title=f"✉️ Reply from {interaction.guild.name} Support",
            description=snip["content"],
            color=discord.Color.blue()
        )
        embed_reply.set_author(name=f"{interaction.user.display_name} (Support)", icon_url=interaction.user.display_avatar.url)

        try:
            await user.send(embed=embed_reply)
        except discord.Forbidden:
            await interaction.response.send_message("❌ Error: Client has DMs disabled.", ephemeral=True)
            return

        database.record_ticket_message(
            ticket["ticket_id"],
            interaction.id,
            interaction.user.id,
            interaction.user.display_name,
            "staff",
            snip["content"]
        )

        conf_embed = views.create_embed(
            title=f"✉️ Snippet `{clean_name}` Sent",
            description=snip["content"],
            color=discord.Color.green()
        )
        conf_embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=conf_embed)

    @snippet_group.command(name="list", description="List all available canned snippets for this server")
    async def snippet_list(self, interaction: discord.Interaction):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
            return

        snippets = database.list_snippets(interaction.guild.id)
        if not snippets:
            await interaction.response.send_message("📁 No snippets created for this server yet. Add one with `/snippet add`.", ephemeral=True)
            return

        fields = []
        for s in snippets[:25]:
            snippet_preview = s["content"][:100] + ("..." if len(s["content"]) > 100 else "")
            fields.append((f"`{s['name']}`", snippet_preview, False))

        embed = views.create_embed(
            title=f"📁 Canned Snippets - {interaction.guild.name}",
            description=f"Total snippets available: **{len(snippets)}**",
            color=discord.Color.blurple(),
            fields=fields
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @snippet_group.command(name="delete", description="Delete a saved canned snippet")
    @app_commands.describe(name="Snippet shortcut name to delete")
    async def snippet_delete(self, interaction: discord.Interaction, name: str):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
            return

        clean_name = name.strip().lower()
        ok = database.delete_snippet(interaction.guild.id, clean_name)
        if ok:
            await interaction.response.send_message(f"✅ Snippet `{clean_name}` was deleted.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Snippet `{clean_name}` was not found.", ephemeral=True)


async def setup(bot):
    await bot.add_cog(StaffCommands(bot))
