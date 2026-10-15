"""
Ticket Management Commands for Enterprise ModMail System.
Provides 18+ high-utility ticket and case management commands.
Supports both slash commands and prefix commands for fast workflow in ticket channels.
"""

import discord
from discord import app_commands
from discord.ext import commands
import datetime
import asyncio
from typing import Optional, Literal

import database
import views
import transcripts

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

class TicketCommands(commands.Cog, name="Ticket Management"):
    def __init__(self, bot):
        self.bot = bot

    # ----------------- CLAIM / UNCLAIM / ASSIGN / TRANSFER -----------------

    @app_commands.command(name="claim", description="Claim the current ticket to indicate you are handling it")
    async def slash_claim(self, interaction: discord.Interaction):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ You must be a staff member to claim tickets.", ephemeral=True)
            return
        await views.handle_claim_interaction(interaction)

    @app_commands.command(name="unclaim", description="Release your claim on the current ticket")
    async def slash_unclaim(self, interaction: discord.Interaction):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ You must be a staff member to unclaim tickets.", ephemeral=True)
            return
        await views.handle_unclaim_interaction(interaction)

    @app_commands.command(name="assign", description="Assign the current ticket to a specific staff member")
    @app_commands.describe(staff="The staff member to assign this ticket to")
    async def slash_assign(self, interaction: discord.Interaction, staff: discord.Member):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
            return

        ticket = database.get_ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("❌ Active ticket not found.", ephemeral=True)
            return

        database.update_ticket(
            ticket["ticket_id"],
            claimed_by_id=staff.id,
            claimed_by_name=staff.display_name,
            status="claimed"
        )
        database.add_ticket_audit(
            ticket["ticket_id"],
            "ASSIGNED",
            interaction.user.id,
            interaction.user.display_name,
            f"Assigned ticket to {staff.display_name}"
        )

        try:
            current_topic = interaction.channel.topic or ""
            base_topic = current_topic.split(" | Claimed:")[0]
            new_topic = f"{base_topic} | Claimed: {staff.display_name} | Priority: {str(ticket.get('priority', 'normal')).upper()}"
            await interaction.channel.edit(topic=new_topic[:1024])
        except Exception:
            pass

        embed = views.create_embed(
            title="📌 Ticket Assigned",
            description=f"Ticket #{ticket.get('ticket_number', 1)} has been assigned to {staff.mention} by {interaction.user.mention}.",
            color=discord.Color.green()
        )
        await interaction.response.send_message(content=f"{staff.mention}", embed=embed)

    @app_commands.command(name="transfer", description="Transfer ticket ownership to another staff member with handover notes")
    @app_commands.describe(staff="Target staff member", handover_note="Brief context or handover note for the new assignee")
    async def slash_transfer(self, interaction: discord.Interaction, staff: discord.Member, handover_note: Optional[str] = None):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
            return

        ticket = database.get_ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("❌ Active ticket not found.", ephemeral=True)
            return

        database.update_ticket(
            ticket["ticket_id"],
            claimed_by_id=staff.id,
            claimed_by_name=staff.display_name,
            status="claimed"
        )
        details = f"Transferred to {staff.display_name}"
        if handover_note:
            details += f". Handover note: {handover_note}"
            database.add_ticket_note(
                ticket["ticket_id"],
                interaction.user.id,
                interaction.user.display_name,
                f"[HANDOVER to {staff.display_name}] {handover_note}"
            )

        database.add_ticket_audit(
            ticket["ticket_id"],
            "TRANSFERRED",
            interaction.user.id,
            interaction.user.display_name,
            details
        )

        embed = views.create_embed(
            title="🔄 Ticket Transferred",
            description=f"Ticket was transferred from {interaction.user.mention} to {staff.mention}.",
            color=discord.Color.blue(),
            fields=[("Handover Note", handover_note or "No handover note attached", False)]
        )
        await interaction.response.send_message(content=f"{staff.mention}", embed=embed)

    # ----------------- PRIORITY & METADATA -----------------

    @app_commands.command(name="priority", description="Set ticket priority level")
    @app_commands.describe(level="The priority level for this ticket")
    async def slash_priority(self, interaction: discord.Interaction, level: Literal["low", "normal", "high", "urgent"]):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
            return

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
            f"Priority changed to {level.upper()}"
        )

        # Update topic
        try:
            current_topic = interaction.channel.topic or ""
            base = current_topic.split(" | Priority:")[0]
            new_topic = f"{base} | Priority: {level.upper()}"
            await interaction.channel.edit(topic=new_topic[:1024])
        except Exception:
            pass

        embed = views.create_embed(
            title="⚡ Priority Updated",
            description=f"Ticket priority set to **{level.upper()}** by {interaction.user.mention}.",
            color=discord.Color.gold() if level in ["high", "urgent"] else discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="ticketinfo", description="View detailed ticket information, metadata, and lifecycle status")
    async def slash_ticket_info(self, interaction: discord.Interaction):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
            return

        ticket = database.get_ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("❌ Active ticket not found.", ephemeral=True)
            return

        tags = database.get_ticket_tags(ticket["ticket_id"])
        notes = database.get_ticket_notes(ticket["ticket_id"])
        messages = database.get_ticket_messages(ticket["ticket_id"])

        opened_dt = ticket.get("opened_at", "")[:19].replace("T", " ")
        fields = [
            ("Ticket Number", f"#{ticket.get('ticket_number', 1)} (DB ID: `{ticket['ticket_id']}`)", True),
            ("Status", f"`{str(ticket.get('status')).upper()}`", True),
            ("Priority", f"`{str(ticket.get('priority')).upper()}`", True),
            ("Client", f"<@{ticket['user_id']}> (`{ticket['user_id']}`)", True),
            ("Claimed By", f"<@{ticket['claimed_by_id']}>" if ticket.get("claimed_by_id") else "Unclaimed", True),
            ("Category", f"`{ticket.get('category_tag', 'General')}`", True),
            ("Tags", ", ".join([f"`{t}`" for t in tags]) if tags else "None", True),
            ("Internal Notes", f"{len(notes)} note(s)", True),
            ("Messages Recorded", f"{len(messages)} message(s)", True),
            ("Opened At", f"{opened_dt} UTC", False),
            ("Initial Subject / Reason", ticket.get("subject") or "None", False)
        ]

        embed = views.create_embed(
            title=f"📋 Ticket #{ticket.get('ticket_number', 1)} Overview",
            description="Detailed operational statistics for this ticket.",
            color=discord.Color.blurple(),
            fields=fields
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="rename", description="Rename the ticket channel safely")
    @app_commands.describe(new_name="New channel name (auto-formatted for Discord)")
    async def slash_rename(self, interaction: discord.Interaction, new_name: str):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
            return

        ticket = database.get_ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("❌ Active ticket not found.", ephemeral=True)
            return

        formatted = new_name.lower().replace(" ", "-")[:100]
        try:
            await interaction.channel.edit(name=formatted)
            database.add_ticket_audit(
                ticket["ticket_id"],
                "RENAMED",
                interaction.user.id,
                interaction.user.display_name,
                f"Channel renamed to {formatted}"
            )
            await interaction.response.send_message(f"✅ Ticket channel renamed to `#{formatted}`.")
        except discord.HTTPException as e:
            await interaction.response.send_message(f"⚠️ Failed to rename channel: {e.text} (Rate limit: 2 renames per 10m).", ephemeral=True)

    @app_commands.command(name="topic", description="Update the ticket topic")
    @app_commands.describe(new_topic="New channel topic")
    async def slash_topic(self, interaction: discord.Interaction, new_topic: str):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
            return

        ticket = database.get_ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("❌ Active ticket not found.", ephemeral=True)
            return

        try:
            await interaction.channel.edit(topic=new_topic[:1024])
            database.add_ticket_audit(
                ticket["ticket_id"],
                "TOPIC_UPDATED",
                interaction.user.id,
                interaction.user.display_name,
                f"Topic updated to: {new_topic[:100]}"
            )
            await interaction.response.send_message(f"✅ Ticket topic updated.")
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to update topic: {e}", ephemeral=True)

    # ----------------- TAGS -----------------

    @app_commands.command(name="tag", description="Add, remove, or list tags for the current ticket")
    @app_commands.describe(action="Action to perform", tag_name="Tag name (e.g. billing, bug, appeal)")
    async def slash_tag(self, interaction: discord.Interaction, action: Literal["add", "remove", "list"], tag_name: Optional[str] = None):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
            return

        ticket = database.get_ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("❌ Active ticket not found.", ephemeral=True)
            return

        if action == "list":
            tags = database.get_ticket_tags(ticket["ticket_id"])
            tag_str = ", ".join([f"`{t}`" for t in tags]) if tags else "No tags on this ticket."
            await interaction.response.send_message(f"🏷️ **Ticket Tags:** {tag_str}", ephemeral=True)
            return

        if not tag_name:
            await interaction.response.send_message("❌ Please specify a tag name.", ephemeral=True)
            return

        clean_tag = tag_name.strip().lower()
        if action == "add":
            ok = database.add_ticket_tag(ticket["ticket_id"], clean_tag)
            if ok:
                database.add_ticket_audit(
                    ticket["ticket_id"],
                    "TAG_ADDED",
                    interaction.user.id,
                    interaction.user.display_name,
                    f"Tag added: {clean_tag}"
                )
                await interaction.response.send_message(f"✅ Added tag `{clean_tag}` to ticket.")
            else:
                await interaction.response.send_message(f"⚠️ Tag `{clean_tag}` already exists on this ticket.", ephemeral=True)
        elif action == "remove":
            ok = database.remove_ticket_tag(ticket["ticket_id"], clean_tag)
            if ok:
                database.add_ticket_audit(
                    ticket["ticket_id"],
                    "TAG_REMOVED",
                    interaction.user.id,
                    interaction.user.display_name,
                    f"Tag removed: {clean_tag}"
                )
                await interaction.response.send_message(f"✅ Removed tag `{clean_tag}` from ticket.")
            else:
                await interaction.response.send_message(f"⚠️ Tag `{clean_tag}` was not found on this ticket.", ephemeral=True)

    # ----------------- INTERNAL NOTES -----------------

    @app_commands.command(name="note", description="Add an internal staff note to this ticket (private from client)")
    @app_commands.describe(content="Note content")
    async def slash_note(self, interaction: discord.Interaction, content: str):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
            return

        ticket = database.get_ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("❌ Active ticket not found.", ephemeral=True)
            return

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

        embed = views.create_embed(
            title="📝 Internal Note Added",
            description=content,
            color=discord.Color.gold()
        )
        embed.set_author(name=f"Note by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="notes", description="View all internal staff notes for this ticket")
    async def slash_notes(self, interaction: discord.Interaction):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
            return

        ticket = database.get_ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("❌ Active ticket not found.", ephemeral=True)
            return

        notes = database.get_ticket_notes(ticket["ticket_id"])
        if not notes:
            await interaction.response.send_message("📝 There are no internal notes recorded for this ticket.", ephemeral=True)
            return

        fields = []
        for n in notes:
            ts = n.get("created_at", "")[:19].replace("T", " ")
            fields.append((f"{n.get('author_name')} ({ts} UTC)", n.get("content"), False))

        embed = views.create_embed(
            title=f"📝 Internal Notes - Ticket #{ticket.get('ticket_number', 1)}",
            description=f"Total: {len(notes)} note(s)",
            color=discord.Color.gold(),
            fields=fields
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ----------------- REPLIES (EXPLICIT & ANONYMOUS) -----------------

    @app_commands.command(name="reply", description="Send an official staff reply to the client")
    @app_commands.describe(message="Message content to deliver to client")
    async def slash_reply(self, interaction: discord.Interaction, message: str):
        await self._send_reply_flow(interaction, message, anonymous=False)

    @app_commands.command(name="anonreply", description="Send an anonymous staff reply to the client (shows as Server Staff)")
    @app_commands.describe(message="Message content to deliver anonymously")
    async def slash_anon_reply(self, interaction: discord.Interaction, message: str):
        await self._send_reply_flow(interaction, message, anonymous=True)

    async def _send_reply_flow(self, interaction: discord.Interaction, message_text: str, anonymous: bool):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
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
            await interaction.response.send_message("❌ Could not locate the client user in Discord.", ephemeral=True)
            return

        # Build DM embed
        author_name = "Server Support Team" if anonymous else f"{interaction.user.display_name} (Support)"
        icon_url = interaction.guild.icon.url if anonymous and interaction.guild.icon else interaction.user.display_avatar.url

        embed_reply = views.create_embed(
            title=f"✉️ Reply from {interaction.guild.name} Support",
            description=message_text,
            color=discord.Color.blue()
        )
        embed_reply.set_author(name=author_name, icon_url=icon_url)

        try:
            await user.send(embed=embed_reply)
        except discord.Forbidden:
            await interaction.response.send_message("❌ User has their Direct Messages disabled or has blocked the bot.", ephemeral=True)
            return
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to deliver message: {e}", ephemeral=True)
            return

        # Record in DB
        database.record_ticket_message(
            ticket["ticket_id"],
            interaction.id,
            interaction.user.id,
            "Anonymous Staff" if anonymous else interaction.user.display_name,
            "staff",
            message_text
        )

        conf_embed = views.create_embed(
            title="✉️ Reply Sent to Client" + (" (Anonymous)" if anonymous else ""),
            description=message_text,
            color=discord.Color.green()
        )
        conf_embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=conf_embed)

    # ----------------- ESCALATION -----------------

    @app_commands.command(name="escalate", description="Escalate ticket to a supervisor role or management tier")
    @app_commands.describe(role="Supervisor or senior role to ping", reason="Reason for escalation")
    async def slash_escalate(self, interaction: discord.Interaction, role: Optional[discord.Role] = None, reason: Optional[str] = "Needs senior review"):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
            return

        ticket = database.get_ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("❌ Active ticket not found.", ephemeral=True)
            return

        database.update_ticket(ticket["ticket_id"], priority="urgent")
        database.add_ticket_audit(
            ticket["ticket_id"],
            "ESCALATED",
            interaction.user.id,
            interaction.user.display_name,
            f"Escalated to {role.name if role else 'Management'}. Reason: {reason}"
        )

        # Allow role to see channel
        if role:
            try:
                await interaction.channel.set_permissions(role, read_messages=True, send_messages=True, read_message_history=True)
            except Exception:
                pass

        embed = views.create_embed(
            title="🚨 Ticket Escalated",
            description=f"This ticket has been marked **URGENT** and escalated by {interaction.user.mention}.",
            color=discord.Color.red(),
            fields=[
                ("Target Role", role.mention if role else "@Management", True),
                ("Escalation Reason", reason, False)
            ]
        )
        content_ping = role.mention if role else None
        await interaction.response.send_message(content=content_ping, embed=embed)

    # ----------------- ADD & REMOVE MEMBERS -----------------

    @app_commands.command(name="add", description="Add an extra user or staff member to this ticket channel")
    @app_commands.describe(member="Member to grant channel access to")
    async def slash_add(self, interaction: discord.Interaction, member: discord.Member):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
            return

        ticket = database.get_ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("❌ Active ticket not found.", ephemeral=True)
            return

        try:
            await interaction.channel.set_permissions(member, read_messages=True, send_messages=True, read_message_history=True)
            database.add_ticket_audit(
                ticket["ticket_id"],
                "MEMBER_ADDED",
                interaction.user.id,
                interaction.user.display_name,
                f"Added {member.display_name} to channel"
            )
            await interaction.response.send_message(f"✅ Added {member.mention} to this ticket channel.")
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to set permissions: {e}", ephemeral=True)

    @app_commands.command(name="remove", description="Remove a user or staff member from this ticket channel")
    @app_commands.describe(member="Member to remove from channel")
    async def slash_remove(self, interaction: discord.Interaction, member: discord.Member):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
            return

        ticket = database.get_ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("❌ Active ticket not found.", ephemeral=True)
            return

        try:
            await interaction.channel.set_permissions(member, overwrite=None)
            database.add_ticket_audit(
                ticket["ticket_id"],
                "MEMBER_REMOVED",
                interaction.user.id,
                interaction.user.display_name,
                f"Removed {member.display_name} from channel"
            )
            await interaction.response.send_message(f"✅ Removed {member.mention} from this ticket channel.")
        except Exception as e:
            await interaction.response.send_message(f"❌ Failed to remove permissions: {e}", ephemeral=True)

    # ----------------- TRANSCRIPT & EXPORT -----------------

    @app_commands.command(name="transcript", description="Generate and export the current ticket transcript on demand")
    @app_commands.describe(format="Choose HTML (interactive) or TXT format")
    async def slash_transcript(self, interaction: discord.Interaction, format: Literal["html", "txt", "both"] = "both"):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
            return

        ticket = database.get_ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("❌ Active ticket not found.", ephemeral=True)
            return

        await interaction.response.defer()

        messages = database.get_ticket_messages(ticket["ticket_id"], include_internal=True)
        notes = database.get_ticket_notes(ticket["ticket_id"])
        audit_logs = database.get_ticket_audit(ticket["ticket_id"])

        files_data = transcripts.save_and_build_transcripts(
            guild_name=interaction.guild.name,
            guild_id=interaction.guild.id,
            ticket_data=ticket,
            messages=messages,
            notes=notes,
            audit_logs=audit_logs,
            closed_by_name=interaction.user.display_name,
            closed_by_id=interaction.user.id
        )

        send_files = []
        if format in ["html", "both"]:
            send_files.append(files_data["html_file"])
        if format in ["txt", "both"]:
            send_files.append(files_data["txt_file"])

        embed = views.create_embed(
            title=f"📄 Transcript Export - Ticket #{ticket.get('ticket_number', 1)}",
            description=f"Generated transcript containing **{len(messages)}** message(s) and **{len(notes)}** internal note(s).",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed, files=send_files)

    # ----------------- ADVANCED CLOSE & REOPEN -----------------

    @app_commands.command(name="close", description="Close the current ticket with custom reason and countdown")
    @app_commands.describe(reason="Reason for closing", delay="Countdown in seconds before channel deletion (0-60)")
    async def slash_close(self, interaction: discord.Interaction, reason: Optional[str] = "Issue resolved by staff", delay: Optional[int] = 5):
        if not is_staff_check(interaction):
            await interaction.response.send_message("❌ Permission denied.", ephemeral=True)
            return

        ticket = database.get_ticket_by_channel(interaction.channel.id)
        if not ticket:
            await interaction.response.send_message("❌ Active ticket not found.", ephemeral=True)
            return

        await interaction.response.defer()
        delay_sec = max(0, min(60, delay or 5))

        # Close in database
        database.close_ticket(
            ticket["ticket_id"],
            interaction.user.id,
            interaction.user.display_name,
            reason=reason
        )

        messages = database.get_ticket_messages(ticket["ticket_id"], include_internal=True)
        notes = database.get_ticket_notes(ticket["ticket_id"])
        audit_logs = database.get_ticket_audit(ticket["ticket_id"])
        config = database.get_guild_config(interaction.guild.id)

        transcript_files = transcripts.save_and_build_transcripts(
            guild_name=interaction.guild.name,
            guild_id=interaction.guild.id,
            ticket_data=ticket,
            messages=messages,
            notes=notes,
            audit_logs=audit_logs,
            closed_by_name=interaction.user.display_name,
            closed_by_id=interaction.user.id
        )

        # Send to logs
        logs_channel_id = config.get("logs_channel_id")
        if logs_channel_id:
            logs_chan = interaction.guild.get_channel(logs_channel_id)
            if logs_chan:
                try:
                    embed_log = views.create_embed(
                        title=f"💾 Ticket #{ticket.get('ticket_number', 1)} Closed",
                        description=f"Ticket for **{ticket.get('user_name')}** was closed by {interaction.user.mention}.",
                        color=discord.Color.red(),
                        fields=[
                            ("Reason", reason, False),
                            ("Priority", str(ticket.get("priority", "normal")).upper(), True),
                            ("Claimed By", ticket.get("claimed_by_name") or "Unclaimed", True),
                            ("Messages", str(len(messages)), True)
                        ]
                    )
                    await logs_chan.send(embed=embed_log, files=[transcript_files["html_file"], transcript_files["txt_file"]])
                except Exception as e:
                    print(f"Logs channel error: {e}")

        # Send to DM
        client_user = interaction.guild.get_member(ticket["user_id"])
        if not client_user:
            try:
                client_user = await self.bot.fetch_user(ticket["user_id"])
            except Exception:
                pass

        if client_user:
            try:
                embed_user = views.create_embed(
                    title=f"💾 Ticket Closed - {interaction.guild.name}",
                    description=(
                        f"Your support ticket #{ticket.get('ticket_number', 1)} in **{interaction.guild.name}** has been closed.\n\n"
                        f"**Reason:** {reason}\n"
                        "A copy of your conversation transcript is attached below."
                    ),
                    color=discord.Color.red()
                )
                survey_view = views.PersistentSatisfactionSurveyView(ticket["ticket_id"]) if config.get("survey_enabled", True) else None
                await client_user.send(embed=embed_user, file=transcript_files["client_html_file"], view=survey_view)
            except Exception:
                pass

        await interaction.followup.send(f"🔒 Ticket closed. Deleting channel in **{delay_sec}** seconds...")
        if delay_sec > 0:
            await asyncio.sleep(delay_sec)
        try:
            await interaction.channel.delete()
        except Exception:
            pass

    @app_commands.command(name="reopen", description="Reopen a closed ticket by ticket ID (Admin only)")
    @app_commands.describe(ticket_id="The numeric database ID or ticket number to reopen")
    @app_commands.default_permissions(administrator=True)
    async def slash_reopen(self, interaction: discord.Interaction, ticket_id: int):
        if not interaction.user.guild_permissions.administrator and interaction.guild.owner_id != interaction.user.id:
            await interaction.response.send_message("❌ Administrator permission required.", ephemeral=True)
            return

        ticket = database.get_ticket_by_id(ticket_id)
        if not ticket or ticket["guild_id"] != interaction.guild.id:
            await interaction.response.send_message(f"❌ Ticket #{ticket_id} was not found in this server.", ephemeral=True)
            return

        if ticket["status"] != "closed":
            await interaction.response.send_message(f"⚠️ Ticket #{ticket_id} is already open (<#{ticket['channel_id']}>).", ephemeral=True)
            return

        config = database.get_guild_config(interaction.guild.id)
        category_channel = interaction.guild.get_channel(config.get("category_id"))
        staff_role = interaction.guild.get_role(config.get("staff_role_id"))

        if not category_channel or not staff_role:
            await interaction.response.send_message("❌ Server configuration missing Category or Staff Role.", ephemeral=True)
            return

        await interaction.response.defer()

        # Create new channel for reopened ticket
        overwrites = {
            interaction.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interaction.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True, manage_messages=True),
            staff_role: discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True)
        }
        clean_name = ticket["user_name"].lower().replace(" ", "")
        channel_name = f"reopen-{clean_name[:12]}"

        new_chan = await interaction.guild.create_text_channel(
            name=channel_name,
            category=category_channel,
            overwrites=overwrites,
            topic=f"Reopened Ticket #{ticket.get('ticket_number', 1)} for {ticket['user_name']}"
        )

        database.update_ticket(ticket["ticket_id"], channel_id=new_chan.id)
        database.reopen_ticket(ticket["ticket_id"], interaction.user.id, interaction.user.display_name)

        embed = views.create_embed(
            title=f"📂 Reopened Ticket #{ticket.get('ticket_number', 1)}",
            description=f"Ticket was reopened by {interaction.user.mention}.",
            color=discord.Color.green(),
            fields=[
                ("Client", f"<@{ticket['user_id']}> (`{ticket['user_id']}`)", True),
                ("Original Subject", ticket.get("subject") or "None", False)
            ]
        )
        action_bar = views.PersistentTicketActionBar()
        await new_chan.send(embed=embed, view=action_bar)
        await interaction.followup.send(f"✅ Ticket #{ticket.get('ticket_number', 1)} reopened in <#{new_chan.id}>.")

    # ----------------- PREFIX COMMANDS INTEGRATION -----------------

    @commands.command(name="r", aliases=["reply"])
    async def prefix_reply(self, ctx, *, message: str):
        """Prefix command to reply to the client."""
        ticket = database.get_ticket_by_channel(ctx.channel.id)
        if not ticket or not is_staff_check(ctx):
            return

        user_id = ticket["user_id"]
        user = ctx.guild.get_member(user_id)
        if not user:
            try:
                user = await self.bot.fetch_user(user_id)
            except Exception:
                pass

        if not user:
            await ctx.send("❌ Error: Client not found.")
            return

        embed_reply = views.create_embed(
            title=f"✉️ Reply from {ctx.guild.name} Support",
            description=message,
            color=discord.Color.blue()
        )
        embed_reply.set_author(name=f"{ctx.author.display_name} (Support)", icon_url=ctx.author.display_avatar.url)

        try:
            await user.send(embed=embed_reply)
            database.record_ticket_message(ticket["ticket_id"], ctx.message.id, ctx.author.id, ctx.author.display_name, "staff", message)
            await ctx.message.add_reaction("✉️")
        except discord.Forbidden:
            await ctx.send("❌ Error: Client has DMs disabled or blocked the bot.")

    @commands.command(name="ar", aliases=["anonreply"])
    async def prefix_anon_reply(self, ctx, *, message: str):
        """Prefix command to reply anonymously."""
        ticket = database.get_ticket_by_channel(ctx.channel.id)
        if not ticket or not is_staff_check(ctx):
            return

        user_id = ticket["user_id"]
        user = ctx.guild.get_member(user_id)
        if not user:
            try:
                user = await self.bot.fetch_user(user_id)
            except Exception:
                pass

        if not user:
            await ctx.send("❌ Error: Client not found.")
            return

        embed_reply = views.create_embed(
            title=f"✉️ Reply from {ctx.guild.name} Support",
            description=message,
            color=discord.Color.blue()
        )
        embed_reply.set_author(name="Server Support Team", icon_url=ctx.guild.icon.url if ctx.guild.icon else None)

        try:
            await user.send(embed=embed_reply)
            database.record_ticket_message(ticket["ticket_id"], ctx.message.id, ctx.author.id, "Anonymous Staff", "staff", message)
            await ctx.message.add_reaction("✉️")
        except discord.Forbidden:
            await ctx.send("❌ Error: Client has DMs disabled or blocked the bot.")

    @commands.command(name="n", aliases=["note"])
    async def prefix_note(self, ctx, *, content: str):
        """Prefix command to add internal note."""
        ticket = database.get_ticket_by_channel(ctx.channel.id)
        if not ticket or not is_staff_check(ctx):
            return

        database.add_ticket_note(ticket["ticket_id"], ctx.author.id, ctx.author.display_name, content)
        database.record_ticket_message(ticket["ticket_id"], ctx.message.id, ctx.author.id, ctx.author.display_name, "staff", content, is_internal=True)
        await ctx.message.add_reaction("📝")


async def setup(bot):
    await bot.add_cog(TicketCommands(bot))
