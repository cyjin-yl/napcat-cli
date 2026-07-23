"""SQLite event store for NapCat CLI.

Replaces the filesystem JSON approach with a SQLite database for:
- Faster querying (time-based filters, type filters)
- No file naming conflicts
- Atomic writes
- Proper indexing for time-based lookups

The database is stored at DATA_DIR/napcat_events.db.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any


def get_db_path(data_dir: Path | None = None) -> Path:
    """Get the path to the events database."""
    if data_dir is None:
        from .config import DATA_DIR
        data_dir = DATA_DIR
    return data_dir / "napcat_events.db"


def get_connection(data_dir: Path | None = None) -> sqlite3.Connection:
    """Get a connection to the events database, creating it if needed."""
    db_path = get_db_path(data_dir)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Initialize the database schema if tables don't exist."""
    # Create tables (IF NOT EXISTS: no-op if table already exists)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS events ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "timestamp INTEGER NOT NULL, "
        "post_type TEXT NOT NULL, "
        "event_type TEXT NOT NULL, "
        "raw_json TEXT NOT NULL, "
        "self_id INTEGER, "
        "group_id INTEGER, "
        "user_id INTEGER, "
        "message_id INTEGER DEFAULT NULL, "
        "message_type TEXT, "
        "sender_id INTEGER, "
        "seen INTEGER NOT NULL DEFAULT 0, "
        "read_timestamp INTEGER DEFAULT NULL, "
        "ocr_text TEXT DEFAULT NULL, "
        "ocr_hash TEXT DEFAULT NULL, "
        "created_at INTEGER NOT NULL"
        ")"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS alerts ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT NOT NULL, "
        "timestamp INTEGER NOT NULL, "
        "raw_json TEXT NOT NULL, "
        "created_at INTEGER NOT NULL"
        ")"
    )
    conn.commit()

    # Create indexes individually (may fail if column missing — migration below fixes that)
    for idx in [
        "idx_events_timestamp ON events(timestamp DESC)",
        "idx_events_post_type ON events(post_type)",
        "idx_events_event_type ON events(event_type)",
        "idx_events_group_id ON events(group_id)",
        "idx_events_user_id ON events(user_id)",
        "idx_events_sender_id ON events(sender_id)",
        "idx_events_seen ON events(seen)",
        "idx_events_read_timestamp ON events(read_timestamp)",
        "idx_alerts_name ON alerts(name)",
        "idx_alerts_timestamp ON alerts(timestamp DESC)",
    ]:
        try:
            conn.execute(f"CREATE INDEX IF NOT EXISTS {idx}")
        except sqlite3.OperationalError:
            pass
    conn.commit()

    # Schema migration: add columns missing from older databases
    for col, ctype in (("message_id", "INTEGER DEFAULT NULL"),
                        ("seen", "INTEGER NOT NULL DEFAULT 0"),
                        ("read_timestamp", "INTEGER DEFAULT NULL"),
                        ("ocr_text", "TEXT DEFAULT NULL"),
                        ("ocr_hash", "TEXT DEFAULT NULL")):
        try:
            conn.execute(f"ALTER TABLE events ADD COLUMN {col} {ctype}")
        except sqlite3.OperationalError:
            pass
    conn.commit()

    # Ensure index exists (may fail if table was old but column was added manually)
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_message_id ON events(message_id)"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_seen ON events(seen)"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass

    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_read_timestamp ON events(read_timestamp)"
        )
        conn.commit()
    except sqlite3.OperationalError:
        pass

# ---------------------------------------------------------------------------
# Writer operations
# ---------------------------------------------------------------------------

def write_event(conn: sqlite3.Connection, event: dict[str, Any]) -> int:
    """Write an event to the database. Returns the new event ID."""
    now = int(time.time())
    ts = event.get("time", now)
    post_type = event.get("post_type", "unknown")
    event_type = event.get("notice_type", event.get("request_type", event.get("meta_event_type", post_type)))
    raw_json = json.dumps(event, ensure_ascii=False)

    self_id = event.get("self_id")
    group_id = event.get("group_id")
    user_id = event.get("user_id")
    message_id = event.get("message_id")
    message_type = event.get("message_type")
    sender_id = event.get("sender", {}).get("user_id") if "sender" in event else None

    cursor = conn.execute(
        "INSERT INTO events "
        "(timestamp, post_type, event_type, raw_json, self_id, group_id, user_id, "
        "message_id, message_type, sender_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, post_type, event_type, raw_json, self_id, group_id, user_id,
         message_id, message_type, sender_id, now),
    )
    conn.commit()
    return cursor.lastrowid


def write_alert(conn: sqlite3.Connection, name: str, data: dict[str, Any]) -> int:
    """Write an alert to the database. Returns the new alert ID."""
    now = int(time.time())
    raw_json = json.dumps(data, ensure_ascii=False)
    alert_ts = data.get("timestamp", now)

    cursor = conn.execute(
        "INSERT INTO alerts (name, timestamp, raw_json, created_at) VALUES (?, ?, ?, ?)",
        (name, alert_ts, raw_json, now),
    )
    conn.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# Reader operations
