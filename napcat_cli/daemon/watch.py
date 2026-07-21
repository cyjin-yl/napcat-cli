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
import logging
import os
import signal
import sys
import time
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# Allow running from any directory
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from napcat_cli.lib.config import DATA_DIR, get_config
from napcat_cli.lib.events import EventsWriter, EventsReader

from napcat_cli.daemon.schemas import ACTION_SCHEMAS
from napcat_cli.wake_presets import build_waker
from napcat_cli.wake_orchestrator import WakeOrchestrator


# ---------------------------------------------------------------------------
# Event Processor (alert generation)
# ---------------------------------------------------------------------------

def _make_rotating_logger(path: Path, max_bytes: int = 2_000_000, backup_count: int = 5) -> logging.Logger:
    """A per-data-dir logger writing to ``path`` with size-based rotation.

    Keeps daemon.log bounded: at ~2 MB it rolls to daemon.log.1 .. daemon.log.5
    (olest deleted), so it can never fill the disk. Idempotent — re-creating it
    for the same path does not stack duplicate handlers.
    """
    logger = logging.getLogger(f"napcat.daemon.{path.parent.name}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not any(getattr(h, "_napcat_marker", False) for h in logger.handlers):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            h = RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8")
            h._napcat_marker = True  # type: ignore[attr-defined]
            h.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
            logger.addHandler(h)
        except Exception:
            pass
    return logger


_TIMEOUT = object()  # sentinel returned by _run_with_timeout on timeout


def _run_with_timeout(fn, timeout: float, *args):
    """Run ``fn(*args)`` in a daemon thread, returning its result.

    If it doesn't finish within ``timeout`` seconds, returns ``_TIMEOUT`` and
    abandons the thread. This is the D-state prevention primitive: a hung FUSE
    syscall (status read / stat) only blocks the abandoned thread, never the
    daemon's main thread or asyncio loop, so the process can't get wedged.
    """
    box: dict = {"done": False, "result": None}

    def _runner():
        try:
            box["result"] = fn(*args)
        except Exception as e:  # a raised exception => not healthy
            box["result"] = e
        box["done"] = True

    t = threading.Thread(target=_runner, daemon=True)
    t.start()
    t.join(timeout)
    return box["result"] if box["done"] else _TIMEOUT


class EventProcessor:
    """Process events and generate alerts."""

    def __init__(self, data_dir: Path, self_id: str = "", wake_command: str = "", wake_on_event: bool = True, *, group_trigger_word: str = "", private_trigger: str = "",
                 waker=None, orchestrator=None,
                 debounce_seconds: float = 3.0, cooldown_seconds: float = 30.0,
                 new_message_idle_seconds: int = 600):
        self.writer = EventsWriter(data_dir)
        self.self_id = self_id
        self.wake_command = wake_command
        self.wake_on_event = wake_on_event
        self.group_trigger_word = group_trigger_word
        self.private_trigger = private_trigger
        self.log_file = data_dir / "daemon.log"
        self._logger = _make_rotating_logger(self.log_file)
        # Wake orchestrator (built by run_daemon from config). When present it
        # owns debounce/cooldown/backlog + the Waker (http->cli auto-fallback).
        # When absent, _wake falls back to the legacy wake_command shell string.
        if orchestrator is not None:
            self.orchestrator = orchestrator
        elif waker is not None:
            self.orchestrator = WakeOrchestrator(
                waker, log=self.log,
                debounce_seconds=debounce_seconds, cooldown_seconds=cooldown_seconds,
                new_message_idle_seconds=new_message_idle_seconds,
                legacy_command=wake_command)
        else:
            self.orchestrator = None

    def log(self, msg: str) -> None:
        try:
            self._logger.info(msg)
        except Exception:
            pass

    def process(self, event: dict) -> None:
        row_id = self.writer.write_event(event)
        self.log(f"Event: row_id={row_id}")
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
        # Track unread messages for the backlog sweep (NEW_MESSAGE itself does
        # not wake; the sweep wakes the agent if they pile up unread).
        if self.orchestrator is not None:
            self.orchestrator.note_new_message(event.get("time") or time.time())

        if self.self_id:
            raw_str = str(raw_msg)
            if f"[CQ:at,qq={self.self_id}]" in raw_str:
                self.writer.write_alert("NAPCAT_CLI_AT_ME", {
                    "summary": f"@mentioned by {nickname} in {'group ' + str(group_id) if group_id else 'DM'}",
                    "sender_id": sender_id,
                    "group_id": str(group_id) if group_id else "",
                    "message_id": msg_id,
                })
                self._wake("AT_ME", event)

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
                            self._wake("REPLY_TO_ME", event)
                            break

        # --- Trigger word detection on plain text (no CQ codes) ---
        segs = event.get("message", [])
        plain_text = "".join(
            (seg.get("data") or {}).get("text", "")
            for seg in (segs if isinstance(segs, list) else [])
            if isinstance(seg, dict) and seg.get("type") == "text"
        )
        if msg_type == "group" and self.group_trigger_word and self.group_trigger_word in plain_text:
            self._wake("GROUP_TRIGGER", event)
        elif msg_type == "private":
            if self.private_trigger == "*" or (self.private_trigger and self.private_trigger in plain_text):
                self._wake("PRIVATE_TRIGGER", event)

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
                self._wake("NEW_POKE", event)
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
            self._wake("PROFILE_LIKE", event)

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
            self._wake("GROUP_ADMIN_CHANGE", event)

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
            self._wake("BOT_BANNED", event)

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
            self._wake("BOT_KICKED_FROM_GROUP", event)
        elif sub_type == "disband":
            self._wake("GROUP_DISBANDED", event)

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
        self._wake("NEW_GROUP_MEMBER", event)

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
            self._wake("MY_MESSAGE_RECALLED", event)

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
        self._wake("NEW_FRIEND", event)

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
        self._wake("NEW_REQUEST", event)

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
                self._wake("BOT_OFFLINE", event)
        elif sub_type == "heartbeat":
            interval = event.get("interval", 0)
            self.log(f"Heartbeat ({interval}s)")

    @staticmethod
    def _event_brief(event: dict | None) -> str:
        """One-line, grep-friendly summary of the triggering event for logs."""
        if not event:
            return "(no event)"
        sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
        who = sender.get("nickname") or event.get("user_id") or "?"
        g = event.get("group_id")
        where = f"group{g}" if g else "dm"
        msg = event.get("message") if event.get("message") is not None else event.get("raw_message", "")
        if isinstance(msg, list):
            text = "".join(
                (s.get("data") or {}).get("text", "")
                for s in msg if isinstance(s, dict) and s.get("type") == "text"
            )
        else:
            text = str(msg)
        return f"who={who} where={where} text={text[:40]!r}"

    def _wake(self, reason: str, event: dict | None = None) -> None:
        if not self.wake_on_event:
            self.log(f"[WAKE] disabled, skip reason={reason}")
            return

        self.log(f"[WAKE] trigger reason={reason} {self._event_brief(event)}")
        self.writer.write_alert("NAPCAT_CLI_NEED_WAKE_UP", {
            "summary": f"Wake up needed: {reason}",
            "reason": reason,
            "timestamp": int(time.time()),
        })

        # Preferred path: hand to the orchestrator (debounce/cooldown/backlog +
        # Waker with http->cli auto-fallback; also owns the legacy_command escape
        # hatch when no backend is configured).
        if self.orchestrator is not None:
            self.orchestrator.submit(reason, event)
            return

        # Legacy path: run wake_command as a shell string (back-compat).
        if self.wake_command:
            self.log(f"Executing wake command: {self.wake_command}")
            try:
                import subprocess
                from napcat_cli.wake import build_wake_command
                cmd = build_wake_command(self.wake_command, reason)
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
# Skills-fs FUSE Manager — spawn, monitor, restart on crash
# ---------------------------------------------------------------------------

