"""
Database Persistence Layer for Enterprise ModMail System
Provides SQLite persistence, connection pooling/thread safety, and automatic schema migration.
Maintains backward compatibility with active_tickets.json and servers/*.json.
"""

import sqlite3
import json
import os
import datetime
import asyncio
from typing import Optional, Dict, Any, List

DB_PATH = "modmail.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Guild settings table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS guild_settings (
        guild_id INTEGER PRIMARY KEY,
        category_id INTEGER,
        staff_role_id INTEGER,
        logs_channel_id INTEGER,
        enabled INTEGER DEFAULT 0,
        blacklist_users TEXT DEFAULT '[]',
        blacklist_roles TEXT DEFAULT '[]',
        setup_completed INTEGER DEFAULT 0,
        auto_assign INTEGER DEFAULT 0,
        inactivity_hours INTEGER DEFAULT 48,
        warning_hours INTEGER DEFAULT 24,
        survey_enabled INTEGER DEFAULT 1,
        anon_replies_default INTEGER DEFAULT 0,
        ticket_naming TEXT DEFAULT 'modmail-{username}',
        welcome_message TEXT,
        close_message TEXT,
        max_open_per_user INTEGER DEFAULT 1,
        created_at TEXT,
        updated_at TEXT
    );
    """)

    # Tickets table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tickets (
        ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_number INTEGER,
        guild_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL UNIQUE,
        user_id INTEGER NOT NULL,
        user_name TEXT NOT NULL,
        user_discriminator TEXT,
        subject TEXT,
        status TEXT DEFAULT 'open',
        priority TEXT DEFAULT 'normal',
        claimed_by_id INTEGER,
        claimed_by_name TEXT,
        category_tag TEXT DEFAULT 'General',
        opened_at TEXT NOT NULL,
        closed_at TEXT,
        closed_by_id INTEGER,
        close_reason TEXT,
        last_message_at TEXT,
        last_message_by TEXT,
        inactivity_warned INTEGER DEFAULT 0,
        rating INTEGER,
        feedback TEXT,
        response_time_seconds INTEGER,
        resolution_time_seconds INTEGER
    );
    """)

    # Indexes for fast lookup
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tickets_channel ON tickets (channel_id);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tickets_guild_status ON tickets (guild_id, status);")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets (user_id);")

    # Ticket internal notes
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ticket_notes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER NOT NULL,
        author_id INTEGER NOT NULL,
        author_name TEXT NOT NULL,
        content TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id) ON DELETE CASCADE
    );
    """)

    # Ticket audit trail
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ticket_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        actor_id INTEGER NOT NULL,
        actor_name TEXT NOT NULL,
        details TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id) ON DELETE CASCADE
    );
    """)

    # Ticket messages
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ticket_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER NOT NULL,
        message_id INTEGER,
        sender_id INTEGER NOT NULL,
        sender_name TEXT NOT NULL,
        sender_type TEXT NOT NULL,
        content TEXT,
        attachments TEXT DEFAULT '[]',
        is_internal INTEGER DEFAULT 0,
        sent_at TEXT NOT NULL,
        FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id) ON DELETE CASCADE
    );
    """)

    # Staff duty status
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS staff_duty (
        user_id INTEGER NOT NULL,
        guild_id INTEGER NOT NULL,
        status TEXT DEFAULT 'on_duty',
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, guild_id)
    );
    """)

    # Canned snippets
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS snippets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        content TEXT NOT NULL,
        created_by INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (guild_id, name)
    );
    """)

    # Ticket tags
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ticket_tags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER NOT NULL,
        tag TEXT NOT NULL,
        UNIQUE (ticket_id, tag),
        FOREIGN KEY (ticket_id) REFERENCES tickets(ticket_id) ON DELETE CASCADE
    );
    """)

    conn.commit()
    conn.close()

    # Perform initial sync / import if needed
    _sync_from_existing_files()

