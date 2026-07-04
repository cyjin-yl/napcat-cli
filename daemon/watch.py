#!/usr/bin/env python3
"""NapCat Watch Daemon — NapCat WS listener + skills-fs HTTP provider.

Connects to NapCat's WebSocket server to receive real-time events,
writes them to the filesystem bridge, and generates alert files.

Also runs an HTTP server implementing the skills-fs HTTP provider contract,
so skills-fs can query events and proxy NapCat API calls.

Alert files generated:
- NAPCAT_CLI_NEW_MESSAGE: Any new message received
- NAPCAT_CLI_AT_ME: Bot was @mentioned
- NAPCAT_CLI_REPLY_TO_ME: Reply to bot's message
- NAPCAT_CLI_NEW_POKE: Poke received
- NAPCAT_CLI_NEW_REQUEST: Friend/group join request
- NAPCAT_CLI_NEED_WAKE_UP: Composite alert - agent should check

The daemon connects via WebSocket to the NapCat server.
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

# Allow running from any directory
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from lib.config import DATA_DIR, get_config
from lib.events import EventsWriter, EventsReader


# ---------------------------------------------------------------------------
# Event Processor (alert generation)
# ---------------------------------------------------------------------------

class EventProcessor:
    """Process events and generate alerts."""

    def __init__(self, data_dir: Path, self_id: str = "", wake_command: str = ""):
        self.writer = EventsWriter(data_dir)
        self.self_id = self_id
        self.wake_command = wake_command
        self.log_file = data_dir / "daemon.log"

    def log(self, msg: str) -> None:
        try:
            line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n"
            with open(self.log_file, "a") as f:
                f.write(line)
        except Exception:
            pass

    def process(self, event: dict) -> None:
        filename = self.writer.write_event(event)
        self.log(f"Event: {filename}")
        post_type = event.get("post_type", "")
        if post_type == "message":
            self._handle_message(event)
        elif post_type == "notice":
            self._handle_notice(event)
        elif post_type == "request":
            self._handle_request(event)
        elif post_type == "meta_event":
            self._handle_meta(event)

    def _handle_message(self, event: dict) -> None:
        msg_type = event.get("message_type", "")
        sender = event.get("sender", {})
        sender_id = str(sender.get("user_id", ""))
        nickname = sender.get("nickname", "")
        raw_msg = event.get("raw_message", event.get("message", ""))
        msg_id = str(event.get("message_id", ""))
        group_id = event.get("group_id", "")

        self.writer.write_alert("NAPCAT_CLI_NEW_MESSAGE", {
            "summary": f"{nickname}({sender_id}): {str(raw_msg)[:50]}",
            "sender_id": sender_id,
            "nickname": nickname,
            "message_type": msg_type,
            "group_id": str(group_id) if group_id else "",
            "message_id": msg_id,
            "raw_message": str(raw_msg),
            "time": event.get("time", 0),
        })

        if self.self_id:
            raw_str = str(raw_msg)
            if f"[CQ:at,qq={self.self_id}]" in raw_str:
                self.writer.write_alert("NAPCAT_CLI_AT_ME", {
                    "summary": f"@mentioned by {nickname} in {'group ' + str(group_id) if group_id else 'DM'}",
                    "sender_id": sender_id,
                    "group_id": str(group_id) if group_id else "",
                    "message_id": msg_id,
                })
                self._wake("AT_ME")

            msg_segments = event.get("message", [])
            if isinstance(msg_segments, list):
                for seg in msg_segments:
                    if isinstance(seg, dict) and seg.get("type") == "reply":
                        data = seg.get("data", {})
                        if isinstance(data, str):
                            try:
                                import urllib.parse
                                data = json.loads(urllib.parse.unquote(data))
                            except Exception:
                                pass
                        if isinstance(data, dict) and str(data.get("id", "")) == msg_id:
                            self.writer.write_alert("NAPCAT_CLI_REPLY_TO_ME", {
                                "summary": f"Reply from {nickname}",
                                "sender_id": sender_id,
                                "group_id": str(group_id) if group_id else "",
                                "message_id": msg_id,
                            })
                            self._wake("REPLY_TO_ME")
                            break

    def _handle_notice(self, event: dict) -> None:
        notice_type = event.get("notice_type", "")
        sub_type = event.get("sub_type", "")

        # --- notify events (poke, profile_like, lucky_king) ---
        if notice_type == "notify":
            self._handle_notify(event, sub_type)
            return

        # --- group events ---
        if notice_type == "group_admin":
            self._handle_group_admin(event)
            return
        if notice_type == "group_ban":
            self._handle_group_ban(event)
            return
        if notice_type == "group_decrease":
            self._handle_group_decrease(event, sub_type)
            return
        if notice_type == "group_increase":
            self._handle_group_increase(event, sub_type)
            return
        if notice_type == "group_upload":
            self._handle_group_upload(event)
            return
        if notice_type == "group_recall":
            self._handle_group_recall(event)
            return
        if notice_type == "group_card":
            self._handle_group_card(event)
            return
        if notice_type == "group_msg_emoji_like":
            self._handle_group_emoji_like(event)
            return

        # --- friend events ---
        if notice_type == "friend_add":
            self._handle_friend_add(event)
            return
        if notice_type == "friend_recall":
            self._handle_friend_recall(event)
            return

        # --- catch-all for unknown notice types ---
        self.writer.write_alert("NAPCAT_CLI_NOTICE", {
            "summary": f"Notice: {notice_type}/{sub_type}",
            "notice_type": notice_type,
            "sub_type": sub_type,
        })

    # ---- notify sub-handlers ----

    def _handle_notify(self, event: dict, sub_type: str) -> None:
        if sub_type == "poke":
            sender_id = str(event.get("user_id", ""))
            group_id = event.get("group_id", "")
            target_id = str(event.get("target_id", ""))
            self.writer.write_alert("NAPCAT_CLI_NEW_POKE", {
                "summary": f"Poke from {sender_id}{' in group ' + str(group_id) if group_id else ''}",
                "sender_id": sender_id,
                "target_id": target_id,
                "group_id": str(group_id) if group_id else "",
            })
            # Wake if bot was poked
            if self.self_id and target_id == self.self_id:
                self._wake("NEW_POKE")
        elif sub_type == "lucky_king":
            self.writer.write_alert("NAPCAT_CLI_NEW_POKE", {
                "summary": "Lucky king (red packet)",
                "group_id": str(event.get("group_id", "")),
                "user_id": str(event.get("user_id", "")),
            })
        elif sub_type == "profile_like":
            operator_id = str(event.get("operator_id", ""))
            operator_nick = event.get("operator_nick", "")
            times = event.get("times", 0)
            self.writer.write_alert("NAPCAT_CLI_NEW_POKE", {
                "summary": f"{operator_nick}({operator_id}) liked profile {times} times",
                "operator_id": operator_id,
                "operator_nick": operator_nick,
                "times": times,
                "sub_type": "profile_like",
            })
            self._wake("PROFILE_LIKE")

    # ---- group sub-handlers ----

    def _handle_group_admin(self, event: dict) -> None:
        sub = event.get("sub_type", "")
        user_id = str(event.get("user_id", ""))
        group_id = str(event.get("group_id", ""))
        action = "promoted" if sub == "set" else "demoted"
        self.writer.write_alert("NAPCAT_CLI_NOTICE", {
            "summary": f"Admin {action}: {user_id} in group {group_id}",
            "notice_type": "group_admin",
            "sub_type": sub,
            "user_id": user_id,
            "group_id": group_id,
        })
        # Wake if bot's admin status changed
        if self.self_id and user_id == self.self_id:
            self._wake("GROUP_ADMIN_CHANGE")

    def _handle_group_ban(self, event: dict) -> None:
        sub = event.get("sub_type", "")
        user_id = str(event.get("user_id", ""))
        group_id = str(event.get("group_id", ""))
        operator_id = str(event.get("operator_id", ""))
        duration = event.get("duration", 0)
        action = "banned" if sub == "ban" else "unbanned"
        self.writer.write_alert("NAPCAT_CLI_NOTICE", {
            "summary": f"{user_id} {action} in group {group_id} by {operator_id} ({duration}s)",
            "notice_type": "group_ban",
            "sub_type": sub,
            "user_id": user_id,
            "group_id": group_id,
            "operator_id": operator_id,
            "duration": duration,
        })
        # Wake if bot was banned
        if self.self_id and user_id == self.self_id and sub == "ban":
            self._wake("BOT_BANNED")

    def _handle_group_decrease(self, event: dict, sub_type: str) -> None:
        user_id = str(event.get("user_id", ""))
        group_id = str(event.get("group_id", ""))
        operator_id = str(event.get("operator_id", ""))
        self.writer.write_alert("NAPCAT_CLI_NOTICE", {
            "summary": f"Member {user_id} left group {group_id} ({sub_type}) by {operator_id}",
            "notice_type": "group_decrease",
            "sub_type": sub_type,
            "user_id": user_id,
            "group_id": group_id,
            "operator_id": operator_id,
        })
        # Wake if bot was kicked
        if sub_type == "kick_me" and self.self_id:
            self._wake("BOT_KICKED_FROM_GROUP")
        elif sub_type == "disband":
            self._wake("GROUP_DISBANDED")

    def _handle_group_increase(self, event: dict, sub_type: str) -> None:
        user_id = str(event.get("user_id", ""))
        group_id = str(event.get("group_id", ""))
        operator_id = str(event.get("operator_id", ""))
        self.writer.write_alert("NAPCAT_CLI_NOTICE", {
            "summary": f"New member {user_id} joined group {group_id} ({sub_type}) by {operator_id}",
            "notice_type": "group_increase",
            "sub_type": sub_type,
            "user_id": user_id,
            "group_id": group_id,
            "operator_id": operator_id,
        })
        self._wake("NEW_GROUP_MEMBER")

    def _handle_group_upload(self, event: dict) -> None:
        file_info = event.get("file", {})
        self.writer.write_alert("NAPCAT_CLI_NOTICE", {
            "summary": f"File uploaded by {event.get('user_id')} in group {event.get('group_id')}: {file_info.get('name', '?')}",
            "notice_type": "group_upload",
            "group_id": str(event.get("group_id", "")),
            "user_id": str(event.get("user_id", "")),
            "file_name": file_info.get("name", ""),
            "file_id": file_info.get("id", ""),
            "file_size": file_info.get("size", 0),
        })

    def _handle_group_recall(self, event: dict) -> None:
        user_id = str(event.get("user_id", ""))
        operator_id = str(event.get("operator_id", ""))
        group_id = str(event.get("group_id", ""))
        msg_id = str(event.get("message_id", ""))
        self.writer.write_alert("NAPCAT_CLI_NOTICE", {
            "summary": f"Message {msg_id} recalled in group {group_id} by {operator_id} (sender: {user_id})",
            "notice_type": "group_recall",
            "group_id": group_id,
            "user_id": user_id,
            "operator_id": operator_id,
            "message_id": msg_id,
        })
        # Wake if bot's own message was recalled
        if self.self_id and user_id == self.self_id:
            self._wake("MY_MESSAGE_RECALLED")

    def _handle_group_card(self, event: dict) -> None:
        user_id = str(event.get("user_id", ""))
        group_id = str(event.get("group_id", ""))
        card_new = event.get("card_new", "")
        card_old = event.get("card_old", "")
        self.writer.write_alert("NAPCAT_CLI_NOTICE", {
            "summary": f"Card changed for {user_id} in group {group_id}: '{card_old}' → '{card_new}'",
            "notice_type": "group_card",
            "group_id": group_id,
            "user_id": user_id,
            "card_old": card_old,
            "card_new": card_new,
        })

    def _handle_group_emoji_like(self, event: dict) -> None:
        user_id = str(event.get("user_id", ""))
        group_id = str(event.get("group_id", ""))
        msg_id = str(event.get("message_id", ""))
        likes = event.get("likes", [])
        emoji_ids = [l.get("emoji_id", "") for l in likes]
        is_add = event.get("is_add", True)
        action = "reacted" if is_add else "removed reaction from"
        self.writer.write_alert("NAPCAT_CLI_NOTICE", {
            "summary": f"{user_id} {action} message {msg_id} with {emoji_ids}",
            "notice_type": "group_msg_emoji_like",
            "group_id": group_id,
            "user_id": user_id,
            "message_id": msg_id,
            "likes": likes,
            "is_add": is_add,
        })

    # ---- friend sub-handlers ----

    def _handle_friend_add(self, event: dict) -> None:
        user_id = str(event.get("user_id", ""))
        self.writer.write_alert("NAPCAT_CLI_NEW_REQUEST", {
            "summary": f"New friend added: {user_id}",
            "notice_type": "friend_add",
            "user_id": user_id,
        })
        self._wake("NEW_FRIEND")

    def _handle_friend_recall(self, event: dict) -> None:
        user_id = str(event.get("user_id", ""))
        msg_id = str(event.get("message_id", ""))
        self.writer.write_alert("NAPCAT_CLI_NOTICE", {
            "summary": f"Friend {user_id} recalled message {msg_id}",
            "notice_type": "friend_recall",
            "user_id": user_id,
            "message_id": msg_id,
        })

    def _handle_request(self, event: dict) -> None:
        req_type = event.get("request_type", "")
        sub_type = event.get("sub_type", "")
        flag = event.get("flag", "")
        user_id = str(event.get("user_id", ""))
        comment = event.get("comment", "")
        alert_data: dict[str, Any] = {
            "summary": f"{req_type} request from {user_id}: {comment[:30]}",
            "request_type": req_type,
            "sub_type": sub_type,
            "user_id": user_id,
            "flag": flag,
            "comment": comment,
        }
        if req_type == "group":
            alert_data["group_id"] = str(event.get("group_id", ""))
            alert_data["summary"] = f"{req_type} {sub_type} request from {user_id} to group {event.get('group_id', '?')}: {comment[:30]}"
        self.writer.write_alert("NAPCAT_CLI_NEW_REQUEST", alert_data)
        self._wake("NEW_REQUEST")

    def _handle_meta(self, event: dict) -> None:
        sub_type = event.get("sub_type", "")
        if sub_type == "lifespan":
            status = event.get("status", "")
            self.log(f"Connection status: {status}")
            if status in ("down", "offline"):
                self.writer.write_alert("NAPCAT_CLI_NOTICE", {
                    "summary": "Bot connection lost",
                    "meta_type": "lifespan",
                    "status": status,
                })
                self._wake("BOT_OFFLINE")
        elif sub_type == "heartbeat":
            interval = event.get("interval", 0)
            self.log(f"Heartbeat ({interval}s)")

    def _wake(self, reason: str) -> None:
        self.writer.write_alert("NAPCAT_CLI_NEED_WAKE_UP", {
            "summary": f"Wake up needed: {reason}",
            "reason": reason,
            "timestamp": int(time.time()),
        })

        # Execute wake_command if configured
        if self.wake_command:
            self.log(f"Executing wake command: {self.wake_command}")
            try:
                import subprocess
                # Replace $REASON with actual reason
                cmd = self.wake_command.replace("$REASON", reason).replace("${REASON}", reason)
                subprocess.run(cmd, shell=True, check=True, timeout=30,
                             capture_output=True, text=True)
            except Exception as e:
                self.log(f"Wake command failed: {e}")


# ---------------------------------------------------------------------------
# In-memory event cache (for HTTP provider reads)
# ---------------------------------------------------------------------------

class EventCache:
    """Thread-safe in-memory cache of recent events."""

    def __init__(self, data_dir: Path, max_events: int = 500):
        self.data_dir = data_dir
        self.max_events = max_events
        self._lock = threading.Lock()
        self._events: list[dict[str, Any]] = []

    def add(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._events.insert(0, event)
            if len(self._events) > self.max_events:
                self._events = self._events[: self.max_events]

    def get(self, limit: int = 50, since: float = 0.0) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._events)
        if since:
            events = [e for e in events if e.get("time", 0) >= since]
        return events[:limit]


# ---------------------------------------------------------------------------
# WebSocket listener
# ---------------------------------------------------------------------------

async def ws_daemon(ws_url: str, processor: EventProcessor, cache: EventCache) -> None:
    """WebSocket daemon - connects to NapCat WS server."""
    retry_delay = 5

    while True:
        try:
            processor.log(f"Connecting to WebSocket: {ws_url}")

            try:
                import aiohttp
                session = aiohttp.ClientSession()

                async with session.ws_connect(ws_url, heartbeat=30) as ws:
                    processor.log(f"Connected to {ws_url}")

                    token = os.environ.get("NAPCAT_TOKEN", "")
                    if token:
                        await ws.send_json({
                            "time": int(time.time()),
                            "post_type": "connect",
                            "token": token,
                        })

                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                event = json.loads(msg.data)
                                processor.process(event)
                                cache.add(event)
                            except json.JSONDecodeError:
                                processor.log(f"Invalid JSON: {msg.data[:100]}")
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break

                await session.close()
                retry_delay = 5

            except ImportError:
                from websockets.connect import connect
                async with connect(ws_url, ping_interval=30) as ws:
                    processor.log(f"Connected to {ws_url}")
                    retry_delay = 5

                    async for message in ws:
                        try:
                            event = json.loads(message)
                            processor.process(event)
                            cache.add(event)
                        except json.JSONDecodeError:
                            processor.log(f"Invalid JSON: {str(message)[:100]}")

        except Exception as e:
            processor.log(f"Connection error: {e}")

        await asyncio.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 60)


# ---------------------------------------------------------------------------
# HTTP Provider (skills-fs compatible endpoint)
# ---------------------------------------------------------------------------

class NapCatHandler(BaseHTTPRequestHandler):
    """HTTP handler implementing skills-fs HTTP provider contract."""

    processor: EventProcessor = None
    cache: EventCache = None
    events_reader: EventsReader = None

    def log_message(self, format: str, *args: Any) -> None:
        pass  # suppress default logging

    def _send_json(self, data: Any, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _send_error(self, status: int, message: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(message.encode("utf-8"))

    def do_POST(self) -> None:
        if self.path not in ("/", "/invoke"):
            self._send_error(404, "Not found")
            return

        length = int(self.headers.get("Content-Length", 0))
        if not length:
            self._send_error(400, "Missing request body")
            return

        try:
            body = self.rfile.read(length)
            req = json.loads(body)
        except json.JSONDecodeError as e:
            self._send_error(400, f"Invalid JSON: {e}")
            return

        action = req.get("action", "")
        params = req.get("params", {})

        if not action:
            self._send_error(400, "Missing action")
            return

        try:
            result = self._dispatch(action, params)
            self._send_json(result)
        except Exception as e:
            self.processor.log(f"Action {action!r} failed: {e}")
            self._send_error(500, str(e))

    def _dispatch(self, action: str, params: dict) -> Any:
        """Dispatch action to handler. Returns data for skills-fs."""

        if action == "get_events":
            events = self.cache.get(
                limit=params.get("limit", 50),
                since=params.get("since", 0.0),
            )
            return {"events": events, "count": len(events)}

        if action == "get_event":
            event_id = params.get("id", "")
            events = self.events_reader.read(limit=1000)
            for e in events:
                if str(e.get("id", "")) == event_id or str(e.get("message_id", "")) == event_id:
                    return e
            return {"error": f"Event {event_id} not found"}

        if action == "get_alerts":
            alerts_dir = self.cache.data_dir / "alerts"
            alerts = []
            if alerts_dir.exists():
                for f in sorted(alerts_dir.iterdir()):
                    if f.name.endswith(".alert"):
                        try:
                            alerts.append(json.loads(f.read_text()))
                        except Exception:
                            alerts.append({"file": f.name, "error": "unreadable"})
            return {"alerts": alerts, "count": len(alerts)}

        if action == "clear_alert":
            name = params.get("name") or params.get("alert_name", "")
            return {"cleared": self.processor.writer.clear_alert(name)}

        if action == "clear_all_alerts":
            return {"cleared": self.processor.writer.clear_all_alerts()}

        # Group / message browsing: events stored by the WebSocket listener are
        # exposed as a navigable filesystem under napcat/groups/{group_id}/...
        if action == "list_groups":
            groups = set()
            for e in self.events_reader.read(limit=10000):
                gid = e.get("group_id")
                if gid:
                    groups.add(str(gid))
            return {"entries": [{"name": g, "kind": "dynamic_dir"} for g in sorted(groups)]}

        if action == "list_time_ranges":
            ranges = ["recent", "1days", "7days", "30days", "90days"]
            return {"entries": [{"name": r, "kind": "dynamic_dir"} for r in ranges]}

        if action == "list_messages":
            group_id = str(params.get("group_id", ""))
            time_range = params.get("time_range", "recent")
            now = time.time()
            cutoff = now
            if time_range == "recent":
                cutoff = now - 3600  # 1 hour
            elif time_range == "1days":
                cutoff = now - 86400
            elif time_range == "7days":
                cutoff = now - 7 * 86400
            elif time_range == "30days":
                cutoff = now - 30 * 86400
            elif time_range == "90days":
                cutoff = now - 90 * 86400
            messages = []
            for e in self.events_reader.read(limit=10000):
                if str(e.get("group_id", "")) != group_id:
                    continue
                if e.get("time", 0) < cutoff:
                    continue
                mid = str(e.get("message_id", e.get("id", "")))
                if mid:
                    messages.append({"name": mid, "kind": "api"})
            return {"entries": messages}

        if action == "get_message":
            group_id = str(params.get("group_id", ""))
            message_id = str(params.get("message_id", ""))
            for e in self.events_reader.read(limit=10000):
                if str(e.get("group_id", "")) != group_id:
                    continue
                if str(e.get("message_id", e.get("id", ""))) == message_id:
                    return e
            return {"error": f"Message {message_id} not found in group {group_id}"}
        # Proxy NapCat API calls through napcat_ prefix
        if action.startswith("napcat_"):
            from lib.api import NapCatAPI
            api = NapCatAPI()
            napcat_action = action.replace("napcat_", "", 1)
            result = api.request(napcat_action, method="POST", json_body=params)
            return result

        self._send_error(404, f"Unknown action: {action}")
        return None


def run_http_server(
    processor: EventProcessor,
    cache: EventCache,
    port: int = 18820,
    host: str = "0.0.0.0",
) -> HTTPServer:
    """Start HTTP provider in a background thread. Returns the server."""
    NapCatHandler.processor = processor
    NapCatHandler.cache = cache
    NapCatHandler.events_reader = EventsReader(cache.data_dir)

    server = HTTPServer((host, port), NapCatHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    processor.log(f"HTTP provider listening on {host}:{port}")
    return server


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_daemon(config_path: str) -> None:
    """Start the WebSocket daemon with HTTP provider."""
    cfg_data = json.loads(Path(config_path).read_text())
    self_id = cfg_data.get("self_id", "")
    wake_command = cfg_data.get("wake_command", "")
    ws_port = cfg_data.get("ws_port", 18800)
    ws_url = f"ws://127.0.0.1:{ws_port}"

    processor = EventProcessor(DATA_DIR, self_id, wake_command)
    cache = EventCache(DATA_DIR)

    # PID file
    pid_file = DATA_DIR / "daemon.pid"
    pid_file.write_text(str(os.getpid()))

    # Determine HTTP port
    http_port = int(os.environ.get("NAPCAT_HTTP_PORT", "18821"))

    # Start HTTP provider
    server = run_http_server(processor, cache, http_port)

    # Signal handling
    loop = asyncio.new_event_loop()

    def shutdown_handler(signum: int, frame: Any) -> None:
        processor.log("Shutting down...")
        pid_file.unlink(missing_ok=True)
        server.shutdown()
        loop.call_soon_threadsafe(loop.stop)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    processor.log(f"NapCat daemon started (PID: {os.getpid()})")
    processor.log(f"WebSocket URL: {ws_url}")
    processor.log(f"HTTP provider port: {http_port}")

    loop.run_until_complete(ws_daemon(ws_url, processor, cache))


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: watch.py <config_path>", file=sys.stderr)
        sys.exit(1)
    run_daemon(sys.argv[1])


if __name__ == "__main__":
    main()
