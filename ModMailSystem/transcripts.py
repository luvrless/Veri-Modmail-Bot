"""
Transcript Generation Engine for ModMail
Generates both classical TXT transcripts (preserving 100% backward compatibility)
and rich, modern, self-contained HTML transcripts with Discord dark mode styling.
"""

import os
import datetime
import html
import discord
from io import BytesIO
from typing import Dict, Any, List, Optional

def generate_txt_transcript(guild_name: str, ticket_data: Dict[str, Any], messages: List[Dict[str, Any]],
                            notes: Optional[List[Dict[str, Any]]] = None,
                            closed_by_name: Optional[str] = None,
                            closed_by_id: Optional[int] = None) -> str:
    lines = [
        "--- ModMail Ticket Transcript ---",
        f"Guild: {guild_name} (ID: {ticket_data.get('guild_id')})",
        f"Ticket Number: #{ticket_data.get('ticket_number', 'N/A')}",
        f"Client: {ticket_data.get('user_name')} (ID: {ticket_data.get('user_id')})",
        f"Priority: {str(ticket_data.get('priority', 'normal')).upper()}",
        f"Category: {ticket_data.get('category_tag', 'General')}",
        f"Opened At: {ticket_data.get('opened_at')}",
        f"Closed By: {closed_by_name or ticket_data.get('closed_by_id', 'N/A')} (ID: {closed_by_id or ticket_data.get('closed_by_id', 'N/A')})",
        f"Closed At: {ticket_data.get('closed_at') or datetime.datetime.now(datetime.timezone.utc).isoformat()}",
        f"Close Reason: {ticket_data.get('close_reason', 'None')}",
        "---------------------------------\n"
    ]

    for msg in messages:
        timestamp = msg.get("sent_at", "")
        if "T" in timestamp:
            timestamp = timestamp.replace("T", " ")[:19]
        author = msg.get("sender_name", "Unknown")
        content = msg.get("content", "")
        is_internal = msg.get("is_internal", False)
        prefix = "[INTERNAL NOTE] " if is_internal else ""

        att_str = ""
        atts = msg.get("attachments", [])
        if atts:
            att_str = f"\n[Attachments: {', '.join(atts)}]"

        lines.append(f"[{timestamp}] {prefix}{author}: {content}{att_str}")

    if notes:
        lines.append("\n--- Internal Staff Notes ---")
        for n in notes:
            ts = n.get("created_at", "").replace("T", " ")[:19]
            lines.append(f"[{ts}] {n.get('author_name')}: {n.get('content')}")

    return "\n".join(lines)