def _sync_from_existing_files():
    """Migrate servers/*.json and active_tickets.json into DB without overwriting existing DB rows."""
    conn = get_connection()
    cursor = conn.cursor()

    # Import servers/*.json
    servers_dir = "servers"
    if os.path.exists(servers_dir):
        for fname in os.listdir(servers_dir):
            if fname.endswith(".json"):
                guild_id_str = fname[:-5]
                try:
                    guild_id = int(guild_id_str)
                    path = os.path.join(servers_dir, fname)
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)

                    cursor.execute("SELECT guild_id FROM guild_settings WHERE guild_id = ?", (guild_id,))
                    if not cursor.fetchone():
                        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
                        cursor.execute("""
                        INSERT INTO guild_settings (
                            guild_id, category_id, staff_role_id, logs_channel_id,
                            enabled, blacklist_users, blacklist_roles, setup_completed,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            guild_id,
                            data.get("category_id"),
                            data.get("staff_role_id"),
                            data.get("logs_channel_id"),
                            1 if data.get("enabled") else 0,
                            json.dumps(data.get("blacklist_users", [])),
                            json.dumps(data.get("blacklist_roles", [])),
                            1 if data.get("setup_completed") else 0,
                            now,
                            now
                        ))
                except Exception as e:
                    print(f"Sync error for {fname}: {e}")

    # Import active_tickets.json
    active_path = "active_tickets.json"
    if os.path.exists(active_path):
        try:
            with open(active_path, "r", encoding="utf-8") as f:
                tickets_data = json.load(f)
            by_user = tickets_data.get("by_user", {})
            for uid_str, tinfo in by_user.items():
                uid = int(uid_str)
                channel_id = tinfo.get("channel_id")
                guild_id = tinfo.get("guild_id")
                started_at = tinfo.get("started_at", datetime.datetime.now(datetime.timezone.utc).isoformat())

                if channel_id and guild_id:
                    cursor.execute("SELECT ticket_id FROM tickets WHERE channel_id = ?", (channel_id,))
                    if not cursor.fetchone():
                        cursor.execute("SELECT COALESCE(MAX(ticket_number), 0) + 1 FROM tickets WHERE guild_id = ?", (guild_id,))
                        ticket_num = cursor.fetchone()[0]
                        cursor.execute("""
                        INSERT INTO tickets (
                            ticket_number, guild_id, channel_id, user_id, user_name,
                            subject, status, priority, opened_at, last_message_at
                        ) VALUES (?, ?, ?, ?, ?, ?, 'open', 'normal', ?, ?)
                        """, (
                            ticket_num, guild_id, channel_id, uid, f"User_{uid}",
                            "Modmail Ticket", started_at, started_at
                        ))
        except Exception as e:
            print(f"Sync error for active tickets: {e}")

    conn.commit()
    conn.close()

# ----------------- GUILD CONFIG -----------------

def get_guild_config(guild_id: int) -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM guild_settings WHERE guild_id = ?", (guild_id,))
    row = cursor.fetchone()
    conn.close()

    if row:
        d = dict(row)
        d["blacklist_users"] = json.loads(d["blacklist_users"]) if d["blacklist_users"] else []
        d["blacklist_roles"] = json.loads(d["blacklist_roles"]) if d["blacklist_roles"] else []
        d["enabled"] = bool(d["enabled"])
        d["setup_completed"] = bool(d["setup_completed"])
        d["auto_assign"] = bool(d["auto_assign"])
        d["survey_enabled"] = bool(d["survey_enabled"])
        d["anon_replies_default"] = bool(d["anon_replies_default"])
        return d

    # Fallback to file if present
    path = f"servers/{guild_id}.json"
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {
                    "guild_id": guild_id,
                    "category_id": data.get("category_id"),
                    "staff_role_id": data.get("staff_role_id"),
                    "logs_channel_id": data.get("logs_channel_id"),
                    "enabled": bool(data.get("enabled", False)),
                    "blacklist_users": data.get("blacklist_users", []),
                    "blacklist_roles": data.get("blacklist_roles", []),
                    "setup_completed": bool(data.get("setup_completed", False)),
                    "auto_assign": False,
                    "inactivity_hours": 48,
                    "warning_hours": 24,
                    "survey_enabled": True,
                    "anon_replies_default": False,
                    "ticket_naming": "modmail-{username}",
                    "welcome_message": None,
                    "close_message": None,
                    "max_open_per_user": 1
                }
        except Exception:
            pass

    return {
        "guild_id": guild_id,
        "category_id": None,
        "staff_role_id": None,
        "logs_channel_id": None,
        "enabled": False,
        "blacklist_users": [],
        "blacklist_roles": [],
        "setup_completed": False,
        "auto_assign": False,
        "inactivity_hours": 48,
        "warning_hours": 24,
        "survey_enabled": True,
        "anon_replies_default": False,
        "ticket_naming": "modmail-{username}",
        "welcome_message": None,
        "close_message": None,
        "max_open_per_user": 1
    }

def save_guild_config(guild_id: int, config: Dict[str, Any]):
    # Save to JSON for backward compatibility
    os.makedirs("servers", exist_ok=True)
    json_path = f"servers/{guild_id}.json"
    legacy_keys = ["guild_id", "category_id", "staff_role_id", "logs_channel_id", "enabled", "blacklist_users", "blacklist_roles", "setup_completed"]
    legacy_dict = {k: config.get(k) for k in legacy_keys}
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(legacy_dict, f, indent=4)

    # Save to SQLite
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_connection()
    cursor = conn.cursor()

    bl_users_json = json.dumps(config.get("blacklist_users", []))
    bl_roles_json = json.dumps(config.get("blacklist_roles", []))

    cursor.execute("""
    INSERT INTO guild_settings (
        guild_id, category_id, staff_role_id, logs_channel_id,
        enabled, blacklist_users, blacklist_roles, setup_completed,
        auto_assign, inactivity_hours, warning_hours, survey_enabled,
        anon_replies_default, ticket_naming, welcome_message, close_message,
        max_open_per_user, created_at, updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(guild_id) DO UPDATE SET
        category_id = excluded.category_id,
        staff_role_id = excluded.staff_role_id,
        logs_channel_id = excluded.logs_channel_id,
        enabled = excluded.enabled,
        blacklist_users = excluded.blacklist_users,
        blacklist_roles = excluded.blacklist_roles,
        setup_completed = excluded.setup_completed,
        auto_assign = excluded.auto_assign,
        inactivity_hours = excluded.inactivity_hours,
        warning_hours = excluded.warning_hours,
        survey_enabled = excluded.survey_enabled,
        anon_replies_default = excluded.anon_replies_default,
        ticket_naming = excluded.ticket_naming,
        welcome_message = excluded.welcome_message,
        close_message = excluded.close_message,
        max_open_per_user = excluded.max_open_per_user,
        updated_at = excluded.updated_at
    """, (
        guild_id,
        config.get("category_id"),
        config.get("staff_role_id"),
        config.get("logs_channel_id"),
        1 if config.get("enabled") else 0,
        bl_users_json,
        bl_roles_json,
        1 if config.get("setup_completed") else 0,
        1 if config.get("auto_assign") else 0,
        config.get("inactivity_hours", 48),
        config.get("warning_hours", 24),
        1 if config.get("survey_enabled", True) else 0,
        1 if config.get("anon_replies_default", False) else 0,
        config.get("ticket_naming", "modmail-{username}"),
        config.get("welcome_message"),
        config.get("close_message"),
        config.get("max_open_per_user", 1),
        now,
        now
    ))
    conn.commit()
    conn.close()

# ----------------- TICKET LIFECYCLE -----------------

def create_ticket(guild_id: int, channel_id: int, user_id: int, user_name: str,
                  subject: str, priority: str = "normal", category_tag: str = "General",
                  user_discriminator: str = "0") -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COALESCE(MAX(ticket_number), 0) + 1 FROM tickets WHERE guild_id = ?", (guild_id,))
    ticket_num = cursor.fetchone()[0]

    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    cursor.execute("""
    INSERT INTO tickets (
        ticket_number, guild_id, channel_id, user_id, user_name, user_discriminator,
        subject, status, priority, category_tag, opened_at, last_message_at, last_message_by
    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?, ?, 'client')
    """, (
        ticket_num, guild_id, channel_id, user_id, user_name, user_discriminator,
        subject, priority, category_tag, now, now
    ))
    ticket_id = cursor.lastrowid

    # Record audit log
    cursor.execute("""
    INSERT INTO ticket_audit (ticket_id, action, actor_id, actor_name, details, created_at)
    VALUES (?, 'CREATED', ?, ?, ?, ?)
    """, (ticket_id, user_id, user_name, f"Ticket #{ticket_num} created with subject: {subject}", now))

    conn.commit()

    cursor.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,))
    row = dict(cursor.fetchone())
    conn.close()

    # Sync active_tickets.json
    _sync_active_tickets_json()

    return row

def get_ticket_by_channel(channel_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tickets WHERE channel_id = ?", (channel_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_ticket_by_id(ticket_id: int) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_active_ticket_by_user(user_id: int, guild_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    if guild_id:
        cursor.execute("SELECT * FROM tickets WHERE user_id = ? AND guild_id = ? AND status != 'closed'", (user_id, guild_id))
    else:
        cursor.execute("SELECT * FROM tickets WHERE user_id = ? AND status != 'closed'", (user_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def get_all_active_tickets(guild_id: Optional[int] = None) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    if guild_id:
        cursor.execute("SELECT * FROM tickets WHERE guild_id = ? AND status != 'closed' ORDER BY ticket_id DESC", (guild_id,))
    else:
        cursor.execute("SELECT * FROM tickets WHERE status != 'closed' ORDER BY ticket_id DESC")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def update_ticket(ticket_id: int, **kwargs) -> Optional[Dict[str, Any]]:
    if not kwargs:
        return get_ticket_by_id(ticket_id)

    set_clauses = [f"{k} = ?" for k in kwargs.keys()]
    values = list(kwargs.values())
    values.append(ticket_id)

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(f"UPDATE tickets SET {', '.join(set_clauses)} WHERE ticket_id = ?", values)
    conn.commit()

    cursor.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,))
    row = cursor.fetchone()
    conn.close()

    _sync_active_tickets_json()
    return dict(row) if row else None

def close_ticket(ticket_id: int, closed_by_id: int, closed_by_name: str, reason: Optional[str] = None) -> Optional[Dict[str, Any]]:
    ticket = get_ticket_by_id(ticket_id)
    if not ticket:
        return None

    now = datetime.datetime.now(datetime.timezone.utc)
    now_str = now.isoformat()

    resolution_seconds = None
    try:
        opened_dt = datetime.datetime.fromisoformat(ticket["opened_at"])
        if opened_dt.tzinfo is None:
            opened_dt = opened_dt.replace(tzinfo=datetime.timezone.utc)
        resolution_seconds = int((now - opened_dt).total_seconds())
    except Exception:
        pass

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE tickets SET
        status = 'closed',
        closed_at = ?,
        closed_by_id = ?,
        close_reason = ?,
        resolution_time_seconds = ?
    WHERE ticket_id = ?
    """, (now_str, closed_by_id, reason, resolution_seconds, ticket_id))

    cursor.execute("""
    INSERT INTO ticket_audit (ticket_id, action, actor_id, actor_name, details, created_at)
    VALUES (?, 'CLOSED', ?, ?, ?, ?)
    """, (ticket_id, closed_by_id, closed_by_name, f"Closed ticket. Reason: {reason or 'No reason provided'}", now_str))

    conn.commit()
    cursor.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,))
    row = cursor.fetchone()
    conn.close()

    _sync_active_tickets_json()
    return dict(row) if row else None

