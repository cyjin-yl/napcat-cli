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
        )

    def read_alerts(
        self,
        name: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Read alerts from the database."""
        conn = self._get_conn()
        return read_alerts(conn, name=name, limit=limit)

    def get_count(self) -> int:
        """Get total event count."""
        from .events_sqlite import get_event_count
        conn = self._get_conn()
        return get_event_count(conn)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