_DEFAULT_MOUNTPOINT = str(Path.home() / ".napcat-data" / "skills")
_DEFAULT_SKILLSFS_CONFIG = str(Path.home() / ".napcat-data" / "skills-fs.json")
def _resolve_shipped_binary() -> str:
    """Return the shipped skills-fs binary path for source-tree dev mode, or '' if absent."""
    shipped = Path(__file__).resolve().parents[2] / "skills-fs" / "skills-fs"
    return str(shipped) if shipped.exists() and shipped.is_file() else ""


class SkillsFsManager:
    """Spawn skills-fs FUSE daemon and monitor its health.

    Lifecycle:
    1. After HTTP provider starts, spawn skills-fs with --daemon --pidfile.
    2. Read PID from pidfile and monitor with os.kill(pid, 0).
    3. If process dies, write degraded status file, restart with backoff.
    4. On shutdown, kill the child and unmount.
    """

    def __init__(
        self,
        processor: EventProcessor,
        mountpoint: str = _DEFAULT_MOUNTPOINT,
        binary: str = "",
        config: str = _DEFAULT_SKILLSFS_CONFIG,
        pidfile: str = "",
    ):
        self.processor = processor
        self.mountpoint = mountpoint
        self.pidfile = pidfile or (DATA_DIR / "skills-fs.pid").as_posix()
        self.config = config

        # Resolve binary: config > shipped > PATH
        self.binary = binary
        if not self.binary:
            # Try shipped binary next to this repo
            shipped = Path(_resolve_shipped_binary())
            if shipped.exists() and shipped.is_file():
                self.binary = str(shipped)
            else:
                # Search PATH
                import shutil
                found = shutil.which("skills-fs")
                if found:
                    self.binary = found
        self._pid: int | None = None
        self._status: str = "stopped"  # healthy | degraded | stopped
        self._max_restarts = 3
        self._restart_count = 0
        self._stale_cleaned = False

    @property
    def status(self) -> str:
        return self._status

    def start(self) -> bool:
        """Spawn skills-fs FUSE daemon. Returns True if mount succeeded."""
        if not self.binary:
            self.processor.log("skills-fs: no binary found, skipping mount")
            self._status = "degraded"
            self._write_degraded("skills-fs binary not found")
            return False

        # Reuse an already-healthy mount instead of stacking a second FUSE daemon
        # (multiple skills-fs on one mountpoint is what deadlocks into D-state).
        if self._existing_mount_healthy():
            self._status = "healthy"
            self._restart_count = 0
            self.processor.log(f"skills-fs: reusing existing healthy mount at {self.mountpoint}")
            return True

        # Clean stale mount before starting
        if not self._stale_cleaned:
            self._clean_stale_mount()
            self._stale_cleaned = True

        args = [
            self.binary, "fuse",
            "--config", self.config,
            "--mountpoint", self.mountpoint,
            "--allow-other",
            "--pidfile", self.pidfile,
            "--log-file", str(DATA_DIR / "skills-fs.log"),
            "--log-level", "info",
            "--daemon",
        ]
        try:
            self.processor.log(f"skills-fs: spawning {args}")
            import subprocess
            subprocess.run(args, check=True, timeout=10)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            self.processor.log(f"skills-fs: spawn failed: {e}")
            self._status = "degraded"
            self._write_degraded(f"spawn failed: {e}")
            return False

        # Read PID from pidfile (daemon mode writes it)
        self._wait_for_pid(max_wait=5)
        if self._pid is None:
            self.processor.log("skills-fs: pidfile not written in time")
            self._status = "degraded"
            self._write_degraded("pid not available")
            return False

        # Verify mount is accessible (timeout-guarded). If the mount isn't healthy
        # within the deadline, kill the child so we don't leave a half-dead FUSE
        # daemon that would deadlock readers into D-state.
        if not self._verify_mount():
            self.processor.log("skills-fs: mount not healthy after spawn — killing child, going degraded")
            self._kill_child()
            self._status = "degraded"
            self._write_degraded("mount verification failed or timed out")
            return False

        self._status = "healthy"
        self._restart_count = 0
        self.processor.log(f"skills-fs: mounted at {self.mountpoint} (PID {self._pid})")
        return True

    def check(self) -> bool:
        """Check if the FUSE process is still alive. Returns True if healthy."""
        if self._pid is None:
            self._status = "stopped"
            return False

        try:
            os.kill(self._pid, 0)
        except OSError:
            # Process is dead
            self._status = "degraded"
            self._write_degraded(f"skills-fs process {self._pid} died")
            self._pid = None
            self._stale_cleaned = False  # allow re-clean on restart
            return False

        # Also verify the mount is still accessible
        if not self._verify_mount():
            self._status = "degraded"
            self._write_degraded("mount no longer accessible")
            return False

        self._status = "healthy"
        return True

    def restart(self) -> bool:
        """Try to restart skills-fs after a crash."""
        self._restart_count += 1
        if self._restart_count > self._max_restarts:
            self._status = "degraded"
            self._write_degraded(
                f"skills-fs crashed {self._restart_count} times, giving up"
            )
            self.processor.log(
                f"skills-fs: exceeded max restarts ({self._max_restarts}), degraded"
            )
            return False

        # Exponential backoff before restart
        delay = min(2 ** self._restart_count, 30)
        self.processor.log(f"skills-fs: restarting in {delay}s (attempt {self._restart_count})")
        import time as _time
        _time.sleep(delay)

        return self.start()

    def stop(self) -> None:
        """Kill the child process and unmount."""
        if self._pid is not None:
            try:
                os.kill(self._pid, signal.SIGTERM)
                self.processor.log(f"skills-fs: sent SIGTERM to PID {self._pid}")
            except OSError:
                pass
        # Lazy unmount to release any blocked readers
        self._unmount()
        # Clean up pidfile
        try:
            Path(self.pidfile).unlink(missing_ok=True)
        except Exception:
            pass
        self._pid = None
        self._status = "stopped"

    # --- internal helpers ---

    def _wait_for_pid(self, max_wait: int = 5) -> None:
        """Wait for the pidfile to appear and read the PID."""
        for _ in range(max_wait * 10):
            try:
                data = Path(self.pidfile).read_text().strip()
                self._pid = int(data)
                return
            except (FileNotFoundError, ValueError):
                import time as _time
                _time.sleep(0.1)

    def _verify_mount(self, timeout: float = 8.0) -> bool:
        """Check that the mountpoint is accessible.

        Timeout-guarded: a hung FUSE daemon can never put this process into
        uninterruptible (D) sleep — the blocking probe runs in a daemon thread
        that is abandoned if it doesn't return within ``timeout``.
        """
        def _probe() -> bool:
            status_file = Path(self.mountpoint) / "status"
            if status_file.exists():
                status_file.read_text()
                return True
            os.stat(self.mountpoint)
            import subprocess
            result = subprocess.run(["mount"], capture_output=True, text=True, timeout=3)
            return self.mountpoint in result.stdout

        res = _run_with_timeout(_probe, timeout)
        if res is _TIMEOUT:
            self.processor.log(f"skills-fs: mount verify timed out ({timeout}s) — hung FUSE?")
            return False
        return res is True

    def _existing_mount_healthy(self) -> bool:
        """True if a skillsfs FUSE mount at our mountpoint is already up and
        responsive. Used to REUSE an existing mount instead of stacking a second
        FUSE daemon on the same point (stacking is what deadlocks into D-state).
        """
        try:
            import subprocess
            r = subprocess.run(["mount"], capture_output=True, text=True, timeout=3)
            if self.mountpoint not in r.stdout:
                return False
        except Exception:
            return False
        if not self._verify_mount(timeout=6.0):
            return False
        # adopt the existing pidfile so we can manage/stop it later
        self._wait_for_pid(max_wait=1)
        return True

    def _kill_child(self) -> None:
        """SIGTERM then SIGKILL the skills-fs child; don't leave a half-dead FUSE."""
        if self._pid is None:
            return
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(self._pid, sig)
            except OSError:
                break
            import time as _time
            _time.sleep(0.5)
            try:
                os.kill(self._pid, 0)
            except OSError:
                break
        self._unmount()
        try:
            Path(self.pidfile).unlink(missing_ok=True)
        except Exception:
            pass
        self._pid = None

    def _clean_stale_mount(self) -> None:
        """Lazy-unmount any stale FUSE mount at our mountpoint."""
        try:
            import subprocess
            result = subprocess.run(
                ["umount", "-l", self.mountpoint],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                self.processor.log(f"skills-fs: cleaned stale mount at {self.mountpoint}")
        except Exception as e:
            self.processor.log(f"skills-fs: stale mount cleanup error: {e}")

    def _unmount(self) -> None:
        """Unmount the FUSE filesystem."""
        try:
            import subprocess
            subprocess.run(
                ["umount", self.mountpoint],
                capture_output=True, text=True, timeout=5,
            )
        except Exception:
            try:
                subprocess.run(
                    ["umount", "-l", self.mountpoint],
                    capture_output=True, text=True, timeout=5,
                )
            except Exception:
                pass

    def _write_degraded(self, reason: str) -> None:
        """Write a degraded status file so skills-fs consumers know the mount is unavailable."""
        degraded_file = Path(self.mountpoint) / "SKILLS_FS_DEGRADED"
        try:
            info = {
                "status": "degraded",
                "reason": reason,
                "timestamp": time.time(),
                "message": "skills-fs FUSE mount is unavailable. Static files (SKILL.md, AGENTS.md) are still accessible but API endpoints will fail.",
            }
            Path(self.mountpoint).mkdir(parents=True, exist_ok=True)
            degraded_file.write_text(json.dumps(info, indent=2, ensure_ascii=False))
        except Exception:
            pass


async def skillsfs_monitor_task(manager: SkillsFsManager, interval: int = 10) -> None:
    """Background task that periodically checks skills-fs health and restarts on failure."""
    while True:
        await asyncio.sleep(interval)
        if manager.status == "stopped":
            continue
        if not manager.check():
            manager.restart()
# ---------------------------------------------------------------------------
# HTTP Provider (skills-fs compatible endpoint)
# ---------------------------------------------------------------------------

class NapCatHandler(BaseHTTPRequestHandler):
    """HTTP handler implementing skills-fs HTTP provider contract."""

    processor: EventProcessor = None
    cache: EventCache = None
    events_reader: EventsReader = None
    skillsfs_manager: SkillsFsManager = None

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


    def do_GET(self) -> None:
        """Handle GET requests with parameterized query support.

        Supports:
        - GET /events?since=&type=&limit=&post_type=
        - GET /alerts?name=&limit=
        - GET /invoke?action=...&param=value  (read-only API actions proxied via GET)
        - GET /status  (bot online status)
        """
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query)

        def _first(k: str, default: str = "") -> str:
            return qs.get(k, [default])[0]

        def _int(k: str, default: int = 0) -> int:
            try:
                return int(_first(k, str(default)))
            except ValueError:
                return default

        if path == "/events":
            try:
                result = self._dispatch("get_events", {
                    "limit": _int("limit", 50),
                    "since": _first("since", "") or None,
                    "post_type": _first("post_type") or None,
                    "event_type": _first("event_type") or None,
                })
                self._send_json(result)
            except Exception as e:
                self._send_error(500, str(e))
            return

        if path == "/alerts":
            try:
                result = self._dispatch("get_alerts", {
                    "name": _first("name") or None,
                    "limit": _int("limit", 50),
                })
                self._send_json(result)
            except Exception as e:
                self._send_error(500, str(e))
            return

        if path == "/status":
            recent = self.cache.get(limit=10)
            online = len(recent) > 0
            result: dict[str, Any] = {
                "online": online,
                "last_event": recent[-1].get("time", None) if recent else None,
            }
            if self.skillsfs_manager is not None:
                result["skills_fs"] = {
                    "status": self.skillsfs_manager.status,
                    "mountpoint": self.skillsfs_manager.mountpoint,
                    "pid": self.skillsfs_manager._pid,
                }
            self._send_json(result)
            return

        if path == "/invoke":
            # GET /invoke only allows read-only actions actually supported by _dispatch.
            _readable = {
                "get_events", "get_event", "get_alerts",
                "list_groups", "list_friends", "list_time_ranges",
                "list_messages", "get_message", "get_stats",
                "list_message_content", "get_message_content", "describe_action",
            }
            action = _first("action")
            if not action:
                self._send_error(400, "Missing action parameter")
                return
            if action not in _readable:
                self._send_error(403, f"Action '{action}' not allowed via GET (use POST for write actions)")
                return
            params = {k: v[0] if len(v) == 1 else v for k, v in qs.items() if k != "action"}
            try:
                result = self._dispatch(action, params)
                self._send_json(result)
            except Exception as e:
                self._send_error(500, str(e))
            return

        self._send_error(404, f"Not found: {path}")
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
    # ------------------------------------------------------------------
    # Multi-type send/reply helper functions
    # ------------------------------------------------------------------

    def _read_file_b64(self, path: str) -> str:
        """Read a file and return base64-encoded string."""
        import base64
        try:
            with open(path, 'rb') as f:
                data = f.read()
        except FileNotFoundError:
            raise ValueError(f"file not found: {path}")
        if len(data) > 20 * 1024 * 1024:
            raise ValueError("file too large (max 20MB)")
        return base64.b64encode(data).decode()

    def _compose_message(self, kind: str, payload: str, qq: str = None, text: str = None) -> list | str | None:
        """Compose message segments by kind.
        Returns a list of segments, a CQ code string, or None (for file kind).
        """
        if kind == "text":
            return [{"type": "text", "data": {"text": payload}}]
        elif kind == "image":
            b64 = self._read_file_b64(payload)
            return [{"type": "image", "data": {"file": f"base64://{b64}"}}]
        elif kind == "file":
            return None  # file upload handled separately via upload API
        elif kind == "cqcode":
            return payload  # return raw string, NapCat parses CQ codes
        elif kind == "at":
            segs = [{"type": "at", "data": {"qq": str(qq)}}]
            if text:
                segs.append({"type": "text", "data": {"text": text}})
            return segs
        elif kind == "json":
            import json
            return json.loads(payload)
        else:
            raise ValueError(f"unknown message kind: {kind}")

    def _format_send_result(self, api_result: dict) -> dict:
        """Format API send result for writeback read."""
        if api_result.get("retcode", -1) == 0:
            data = api_result.get("data", {})
            mid = data.get("message_id", 0)
            return {"status": "ok", "message_id": mid}
        return {"error": api_result.get("message", "unknown"), "retcode": api_result.get("retcode", 0)}

    def _upload_file(self, scope: str, target_id: str, path: str) -> dict:
        """Upload a file via base64 to group or private chat."""
        from napcat_cli.lib.api import NapCatAPI
        api = NapCatAPI()
        b64 = self._read_file_b64(path)
        import os
        name = os.path.basename(path)
        if scope == "group":
            result = api.call("upload_group_file", group_id=target_id, file=f"base64://{b64}", name=name)
        else:
            result = api.call("upload_private_file", user_id=target_id, file=f"base64://{b64}", name=name)
        return self._format_send_result(result)

    def _compose_reply(self, base_segments: list, message_id: str) -> list:
        """Prepend a reply segment to base segments."""
        return [{"type": "reply", "data": {"id": str(message_id)}}] + base_segments


    def _dispatch(self, action: str, params: dict) -> Any:
        """Dispatch action to handler. Returns data for skills-fs."""
        from napcat_cli.lib.events_sqlite import get_connection, read_events as db_read_events, read_alerts as db_read_alerts, get_event_count
        db = get_connection(self.cache.data_dir)

        if action == "get_events":
            events = db_read_events(
                db,
                limit=params.get("limit", 50),
                since=params.get("since", None),
                post_type=params.get("post_type", None),
                event_type=params.get("event_type", None),
            )
            return {"events": events, "count": len(events)}

        if action == "get_event":
            event_id = params.get("id", "")
            from napcat_cli.lib.events_sqlite import read_events as db_read
            events = db_read(db, limit=1000)
            for e in events:
                if str(e.get("message_id", "")) == event_id:
                    return e
            return {"error": f"Event {event_id} not found"}

        if action == "get_alerts":
            alerts = db_read_alerts(db, name=params.get("name"), limit=params.get("limit", 50))
            return {"alerts": alerts, "count": len(alerts)}

        if action == "clear_alert":
            name = params.get("name") or params.get("alert_name", "")
            from napcat_cli.lib.events_sqlite import clear_alerts
            count = clear_alerts(self.processor.writer.conn, name)
            return {"cleared": count}

        if action == "clear_all_alerts":
            from napcat_cli.lib.events_sqlite import clear_alerts
            count = clear_alerts(self.processor.writer.conn)
            return {"cleared": count}

        # Group / message browsing via SQLite indexes
        if action == "list_groups":
            groups = set()
            rows = db.execute("SELECT DISTINCT group_id FROM events WHERE group_id IS NOT NULL ORDER BY group_id").fetchall()
            for r in rows:
                groups.add(str(r["group_id"]))
            return {"entries": [{"name": g, "kind": "dynamic_dir"} for g in sorted(groups)]}

        if action == "list_friends":
            rows = db.execute(
                "SELECT DISTINCT user_id FROM events WHERE post_type='message' AND message_type='private' AND user_id IS NOT NULL ORDER BY user_id"
            ).fetchall()
            users = [str(r["user_id"]) for r in rows if r["user_id"]]
            return {"entries": [{"name": u, "kind": "dynamic_dir"} for u in sorted(users)]}

        if action == "list_time_ranges":
            ranges = ["recent", "1days", "7days", "30days", "90days"]
            entries = [{"name": r, "kind": "dynamic_dir"} for r in ranges]
            group_id = str(params.get("group_id", ""))
            user_id = str(params.get("user_id", ""))
            if group_id:
                entries.extend([
                    {"name": "kick", "kind": "api"},
                    {"name": "ban", "kind": "api"},
                    {"name": "admin", "kind": "api"},
                    {"name": "card", "kind": "api"},
                    {"name": "name", "kind": "api"},
                    {"name": "leave", "kind": "api"},
                    {"name": "info", "kind": "api"},
                    {"name": "members", "kind": "api"},
                    {"name": "essence_list", "kind": "api"},
                    {"name": "poke", "kind": "api"},
                    {"name": "honor", "kind": "api"},
                    {"name": "announce", "kind": "api"},
                    {"name": "send", "kind": "dir"},
                    {"name": "kick.schema", "kind": "blob"},
                    {"name": "ban.schema", "kind": "blob"},
                    {"name": "admin.schema", "kind": "blob"},
                    {"name": "card.schema", "kind": "blob"},
                    {"name": "name.schema", "kind": "blob"},
                    {"name": "leave.schema", "kind": "blob"},
                    {"name": "poke.schema", "kind": "blob"},
                    {"name": "announce.schema", "kind": "blob"},
                ])
            elif user_id:
                entries.extend([
                    {"name": "info", "kind": "api"},
                    {"name": "remark", "kind": "api"},
                    {"name": "send", "kind": "dir"},
                    {"name": "remark.schema", "kind": "blob"},
                ])
            return {"entries": entries}
        if action == "list_messages":
            group_id = str(params.get("group_id", ""))
            user_id = str(params.get("user_id", ""))
            time_range = params.get("time_range", "recent")
            now = time.time()
            cutoff = now
            if time_range == "recent":
                cutoff = now - 3600
            elif time_range == "1days":
                cutoff = now - 86400
            elif time_range == "7days":
                cutoff = now - 7 * 86400
            elif time_range == "30days":
                cutoff = now - 30 * 86400
            elif time_range == "90days":
                cutoff = now - 90 * 86400

            query = "SELECT message_id, timestamp, post_type, event_type, raw_json FROM events WHERE post_type='message' AND timestamp >= ?"
            qparams = [cutoff]
            if group_id:
                query += " AND group_id = ?"
                qparams.append(int(group_id))
            if user_id:
                query += " AND user_id = ?"
                qparams.append(int(user_id))
            query += " ORDER BY timestamp DESC LIMIT 500"
            rows = db.execute(query, qparams).fetchall()
            messages = []
            for r in rows:
                mid = str(r["message_id"]) if r["message_id"] else ""
                if mid:
                    messages.append({"name": mid, "kind": "dynamic_dir", "time": r["timestamp"]})
            return {"entries": messages}

        if action == "get_message":
            group_id = str(params.get("group_id", ""))
            user_id = str(params.get("user_id", ""))
            message_id = str(params.get("message_id", ""))
            query = "SELECT raw_json FROM events WHERE post_type='message' AND message_id = ?"
            qparams = [message_id]
            if group_id:
                query += " AND group_id = ?"
                qparams.append(int(group_id))
            if user_id:
                query += " AND user_id = ?"
                qparams.append(int(user_id))
            row = db.execute(query, qparams).fetchone()
            if row:
                import json as json_mod
                from napcat_cli.lib.message import format_message, extract_file_paths
                event = json_mod.loads(row["raw_json"])
                msg = event.get("message", [])
                event["formatted_text"] = format_message(msg)
                event["files"] = extract_file_paths(msg)
                return event
            scope = f"group {group_id}" if group_id else f"friend {user_id}"
            return {"error": f"Message {message_id} not found in {scope}"}

        if action == "get_stats":
            event_count = get_event_count(db)
            from napcat_cli.lib.events_sqlite import get_alert_count
            alert_count = get_alert_count(db)
            return {"event_count": event_count, "alert_count": alert_count}

        if action == "send_group_message":
            group_id = str(params.get("group_id", ""))
            message = params.get("message", "")
            if not group_id or not message:
                return {"error": "group_id and message are required", "expected_schema": ACTION_SCHEMAS["send_group_message"]}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            return api.call("send_msg", message_type="group", group_id=int(group_id) if group_id.isdigit() else group_id, message=message)

        if action == "send_private_message":
            user_id = str(params.get("user_id", ""))
            message = params.get("message", "")
            if not user_id or not message:
                return {"error": "user_id and message are required", "expected_schema": ACTION_SCHEMAS["send_private_message"]}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            return api.call("send_msg", message_type="private", user_id=int(user_id) if user_id.isdigit() else user_id, message=message)

        # ---- send_group_* ----
        if action == "send_group_text":
            group_id = str(params.get("group_id", ""))
            payload = params.get("_payload", "")
            if not group_id or not payload:
                return {"error": "group_id and payload are required", "expected_schema": ACTION_SCHEMAS.get("send_group_text", {})}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            msg = self._compose_message("text", payload)
            r = api.call("send_msg", message_type="group", group_id=int(group_id) if group_id.isdigit() else group_id, message=msg)
            return self._format_send_result(r)

        if action == "send_group_image":
            group_id = str(params.get("group_id", ""))
            payload = params.get("_payload", "")
            if not group_id or not payload:
                return {"error": "group_id and payload (file path) are required", "expected_schema": ACTION_SCHEMAS.get("send_group_image", {})}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            try:
                msg = self._compose_message("image", payload)
            except ValueError as e:
                return {"error": str(e)}
            r = api.call("send_msg", message_type="group", group_id=int(group_id) if group_id.isdigit() else group_id, message=msg)
            return self._format_send_result(r)

        if action == "send_group_file":
            group_id = str(params.get("group_id", ""))
            payload = params.get("_payload", "")
            if not group_id or not payload:
                return {"error": "group_id and payload (file path) are required", "expected_schema": ACTION_SCHEMAS.get("send_group_file", {})}
            try:
                return self._upload_file("group", group_id, payload)
            except ValueError as e:
                return {"error": str(e)}

        if action == "send_group_cqcode":
            group_id = str(params.get("group_id", ""))
            payload = params.get("_payload", "")
            if not group_id or not payload:
                return {"error": "group_id and payload (CQ code) are required", "expected_schema": ACTION_SCHEMAS.get("send_group_cqcode", {})}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            msg = self._compose_message("cqcode", payload)
            r = api.call("send_msg", message_type="group", group_id=int(group_id) if group_id.isdigit() else group_id, message=msg)
            return self._format_send_result(r)

        if action == "send_group_at":
            group_id = str(params.get("group_id", ""))
            qq = str(params.get("qq", ""))
            text = str(params.get("text", ""))
            if not group_id or not qq:
                return {"error": "group_id and qq are required", "expected_schema": ACTION_SCHEMAS.get("send_group_at", {})}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            msg = self._compose_message("at", None, qq=qq, text=text)
            r = api.call("send_msg", message_type="group", group_id=int(group_id) if group_id.isdigit() else group_id, message=msg)
            return self._format_send_result(r)

        if action == "send_group_json":
            group_id = str(params.get("group_id", ""))
            message = params.get("message", "")
            if not group_id or not message:
                return {"error": "group_id and message are required", "expected_schema": ACTION_SCHEMAS.get("send_group_json", {})}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            r = api.call("send_msg", message_type="group", group_id=int(group_id) if group_id.isdigit() else group_id, message=message)
            return self._format_send_result(r)

        # ---- send_private_* ----
        if action == "send_private_text":
            user_id = str(params.get("user_id", ""))
            payload = params.get("_payload", "")
            if not user_id or not payload:
                return {"error": "user_id and payload are required", "expected_schema": ACTION_SCHEMAS.get("send_private_text", {})}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            msg = self._compose_message("text", payload)
            r = api.call("send_msg", message_type="private", user_id=int(user_id) if user_id.isdigit() else user_id, message=msg)
            return self._format_send_result(r)

        if action == "send_private_image":
            user_id = str(params.get("user_id", ""))
            payload = params.get("_payload", "")
            if not user_id or not payload:
                return {"error": "user_id and payload (file path) are required", "expected_schema": ACTION_SCHEMAS.get("send_private_image", {})}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            try:
                msg = self._compose_message("image", payload)
            except ValueError as e:
                return {"error": str(e)}
            r = api.call("send_msg", message_type="private", user_id=int(user_id) if user_id.isdigit() else user_id, message=msg)
            return self._format_send_result(r)

        if action == "send_private_file":
            user_id = str(params.get("user_id", ""))
            payload = params.get("_payload", "")
            if not user_id or not payload:
                return {"error": "user_id and payload (file path) are required", "expected_schema": ACTION_SCHEMAS.get("send_private_file", {})}
            try:
                return self._upload_file("private", user_id, payload)
            except ValueError as e:
                return {"error": str(e)}

        if action == "send_private_cqcode":
            user_id = str(params.get("user_id", ""))
            payload = params.get("_payload", "")
            if not user_id or not payload:
                return {"error": "user_id and payload (CQ code) are required", "expected_schema": ACTION_SCHEMAS.get("send_private_cqcode", {})}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            msg = self._compose_message("cqcode", payload)
            r = api.call("send_msg", message_type="private", user_id=int(user_id) if user_id.isdigit() else user_id, message=msg)
            return self._format_send_result(r)

        if action == "send_private_at":
            user_id = str(params.get("user_id", ""))
            qq = str(params.get("qq", ""))
            text = str(params.get("text", ""))
            if not user_id or not qq:
                return {"error": "user_id and qq are required", "expected_schema": ACTION_SCHEMAS.get("send_private_at", {})}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            msg = self._compose_message("at", None, qq=qq, text=text)
            r = api.call("send_msg", message_type="private", user_id=int(user_id) if user_id.isdigit() else user_id, message=msg)
            return self._format_send_result(r)

        if action == "send_private_json":
            user_id = str(params.get("user_id", ""))
            message = params.get("message", "")
            if not user_id or not message:
                return {"error": "user_id and message are required", "expected_schema": ACTION_SCHEMAS.get("send_private_json", {})}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            r = api.call("send_msg", message_type="private", user_id=int(user_id) if user_id.isdigit() else user_id, message=message)
            return self._format_send_result(r)

        # ---- reply_group_* ----
        if action == "reply_group_text":
            group_id = str(params.get("group_id", ""))
            message_id = str(params.get("message_id", ""))
            payload = params.get("_payload", "")
            if not group_id or not message_id or not payload:
                return {"error": "group_id, message_id, and payload are required", "expected_schema": ACTION_SCHEMAS.get("reply_group_text", {})}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            base = self._compose_message("text", payload)
            msg = self._compose_reply(base, message_id)
            r = api.call("send_msg", message_type="group", group_id=int(group_id) if group_id.isdigit() else group_id, message=msg)
            return self._format_send_result(r)

        if action == "reply_group_image":
            group_id = str(params.get("group_id", ""))
            message_id = str(params.get("message_id", ""))
            payload = params.get("_payload", "")
            if not group_id or not message_id or not payload:
                return {"error": "group_id, message_id, and payload are required", "expected_schema": ACTION_SCHEMAS.get("reply_group_image", {})}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            try:
                base = self._compose_message("image", payload)
            except ValueError as e:
                return {"error": str(e)}
            msg = self._compose_reply(base, message_id)
            r = api.call("send_msg", message_type="group", group_id=int(group_id) if group_id.isdigit() else group_id, message=msg)
            return self._format_send_result(r)

        if action == "reply_group_file":
            group_id = str(params.get("group_id", ""))
            payload = params.get("_payload", "")
            if not group_id or not payload:
                return {"error": "group_id and payload (file path) are required", "expected_schema": ACTION_SCHEMAS.get("reply_group_file", {})}
            try:
                return self._upload_file("group", group_id, payload)
            except ValueError as e:
                return {"error": str(e)}

        if action == "reply_group_cqcode":
            group_id = str(params.get("group_id", ""))
            message_id = str(params.get("message_id", ""))
            payload = params.get("_payload", "")
            if not group_id or not message_id or not payload:
                return {"error": "group_id, message_id, and payload are required", "expected_schema": ACTION_SCHEMAS.get("reply_group_cqcode", {})}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            cq_str = self._compose_message("cqcode", payload)
            msg = [{"type": "reply", "data": {"id": str(message_id)}}, {"type": "text", "data": {"text": cq_str}}]
            r = api.call("send_msg", message_type="group", group_id=int(group_id) if group_id.isdigit() else group_id, message=msg)
            return self._format_send_result(r)

        if action == "reply_group_at":
            group_id = str(params.get("group_id", ""))
            message_id = str(params.get("message_id", ""))
            qq = str(params.get("qq", ""))
            text = str(params.get("text", ""))
            if not group_id or not message_id or not qq:
                return {"error": "group_id, message_id, and qq are required", "expected_schema": ACTION_SCHEMAS.get("reply_group_at", {})}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            base = self._compose_message("at", None, qq=qq, text=text)
            msg = self._compose_reply(base, message_id)
            r = api.call("send_msg", message_type="group", group_id=int(group_id) if group_id.isdigit() else group_id, message=msg)
            return self._format_send_result(r)

        if action == "reply_group_json":
            group_id = str(params.get("group_id", ""))
            message_id = str(params.get("message_id", ""))
            message = params.get("message", "")
            if not group_id or not message_id or not message:
                return {"error": "group_id, message_id, and message are required", "expected_schema": ACTION_SCHEMAS.get("reply_group_json", {})}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            msg = [{"type": "reply", "data": {"id": str(message_id)}}] + message
            r = api.call("send_msg", message_type="group", group_id=int(group_id) if group_id.isdigit() else group_id, message=msg)
            return self._format_send_result(r)

        # ---- reply_private_* ----
        if action == "reply_private_text":
            user_id = str(params.get("user_id", ""))
            message_id = str(params.get("message_id", ""))
            payload = params.get("_payload", "")
            if not user_id or not message_id or not payload:
                return {"error": "user_id, message_id, and payload are required", "expected_schema": ACTION_SCHEMAS.get("reply_private_text", {})}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            base = self._compose_message("text", payload)
            msg = self._compose_reply(base, message_id)
            r = api.call("send_msg", message_type="private", user_id=int(user_id) if user_id.isdigit() else user_id, message=msg)
            return self._format_send_result(r)

        if action == "reply_private_image":
            user_id = str(params.get("user_id", ""))
            message_id = str(params.get("message_id", ""))
            payload = params.get("_payload", "")
            if not user_id or not message_id or not payload:
                return {"error": "user_id, message_id, and payload are required", "expected_schema": ACTION_SCHEMAS.get("reply_private_image", {})}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            try:
                base = self._compose_message("image", payload)
            except ValueError as e:
                return {"error": str(e)}
            msg = self._compose_reply(base, message_id)
            r = api.call("send_msg", message_type="private", user_id=int(user_id) if user_id.isdigit() else user_id, message=msg)
            return self._format_send_result(r)

        if action == "reply_private_file":
            user_id = str(params.get("user_id", ""))
            payload = params.get("_payload", "")
            if not user_id or not payload:
                return {"error": "user_id and payload (file path) are required", "expected_schema": ACTION_SCHEMAS.get("reply_private_file", {})}
            try:
                return self._upload_file("private", user_id, payload)
            except ValueError as e:
                return {"error": str(e)}

        if action == "reply_private_cqcode":
            user_id = str(params.get("user_id", ""))
            message_id = str(params.get("message_id", ""))
            payload = params.get("_payload", "")
            if not user_id or not message_id or not payload:
                return {"error": "user_id, message_id, and payload are required", "expected_schema": ACTION_SCHEMAS.get("reply_private_cqcode", {})}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            cq_str = self._compose_message("cqcode", payload)
            msg = [{"type": "reply", "data": {"id": str(message_id)}}, {"type": "text", "data": {"text": cq_str}}]
            r = api.call("send_msg", message_type="private", user_id=int(user_id) if user_id.isdigit() else user_id, message=msg)
            return self._format_send_result(r)

        if action == "reply_private_at":
            user_id = str(params.get("user_id", ""))
            message_id = str(params.get("message_id", ""))
            qq = str(params.get("qq", ""))
            text = str(params.get("text", ""))
            if not user_id or not message_id or not qq:
                return {"error": "user_id, message_id, and qq are required", "expected_schema": ACTION_SCHEMAS.get("reply_private_at", {})}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            base = self._compose_message("at", None, qq=qq, text=text)
            msg = self._compose_reply(base, message_id)
            r = api.call("send_msg", message_type="private", user_id=int(user_id) if user_id.isdigit() else user_id, message=msg)
            return self._format_send_result(r)

        if action == "reply_private_json":
            user_id = str(params.get("user_id", ""))
            message_id = str(params.get("message_id", ""))
            message = params.get("message", "")
            if not user_id or not message_id or not message:
                return {"error": "user_id, message_id, and message are required", "expected_schema": ACTION_SCHEMAS.get("reply_private_json", {})}
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            msg = [{"type": "reply", "data": {"id": str(message_id)}}] + message
            r = api.call("send_msg", message_type="private", user_id=int(user_id) if user_id.isdigit() else user_id, message=msg)
            return self._format_send_result(r)

        if action == "list_message_content":
            message_id = str(params.get("message_id", ""))
            group_id = str(params.get("group_id", ""))
            user_id = str(params.get("user_id", ""))
            query = "SELECT raw_json FROM events WHERE post_type='message' AND message_id = ?"
            qparams = [message_id]
            if group_id:
                query += " AND group_id = ?"
                qparams.append(int(group_id))
            if user_id:
                query += " AND user_id = ?"
                qparams.append(int(user_id))
            row = db.execute(query, qparams).fetchone()
            if not row:
                return {"error": f"Message {message_id} not found"}
            event = json.loads(row["raw_json"])
            segments = event.get("message", [])
            entries = [{"name": "metadata", "kind": "api"}, {"name": "text", "kind": "api"}, {"name": "reply", "kind": "dir"}]
            image_count = 0
            for seg in segments:
                stype = seg.get("type", "")
                if stype == "image":
                    image_count += 1
                    entries.append({"name": f"image" if image_count == 1 else f"image_{image_count}", "kind": "api"})
                elif stype == "file":
                    file_count += 1
                    entries.append({"name": f"file" if file_count == 1 else f"file_{file_count}", "kind": "api"})
                elif stype == "video":
                    entries.append({"name": "video", "kind": "api"})
                elif stype == "record":
                    entries.append({"name": "record", "kind": "api"})
                elif stype == "forward":
                    entries.append({"name": "forward", "kind": "api"})
            return {"entries": entries}

        if action == "get_message_content":
            message_id = str(params.get("message_id", ""))
            content = str(params.get("content", ""))
            group_id = str(params.get("group_id", ""))
            user_id = str(params.get("user_id", ""))
            query = "SELECT raw_json FROM events WHERE post_type='message' AND message_id = ?"
            qparams = [message_id]
            if group_id:
                query += " AND group_id = ?"
                qparams.append(int(group_id))
            if user_id:
                query += " AND user_id = ?"
                qparams.append(int(user_id))
            row = db.execute(query, qparams).fetchone()
            if not row:
                return {"error": f"Message {message_id} not found"}
            event = json.loads(row["raw_json"])
            segments = event.get("message", [])
            if not content:
                # No selector: return all available content
                result = {
                    "metadata": {
                        "message_id": event.get("message_id"),
                        "sender": event.get("sender", {}).get("nickname", ""),
                        "time": event.get("time"),
                        "group_id": event.get("group_id"),
                        "user_id": event.get("user_id"),
                        "message_type": event.get("message_type"),
                    },
                }
                texts = [seg.get("data", {}).get("text", "") for seg in segments if seg.get("type") == "text"]
                if texts:
                    result["text"] = "\n".join(texts)
                for seg in segments:
                    stype = seg.get("type", "")
                    if stype == "image":
                        result.setdefault("image", []).append(seg.get("data", {}))
                    elif stype == "video":
                        result.setdefault("video", []).append(seg.get("data", {}))
                    elif stype == "record":
                        result.setdefault("record", []).append(seg.get("data", {}))
                    elif stype == "file":
                        result.setdefault("file", []).append(seg.get("data", {}))
                    elif stype == "forward":
                        result.setdefault("forward", []).append(seg.get("data", {}))
                return result

            if content == "metadata":
                return {
                    "message_id": event.get("message_id"),
                    "sender": event.get("sender", {}).get("nickname", ""),
                    "time": event.get("time"),
                    "group_id": event.get("group_id"),
                    "user_id": event.get("user_id"),
                    "message_type": event.get("message_type"),
                }

            if content == "text":
                texts = [seg.get("data", {}).get("text", "") for seg in segments if seg.get("type") == "text"]
                return {"text": "\n".join(texts)}

            # Parse image/file selectors like "image", "image_2", "file_3"
            if content.startswith("image") or content.startswith("file"):
                base = content.rsplit("_", 1)[0] if "_" in content else content
                if "_" in content:
                    try:
                        idx = int(content.rsplit("_", 1)[1])
                    except ValueError:
                        return {"error": f"Invalid content selector: {content}"}
                else:
                    idx = 1
                collected = []
                for seg in segments:
                    if seg.get("type") == base:
                        collected.append(seg.get("data", {}))
                if idx <= len(collected):
                    return collected[idx - 1]
                return {"error": f"Content {content} not found in message"}

            if content == "video":
                for seg in segments:
                    if seg.get("type") == "video":
                        return seg.get("data", {})
                return {"error": "No video in message"}

            if content == "record":
                for seg in segments:
                    if seg.get("type") == "record":
                        return seg.get("data", {})
                return {"error": "No record in message"}

            if content == "forward":
                for seg in segments:
                    if seg.get("type") == "forward":
                        return seg.get("data", {})
                return {"error": "No forward in message"}

            return {"error": f"Unknown content selector: {content}"}

        if action == "describe_action":
            action_name = params.get("action", "")
            schema = ACTION_SCHEMAS.get(action_name)
            if schema:
                return schema
            return {"error": f"Unknown action: {action_name}"}

        # ---- napcat_delete_msg with recall rule hints ----
        if action == "napcat_delete_msg":
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            r = api.call("delete_msg", **params)
            if isinstance(r, dict) and "error" in r:
                r["hint"] = "Recall rules: bot recalling its own message must act within 2 minutes. Admin can recall any member message. Group owner can recall any message. No time limit for admin/owner recalling others' messages."
            return r
        # Proxy NapCat API calls through napcat_ prefix
        if action.startswith("napcat_"):
            from napcat_cli.lib.api import NapCatAPI
            api = NapCatAPI()
            napcat_action = action.replace("napcat_", "", 1)
            return api.call(napcat_action, **params)

        return {"error": f"Unknown action: {action}"}