def reopen_ticket(ticket_id: int, reopened_by_id: int, reopened_by_name: str) -> Optional[Dict[str, Any]]:
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    UPDATE tickets SET
        status = 'open',
        closed_at = NULL,
        closed_by_id = NULL,
        close_reason = NULL,
        inactivity_warned = 0
    WHERE ticket_id = ?
    """, (ticket_id,))

    cursor.execute("""
    INSERT INTO ticket_audit (ticket_id, action, actor_id, actor_name, details, created_at)
    VALUES (?, 'REOPENED', ?, ?, 'Ticket was reopened', ?)
    """, (ticket_id, reopened_by_id, reopened_by_name, now))

    conn.commit()
    cursor.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,))
    row = cursor.fetchone()
    conn.close()

    _sync_active_tickets_json()
    return dict(row) if row else None

def _sync_active_tickets_json():
    """Keeps active_tickets.json in sync with open tickets in database."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, channel_id, guild_id, opened_at FROM tickets WHERE status != 'closed'")
    rows = cursor.fetchall()
    conn.close()

    data = {
        "by_user": {},
        "by_channel": {}
    }
    for r in rows:
        uid_str = str(r["user_id"])
        cid_str = str(r["channel_id"])
        data["by_user"][uid_str] = {
            "channel_id": r["channel_id"],
            "guild_id": r["guild_id"],
            "started_at": r["opened_at"]
        }
        data["by_channel"][cid_str] = uid_str

    try:
        with open("active_tickets.json", "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        print(f"Error syncing active_tickets.json: {e}")

# ----------------- AUDIT & NOTES -----------------

def add_ticket_audit(ticket_id: int, action: str, actor_id: int, actor_name: str, details: str = ""):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO ticket_audit (ticket_id, action, actor_id, actor_name, details, created_at)
    VALUES (?, ?, ?, ?, ?, ?)
    """, (ticket_id, action, actor_id, actor_name, details, now))
    conn.commit()
    conn.close()

def get_ticket_audit(ticket_id: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ticket_audit WHERE ticket_id = ? ORDER BY id ASC", (ticket_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def add_ticket_note(ticket_id: int, author_id: int, author_name: str, content: str):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO ticket_notes (ticket_id, author_id, author_name, content, created_at)
    VALUES (?, ?, ?, ?, ?)
    """, (ticket_id, author_id, author_name, content, now))
    cursor.execute("""
    INSERT INTO ticket_audit (ticket_id, action, actor_id, actor_name, details, created_at)
    VALUES (?, 'NOTE_ADDED', ?, ?, ?, ?)
    """, (ticket_id, author_id, author_name, f"Added internal note: {content[:80]}", now))
    conn.commit()
    conn.close()

