"""Event filesystem bridge.

Events are stored as individual JSON files under DATA_DIR/events/.
Alert files are stored as .alert files under DATA_DIR/alerts/.

This implements the skills-fs pattern: the filesystem IS the interface.
An agent reads events and alerts by scanning these directories.
"""
from __future__ import annotations

import json
import time
import uuid
import fcntl
from pathlib import Path
from typing import Any


class EventsWriter:
    """Write events and alerts to the filesystem."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.events_dir = data_dir / "events"
        self.alerts_dir = data_dir / "alerts"
        self.events_dir.mkdir(exist_ok=True)
        self.alerts_dir.mkdir(exist_ok=True)

    def write_event(self, event: dict[str, Any], event_type: str = "") -> str:
        """Write an event to the filesystem.

        Returns the event filename.
        """
        ts = event.get("time", int(time.time()))
        ptype = event.get("post_type", "unknown")

        if not event_type:
            event_type = event.get("notice_type", event.get("request_type", ptype))

        # 🔴 严重问题：竞态条件和性能问题
        # 问题1：在多进程/多线程环境下，多个进程可能同时检查文件是否存在，然后都尝试写入同名文件，导致数据丢失
        # 问题2：每次写入都要循环检查文件是否存在，在大量事件时性能很差
        # 问题3：文件命名冲突时使用循环递增计数器，在高并发场景下不可靠
        # 必须改进：使用原子性文件操作（如O_EXCL标志）或添加进程锁（fcntl/flock）
        # Filename: {timestamp}_{post_type}_{event_type}_{counter}.json
        filename = f"{ts}_{ptype}_{event_type}.json"
        path = self.events_dir / filename

        # Avoid overwriting: append counter if exists
        counter = 0
        while path.exists():
            counter += 1
            filename = f"{ts}_{ptype}_{event_type}_{counter}.json"
            path = self.events_dir / filename

        # 使用UUID保证文件名唯一性，避免竞态条件
        unique_filename = f"{uuid.uuid4().hex}_{filename}"
        path = self.events_dir / unique_filename

        # 使用临时文件+原子重命名确保写入安全
        temp_path = path.with_suffix('.tmp')
        temp_path.write_text(json.dumps(event, indent=2, ensure_ascii=False))
        temp_path.replace(path)  # 原子性重命名

        return unique_filename

    def write_alert(self, alert_name: str, data: dict[str, Any]) -> Path:
        """Write an alert file. Alert files trigger agent attention.

        Alert naming convention:
        - NAPCAT_CLI_NEW_MESSAGE: New message received
        - NAPCAT_CLI_NEW_POKE: Poke received
        - NAPCAT_CLI_NEED_WAKE_UP: Agent should be woken up
        - NAPCAT_CLI_NEW_REQUEST: Friend/group request
        - NAPCAT_CLI_AT_ME: Bot was @mentioned
        - NAPCAT_CLI_REPLY_TO_ME: Reply to bot's message
        """
        alert_path = self.alerts_dir / f"{alert_name}.alert"
        alert_data = {
            "name": alert_name,
            "timestamp": int(time.time()),
            **data,
        }

        # 🔴 严重问题：竞态条件和数据结构不一致
        # 问题1：读取-修改-写入操作没有原子性保护，多个进程同时操作会导致数据丢失
        # 问题2：异常处理逻辑不完整，可能导致数据结构不一致
        # 问题3：alerts列表不断增长，从不清理，长期运行会导致文件过大
        # 必须改进：使用文件锁（fcntl/flock）或原子性重命名操作，添加定期清理机制
        # If alert already exists, append to a list
        if alert_path.exists():
            try:
                existing = json.loads(alert_path.read_text())
                if isinstance(existing, dict) and "alerts" in existing:
                    existing["alerts"].append(alert_data)
                    existing["count"] = len(existing["alerts"])
                    existing["last"] = alert_data["timestamp"]
                else:
                    existing = {"alerts": [existing, alert_data], "count": 2, "last": alert_data["timestamp"]}
            except Exception:
                existing = {"alerts": [alert_data], "count": 1, "last": alert_data["timestamp"]}
        else:
            existing = {"alerts": [alert_data], "count": 1, "last": alert_data["timestamp"]}

        # 使用临时文件+原子重命名确保写入安全
        temp_alert_path = alert_path.with_suffix('.tmp')
        temp_alert_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False))
        temp_alert_path.replace(alert_path)  # 原子性重命名

        return alert_path

    def clear_alert(self, name: str) -> bool:
        """Clear a specific alert file."""
        path = self.alerts_dir / f"{name}.alert"
        if path.exists():
            path.unlink()
            return True
        return False

    def clear_all_alerts(self) -> int:
        """Clear all alert files. Returns count of cleared files."""
        count = 0
        for f in self.alerts_dir.glob("*.alert"):
            f.unlink()
            count += 1
        return count


class EventsReader:
    """Read events from the filesystem."""

    def __init__(self, data_dir: Path):
        self.events_dir = data_dir / "events"

    def read(
        self,
        limit: int = 50,
        event_type: str | None = None,
        since: int | None = None,
    ) -> list[dict[str, Any]]:
        """Read events from filesystem, newest first."""
        if not self.events_dir.exists():
            return []

        events = []
        for f in sorted(self.events_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if len(events) >= limit:
                break

            # Extract stem for both type and time filters
            stem = f.stem

            # Filter by type
            if event_type:
                if event_type not in stem:
                    continue

            # Filter by time
            if since:
                ts_str = stem.split("_")[0]
                try:
                    ts = int(ts_str)
                    if ts < since:
                        continue
                except ValueError:
                    pass

            try:
                data = json.loads(f.read_text())
                events.append(data)
            except Exception:
                continue

        return events