# ---------------------------------------------------------------------------
def read_events(
    conn: sqlite3.Connection,
    limit: int = 50,
    event_type: str | None = None,
    since: int | None = None,
    post_type: str | None = None,
    group_id: int | None = None,
    user_id: int | None = None,
    keyword: str | None = None,
    mark_seen: bool = False,
) -> list[dict[str, Any]]:
    """Read events from the database, newest first.

    Args:
        mark_seen: If True, mark returned events as seen (auto-set when read via API/CLI/FS).
    """
    query = "SELECT id, raw_json FROM events WHERE 1=1"
    params: list[Any] = []

    if event_type:
        query += " AND event_type = ?"
        params.append(event_type)
    if since:
        query += " AND timestamp >= ?"
        params.append(since)
    if post_type:
        query += " AND post_type = ?"
        params.append(post_type)
    if group_id:
        query += " AND group_id = ?"
        params.append(group_id)
    if user_id:
        query += " AND (user_id = ? OR sender_id = ?)"
        params.append(user_id)
        params.append(user_id)
    if keyword:
        query += " AND raw_json LIKE ?"
        params.append(f"%{keyword}%")

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    cur = conn.execute(query, params)
    rows = cur.fetchall()
    events = []
    event_ids = []
    for r in rows:
        event = json.loads(r["raw_json"])
        event["id"] = r["id"]
        events.append(event)
        event_ids.append(r["id"])

    if mark_seen and event_ids:
        placeholders = ",".join("?" * len(event_ids))
        conn.execute(f"UPDATE events SET seen = 1 WHERE id IN ({placeholders}) AND seen = 0", event_ids)
        conn.commit()

    return events

def read_alerts(
    conn: sqlite3.Connection,
    name: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Read alerts from the database."""
    query = "SELECT name, raw_json FROM alerts WHERE 1=1"
    params: list[Any] = []

    if name:
        query += " AND name = ?"
        params.append(name)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    return [{"name": r["name"], **json.loads(r["raw_json"])} for r in rows]


def clear_alerts(conn: sqlite3.Connection, name: str | None = None) -> int:
    """Clear alerts. If name is given, clear only that alert type.
    Returns count of deleted rows."""
    cur = conn
    if name:
        cur = conn.execute("DELETE FROM alerts WHERE name = ?", (name,))
    else:
        cur = conn.execute("DELETE FROM alerts")
    deleted = cur.rowcount
    conn.commit()
    return deleted


def get_event_count(conn: sqlite3.Connection) -> int:
    """Get total event count."""
    return conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]


def get_alert_count(conn: sqlite3.Connection, name: str | None = None) -> int:
    """Get alert count, optionally filtered by name."""
    if name:
        return conn.execute("SELECT COUNT(*) FROM alerts WHERE name = ?", (name,)).fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]


def mark_event_seen(
    conn: sqlite3.Connection,
    message_id: int,
    group_id: int | None = None,
    user_id: int | None = None,
) -> int:
    """Mark an event as seen. Returns count of updated rows."""
    query = "UPDATE events SET seen = 1 WHERE message_id = ?"
    params = [message_id]
    if group_id:
        query += " AND group_id = ?"
        params.append(group_id)
    if user_id:
        query += " AND (user_id = ? OR sender_id = ?)"
        params.append(user_id)
        params.append(user_id)
    cur = conn.execute(query, params)
    conn.commit()
    return cur.rowcount


def mark_event_read(
    conn: sqlite3.Connection,
    message_id: int,
    group_id: int | None = None,
    user_id: int | None = None,
) -> int:
    """Mark an event as read (sets read_timestamp). Returns count of updated rows."""
    query = "UPDATE events SET seen = 1, read_timestamp = ? WHERE message_id = ?"
    params = [int(time.time()), message_id]
    if group_id:
        query += " AND group_id = ?"
        params.append(group_id)
    if user_id:
        query += " AND (user_id = ? OR sender_id = ?)"
        params.append(user_id)
        params.append(user_id)
    cur = conn.execute(query, params)
    conn.commit()
    return cur.rowcount


def mark_alerts_read(
    conn: sqlite3.Connection,
    name: str | None = None,
) -> int:
    """Mark all alerts as read (deletes them). Returns count of deleted rows."""
    return clear_alerts(conn, name)


def prune_events(conn: sqlite3.Connection, older_than_days: int = 7) -> int:
    """Remove events older than specified days."""
    cutoff = int(time.time()) - (older_than_days * 86400)
    cur = conn.execute("DELETE FROM events WHERE timestamp < ?", (cutoff,))
    deleted = cur.rowcount
    conn.commit()
    return deleted