def get_ticket_notes(ticket_id: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM ticket_notes WHERE ticket_id = ? ORDER BY id ASC", (ticket_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

# ----------------- MESSAGES & TRANSCRIPTS -----------------

def record_ticket_message(ticket_id: int, message_id: int, sender_id: int, sender_name: str,
                          sender_type: str, content: str, attachments: Optional[List[str]] = None,
                          is_internal: bool = False):
    now = datetime.datetime.now(datetime.timezone.utc)
    now_str = now.isoformat()
    att_json = json.dumps(attachments or [])

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO ticket_messages (
        ticket_id, message_id, sender_id, sender_name, sender_type, content, attachments, is_internal, sent_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (ticket_id, message_id, sender_id, sender_name, sender_type, content, att_json, 1 if is_internal else 0, now_str))

    # Update ticket last_message
    if not is_internal:
        cursor.execute("SELECT opened_at, response_time_seconds FROM tickets WHERE ticket_id = ?", (ticket_id,))
        t = cursor.fetchone()
        resp_update = ""
        vals = [now_str, sender_type]

        # Calculate initial response time if this is first staff reply
        if sender_type == "staff" and t and t["response_time_seconds"] is None:
            try:
                op_dt = datetime.datetime.fromisoformat(t["opened_at"])
                if op_dt.tzinfo is None:
                    op_dt = op_dt.replace(tzinfo=datetime.timezone.utc)
                diff = int((now - op_dt).total_seconds())
                resp_update = ", response_time_seconds = ?"
                vals.append(diff)
            except Exception:
                pass

        vals.append(ticket_id)
        cursor.execute(f"UPDATE tickets SET last_message_at = ?, last_message_by = ?, inactivity_warned = 0{resp_update} WHERE ticket_id = ?", vals)

    conn.commit()
    conn.close()

def get_ticket_messages(ticket_id: int, include_internal: bool = True) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    if include_internal:
        cursor.execute("SELECT * FROM ticket_messages WHERE ticket_id = ? ORDER BY id ASC", (ticket_id,))
    else:
        cursor.execute("SELECT * FROM ticket_messages WHERE ticket_id = ? AND is_internal = 0 ORDER BY id ASC", (ticket_id,))
    rows = []
    for r in cursor.fetchall():
        d = dict(r)
        d["attachments"] = json.loads(d["attachments"]) if d["attachments"] else []
        rows.append(d)
    conn.close()
    return rows

# ----------------- STAFF DUTY & WORKLOAD -----------------

def set_staff_duty(guild_id: int, user_id: int, status: str = "on_duty"):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO staff_duty (user_id, guild_id, status, updated_at)
    VALUES (?, ?, ?, ?)
    ON CONFLICT(user_id, guild_id) DO UPDATE SET
        status = excluded.status,
        updated_at = excluded.updated_at
    """, (user_id, guild_id, status, now))
    conn.commit()
    conn.close()

def get_staff_duty(guild_id: int, user_id: int) -> str:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM staff_duty WHERE user_id = ? AND guild_id = ?", (user_id, guild_id))
    row = cursor.fetchone()
    conn.close()
    return row["status"] if row else "on_duty"

def get_all_on_duty_staff(guild_id: int) -> List[int]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM staff_duty WHERE guild_id = ? AND status = 'on_duty'", (guild_id,))
    rows = [r["user_id"] for r in cursor.fetchall()]
    conn.close()
    return rows

def get_staff_active_ticket_count(guild_id: int, user_id: int) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND claimed_by_id = ? AND status != 'closed'", (guild_id, user_id))
    cnt = cursor.fetchone()[0]
    conn.close()
    return cnt

def get_least_busy_staff(guild_id: int, candidate_user_ids: List[int]) -> Optional[int]:
    if not candidate_user_ids:
        return None
    counts = []
    for uid in candidate_user_ids:
        duty = get_staff_duty(guild_id, uid)
        if duty == "on_duty":
            cnt = get_staff_active_ticket_count(guild_id, uid)
            counts.append((cnt, uid))
    if not counts:
        return None
    counts.sort(key=lambda x: x[0])
    return counts[0][1]

# ----------------- SNIPPETS / CANNED REPLIES -----------------

def add_snippet(guild_id: int, name: str, content: str, created_by: int):
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO snippets (guild_id, name, content, created_by, created_at)
    VALUES (?, ?, ?, ?, ?)
    ON CONFLICT(guild_id, name) DO UPDATE SET
        content = excluded.content,
        created_by = excluded.created_by,
        created_at = excluded.created_at
    """, (guild_id, name.lower().strip(), content, created_by, now))
    conn.commit()
    conn.close()