def generate_html_transcript(guild_name: str, ticket_data: Dict[str, Any], messages: List[Dict[str, Any]],
                             notes: Optional[List[Dict[str, Any]]] = None,
                             audit_logs: Optional[List[Dict[str, Any]]] = None,
                             include_internal: bool = True) -> str:
    ticket_num = ticket_data.get("ticket_number", 1)
    user_name = html.escape(str(ticket_data.get("user_name", "User")))
    user_id = ticket_data.get("user_id", 0)
    priority = html.escape(str(ticket_data.get("priority", "normal")).upper())
    status = html.escape(str(ticket_data.get("status", "closed")).upper())
    subject = html.escape(str(ticket_data.get("subject", "No subject provided")))
    opened_at = ticket_data.get("opened_at", "")[:19].replace("T", " ")
    closed_at = (ticket_data.get("closed_at") or "")[:19].replace("T", " ")
    rating = ticket_data.get("rating")
    rating_stars = "⭐" * rating if rating else "Not Rated"

    priority_colors = {
        "LOW": "#57F287",
        "NORMAL": "#5865F2",
        "HIGH": "#FEE75C",
        "URGENT": "#ED4245"
    }
    p_color = priority_colors.get(priority, "#5865F2")

    msg_html_list = []
    for m in messages:
        is_internal = bool(m.get("is_internal"))
        if is_internal and not include_internal:
            continue

        sender_type = m.get("sender_type", "client")
        sender_name = html.escape(str(m.get("sender_name", "Unknown")))
        content = html.escape(str(m.get("content", ""))).replace("\n", "<br>")
        sent_at = m.get("sent_at", "")[:19].replace("T", " ")

        # Badge styling
        if is_internal:
            badge = '<span class="badge badge-internal">INTERNAL NOTE</span>'
            row_class = "msg-row internal"
        elif sender_type == "staff":
            badge = '<span class="badge badge-staff">SUPPORT STAFF</span>'
            row_class = "msg-row staff"
        elif sender_type == "system":
            badge = '<span class="badge badge-system">SYSTEM</span>'
            row_class = "msg-row system"
        else:
            badge = '<span class="badge badge-client">CLIENT</span>'
            row_class = "msg-row client"

        # Avatar initial
        initial = (sender_name[:1] or "?").upper()

        # Attachments rendering
        att_html = ""
        atts = m.get("attachments", [])
        if atts:
            att_items = []
            for a in atts:
                safe_url = html.escape(a)
                fname = safe_url.split("/")[-1].split("?")[0]
                if any(fname.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]):
                    att_items.append(f'<div class="att-card"><img src="{safe_url}" alt="Attachment" class="att-img" /><br><a href="{safe_url}" target="_blank" class="att-link">🔗 {html.escape(fname)}</a></div>')
                else:
                    att_items.append(f'<div class="att-card"><a href="{safe_url}" target="_blank" class="att-link">📎 {html.escape(fname)}</a></div>')
            att_html = f'<div class="attachments-wrap">{"".join(att_items)}</div>'

        msg_html_list.append(f"""
        <div class="{row_class}">
            <div class="avatar">{initial}</div>
            <div class="msg-body">
                <div class="msg-header">
                    <span class="author">{sender_name}</span>
                    {badge}
                    <span class="timestamp">{sent_at} UTC</span>
                </div>
                <div class="msg-content">{content or '<i>[No text content]</i>'}</div>
                {att_html}
            </div>
        </div>
        """)

    # Render internal notes section if requested
    notes_html = ""
    if notes and include_internal:
        n_items = []
        for n in notes:
            n_author = html.escape(str(n.get("author_name", "Staff")))
            n_text = html.escape(str(n.get("content", ""))).replace("\n", "<br>")
            n_ts = n.get("created_at", "")[:19].replace("T", " ")
            n_items.append(f"""
            <div class="note-box">
                <div class="note-meta"><strong>{n_author}</strong> &bull; {n_ts} UTC</div>
                <div class="note-text">{n_text}</div>
            </div>
            """)
        notes_html = f"""
        <div class="section-card">
            <h3 class="section-title">📝 Internal Staff Notes ({len(notes)})</h3>
            {"".join(n_items)}
        </div>
        """

    # Render audit timeline
    audit_html = ""
    if audit_logs:
        a_items = []
        for a in audit_logs:
            act = html.escape(str(a.get("action", "EVENT")))
            act_name = html.escape(str(a.get("actor_name", "System")))
            details = html.escape(str(a.get("details", "")))
            ts = a.get("created_at", "")[:19].replace("T", " ")
            a_items.append(f"""
            <div class="audit-item">
                <span class="audit-badge">{act}</span>
                <span class="audit-text"><strong>{act_name}</strong> - {details}</span>
                <span class="audit-time">{ts} UTC</span>
            </div>
            """)
        audit_html = f"""
        <div class="section-card">
            <h3 class="section-title">🛡️ Audit Trail ({len(audit_logs)})</h3>
            <div class="audit-list">
                {"".join(a_items)}
            </div>
        </div>
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ModMail Ticket #{ticket_num} - {user_name}</title>
    <style>
        :root {{
            --bg-dark: #1e1f22;
            --bg-medium: #2b2d31;
            --bg-light: #313338;
            --border: #3f4147;
            --text-main: #dbdee1;
            --text-muted: #949ba4;
            --text-heading: #f2f3f5;
            --blurple: #5865F2;
            --green: #57F287;
            --red: #ED4245;
            --yellow: #FEE75C;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background-color: var(--bg-dark);
            color: var(--text-main);
            font-family: "gg sans", "Noto Sans", "Helvetica Neue", Helvetica, Arial, sans-serif;
            font-size: 14px;
            line-height: 1.5;
            padding: 24px;
        }}
        .container {{
            max-width: 950px;
            margin: 0 auto;
        }}
        .header-card {{
            background: linear-gradient(135deg, #2b2d31 0%, #1e1f22 100%);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 24px;
            margin-bottom: 24px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.3);
        }}
        .title-row {{
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 12px;
            flex-wrap: wrap;
            gap: 12px;
        }}
        .title-row h1 {{
            font-size: 24px;
            color: var(--text-heading);
            font-weight: 700;
        }}
        .badges-top {{
            display: flex;
            gap: 8px;
        }}
        .grid-meta {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 12px;
            margin-top: 16px;
            padding-top: 16px;
            border-top: 1px solid var(--border);
        }}
        .meta-item {{
            font-size: 13px;
        }}
        .meta-item .label {{
            color: var(--text-muted);
            text-transform: uppercase;
            font-size: 11px;
            font-weight: 600;
            display: block;
            margin-bottom: 2px;
        }}
        .badge {{
            padding: 3px 8px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        .badge-status {{ background: #2f3136; border: 1px solid var(--border); color: #fff; }}
        .badge-prio {{ background: {p_color}22; border: 1px solid {p_color}; color: {p_color}; }}
        .badge-client {{ background: rgba(88, 101, 242, 0.2); color: #858ff7; }}
        .badge-staff {{ background: rgba(87, 242, 135, 0.2); color: #57F287; }}
        .badge-internal {{ background: rgba(254, 231, 92, 0.2); color: #FEE75C; border: 1px solid #FEE75C66; }}
        .badge-system {{ background: rgba(148, 155, 164, 0.2); color: #dbdee1; }}
        
        .chat-container {{
            background: var(--bg-light);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 24px;
        }}
        .msg-row {{
            display: flex;
            gap: 16px;
            padding: 12px 14px;
            border-radius: 8px;
            margin-bottom: 8px;
            transition: background 0.15s;
        }}
        .msg-row:hover {{
            background: rgba(255,255,255,0.03);
        }}
        .msg-row.internal {{
            background: rgba(254, 231, 92, 0.05);
            border-left: 3px solid #FEE75C;
        }}
        .avatar {{
            width: 40px;
            height: 40px;
            border-radius: 50%;
            background: var(--blurple);
            color: #fff;
            font-weight: 700;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
            font-size: 16px;
        }}
        .msg-body {{
            flex: 1;
        }}
        .msg-header {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 4px;
        }}
        .author {{
            font-weight: 600;
            color: var(--text-heading);
            font-size: 15px;
        }}
        .timestamp {{
            font-size: 12px;
            color: var(--text-muted);
            margin-left: auto;
        }}
        .msg-content {{
            font-size: 14px;
            color: var(--text-main);
            word-break: break-word;
        }}
        .attachments-wrap {{
            margin-top: 10px;
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }}
        .att-card {{
            background: var(--bg-medium);
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 8px;
            max-width: 300px;
        }}
        .att-img {{
            max-width: 100%;
            border-radius: 6px;
            margin-bottom: 4px;
            display: block;
        }}
        .att-link {{
            color: #58a6ff;
            text-decoration: none;
            font-size: 12px;
            word-break: break-all;
        }}
        .att-link:hover {{ text-decoration: underline; }}
        
        .section-card {{
            background: var(--bg-medium);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 24px;
        }}
        .section-title {{
            font-size: 16px;
            color: var(--text-heading);
            margin-bottom: 14px;
            font-weight: 600;
        }}
        .note-box {{
            background: var(--bg-light);
            border-left: 3px solid #FEE75C;
            padding: 10px 14px;
            border-radius: 6px;
            margin-bottom: 10px;
        }}
        .note-meta {{
            font-size: 12px;
            color: var(--text-muted);
            margin-bottom: 4px;
        }}
        .audit-list {{
            display: flex;
            flex-direction: column;
            gap: 8px;
        }}
        .audit-item {{
            display: flex;
            align-items: center;
            gap: 12px;
            background: var(--bg-light);
            padding: 8px 12px;
            border-radius: 6px;
            font-size: 13px;
        }}
        .audit-badge {{
            background: #40444b;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
        }}
        .audit-text {{
            flex: 1;
        }}
        .audit-time {{
            font-size: 11px;
            color: var(--text-muted);
        }}
        footer {{
            text-align: center;
            color: var(--text-muted);
            font-size: 12px;
            padding: 16px 0;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header-card">
            <div class="title-row">
                <h1>🛡️ ModMail Ticket #{ticket_num}</h1>
                <div class="badges-top">
                    <span class="badge badge-status">{status}</span>
                    <span class="badge badge-prio">{priority}</span>
                </div>
            </div>
            <div><strong>Subject:</strong> {subject}</div>
            <div class="grid-meta">
                <div class="meta-item"><span class="label">Server</span>{html.escape(guild_name)}</div>
                <div class="meta-item"><span class="label">Client</span>{user_name} (`{user_id}`)</div>
                <div class="meta-item"><span class="label">Opened</span>{opened_at} UTC</div>
                <div class="meta-item"><span class="label">Closed</span>{closed_at or 'Still Active'} UTC</div>
                <div class="meta-item"><span class="label">Rating</span>{rating_stars}</div>
            </div>
        </div>

        <div class="chat-container">
            {"".join(msg_html_list) if msg_html_list else '<div style="color:var(--text-muted); text-align:center; padding: 20px;">No messages recorded in this ticket.</div>'}
        </div>

        {notes_html}
        {audit_html}

        <footer>Enterprise ModMail Transcript &bull; Generated on {datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")} UTC</footer>
    </div>
</body>
</html>
"""

def save_and_build_transcripts(guild_name: str, guild_id: int, ticket_data: Dict[str, Any],
                               messages: List[Dict[str, Any]],
                               notes: Optional[List[Dict[str, Any]]] = None,
                               audit_logs: Optional[List[Dict[str, Any]]] = None,
                               closed_by_name: Optional[str] = None,
                               closed_by_id: Optional[int] = None) -> Dict[str, Any]:
    """Generates and saves both TXT and HTML transcripts in transcripts/{guild_id}/."""
    guild_dir = f"transcripts/{guild_id}"
    os.makedirs(guild_dir, exist_ok=True)

    timestamp_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    user_id = ticket_data.get("user_id", 0)

    # 1. TXT Transcript (exact format as original bot for backward compatibility)
    txt_content = generate_txt_transcript(
        guild_name=guild_name,
        ticket_data=ticket_data,
        messages=messages,
        notes=notes,
        closed_by_name=closed_by_name,
        closed_by_id=closed_by_id
    )
    txt_path = f"{guild_dir}/ticket_{user_id}_{timestamp_str}.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(txt_content)

    # 2. HTML Transcript
    html_content = generate_html_transcript(
        guild_name=guild_name,
        ticket_data=ticket_data,
        messages=messages,
        notes=notes,
        audit_logs=audit_logs,
        include_internal=True
    )
    html_path = f"{guild_dir}/ticket_{user_id}_{timestamp_str}.html"
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    # Client-safe HTML (without internal notes)
    client_html_content = generate_html_transcript(
        guild_name=guild_name,
        ticket_data=ticket_data,
        messages=messages,
        notes=None,
        audit_logs=None,
        include_internal=False
    )

    return {
        "txt_path": txt_path,
        "html_path": html_path,
        "txt_file": discord.File(BytesIO(txt_content.encode("utf-8")), filename=f"ticket_{user_id}_{timestamp_str}.txt"),
        "html_file": discord.File(BytesIO(html_content.encode("utf-8")), filename=f"ticket_{user_id}_{timestamp_str}.html"),
        "client_html_file": discord.File(BytesIO(client_html_content.encode("utf-8")), filename=f"transcript_{user_id}.html"),
        "timestamp_str": timestamp_str
    }