def run_http_server(
    processor: EventProcessor,
    cache: EventCache,
    port: int = 18820,
    host: str = "0.0.0.0",
    skillsfs_manager: SkillsFsManager | None = None,
) -> HTTPServer:
    """Start HTTP provider in a background thread. Returns the server."""
    NapCatHandler.processor = processor
    NapCatHandler.cache = cache
    NapCatHandler.events_reader = EventsReader(cache.data_dir)
    NapCatHandler.skillsfs_manager = skillsfs_manager

    server = HTTPServer((host, port), NapCatHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    processor.log(f"HTTP provider listening on {host}:{port}")
    return server


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def health_check_task(processor: EventProcessor, api_url: str, token: str, interval: int = 60, cooldown: int = 300) -> None:
    """Periodically check if NapCat bot is online via HTTP API.
    
    Runs as a separate task alongside ws_daemon so a hung WS connection
    doesn't block health checks. Uses HTTP API (not WS) to detect
    'WS connected but bot offline' cases.
    
    Args:
        processor: EventProcessor for logging and wake commands.
        api_url: NapCat HTTP API URL.
        token: API authentication token.
        interval: Check interval in seconds (default: 60).
        cooldown: Minimum seconds between wake attempts (default: 300).
    """
    import urllib.request
    last_wake = 0.0
    loop = asyncio.get_running_loop()
    
    while True:
        try:
            def probe():
                url = f"{api_url}/get_status"
                req = urllib.request.Request(url, method="POST")
                req.add_header("Content-Type", "application/json")
                if token:
                    req.add_header("Authorization", f"Bearer {token}")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return json.loads(resp.read())
            
            data = await loop.run_in_executor(None, probe)
            online = bool(data.get("data", {}).get("online", False))
            
            if not online:
                now = time.time()
                if now - last_wake > cooldown:
                    processor.log(f"Health check: bot is offline. Attempting wake...")
                    try:
                        processor._wake("HEALTH_CHECK_OFFLINE")
                        last_wake = now
                        processor.log("Wake command sent.")
                    except Exception as e:
                        processor.log(f"Wake command failed: {e}")
                else:
                    processor.log(f"Health check: bot is offline (cooldown: {int(cooldown - (now - last_wake))}s remaining)")
            else:
                processor.log("Health check: bot is online.")
        except Exception as e:
            processor.log(f"Health check failed: {e}")
        
        await asyncio.sleep(interval)


async def backlog_sweep_task(processor: "EventProcessor", interval: int = 45) -> None:
    """Periodically nudge the agent to read unread messages that piled up.

    NEW_MESSAGE events are tracked (not woken) by the orchestrator; if they sit
    unread longer than ``new_message_idle_seconds`` this fires a single
    ``NEW_MESSAGE_BACKLOG`` wake so the agent scans the inbox.
    """
    await asyncio.sleep(interval)  # let the daemon warm up first
    while True:
        try:
            if processor.orchestrator is not None:
                processor.orchestrator.maybe_backlog_sweep()
        except Exception as e:
            processor.log(f"backlog sweep error: {e}")
        await asyncio.sleep(interval)


def run_daemon(config_path: str) -> None:
    """Start the WebSocket daemon with HTTP provider."""
    cfg_data = json.loads(Path(config_path).read_text())
    self_id = cfg_data.get("self_id", "")
    wake_command = cfg_data.get("wake_command", "")
    wake_on_event = bool(cfg_data.get("wake_enabled", cfg_data.get("wake_on_event", True)))
    group_trigger = cfg_data.get("group_trigger_word", "")
    private_trigger = cfg_data.get("private_trigger", "")

    # Build the wake Waker (http->cli auto-fallback) from the wake_* config.
    from napcat_cli.lib.config import NapCatConfig
    wake_cfg = NapCatConfig()
    for _k in ("wake_preset", "wake_primary", "wake_session", "wake_http_url",
               "wake_http_key", "wake_http_session_id", "wake_cli_command"):
        if _k in cfg_data:
            setattr(wake_cfg, _k, cfg_data[_k])
    waker = build_waker(wake_cfg)
    debounce = float(cfg_data.get("wake_debounce_seconds", 3.0))
    cooldown = float(cfg_data.get("wake_cooldown_seconds", 30.0))
    nm_idle = int(cfg_data.get("wake_new_message_idle_seconds", 600))

    processor = EventProcessor(DATA_DIR, self_id, wake_command, wake_on_event,
        group_trigger_word=group_trigger,
        private_trigger=private_trigger,
        waker=waker, debounce_seconds=debounce, cooldown_seconds=cooldown,
        new_message_idle_seconds=nm_idle,
    )
    cache = EventCache(DATA_DIR)

    # PID file
    pid_file = DATA_DIR / "daemon.pid"
    pid_file.write_text(str(os.getpid()))

    # Determine ports
    http_port = int(cfg_data.get("http_port", os.environ.get("NAPCAT_HTTP_PORT", "18821")))
    ws_port = int(cfg_data.get("ws_port", os.environ.get("NAPCAT_WS_PORT", "18800")))
    ws_url = cfg_data.get("ws_url", f"ws://127.0.0.1:{ws_port}")

    # API URL for health checks (HTTP, not WS)
    api_url = cfg_data.get("api_url", os.environ.get("NAPCAT_API_URL", "http://127.0.0.1:18801"))
    token = cfg_data.get("token", os.environ.get("NAPCAT_TOKEN", ""))

    # --- Start skills-fs FUSE mount ---
    # Treat empty strings as unset so an old config (which wrote "" for these)
    # still falls back to the defaults instead of spawning with blank args.
    skills_fs_enabled = cfg_data.get("skills_fs_enabled", True)
    skills_fs_mountpoint = cfg_data.get("skills_fs_mountpoint") or _DEFAULT_MOUNTPOINT
    skills_fs_binary = cfg_data.get("skills_fs_binary") or ""
    skills_fs_config = cfg_data.get("skills_fs_config") or _DEFAULT_SKILLSFS_CONFIG

    skillsfs_mgr: SkillsFsManager | None = None
    if skills_fs_enabled:
        skillsfs_mgr = SkillsFsManager(
            processor,
            mountpoint=skills_fs_mountpoint,
            binary=skills_fs_binary,
            config=skills_fs_config,
        )
        skillsfs_mgr.start()

    # Start HTTP provider (after skills-fs so handler can reference manager)
    server = run_http_server(processor, cache, http_port, skillsfs_manager=skillsfs_mgr)
    # Signal handling
    loop = asyncio.new_event_loop()

    def shutdown_handler(signum: int, frame: Any) -> None:
        processor.log("Shutting down...")
        pid_file.unlink(missing_ok=True)
        if skillsfs_mgr is not None:
            skillsfs_mgr.stop()
        server.shutdown()
        loop.call_soon_threadsafe(loop.stop)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    processor.log(f"NapCat daemon started (PID: {os.getpid()})")
    processor.log(f"WebSocket URL: {ws_url}")
    processor.log(f"HTTP provider port: {http_port}")

    # Run WS daemon, health check, and skills-fs monitor as parallel tasks
    tasks = [
        loop.create_task(ws_daemon(ws_url, processor, cache)),
        loop.create_task(health_check_task(processor, api_url, token)),
    ]
    if skillsfs_mgr is not None:
        tasks.append(loop.create_task(skillsfs_monitor_task(skillsfs_mgr)))
    if processor.orchestrator is not None:
        tasks.append(loop.create_task(backlog_sweep_task(processor)))

    # Wait for either to finish (shutdown or WS crash)
    done, _ = loop.run_until_complete(asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED))
    for t in done:
        if t.exception():
            processor.log(f"Task exception: {t.exception()}")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: watch.py <config_path>", file=sys.stderr)
        sys.exit(1)
    run_daemon(sys.argv[1])


if __name__ == "__main__":
    main()