def get_snippet(guild_id: int, name: str) -> Optional[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM snippets WHERE guild_id = ? AND name = ?", (guild_id, name.lower().strip()))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def list_snippets(guild_id: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM snippets WHERE guild_id = ? ORDER BY name ASC", (guild_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def delete_snippet(guild_id: int, name: str) -> bool:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM snippets WHERE guild_id = ? AND name = ?", (guild_id, name.lower().strip()))
    changed = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return changed

# ----------------- TAGS -----------------

def add_ticket_tag(ticket_id: int, tag: str) -> bool:
    tag = tag.strip().lower()
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO ticket_tags (ticket_id, tag) VALUES (?, ?)", (ticket_id, tag))
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        conn.close()
        return False

def remove_ticket_tag(ticket_id: int, tag: str) -> bool:
    tag = tag.strip().lower()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM ticket_tags WHERE ticket_id = ? AND tag = ?", (ticket_id, tag))
    changed = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return changed

def get_ticket_tags(ticket_id: int) -> List[str]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT tag FROM ticket_tags WHERE ticket_id = ? ORDER BY tag ASC", (ticket_id,))
    rows = [r["tag"] for r in cursor.fetchall()]
    conn.close()
    return rows

# ----------------- RATINGS & CSAT -----------------

def record_feedback(ticket_id: int, rating: int, feedback: Optional[str] = None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE tickets SET rating = ?, feedback = ? WHERE ticket_id = ?", (rating, feedback, ticket_id))
    conn.commit()
    conn.close()

# ----------------- ANALYTICS & STATS -----------------

def get_guild_stats(guild_id: int) -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM tickets WHERE guild_id = ?", (guild_id,))
    total_tickets = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND status != 'closed'", (guild_id,))
    open_tickets = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM tickets WHERE guild_id = ? AND status = 'closed'", (guild_id,))
    closed_tickets = cursor.fetchone()[0]

    cursor.execute("SELECT AVG(response_time_seconds) FROM tickets WHERE guild_id = ? AND response_time_seconds IS NOT NULL", (guild_id,))
    avg_first_response = cursor.fetchone()[0] or 0

    cursor.execute("SELECT AVG(resolution_time_seconds) FROM tickets WHERE guild_id = ? AND resolution_time_seconds IS NOT NULL", (guild_id,))
    avg_resolution = cursor.fetchone()[0] or 0

    cursor.execute("SELECT AVG(rating), COUNT(rating) FROM tickets WHERE guild_id = ? AND rating IS NOT NULL", (guild_id,))
    r_row = cursor.fetchone()
    avg_rating = r_row[0] or 0
    rated_count = r_row[1] or 0

    conn.close()

    return {
        "total_tickets": total_tickets,
        "open_tickets": open_tickets,
        "closed_tickets": closed_tickets,
        "avg_first_response_sec": int(avg_first_response),
        "avg_resolution_sec": int(avg_resolution),
        "avg_rating": round(avg_rating, 2),
        "rated_count": rated_count
    }

def get_staff_leaderboard(guild_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT 
        claimed_by_id,
        claimed_by_name,
        COUNT(ticket_id) as total_claimed,
        SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) as total_closed,
        AVG(response_time_seconds) as avg_resp,
        AVG(rating) as avg_rating
    FROM tickets
    WHERE guild_id = ? AND claimed_by_id IS NOT NULL
    GROUP BY claimed_by_id
    ORDER BY total_closed DESC, total_claimed DESC
    LIMIT ?
    """, (guild_id, limit))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def get_staff_stats(guild_id: int, staff_id: int) -> Dict[str, Any]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT 
        COUNT(ticket_id) as total_claimed,
        SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) as total_closed,
        AVG(response_time_seconds) as avg_resp,
        AVG(resolution_time_seconds) as avg_res,
        AVG(rating) as avg_rating,
        COUNT(rating) as rated_count
    FROM tickets
    WHERE guild_id = ? AND claimed_by_id = ?
    """, (guild_id, staff_id))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else {
        "total_claimed": 0, "total_closed": 0, "avg_resp": 0,
        "avg_res": 0, "avg_rating": 0, "rated_count": 0
    }

def get_user_history(guild_id: int, user_id: int) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT ticket_id, ticket_number, status, priority, opened_at, closed_at, close_reason, rating
    FROM tickets
    WHERE guild_id = ? AND user_id = ?
    ORDER BY ticket_id DESC
    LIMIT 20
    """, (guild_id, user_id))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def search_tickets(guild_id: int, query: str, limit: int = 15) -> List[Dict[str, Any]]:
    conn = get_connection()
    cursor = conn.cursor()
    param = f"%{query}%"
    cursor.execute("""
    SELECT DISTINCT t.ticket_id, t.ticket_number, t.user_name, t.user_id, t.status, t.priority, t.subject, t.opened_at
    FROM tickets t
    LEFT JOIN ticket_messages m ON t.ticket_id = m.ticket_id
    WHERE t.guild_id = ? AND (
        t.user_name LIKE ? OR 
        CAST(t.user_id AS TEXT) LIKE ? OR
        t.subject LIKE ? OR
        m.content LIKE ?
    )
    ORDER BY t.ticket_id DESC
    LIMIT ?
    """, (guild_id, param, param, param, param, limit))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

# ----------------- INACTIVITY -----------------

def get_inactive_tickets_to_warn(guild_id: int, warning_hours: int) -> List[Dict[str, Any]]:
    if warning_hours <= 0:
        return []
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=warning_hours)).isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT * FROM tickets
    WHERE guild_id = ? AND status != 'closed' AND inactivity_warned = 0
    AND (last_message_at IS NOT NULL AND last_message_at <= ?)
    """, (guild_id, cutoff))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows

def get_inactive_tickets_to_close(guild_id: int, inactivity_hours: int) -> List[Dict[str, Any]]:
    if inactivity_hours <= 0:
        return []
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=inactivity_hours)).isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT * FROM tickets
    WHERE guild_id = ? AND status != 'closed'
    AND (last_message_at IS NOT NULL AND last_message_at <= ?)
    """, (guild_id, cutoff))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows
