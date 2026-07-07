"""HTTP client for the napcat daemon provider (port 18821)."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from napcat_cli.lib.message import format_message, extract_file_paths


@dataclass
class ChatItem:
    """A conversation entry (group or friend)."""
    id: str              # group_id or user_id
    name: str            # display name
    kind: str            # "group" or "private"
    remark: str = ""     # remark set by user for friends
    last_message: str = ""
    last_sender: str = ""
    last_time: int = 0
    unread: int = 0


@dataclass
class Message:
    """A single message."""
    id: str
    sender_id: str
    sender_name: str
    content: str
    time: int
    is_self: bool = False


def _http_post(url: str, body: dict, timeout: int = 10) -> dict:
    """Synchronous HTTP POST helper using urllib."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}


# Resolve the napcat CLI script path (same repo)
_NAPCAT_SCRIPT = str(Path(__file__).resolve().parent.parent / "napcat")


class DaemonClient:
    """Client for the napcat daemon HTTP provider on port 18821."""

    def __init__(self, port: int | None = None) -> None:
        self.url = f"http://127.0.0.1:{port or int(os.environ.get('NAPCAT_HTTP_PORT', '18821'))}/invoke"

    async def call(self, action: str, params: dict | None = None) -> dict:
        """Call a daemon action via POST JSON (async, non-blocking)."""
        body = {"action": action, "params": params or {}}
        return await asyncio.to_thread(_http_post, self.url, body)

    async def get_events(self, limit: int = 100) -> list[dict]:
        """Get recent events from daemon."""
        result = await self.call("get_events", {"limit": limit})
        return result.get("events", [])

    async def get_alerts(self) -> list[dict]:
        """Get pending alerts."""
        result = await self.call("get_alerts", {})
        return result.get("alerts", [])

    async def get_friends(self) -> list[dict]:
        """Get friend list."""
        result = await self.call("napcat_get_friend_list", {})
        if result.get("retcode") == 0:
            return result.get("data", [])
        return []

    async def get_groups(self) -> list[dict]:
        """Get group list."""
        result = await self.call("napcat_get_group_list", {})
        if result.get("retcode") == 0:
            return result.get("data", [])
        return []

    async def get_message_history(self, message_type: str, target_id: str, count: int = 50, start_id: int = 0) -> list[dict]:
        """Get message history for a group or friend."""
        params: dict = {"count": count}
        if message_type == "group":
            params["group_id"] = str(target_id)
            action = "napcat_get_group_msg_history"
        else:
            params["user_id"] = str(target_id)
            action = "napcat_get_friend_msg_history"
        if start_id:
            params["message_seq"] = start_id
        result = await self.call(action, params)
        if result.get("retcode") == 0:
            data = result.get("data") or {}
            messages = data.get("messages", []) if isinstance(data, dict) else (data or [])
            # Add formatted_text and files to each message
            for msg in messages:
                msg_segments = msg.get("message", [])
                if isinstance(msg_segments, list):
                    msg["formatted_text"] = format_message(msg_segments)
                    msg["files"] = extract_file_paths(msg_segments)
                else:
                    msg["formatted_text"] = str(msg_segments)
                    msg["files"] = []
            return messages
        return []

    async def send_message(self, message_type: str, target_id: str, message: str) -> dict:
        """Send a message."""
        segments = [{"type": "text", "data": {"text": message}}]
        params: dict = {"message_type": message_type, "message": segments}
        if message_type == "group":
            params["group_id"] = str(target_id)
        else:
            params["user_id"] = str(target_id)
        return await self.call("napcat_send_msg", params)

    async def run_napcat_cli(self, args: list[str]) -> tuple[str, str, int]:
        """Run the napcat CLI command and return (stdout, stderr, code).
        This is the reuse path for slash commands — no rewrite."""
        def _run() -> tuple[str, str, int]:
            try:
                result = subprocess.run(
                    ["python3", _NAPCAT_SCRIPT] + args,
                    capture_output=True, text=True, timeout=15
                )
                return result.stdout, result.stderr, result.returncode
            except Exception as e:
                return "", str(e), 1
        return await asyncio.to_thread(_run)


# Cached instance
_client: DaemonClient | None = None

def get_client() -> DaemonClient:
    global _client
    if _client is None:
        _client = DaemonClient()
    return _client
