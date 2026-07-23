"""Event storage for NapCat CLI — SQLite backend.

Events and alerts are stored in a SQLite database (napcat_events.db) for
fast querying, atomic writes, and proper indexing.

The SQLite database is exposed through the skills-fs HTTP provider so agents
can query it via HTTP.

Usage:
    reader = EventsReader(data_dir)
    events = reader.read(limit=20, since=1700000000, event_type="message")

    writer = EventsWriter(data_dir)
    writer.write_event({"post_type": "message", ...})
    writer.write_alert("NAPCAT_CLI_NEW_MESSAGE", {"sender_id": 123})
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from .events_sqlite import (
    clear_alerts,
    get_connection,
    get_db_path,
    read_alerts,
    read_events,
    write_alert,
    write_event,
)


class EventsWriter:
    """Write events and alerts to SQLite database."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.conn = get_connection(data_dir)

    def write_event(self, event: dict[str, Any], event_type: str = "") -> int:
        """Write an event to the database. Returns the row ID."""
        return write_event(self.conn, event)

    def write_alert(self, alert_name: str, data: dict[str, Any]) -> int:
        """Write an alert to the database. Returns the row ID."""
        return write_alert(self.conn, alert_name, data)

    def clear_alert(self, name: str) -> bool:
        """Clear a specific alert."""
        count = clear_alerts(self.conn, name)
        return count > 0

    def clear_all_alerts(self) -> int:
        """Clear all alerts. Returns count cleared."""
        return clear_alerts(self.conn)

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()


class EventsReader:
    """Read events from SQLite database."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.db_path = get_db_path(data_dir)
        self._conn: sqlite3.Connection | None = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = get_connection(self.data_dir)
        return self._conn

    def read(
        self,
        limit: int = 50,
        event_type: str | None = None,
        since: int | None = None,
        post_type: str | None = None,
        group_id: int | None = None,
        user_id: int | None = None,
        keyword: str | None = None,
        mark_seen: bool = True,
    ) -> list[dict[str, Any]]:
        """Read events from SQLite, newest first.

        Args:
            limit: Maximum number of events to return.
            event_type: Filter by event type (e.g., "message", "heartbeat").
            since: Only include events with timestamp >= since.
            post_type: Filter by post_type (e.g., "message", "notice").
            group_id: Filter by group_id.
            user_id: Filter by user_id or sender_id.
            keyword: Filter by keyword in raw_json (LIKE query).
            mark_seen: If True (default), mark returned events as seen (auto-set when read via API/CLI/FS).
        """
        conn = self._get_conn()
        return read_events(
            conn,
            limit=limit,
            event_type=event_type,
            since=since,
            post_type=post_type,
            group_id=group_id,
            user_id=user_id,
            keyword=keyword,
            mark_seen=mark_seen,
        )

    def read_alerts(
        self,
        name: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Read alerts from the database."""
        conn = self._get_conn()
        return read_alerts(conn, name=name, limit=limit)

    def mark_seen(self, event_ids: list[int]) -> int:
        """Mark events as seen (auto-set when read via API/CLI/FS).

        Returns count of events updated.
        """
        if not event_ids:
            return 0
        conn = self._get_conn()
        placeholders = ",".join("?" * len(event_ids))
        cur = conn.execute(
            f"UPDATE events SET seen = 1 WHERE id IN ({placeholders}) AND seen = 0",
            event_ids,
        )
        conn.commit()
        return cur.rowcount

    def mark_read(self, event_ids: list[int]) -> int:
        """Mark events as read (explicit user/Agent action).

        Also sets read_timestamp to current time.
        Returns count of events updated.
        """
        if not event_ids:
            return 0
        import time
        now = int(time.time())
        conn = self._get_conn()
        placeholders = ",".join("?" * len(event_ids))
        cur = conn.execute(
            f"UPDATE events SET read_timestamp = ?, seen = 1 WHERE id IN ({placeholders})",
            [int(time.time())] + event_ids,
        )
        conn.commit()
        return cur.rowcount

    def mark_read_up_to(self, group_id: int | None, user_id: int | None, timestamp: int) -> int:
        """Mark all events up to timestamp as read for a conversation.

        If group_id is given, marks group messages.
        If user_id is given, marks private messages.
        Both can be None to mark all.

        Returns count of events updated.
        """
        import time
        now = int(time.time())
        conn = self._get_conn()
        where = ["read_timestamp IS NULL", "timestamp <= ?"]
        params = [timestamp]
        if group_id is not None:
            where.append("group_id = ?")
            params.append(group_id)
        if user_id is not None:
            where.append("user_id = ?")
            params.append(user_id)
        cur = conn.execute(
            f"UPDATE events SET read_timestamp = ?, seen = 1 WHERE {' AND '.join(where)}",
            [int(time.time())] + params,
        )
        conn.commit()
        return cur.rowcount

    def get_unread_count(self, group_id: int | None = None, user_id: int | None = None) -> int:
        """Get count of unread (not read) events for a conversation."""
        conn = self._get_conn()
        where = ["read_timestamp IS NULL"]
        params = []
        if group_id is not None:
            where.append("group_id = ?")
            params.append(group_id)
        if user_id is not None:
            where.append("user_id = ?")
            params.append(user_id)
        cur = conn.execute(
            f"SELECT COUNT(*) FROM events WHERE {' AND '.join(where)}",
            params,
        )
        return cur.fetchone()[0]

    def get_seen_status(self, event_ids: list[int]) -> dict[int, bool]:
        """Get seen status for a list of event IDs."""
        if not event_ids:
            return {}
        conn = self._get_conn()
        placeholders = ",".join("?" * len(event_ids))
        cur = conn.execute(
            f"SELECT id, seen FROM events WHERE id IN ({','.join('?' * len(event_ids))})",
            event_ids,
        )
        return {row[0]: bool(row[1]) for row in cur.fetchall()}

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
