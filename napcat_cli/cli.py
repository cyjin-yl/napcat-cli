#!/usr/bin/env python3
"""napcat-cli - Standalone CLI for NapCat QQ bot management.

Usage:
    napcat api <endpoint> [--data JSON] [--method POST|GET]
    napcat send <group|private> <id> -m "<message>" [--file FILE] [--at USERS]
    napcat recall <message_id> [--group GROUP]
    napcat group <command> [args...]
    napcat friend <command> [args...]
    napcat file <command> [args...]
    napcat daemon [start|stop|status]
    napcat config [get|set] <key> [value]
    napcat alerts [--clear]

Environment:
    NAPCAT_API_URL  HTTP API URL (default: http://127.0.0.1:18801)
    NAPCAT_DATA_DIR Data directory (default: ~/.napcat-data)
    NAPCAT_TOKEN    API authentication token
"""
from __future__ import annotations

from napcat_cli.__init__ import __version__
import argparse
import json
import os
import sys
from pathlib import Path
import base64

# Project root
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from napcat_cli.lib.api import NapCatAPI
from napcat_cli.lib.config import get_config, DATA_DIR
from napcat_cli.lib.events import EventsReader



def require_online(api: NapCatAPI) -> bool:
    """Check bot is online; print error and return False if offline."""
    if not api.is_online():
        print("Error: bot is offline. Check 'napcat status' or restart NapCat.", file=sys.stderr)
        return False
    return True


def _port_in_use(port: int) -> bool:
    """True if something is already listening on 127.0.0.1:<port>.

    Guards against stacking a second daemon (or starting one while a D-state
    zombie still holds the port)."""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        return s.connect_ex(("127.0.0.1", int(port))) == 0
    finally:
        s.close()


def _normalize_file_path(path: str) -> str:
    """Convert local file path to file:// URL if needed."""
    if path.startswith(("http://", "https://", "file://", "base64://")):
        return path
    p = Path(path)
    if p.exists():
        return "file://" + str(p.resolve())
    print(f"Error: file not found: {path}", file=sys.stderr)
    return path

def cmd_api(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat api - Raw API access like 'gh api'."""
    endpoint = args.endpoint
    method = args.method or ("POST" if args.data else "GET")

    data: dict | None = None
    if args.data:
        try:
            data = json.loads(args.data)
        except json.JSONDecodeError as e:
            print(f"Error: Invalid JSON: {e}", file=sys.stderr)
            return 1

    result = api.request(endpoint, method=method, json_body=data)

    if args.output == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.output == "value" and "data" in result:
        val = result["data"]
        if isinstance(val, (dict, list)):
            print(json.dumps(val, indent=2, ensure_ascii=False))
        else:
            print(val)
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))

    return 0 if result.get("retcode") == 0 or result.get("status") == "ok" else 1


def cmd_send(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat send - Send a message."""
    if not require_online(api):
        return 1

    msg_type = "group" if args.target_type == "group" else "private"
    target_id = args.target_id

    # Build message segments
    segments = []
    message_text = args.message or args.message_text
    if not message_text:
        print("Error: message is required (positional or --message)", file=sys.stderr)
        return 1
    if args.at:
        for uid in args.at:
            segments.append({"type": "at", "data": {"qq": uid}})

    if args.file:
        file_path = args.file
        orig_name = Path(file_path).name
        if file_path.startswith(("http://", "https://", "file://", "base64://")):
            pass
        elif Path(file_path).exists():
            file_path = "file://" + str(Path(file_path).resolve())
        else:
            print(f"Warning: file not found '{args.file}', sending as-is (NapCat may reject)", file=sys.stderr)
        segments.append({"type": "file", "data": {"file": file_path, "name": orig_name}})
    elif args.image:
        image_path = args.image
        if image_path.startswith(("http://", "https://", "file://", "base64://")):
            pass
        elif Path(image_path).exists():
            try:
                data = Path(image_path).read_bytes()
                b64 = base64.b64encode(data).decode()
                segments.append({"type": "image", "data": {"file": f"base64://{b64}"}})
            except Exception:
                image_path = "file://" + str(Path(image_path).resolve())
                segments.append({"type": "image", "data": {"file": image_path}})
        else:
            print(f"Warning: image not found '{args.image}', sending as-is", file=sys.stderr)
            segments.append({"type": "image", "data": {"file": image_path}})

    # message is now required positional arg
    segments.append({"type": "text", "data": {"text": message_text}})

    kwargs: dict = {"message_type": msg_type, "message": segments}
    if msg_type == "group":
        kwargs["group_id"] = target_id
    else:
        kwargs["user_id"] = target_id

    result = api.call("send_msg", **kwargs)

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("retcode") == 0 else 1

    if result.get("retcode") == 0:
        data = result.get("data", {})
        msg_id = data.get("message_id", "unknown")
        print(f"Sent message_id={msg_id}", file=sys.stderr)
        return 0
    else:
        print(f"Error: {result.get('message', 'Unknown error')}", file=sys.stderr)
        return 1


def cmd_reply(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat reply - Reply to a message."""
    if not require_online(api):
        return 1

    msg_type = "group" if args.target_type == "group" else "private"
    target_id = args.target_id

    # Build message segments
    segments = [{"type": "reply", "data": {"id": args.message_id}}]
    message_text = args.message or args.message_text
    if not message_text:
        print("Error: reply message is required (positional or --message)", file=sys.stderr)
        return 1
    if args.at:
        for uid in args.at:
            segments.append({"type": "at", "data": {"qq": uid}})

    if args.file:
        file_path = args.file
        orig_name = Path(file_path).name
        if file_path.startswith(("http://", "https://", "file://", "base64://")):
            pass
        elif Path(file_path).exists():
            file_path = "file://" + str(Path(file_path).resolve())
        else:
            print(f"Warning: file not found '{args.file}', sending as-is (NapCat may reject)", file=sys.stderr)
        segments.append({"type": "file", "data": {"file": file_path, "name": orig_name}})
    elif args.image:
        image_path = args.image
        if image_path.startswith(("http://", "https://", "file://", "base64://")):
            pass
        elif Path(image_path).exists():
            try:
                data = Path(image_path).read_bytes()
                b64 = base64.b64encode(data).decode()
                segments.append({"type": "image", "data": {"file": f"base64://{b64}"}})
            except Exception:
                image_path = "file://" + str(Path(image_path).resolve())
                segments.append({"type": "image", "data": {"file": image_path}})
        else:
            print(f"Warning: image not found '{args.image}', sending as-is", file=sys.stderr)
            segments.append({"type": "image", "data": {"file": image_path}})

    segments.append({"type": "text", "data": {"text": message_text}})

    kwargs: dict = {"message_type": msg_type, "message": segments}
    if msg_type == "group":
        kwargs["group_id"] = target_id
    else:
        kwargs["user_id"] = target_id

    result = api.call("send_msg", **kwargs)

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("retcode") == 0 else 1

    if result.get("retcode") == 0:
        data = result.get("data", {})
        msg_id = data.get("message_id", "unknown")
        print(f"Replied with message_id={msg_id}", file=sys.stderr)
        return 0
    else:
        print(f"Error: {result.get('message', 'Unknown error')}", file=sys.stderr)
        return 1


def cmd_recall(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat recall - Recall a message."""
    if not require_online(api):
        return 1
    kwargs: dict = {"message_id": args.message_id}
    if args.group:
        kwargs["group_id"] = args.group

    result = api.call("delete_msg", **kwargs)

    if result.get("retcode") == 0:
        print("Message recalled successfully", file=sys.stderr)
        return 0
    else:
        print(f"Error: {result.get('message', result)}", file=sys.stderr)
        return 1


def cmd_group(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat group - Group management."""
    sub = args.subcommand
    if not require_online(api):
        return 1

    if sub == "info":
        result = api.call("get_group_info", group_id=args.group_id, no_cache=args.no_cache)
    elif sub == "members":
        result = api.call("get_group_member_list", group_id=args.group_id, no_cache=args.no_cache)
    elif sub == "member":
        result = api.call("get_group_member_info", group_id=args.group_id, user_id=args.user_id, no_cache=args.no_cache)
    elif sub == "mute":
        secs = args.duration or 30 * 60
        result = api.call("set_group_ban", group_id=args.group_id, user_id=args.user_id, duration=secs)
    elif sub == "unmute":
        result = api.call("set_group_ban", group_id=args.group_id, user_id=args.user_id, duration=0)
    elif sub == "kick":
        kwargs: dict = {"group_id": args.group_id, "user_id": args.user_id}
        if hasattr(args, 'reject'):
            kwargs["reject_add_request"] = True
        result = api.call("set_group_kick", **kwargs)
    elif sub == "admin":
        result = api.call("set_group_admin", group_id=args.group_id, user_id=args.user_id, enable=args.enable)
    elif sub == "rename":
        result = api.call("set_group_card", group_id=args.group_id, user_id=args.user_id, card=args.card)
    elif sub == "remark":
        result = api.call("set_group_remark", group_id=args.group_id, remark=args.remark)
    elif sub == "announce":
        result = api.call("send_group_notice", group_id=args.group_id, content=args.content)
        if result.get("retcode") == 200 and result.get("data") is None:
            print("Group announcements are not supported by this NapCat instance.", file=sys.stderr)
    elif sub == "list":
        kwargs = {}
        if args.limit is not None:
            kwargs["limit"] = args.limit
        result = api.call("get_group_list", **kwargs)
        if args.limit is not None and result.get("retcode") == 0 and result.get("data"):
            result["data"] = result["data"][:args.limit]
    elif sub == "essence":
        result = api.call("get_essence_list", group_id=args.group_id)
        if result.get("retcode") == 200 and result.get("data") is None:
            print("Essence messages are not supported by this NapCat instance.", file=sys.stderr)
    elif sub == "set_essence":
        result = api.call("set_essence", group_id=args.group_id, message_id=args.message_id)
    elif sub == "delete_essence":
        result = api.call("delete_essence", group_id=args.group_id, message_id=args.message_id)
    elif sub == "poke":
        result = api.call("send_poke", group_id=args.group_id, user_id=args.user_id)
        if result.get("retcode") == 200 and result.get("data") is None:
            print("Group poke is not supported by this NapCat instance.", file=sys.stderr)
    else:
        print(f"Unknown group command: {sub}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("retcode") == 0 else 1


def cmd_friend(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat friend - Friend management."""
    sub = args.subcommand
    if not require_online(api):
        return 1

    if sub == "list":
        result = api.call("get_friend_list", no_cache=args.no_cache)
    elif sub == "remark":
        result = api.call("set_friend_remark", user_id=args.user_id, remark=args.remark)
    elif sub == "add":
        print("Error: QQ protocol does not support proactive friend requests. "
              "Friend requests can only be approved/rejected via 'napcat api' using "
              "set_friend_add_request with the flag from an incoming request event.", file=sys.stderr)
        return 1
    elif sub == "delete":
        result = api.call("delete_friend", user_id=args.user_id)
    else:
        print(f"Unknown friend command: {sub}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("retcode") == 0 else 1


def cmd_file(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat file - File operations."""
    if not require_online(api):
        return 1
    sub = args.subcommand

    if sub == "upload-group":
        file_path = _normalize_file_path(args.file)
        kwargs: dict = {"group_id": args.group_id, "file": file_path, "name": args.name or Path(args.file).name}
        if args.folder:
            kwargs["folder"] = args.folder
        result = api.call("upload_group_file", **kwargs)
    elif sub == "upload-private":
        file_path = _normalize_file_path(args.file)
        result = api.call("upload_private_file", user_id=args.user_id, file=file_path, name=args.name or Path(args.file).name)
    elif sub == "list-group":
        result = api.call("get_group_root_files", group_id=args.group_id)
    elif sub == "list-folder":
        result = api.call("get_group_files_by_folder", group_id=args.group_id, folder_id=args.folder_id)
    elif sub == "info":
        result = api.call("get_file", group_id=args.group_id, file_id=args.file_id)
    elif sub == "download":
        result = api.call("get_file", group_id=args.group_id, file_id=args.file_id)
        if result.get("retcode") == 0:
            local_path = result["data"]["file"]
            print(f"File available at: {local_path}", file=sys.stderr)
            if args.output_dir:
                import shutil
                dst = Path(args.output_dir) / Path(local_path).name
                shutil.copy2(local_path, dst)
                print(f"Copied to: {dst}", file=sys.stderr)
    else:
        print(f"Unknown file command: {sub}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("retcode") == 0 else 1


def cmd_daemon(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat daemon - Manage the watch daemon."""
    import subprocess
    # daemon launched via -m

    if args.subcommand == "start":
        # Refuse to stack a second daemon on the same port — multiple daemons
        # each spawning their own skills-fs on one mountpoint is what deadlocks.
        cfg = get_config()
        if _port_in_use(cfg.http_port):
            print(f"Error: http_port {cfg.http_port} already in use (another daemon running, "
                  f"or a stale/D-state process holding it). Stop it first or change http_port.",
                  file=sys.stderr)
            return 1
        # Check for existing daemon before starting a new one
        pid_file = DATA_DIR / "daemon.pid"
        if pid_file.exists():
            existing_pid = int(pid_file.read_text().strip())
            try:
                os.kill(existing_pid, 0)  # Check if alive without killing
                print(f"Daemon already running (PID {existing_pid}). Use 'napcat daemon stop' first.", file=sys.stderr)
                return 1
            except ProcessLookupError:
                pid_file.unlink()  # Stale PID file, clean up
        # Write config for daemon
        cfg = get_config()
        cfg_path = DATA_DIR / "daemon.json"
        cfg_dict = {
            "self_id": cfg.self_id or "",
            "wake_command": cfg.wake_command,
            "wake_on_event": cfg.wake_on_event,
            "ws_port": cfg.ws_port,
            "http_port": cfg.http_port,
            "group_trigger_word": cfg.group_trigger_word,
            "private_trigger": cfg.private_trigger,
            "skills_fs_enabled": cfg.skills_fs_enabled,
            "skills_fs_mountpoint": cfg.skills_fs_mountpoint,
            "skills_fs_binary": cfg.skills_fs_binary,
            "skills_fs_config": cfg.skills_fs_config,
            "wake_enabled": cfg.wake_enabled,
            "wake_preset": cfg.wake_preset,
            "wake_primary": cfg.wake_primary,
            "wake_session": cfg.wake_session,
            "wake_http_url": cfg.wake_http_url,
            "wake_http_session_id": cfg.wake_http_session_id,
            "wake_cli_command": cfg.wake_cli_command,
            "wake_debounce_seconds": cfg.wake_debounce_seconds,
            "wake_cooldown_seconds": cfg.wake_cooldown_seconds,
            "wake_new_message_idle_seconds": cfg.wake_new_message_idle_seconds,
        }
        cfg_path.write_text(json.dumps(cfg_dict, indent=2))

        # Launch daemon as background process
        cmd = [sys.executable, "-m", "napcat_cli.daemon.watch", str(cfg_path)]
        proc = subprocess.Popen(cmd, stdout=open(DATA_DIR / "daemon.log", "a"), stderr=subprocess.STDOUT)
        print(f"Daemon started (PID: {proc.pid})", file=sys.stderr)
        print(f"Log: {DATA_DIR / 'daemon.log'}")
        return 0

    elif args.subcommand == "stop":
        pid_file = DATA_DIR / "daemon.pid"
        if pid_file.exists():
            pid = int(pid_file.read_text().strip())
            try:
                os.kill(pid, 15)
                pid_file.unlink()
                print(f"Daemon (PID {pid}) stopped", file=sys.stderr)
            except ProcessLookupError:
                print("Daemon not running", file=sys.stderr)
                pid_file.unlink()
                return 0
            return 0
        else:
            print("No daemon PID found", file=sys.stderr)
            return 1

    elif args.subcommand == "status":
        pid_file = DATA_DIR / "daemon.pid"
        if pid_file.exists():
            pid = int(pid_file.read_text().strip())
            try:
                os.kill(pid, 0)
                print(f"Daemon running (PID: {pid})", file=sys.stderr)
                log = DATA_DIR / "daemon.log"
                if log.exists():
                    lines = log.read_text().splitlines()[-10:]
                    print("Recent log:", file=sys.stderr)
                    for line in lines:
                        print(f"  {line}", file=sys.stderr)
                return 0
            except ProcessLookupError:
                print("Daemon not running (stale PID file)", file=sys.stderr)
                return 1
        else:
            print("Daemon not running", file=sys.stderr)
            return 1

    else:
        print(f"Unknown daemon command: {args.subcommand}", file=sys.stderr)
        return 1


def cmd_events(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat events - Read events from the filesystem bridge."""
    reader = EventsReader(DATA_DIR)
    events = reader.read(limit=args.limit, event_type=args.type, since=args.since)
    if getattr(args, "no_heartbeat", False):
        events = [e for e in events if e.get("meta_event_type") != "heartbeat"]

    if args.output == "json":
        print(json.dumps(events, indent=2, ensure_ascii=False))
    else:
        for ev in events:
            ts = ev.get("time", 0)
            ptype = ev.get("post_type", "?")
            ntype = ev.get("notice_type", ev.get("request_type", "?"))
            msg = ev.get("message", ev.get("raw_message", ""))
            sender = ev.get("sender", {})
            nickname = sender.get("nickname", "?") if isinstance(sender, dict) else "?"
            print(f"[{ts}] {ptype}/{ntype} from {nickname}: {msg[:100]}")

    return 0


def cmd_alerts(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat alerts - Check or clear alerts from SQLite database."""
    from napcat_cli.lib.events import EventsReader
    reader = EventsReader(DATA_DIR)

    if getattr(args, "subcommand", None) == "clear":
        reader._get_conn()
        from napcat_cli.lib.events_sqlite import clear_alerts
        count = clear_alerts(reader._conn)
        print(f"All alerts cleared ({count} removed)", file=sys.stderr)
        return 0

    alerts = reader.read_alerts()
    if not alerts:
        print("No pending alerts", file=sys.stderr)
        return 0

    print(f"Pending alerts ({len(alerts)}):", file=sys.stderr)
    for a in alerts:
        name = a.get("name", "?")
        summary = a.get("summary", a.get("text", "?"))
        print(f"  [{name}] {summary[:80]}", file=sys.stderr)

    return 0 if alerts else 1
def cmd_batch(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat batch - Batch operations (kick, mute, unmute)."""
    if not require_online(api):
        return 1

    batch_cmd = getattr(args, "batch_command", None)
    group_id = str(args.group_id)
    user_ids = [str(uid) for uid in args.user_ids]
    results = []

    if batch_cmd == "kick":
        reject = getattr(args, "reject", False)
        for uid in user_ids:
            kwargs = {"group_id": group_id, "user_id": uid}
            if reject:
                kwargs["reject_add_request"] = True
            result = api.call("set_group_kick", **kwargs)
            results.append({"user_id": uid, "action": "kick", "result": result})

    elif batch_cmd == "mute":
        duration = getattr(args, "duration", 1800)
        for uid in user_ids:
            result = api.call("set_group_ban", group_id=group_id, user_id=uid, duration=duration)
            results.append({"user_id": uid, "action": "mute", "result": result})

    elif batch_cmd == "unmute":
        for uid in user_ids:
            result = api.call("set_group_ban", group_id=group_id, user_id=uid, duration=0)
            results.append({"user_id": uid, "action": "unmute", "result": result})

    else:
        print("Unknown batch command. Use: kick, mute, unmute", file=sys.stderr)
        return 1

    # Summary
    success = sum(1 for r in results if r["result"].get("retcode") == 0 or r["result"].get("status") == "ok")
    failed = len(results) - success
    print(f"Batch {batch_cmd}: {success}/{len(results)} succeeded", file=sys.stderr)
    if failed:
        print(f"  Failed: {failed} operations", file=sys.stderr)

    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0 if failed == 0 else 1


def cmd_config(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat config - Manage configuration."""
    cfg = get_config()

    if args.subcommand == "get":
        key = args.key
        val = getattr(cfg, key, None)
        if val is not None:
            print(val)
        else:
            print(f"Unknown key: {key}", file=sys.stderr)
            return 1
        return 0

    elif args.subcommand == "set":
        cfg.set(args.key, args.value)
        cfg.save()
        print(f"Set {args.key} = {args.value}", file=sys.stderr)
        return 0

    elif args.subcommand == "show":
        items = [
            ("api_url", cfg.api_url),
            ("token", cfg.token),
            ("self_id", cfg.self_id),
            ("data_dir", str(DATA_DIR)),
            ("webhook_port", cfg.webhook_port),
            ("ws_port", cfg.ws_port),
            ("http_port", cfg.http_port),
            ("wake_on_event", cfg.wake_on_event),
            ("wake_command", cfg.wake_command),
            ("wake_enabled", cfg.wake_enabled),
            ("wake_preset", cfg.wake_preset),
            ("wake_primary", cfg.wake_primary),
            ("wake_session", cfg.wake_session),
            ("wake_http_url", cfg.wake_http_url),
            ("wake_http_key", "(set)" if cfg.wake_http_key else ""),
            ("wake_http_session_id", cfg.wake_http_session_id),
            ("wake_cli_command", cfg.wake_cli_command),
            ("wake_debounce_seconds", cfg.wake_debounce_seconds),
            ("wake_cooldown_seconds", cfg.wake_cooldown_seconds),
            ("wake_new_message_idle_seconds", cfg.wake_new_message_idle_seconds),
        ]
        for key, val in items:
            print(f"{key}: {val}")
        return 0

    else:
        print(f"Unknown config command: {args.subcommand}", file=sys.stderr)
        return 1


def cmd_status(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat status - Check bot status."""
    # Check login info
    login = api.call("get_login_info")
    if login.get("retcode") != 0:
        print("Not logged in", file=sys.stderr)
        return 1

    # Check online status
    status = api.call("get_status")
    data = status.get("data", {})
    online = data.get("online", False)
    good = data.get("good", False)

    nickname = login["data"].get("nickname", "Unknown")
    user_id = login["data"].get("user_id", "Unknown")

    status_text = "在线" if online else "离线"
    health_text = "状态良好" if good else "状态异常"
    print(f"Logged in as: {nickname} ({user_id})", file=sys.stderr)
    print(f"Status: {status_text} ({health_text})", file=sys.stderr)

    # Update config with self_id
    cfg = get_config()
    if cfg.self_id is None:
        cfg.self_id = str(login["data"].get("user_id", ""))
        cfg.save()

    # Print full JSON
    print(json.dumps({"login": login["data"], "status": data}, indent=2, ensure_ascii=False))

    if not online:
        print("Bot is offline. Try 'napcat daemon start' to restart, or check WebUI at http://127.0.0.1:6099/webui", file=sys.stderr)
        return 1
    return 0


def cmd_ocr(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat ocr - OCR an image."""
    if not require_online(api):
        return 1
    image_path = args.image
    if not image_path.startswith(("http://", "https://", "file://", "base64://")):
        if Path(image_path).exists():
            image_path = "file://" + str(Path(image_path).resolve())
        else:
            print(f"Error: image file not found: {args.image}", file=sys.stderr)
            return 1
    result = api.call("ocr_image", image=image_path)
    if result.get("retcode") != 0:
        msg = result.get("message", "")
        if "not supported" in str(msg).lower() or "not exist" in str(msg).lower():
            print(f"Error: OCR is not supported by this NapCat instance: {msg}", file=sys.stderr)
        else:
            print(f"Error: OCR failed: {msg}", file=sys.stderr)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("retcode") == 0 else 1


def cmd_translate(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat translate - QQ translation (may not be available in all NapCat versions)."""
    if not require_online(api):
        return 1
    result = api.call("qq_translate", text=args.text, from_lang=args.from_lang, to_lang=args.to_lang)
    if result.get("retcode") != 0:
        msg = result.get("message", "")
        if "not supported" in str(msg).lower() or "not exist" in str(msg).lower():
            print(f"Error: QQ translation is not supported by this NapCat instance: {msg}", file=sys.stderr)
        else:
            print(f"Translation failed: {msg}", file=sys.stderr)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("retcode") == 0 else 1


def cmd_like(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat like - Send profile like (thumbs up)."""
    if not require_online(api):
        return 1
    times = max(1, min(10, args.times or 1))
    result = api.call("send_like", user_id=args.user_id, times=times)
    if result.get("retcode") == 0:
        print(f"Liked user {args.user_id} ({times} time(s))", file=sys.stderr)
        return 0
    else:
        print(f"Error: {result.get('message', result)}", file=sys.stderr)
        return 1



def cmd_send_forward(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat send_forward - Forward a message to a group."""
    if api.is_api_supported("napcat_forward_msg") is False:
        print("Error: API 'napcat_forward_msg' is not supported by this NapCat instance.", file=sys.stderr)
        return 1
    if not require_online(api):
        return 1
    result = api.call("napcat_forward_msg", group_id=args.group_id, message_id=args.message_id)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("retcode") == 0 else 1


def cmd_send_poke(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat send_poke - Poke a member in a group."""
    if not require_online(api):
        return 1
    if args.target_type == "group":
        result = api.call("send_poke", group_id=args.target_id, user_id=args.user_id)
    else:
        result = api.call("send_poke", user_id=args.target_id)
    if result.get("retcode") != 0:
        msg = result.get("message", result)
        if "private" in str(msg).lower() or args.target_type == "private":
            print(f"Error: Private poke is not supported by this NapCat instance: {msg}", file=sys.stderr)
        else:
            print(f"Error: {msg}", file=sys.stderr)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("retcode") == 0 else 1


def cmd_create_schedule(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat create_schedule - Create a scheduled message."""
    if api.is_api_supported("create_schedule") is False:
        print("Error: API 'create_schedule' is not supported by this NapCat instance.", file=sys.stderr)
        return 1
    if not require_online(api):
        return 1
    msg_type = args.target_type
    target_id = args.target_id
    message = args.message
    if args.message_type == "array":
        try:
            message = json.loads(message)
        except json.JSONDecodeError:
            print(f"Error: message must be valid JSON when --message-type=array", file=sys.stderr)
            return 1
    kwargs: dict = {
        "message": message,
        "repeat": args.repeat or args.times,
        "interval": args.repeat_interval,
    }
    if msg_type == "group":
        kwargs["group_id"] = target_id
    else:
        kwargs["user_id"] = target_id
    result = api.call("create_schedule", **kwargs)
    if result.get("retcode") != 0:
        msg = result.get("message", "")
        if "not supported" in str(msg).lower() or "not exist" in str(msg).lower() or "no such" in str(msg).lower():
            print(f"Error: Scheduled messages are not supported by this NapCat instance: {msg}", file=sys.stderr)
        else:
            print(f"Error: {msg}", file=sys.stderr)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("retcode") == 0 else 1


def cmd_schedule_list(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat schedule list - List scheduled messages."""
    if api.is_api_supported("get_schedule_list") is False:
        print("Error: API 'get_schedule_list' is not supported by this NapCat instance.", file=sys.stderr)
        return 1
    if not require_online(api):
        return 1
    kwargs: dict = {}
    if args.group:
        kwargs["group_id"] = args.group
    if args.user:
        kwargs["user_id"] = args.user
    result = api.call("get_schedule_list", **kwargs)
    if result.get("retcode") != 0:
        msg = result.get("message", "")
        if "not supported" in str(msg).lower() or "not exist" in str(msg).lower() or "no such" in str(msg).lower():
            print(f"Error: Scheduled messages are not supported by this NapCat instance: {msg}", file=sys.stderr)
        else:
            print(f"Error: {msg}", file=sys.stderr)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("retcode") == 0 else 1


def cmd_schedule_cancel(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat schedule cancel - Cancel a scheduled message."""
    if not require_online(api):
        return 1
    kwargs: dict = {"schedule_id": args.schedule_id}
    if args.group:
        kwargs["group_id"] = args.group
    if args.user:
        kwargs["user_id"] = args.user
    result = api.call("delete_schedule", **kwargs)
    if result.get("retcode") != 0:
        msg = result.get("message", "")
        if "not supported" in str(msg).lower() or "not exist" in str(msg).lower() or "no such" in str(msg).lower():
            print(f"Error: Scheduled messages are not supported by this NapCat instance: {msg}", file=sys.stderr)
        else:
            print(f"Error: {msg}", file=sys.stderr)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("retcode") == 0 else 1


def cmd_schedule(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat schedule - Manage scheduled messages."""
    sub = args.subcommand
    if sub == "list":
        return cmd_schedule_list(args, api)
    elif sub == "cancel":
        return cmd_schedule_cancel(args, api)
    else:
        print("Error: unknown schedule subcommand", file=sys.stderr)
        return 1


def cmd_get_cookies(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat get_cookies - Get QQ web cookies."""
    if not require_online(api):
        return 1
    result = api.call("napcat_get_cookies", domain=args.domain)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("retcode") == 0 else 1


def cmd_get_stranger_info(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat get_stranger_info - Get stranger info by QQ number."""
    if not require_online(api):
        return 1
    kwargs: dict = {"user_id": str(args.user_id)}
    if hasattr(args, "no_cache") and args.no_cache:
        kwargs["no_cache"] = True
    result = api.call("get_stranger_info", **kwargs)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("retcode") == 0 else 1


def cmd_react(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat react - Send emoji reaction on a group message."""
    if not require_online(api):
        return 1
    result = api.call("set_msg_emoji_like", group_id=args.group_id, message_id=args.message_id, emoji_id=args.emoji_id)
    if result.get("retcode") == 0:
        print(f"Emoji reaction sent (emoji_id={args.emoji_id})", file=sys.stderr)
        return 0
    else:
        print(f"Error: {result.get('message', result)}", file=sys.stderr)
        return 1

def cmd_search(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat search - Search messages by keyword in event history."""
    reader = EventsReader(DATA_DIR)
    keyword = args.keyword or getattr(args, "keyword_flag", None)
    if not keyword:
        print("Error: keyword is required (use positional or --keyword)", file=sys.stderr)
        return 1
    events = reader.read(
        limit=args.limit,
        since=args.since,
        event_type=args.event_type,
        keyword=keyword,
    )
    results = events
    print(f"Found {len(results)} matching messages", file=sys.stderr)
    return 0


def cmd_msg(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat msg - Browse recent messages or query a specific message ID."""
    
    # Direct message ID lookup
    if args.message_id is not None:
        reader = EventsReader(DATA_DIR)
        msg_id = int(args.message_id)
        events = reader.read(limit=500, event_type="message")
        for e in events:
            if e.get("message_id") == msg_id:
                sender = e.get("sender", {})
                msg = e.get("message", "")
                text = ""
                if isinstance(msg, list):
                    for seg in msg:
                        if seg.get("type") == "text":
                            text += seg.get("data", {}).get("text", "")
                elif isinstance(msg, str):
                    text = msg
                result = {
                    "message_id": msg_id,
                    "time": e.get("time"),
                    "sender": f"{sender.get('nickname', '?')} ({sender.get('user_id', '?')})",
                    "group_id": e.get("group_id", ""),
                    "message_type": e.get("message_type", ""),
                    "text": text[:500],
                    "raw_message": e.get("raw_message", ""),
                }
                print(json.dumps(result, indent=2, ensure_ascii=False))
                return 0
        print(f"Message ID {msg_id} not found in event history", file=sys.stderr)
        return 1
    
    # Browse mode
    reader = EventsReader(DATA_DIR)
    group_id: int | None = int(args.group_id) if args.group_id else None
    user_id: int | None = int(args.user_id) if args.user_id else None
    events = reader.read(
        limit=args.limit,
        since=args.since,
        event_type="message",
        group_id=group_id,
        user_id=user_id,
    )
    results = []
    for e in events:
        sender = e.get("sender", {})
        text = ""
        msg = e.get("message", "")
        if isinstance(msg, list):
            for seg in msg:
                if seg.get("type") == "text":
                    text += seg.get("data", {}).get("text", "")
        elif isinstance(msg, str):
            text = msg
        results.append({
            "time": e.get("time"),
            "sender": f"{sender.get('nickname', '?')} ({sender.get('user_id', '?')})",
            "group_id": e.get("group_id", ""),
            "message_type": e.get("message_type", ""),
            "message_id": e.get("message_id", ""),
            "text": text[:200],
        })
    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 0

def cmd_phone(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat phone — Launch mobile-style TUI interface or run subcommand."""
    import asyncio
    import os as _os

    if args.port:
        _os.environ["NAPCAT_HTTP_PORT"] = str(args.port)

    # Handle subcommands for scriptable CLI access
    if hasattr(args, "phone_subcommand") and args.phone_subcommand:
        subcmd = args.phone_subcommand
        if subcmd == "status":
            return cmd_status(args)
        elif subcmd == "config":
            return cmd_config_show(args)
        elif subcmd == "alerts":
            return cmd_alerts_check(args)
        elif subcmd == "events":
            limit = getattr(args, "limit", 50)
            no_hb = getattr(args, "no_heartbeat", False)
            fake_args = argparse.Namespace(limit=limit, no_heartbeat=no_hb, output=None, output_file=None, event_type=None, since=None, json=False, text=False, array=False)
            return cmd_events(fake_args, api)
        elif subcmd == "msg":
            target_type = getattr(args, "target_type", None)
            target_id = getattr(args, "target_id", None)
            message = getattr(args, "message", None)
            if not all([target_type, target_id, message]):
                print("Error: target_type, target_id, and message are required", file=sys.stderr)
                return 1
            fake_args = argparse.Namespace(message_type=target_type, target_id=str(target_id), message=message, at=[], file=None, image=None, json=False)
            return cmd_send(fake_args, api)
        else:
            print(f"Unknown phone subcommand: {subcmd}", file=sys.stderr)
            return 1

    if args.non_interactive:
        # Quick status check mode
        from tui.api import get_client
        client = get_client()
        try:
            events = asyncio.run(client.get_events(limit=5))
            alerts = asyncio.run(client.get_alerts())
            print(f"Events: {len(events)}, Alerts: {len(alerts)}")
            for e in events[:3]:
                print(f"  - {e.get('post_type', '?')}: {e.get('message_type', '')} {e.get('sender', {}).get('nickname', '')}")
            return 0
        except Exception as e:
            print(f"Error connecting to daemon: {e}", file=sys.stderr)
            return 1

    # Full TUI mode
    from tui.app import NapCatApp
    app = NapCatApp()
    app.run()
    return 0

def _call_provider(action: str, params: dict, port: int) -> dict:
    """Call the daemon HTTP provider at http://127.0.0.1:<port>/invoke."""
    import urllib.request
    import urllib.error

    url = f"http://127.0.0.1:{port}/invoke"
    body = json.dumps({"action": action, "params": params}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"status": "error", "message": raw, "code": e.code}
    except urllib.error.URLError as e:
        return {"status": "error", "message": f"Connection failed: {e.reason}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}


def cmd_message(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat message - Query message content via daemon provider."""
    cfg = get_config()
    port = cfg.http_port

    params: dict = {"message_id": str(args.message_id)}
    if args.group:
        params["group_id"] = str(args.group)
    if args.user:
        params["user_id"] = str(args.user)
    if args.content:
        params["content"] = args.content

    result = _call_provider("get_message_content", params, port)

    if result.get("status") == "error":
        print(f"Error: {result.get('message', 'Unknown error')}", file=sys.stderr)
        return 1

    if args.content:
        # Specific content requested — print that field
        key = args.content
        val = result.get(key)
        if val is not None:
            if isinstance(val, (dict, list)):
                print(json.dumps(val, indent=2, ensure_ascii=False))
            else:
                print(val)
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        # No --content: print metadata + content summary
        meta = result.get("metadata", {})
        if meta:
            print("=== Message Metadata ===")
            for k, v in meta.items():
                print(f"  {k}: {v}")
            print()

        # Show content segments
        for key in ("text", "image", "file", "video", "record", "forward"):
            val = result.get(key)
            if val is not None:
                print(f"=== {key.upper()} ===")
                if isinstance(val, (dict, list)):
                    print(json.dumps(val, indent=2, ensure_ascii=False))
                else:
                    print(val)
                print()

    return 0


def _load_local_schemas() -> dict:
    """Load ACTION_SCHEMAS from daemon/schemas.py without importing."""
    schemas_file = ROOT / "daemon" / "schemas.py"
    if not schemas_file.exists():
        return {}
    raw = schemas_file.read_text()
    # Extract the ACTION_SCHEMAS dict with a simple AST-free approach:
    # Find the dict literal between ACTION_SCHEMAS = { and the closing }
    start = raw.find('ACTION_SCHEMAS')
    if start == -1:
        return {}
    brace_start = raw.find('{', start)
    if brace_start == -1:
        return {}
    # Count braces to find the matching close
    depth = 0
    for i, ch in enumerate(raw[brace_start:], start=brace_start):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                expr = raw[brace_start:i + 1]
                break
    else:
        return {}
    try:
        return eval(expr, {"__builtins__": {}}, {})
    except Exception:
        return {}


def cmd_schema(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat schema - Describe an action schema."""
    cfg = get_config()
    port = cfg.http_port

    local_schemas = _load_local_schemas()

    # --list mode: print all available actions
    if args.list:
        # Also try to get from HTTP
        try:
            result = _call_provider("describe_action", {"action": "__list__"}, port)
            if isinstance(result, list) and result:
                actions = sorted(result)
            else:
                actions = sorted(local_schemas.keys())
        except Exception:
            actions = sorted(local_schemas.keys())

        if not actions:
            print("No actions found.", file=sys.stderr)
            return 1

        print(f"Available actions ({len(actions)}):")
        for a in actions:
            desc = local_schemas.get(a, {}).get("description", "")
            req = local_schemas.get(a, {}).get("required", [])
            req_str = f" [{', '.join(req)}]" if req else ""
            print(f"  {a}{req_str}")
            if desc:
                print(f"    {desc}")
        return 0

    action = args.action
    if action is None:
        print("Error: action name required. Use 'napcat schema --list' to see available actions.", file=sys.stderr)
        return 1

    # Try HTTP provider first
    result = _call_provider("describe_action", {"action": action}, port)

    # If HTTP fails or action not found, fall back to local schemas.py
    if result.get("status") == "error" or not result.get("params"):
        if action in local_schemas:
            result = local_schemas[action]
        else:
            print(f"Error: unknown action '{action}'", file=sys.stderr)
            actions = sorted(local_schemas.keys()) if local_schemas else []
            if actions:
                print(f"Available actions: {', '.join(actions)}", file=sys.stderr)
            return 1

    print(f"Action: {action}")
    print(f"Description: {result.get('description', 'N/A')}")
    print(f"Required params: {', '.join(result.get('required', [])) or 'none'}")
    print(f"All params: {', '.join(result.get('params', [])) or 'none'}")
    example = result.get("example", {})
    if example:
        print(f"Example:")
        print(json.dumps(example, indent=2, ensure_ascii=False))

    return 0


FS_TREE = """\
/napcat/
├── AGENTS.md                          # Full guide
│
├── Legacy write files (root level):
│   ├── send_group                     # JSON: {"message": [...]}
│   ├── send_private                   # JSON: {"user_id": N, "message": [...]}
│   ├── delete                         # JSON: {"message_id": N}
│   ├── clear_alert                    # JSON: {"alert_name": "..."}
│   ├── clear_all_alerts               # {}
│   ├── group_kick                     # JSON: {"user_id": N}
│   ├── group_ban                      # JSON: {"user_id": N, "duration": N}
│   ├── group_admin                    # JSON: {"user_id": N, "enable": true/false}
│   ├── group_card                     # JSON: {"user_id": N, "card": "..."}
│   ├── group_name                     # JSON: {"name": "..."}
│   ├── group_leave                    # {}
│   ├── group_announce                 # JSON: {"content": "..."}
│   ├── group_essence                  # JSON: {"message_id": N}
│   ├── delete_essence                 # JSON: {"message_id": N}
│   ├── friend_add                     # JSON: {"user_id": N, "remark": "..."}
│   ├── friend_delete                  # JSON: {"user_id": N}
│   ├── friend_remark                  # JSON: {"user_id": N, "remark": "..."}
│   └── send_poke                      # JSON: {"user_id": N}
│
├── groups/:group_id/                  # Per-group operations (path param: group_id)
│   ├── send                           # JSON: {"message": [...]}
│   ├── kick                           # JSON: {"user_id": N}
│   ├── ban                            # JSON: {"user_id": N, "duration": N}
│   ├── admin                          # JSON: {"user_id": N, "enable": true/false}
│   ├── card                           # JSON: {"user_id": N, "card": "..."}
│   ├── name                           # JSON: {"name": "..."}
│   ├── leave                          # {}
│   ├── info                           # (read-only)
│   ├── members                        # (read-only)
│   ├── essence_list                   # (read-only)
│   ├── poke                           # JSON: {"user_id": N}
│   ├── honor                          # (read-only)
│   ├── announce                       # JSON: {"content": "..."}
│   │
│   └── :time_range/:message_id/       # Per-message content directory
│       ├── metadata                   # sender, time, message_type, etc.
│       ├── text                       # Plaintext of all text segments
│       ├── image                      # URL, file, file_size, summary
│       ├── image_2, image_3, ...      # Additional images
│       ├── file                       # File segment info
│       ├── video                      # Video segment info
│       ├── record                     # Voice record info
│       └── forward                    # Forward message info
│
├── friends/:user_id/                  # Per-friend operations (path param: user_id)
│   ├── send                           # JSON: {"message": [...]}
│   ├── info                           # (read-only)
│   ├── remark                         # JSON: {"remark": "..."}
│   │
│   └── :time_range/:message_id/       # Same structure as groups/ above
│
└── get_image                          # Legacy: get image by message_id
"""


def _default_wake_prompt(reason: str) -> str:
    return (f"[napcat-cli 手动唤醒] reason={reason}。请用 `napcat events` / `napcat alerts` "
            f"查看最新动态，结合上下文酌情处理与回复。")


def _build_waker_for_cli(cfg, transport: str | None = None):
    """Build a Waker from config, optionally overriding the primary transport."""
    from napcat_cli.wake_presets import build_waker
    if transport and transport in ("http", "cli"):
        import copy
        cfg = copy.copy(cfg)
        cfg.wake_primary = transport
    return build_waker(cfg)


def cmd_wake(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat wake - Deliver a wake to the configured agent (HTTP/CLI, auto-fallback)."""
    cfg = get_config()
    sub = getattr(args, "wake_subcommand", None)

    if sub == "test":
        return _wake_test(cfg)
    if sub == "sessions":
        return _wake_sessions(cfg)

    reason = getattr(args, "reason", "manual")
    prompt = getattr(args, "prompt", None) or _default_wake_prompt(reason)
    transport = getattr(args, "transport", None)
    dry_run = getattr(args, "dry_run", False)
    timeout = getattr(args, "wake_timeout", None) or 120.0

    waker = _build_waker_for_cli(cfg, transport)

    # No backend configured → legacy wake_command escape hatch, else guidance.
    if waker.empty:
        if cfg.wake_command:
            from napcat_cli.wake import build_wake_command
            cmd = build_wake_command(cfg.wake_command, reason)
            if dry_run:
                print(cmd)
                return 0
            import subprocess
            try:
                r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
                if r.stdout:
                    print(r.stdout, end="")
                print(f"Legacy wake executed ({reason})", file=sys.stderr)
                return r.returncode
            except Exception as e:
                print(f"Legacy wake failed: {e}", file=sys.stderr)
                return 1
        print("No wake backend configured. Run `napcat setup` (Hermes preset) or set "
              "wake_preset/wake_session/wake_http_*/wake_cli_command.", file=sys.stderr)
        return 1

    if dry_run:
        # Render every configured transport so the user sees what would fire.
        print(f"# dry-run — reason={reason}, primary={waker.primary}")
        any_shown = False
        for b in waker.backends:
            res = b.wake(prompt, reason, {}, "dry-run", dry_run=True)
            print(f"[{b.name}] {res.detail}")
            any_shown = True
        if not any_shown:
            print("(no transport configured)")
        return 0

    res = waker.wake(prompt, reason, timeout=timeout)
    if res.ok:
        print(f"Wake delivered via {res.transport} ({reason}) — {res.detail}", file=sys.stderr)
        return 0
    print(f"Wake failed via {res.transport}: {res.detail}", file=sys.stderr)
    return 1


def _wake_test(cfg) -> int:
    """Probe each configured wake transport."""
    waker = _build_waker_for_cli(cfg)
    print(f"preset={cfg.wake_preset} primary={cfg.wake_primary} session={cfg.wake_session}")
    if waker.empty:
        if cfg.wake_command:
            print(f"  [legacy] wake_command is set: {cfg.wake_command[:80]}")
        else:
            print("  No wake backend configured. Run `napcat setup`.")
        return 1
    rc = 0
    for t in waker.test():
        tag = "OK" if (t["configured"] and t["reachable"]) else "--"
        print(f"  [{tag}] {t['transport']:5} configured={t['configured']} reachable={t['reachable']} ({t['label']})")
        if t["configured"] and not t["reachable"]:
            rc = 1
    # CLI: also note whether the hermes binary is on PATH
    if cfg.wake_preset == "hermes":
        import shutil
        print(f"  hermes on PATH: {bool(shutil.which('hermes'))}")
    return rc


def _wake_sessions(cfg) -> int:
    """List agent sessions (Hermes /api/sessions when the HTTP backend is configured)."""
    waker = _build_waker_for_cli(cfg)
    sessions = waker.list_sessions()
    if sessions is None:
        print("Sessions listing requires the HTTP backend (configure wake_http_url + "
              "wake_http_key, e.g. via `napcat setup` opt-in).", file=sys.stderr)
        return 1
    if not sessions:
        print("No sessions found.")
        return 0
    print(f"Sessions ({len(sessions)}):")
    for s in sessions[:30]:
        print(f"  {s.get('id','?'):40} {s.get('title','')[:50]}  {s.get('last_active','')}")
    return 0


def cmd_fs(args: argparse.Namespace, api: NapCatAPI) -> int:
    """napcat fs - Show skills-fs directory tree and workflow."""
    cfg = get_config()
    port = cfg.http_port

    # Query /status for skills-fs health
    skills_fs_info = None
    try:
        import urllib.request
        import urllib.error
        import urllib.parse
        host = urllib.parse.urlparse(cfg.api_url).hostname or "127.0.0.1"
        url = f"http://{host}:{port}/status"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            status_data = json.loads(resp.read().decode("utf-8"))
            skills_fs_info = status_data.get("skills_fs")
    except Exception:
        pass

    if skills_fs_info:
        print("=== Skills-FS Status ===")
        st = skills_fs_info.get("status", "unknown")
        print(f"  Status: {st}")
        mp = skills_fs_info.get("mountpoint", "")
        if mp:
            print(f"  Mountpoint: {mp}")
        pid = skills_fs_info.get("pid")
        if pid:
            print(f"  PID: {pid}")
        print()
    else:
        print("=== Skills-FS Status ===")
        print("  Not available (daemon not running or skills-fs disabled)")
        print()

    # Do not traverse the FUSE mountpoint — it may block (D-state).
    print("Mountpoint contents: use `napcat events` / `napcat alerts`, or `ls <mountpoint>` manually.")
    print("  (CLI does not traverse the FUSE mount to avoid blocking.)")
    print()

    # Show static tree & workflow
    print(FS_TREE)
    print("Workflow:")
    print("  1. Read <file>.schema to see the expected JSON format.")
    print("  2. Write JSON matching the schema to <file>.")
    print("  3. Read <file> back to see the result (or error with schema hint).")
    print()
    print("Path parameters (:group_id, :user_id, :time_range, :message_id, :content)")
    print("are resolved by skills-fs before calling the provider.")
    print()
    print("Writeback: write errors return {\"error\": ..., \"expected_schema\": ...} on read.")
    return 0
def main() -> int:
    parser = argparse.ArgumentParser(
        prog="napcat",
        description="NapCat QQ CLI - Standalone CLI and daemon for QQ bot management",
    )
    parser.add_argument("--api-url", default=None, help="Override NAPCAT_API_URL")
    parser.add_argument("--token", default=None, help="Override NAPCAT_TOKEN")
    parser.add_argument("--data-dir", default=None, help="Override NAPCAT_DATA_DIR")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    parser.add_argument("--timeout", type=int, default=None, help="API request timeout in seconds (default: 30)")
    parser.add_argument("--json", action="store_true", help="Output JSON only (suppress stderr)")
    parser.add_argument("--quiet", action="store_true", help="Suppress stderr output")
    parser.add_argument("--version", action="version", version=f"napcat-cli {__version__}")

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # --- api ---
    api_p = subparsers.add_parser("api", help="Raw API access (like gh api)")
    api_p.add_argument("endpoint", help="API endpoint (e.g., get_login_info)")
    api_p.add_argument("--method", choices=["GET", "POST", "PUT", "DELETE"], default=None)
    api_p.add_argument("--data", "-d", help="JSON data for POST/PUT requests")
    api_p.add_argument("--output", "-o", choices=["json", "value", "raw"], default="json")

    # --- send ---
    send_p = subparsers.add_parser("send", help="Send a message")
    send_p.add_argument("target_type", choices=["group", "private"])
    send_p.add_argument("target_id", help="Group ID or user ID")
    send_p.add_argument("message", nargs="?", help="Message text (or use --message)")
    send_p.add_argument("--message", dest="message_text", help="Message text")
    send_p.add_argument("--at", action="append", help="QQ number to @ (repeatable)")
    send_p.add_argument("--file", help="File to send")
    send_p.add_argument("--image", help="Image to send")
    send_p.add_argument("--json", action="store_true", help="Output JSON only")

    # --- reply ---
    reply_p = subparsers.add_parser("reply", help="Reply to a message")
    reply_p.add_argument("target_type", choices=["group", "private"])
    reply_p.add_argument("target_id", help="Group ID or user ID")
    reply_p.add_argument("message_id", help="Message ID to reply to")
    reply_p.add_argument("message", nargs="?", help="Reply text (or use --message)")
    reply_p.add_argument("--message", dest="message_text", help="Reply text")
    reply_p.add_argument("--at", action="append", help="QQ number to @ (repeatable)")
    reply_p.add_argument("--file", help="File to attach")
    reply_p.add_argument("--image", help="Image to attach")
    reply_p.add_argument("--json", action="store_true", help="Output JSON only")

    # --- recall ---
    recall_p = subparsers.add_parser("recall", help="Recall a message")
    recall_p.add_argument("message_id", help="Message ID to recall")
    recall_p.add_argument("--group", help="Group ID (for group messages)")

    # --- group ---
    group_p = subparsers.add_parser("group", help="Group management")
    group_sub = group_p.add_subparsers(dest="subcommand")

    gi = group_sub.add_parser("info", help="Get group info")
    gi.add_argument("group_id")
    gi.add_argument("--no-cache", action="store_true")

    gm = group_sub.add_parser("members", help="List group members")
    gm.add_argument("group_id")
    gm.add_argument("--no-cache", action="store_true")
    gm.add_argument("--limit", type=int, default=None, help="Max events to return")

    gmi = group_sub.add_parser("member", help="Get member info")
    gmi.add_argument("group_id")
    gmi.add_argument("user_id")
    gmi.add_argument("--no-cache", action="store_true")

    gmute = group_sub.add_parser("mute", help="Mute a member")
    gmute.add_argument("group_id")
    gmute.add_argument("user_id")
    gmute.add_argument("--duration", "-d", type=int, default=None)

    gu = group_sub.add_parser("unmute", help="Unmute a member")
    gu.add_argument("group_id")
    gu.add_argument("user_id")

    gk = group_sub.add_parser("kick", help="Kick a member")
    gk.add_argument("group_id")
    gk.add_argument("user_id")
    gk.add_argument("--reject", action="store_true", help="Prevent rejoin")

    ga = group_sub.add_parser("admin", help="Set/remove admin")
    ga.add_argument("group_id")
    ga.add_argument("user_id")
    ga.add_argument("--enable", action="store_true", default=True)
    ga.add_argument("--disable", action="store_true", default=False)

    gr = group_sub.add_parser(
        "rename",
        help="Set group card name",
        usage="napcat group rename <group_id> <user_id> <card>",
        epilog="Example: napcat group rename 1050866499 3914024488 '测试名片'",
    )
    gr.add_argument("group_id")
    gr.add_argument("user_id")
    gr.add_argument("card", help="Card name")

    gre = group_sub.add_parser("remark", help="Set group remark")
    gre.add_argument("group_id")
    gre.add_argument("remark")

    gan = group_sub.add_parser("announce", help="Send group announcement")
    gan.add_argument("group_id")
    gan.add_argument("content")

    gl = group_sub.add_parser("list", help="List groups")
    gl.add_argument("--limit", "-n", type=int, default=None, help="Max groups to show")

    gp = group_sub.add_parser("poke", help="Poke a member")
    gp.add_argument("group_id")
    gp.add_argument("user_id")

    ge = group_sub.add_parser("essence", help="List essence messages")
    ge.add_argument("group_id")

    ges = group_sub.add_parser("set_essence", help="Set a message as essence")
    ges.add_argument("group_id")
    ges.add_argument("message_id")

    gdes = group_sub.add_parser("delete_essence", help="Delete an essence message")
    gdes.add_argument("group_id")
    gdes.add_argument("message_id")


    # --- friend ---
    friend_p = subparsers.add_parser("friend", help="Friend management")
    friend_sub = friend_p.add_subparsers(dest="subcommand")

    fl = friend_sub.add_parser("list", help="List friends")
    fl.add_argument("--no-cache", action="store_true", default=False)
    fi = friend_sub.add_parser("info", help="Get user info")
    fi.add_argument("user_id")
    fi.add_argument("--no-cache", action="store_true")
    fr = friend_sub.add_parser("remark", help="Set friend remark")
    fr.add_argument("user_id")
    fr.add_argument("remark")
    fa = friend_sub.add_parser("add", help="Send friend request")
    fa.add_argument("user_id")
    fa.add_argument("--remark", default=None)
    fd = friend_sub.add_parser("delete", help="Delete friend")
    fd.add_argument("user_id")

    # --- file ---
    file_p = subparsers.add_parser("file", help="File operations")
    file_sub = file_p.add_subparsers(dest="subcommand")

    fg = file_sub.add_parser("upload-group", help="Upload file to group")
    fg.add_argument("group_id")
    fg.add_argument("file")
    fg.add_argument("--name", default=None)
    fg.add_argument("--folder", default=None)

    fp = file_sub.add_parser("upload-private", help="Upload file privately")
    fp.add_argument("user_id")
    fp.add_argument("file")
    fp.add_argument("--name", default=None)

    flg = file_sub.add_parser("list-group", help="List group files")
    flg.add_argument("group_id")

    flf = file_sub.add_parser("list-folder", help="List folder contents")
    flf.add_argument("group_id")
    flf.add_argument("folder_id")

    finfo = file_sub.add_parser("info", help="Get file info")
    finfo.add_argument("group_id", help="Group ID")
    finfo.add_argument("file_id", help="File ID")

    fd = file_sub.add_parser("download", help="Download a file")
    fd.add_argument("group_id", help="Group ID")
    fd.add_argument("file_id", help="File ID")
    fd.add_argument("--output-dir", "-o", default=None)

    # --- daemon ---
    daemon_p = subparsers.add_parser("daemon", help="Manage watch daemon")
    daemon_p.add_argument("subcommand", choices=["start", "stop", "status"])

    # --- events ---
    events_p = subparsers.add_parser("events", help="Read events from SQLite database")
    events_p.add_argument("--type", default=None, help="Filter by event type")
    events_p.add_argument("--since", type=int, default=None, help="Events after timestamp")
    events_p.add_argument("--limit", "-n", type=int, default=50, help="Max events to read")
    events_p.add_argument("--output", "-o", choices=["json", "text"], default="json")
    events_p.add_argument("--no-heartbeat", action="store_true", help="Skip heartbeat events")

    # --- alerts ---
    alerts_p = subparsers.add_parser("alerts", help="Check or clear alerts")
    alerts_sub = alerts_p.add_subparsers(dest="subcommand")
    alerts_sub.add_parser("check", help="Show pending alerts")
    ac = alerts_sub.add_parser("clear", help="Clear all alerts")

    # --- config ---
    config_p = subparsers.add_parser("config", help="Manage configuration")
    config_sub = config_p.add_subparsers(dest="subcommand")
    cget = config_sub.add_parser("get", help="Get a config value")
    cget.add_argument("key")
    cset = config_sub.add_parser("set", help="Set a config value")
    cset.add_argument("key")
    cset.add_argument("value")
    config_sub.add_parser("show", help="Show all config")

    # --- status ---
    subparsers.add_parser("status", help="Check bot login status")

    # --- ocr ---
    ocr_p = subparsers.add_parser("ocr", help="OCR an image")
    ocr_p.add_argument("image", help="Image file path or URL")

    # --- translate ---
    trans_p = subparsers.add_parser("translate", help="QQ translation")
    trans_p.add_argument("text", help="Text to translate")
    trans_p.add_argument("--from", dest="from_lang", default="auto")
    trans_p.add_argument("--to", dest="to_lang", default="zh")


    # --- like ---
    like_p = subparsers.add_parser("like", help="Send profile like")
    like_p.add_argument("user_id", help="User QQ number to like")
    like_p.add_argument("times", type=int, default=1, nargs="?", help="Like times (1-10, default: 1)")

    # --- react ---
    react_p = subparsers.add_parser("react", help="Send emoji reaction on group message")
    react_p.add_argument("group_id", help="Group ID")
    react_p.add_argument("message_id", help="Message ID to react to")
    react_p.add_argument("emoji_id", help="Emoji ID (e.g. 166, 386)")

    # --- search ---
    search_p = subparsers.add_parser("search", help="Search messages by keyword in event history")
    search_p.add_argument("keyword", nargs="?", default=None, help="Keyword to search for (positional)")
    search_p.add_argument("--keyword", dest="keyword_flag", default=None, help="Keyword to search for (flag)")
    search_p.add_argument("--limit", "-n", type=int, default=50, help="Max events to scan (default: 50)")
    search_p.add_argument("--since", type=int, default=None, help="Events after timestamp")
    search_p.add_argument("--event-type", "-t", default=None, help="Filter by event type")

    # --- msg ---
    msg_p = subparsers.add_parser("msg", help="Browse recent messages or query a specific message ID")
    msg_p.add_argument("message_id", nargs="?", default=None, help="Message ID to look up directly (positional)")
    msg_p.add_argument("--group", "-g", dest="group_id", default=None, help="Filter by group ID")
    msg_p.add_argument("--user", "-u", dest="user_id", default=None, help="Filter by user ID")
    msg_p.add_argument("--limit", "-n", type=int, default=50, help="Max messages to show (default: 50)")
    msg_p.add_argument("--since", type=int, default=None, help="Messages after timestamp")
    # --- phone ---
    # --- batch ---
    batch_p = subparsers.add_parser("batch", help="Batch operations (kick, mute, unmute)")
    batch_sub = batch_p.add_subparsers(dest="batch_command")

    # batch kick
    batch_kick = batch_sub.add_parser("kick", help="Kick multiple members from a group")
    batch_kick.add_argument("group_id", help="Group ID")
    batch_kick.add_argument("user_ids", nargs="+", help="User IDs to kick")
    batch_kick.add_argument("--reject", action="store_true", default=False, help="Prevent rejoin")

    # batch mute
    batch_mute = batch_sub.add_parser("mute", help="Mute multiple members in a group")
    batch_mute.add_argument("group_id", help="Group ID")
    batch_mute.add_argument("user_ids", nargs="+", help="User IDs to mute")
    batch_mute.add_argument("--duration", "-d", type=int, default=1800, help="Duration in seconds (default: 1800)")

    # batch unmute
    batch_unmute = batch_sub.add_parser("unmute", help="Unmute multiple members in a group")
    batch_unmute.add_argument("group_id", help="Group ID")
    batch_unmute.add_argument("user_ids", nargs="+", help="User IDs to unmute")

    phone_p = subparsers.add_parser("phone", help="Launch mobile-style TUI interface")
    phone_p.add_argument("--port", type=int, default=None, help="Daemon HTTP port (default: NAPCAT_HTTP_PORT or 18821)")
    phone_p.add_argument("--non-interactive", action="store_true", help="Run once and exit (status check)")

    # Phone subcommands for scriptable CLI access to TUI features
    phone_sub = phone_p.add_subparsers(dest="phone_subcommand")
    phone_msg = phone_sub.add_parser("msg", help="Send message via TUI")
    phone_msg.add_argument("target_type", choices=["group", "private"])
    phone_msg.add_argument("target_id")
    phone_msg.add_argument("message", help="Message text")
    phone_status = phone_sub.add_parser("status", help="Check bot status")
    phone_events = phone_sub.add_parser("events", help="View events")
    phone_events.add_argument("--limit", "-n", type=int, default=50)
    phone_events.add_argument("--no-heartbeat", action="store_true")
    phone_alerts = phone_sub.add_parser("alerts", help="Check alerts")
    phone_config = phone_sub.add_parser("config", help="View config")

    # --- message ---
    message_p = subparsers.add_parser("message", help="Query message content via daemon provider")
    message_p.add_argument("message_id", help="Message ID")
    message_p.add_argument("--content", choices=["metadata", "text", "image", "file", "video", "record", "forward"], default=None, help="Specific content to retrieve")
    message_p.add_argument("--group", "-g", default=None, help="Filter by group ID")
    message_p.add_argument("--user", "-u", default=None, help="Filter by user ID")

    # --- schema ---
    schema_p = subparsers.add_parser("schema", help="Describe an action schema")
    schema_p.add_argument("--list", action="store_true", help="List all available actions")
    schema_p.add_argument("action", nargs="?", default=None, help="Action name (e.g., send_group_message)")

    # --- send_forward ---
    sf = subparsers.add_parser("send_forward", help="Forward a message to a group")
    sf.add_argument("message_id", help="Message ID to forward")
    sf.add_argument("group_id", help="Target group ID")

    # --- send_poke ---
    sp = subparsers.add_parser("send_poke", help="Poke a member in a group or private user")
    sp.add_argument("target_type", choices=["group", "private"])
    sp.add_argument("target_id", help="Group ID or user ID")
    sp.add_argument("user_id", help="User ID to poke (group only; ignored for private)")

    # --- create_schedule ---
    cs = subparsers.add_parser("create_schedule", help="Create a scheduled message")
    cs.add_argument("target_type", choices=["group", "private"])
    cs.add_argument("target_id", help="Group ID or user ID")
    cs.add_argument("message", help="Message text to schedule")
    cs.add_argument("--repeat", type=int, default=0, help="Number of repeats (0 = once)")
    cs.add_argument("--times", type=int, default=0, help="Times to send (deprecated, use --repeat)")
    cs.add_argument("--repeat-interval", type=int, default=60, help="Interval between repeats in seconds")
    cs.add_argument("--message-type", choices=["text", "array"], default="text", help="Message type")

    # --- schedule ---
    schedule_p = subparsers.add_parser("schedule", help="Manage scheduled messages")
    schedule_sub = schedule_p.add_subparsers(dest="subcommand")

    schedule_list_p = schedule_sub.add_parser("list", help="List scheduled messages")
    schedule_list_p.add_argument("--group", help="Group ID to filter")
    schedule_list_p.add_argument("--user", help="User ID to filter")

    schedule_cancel_p = schedule_sub.add_parser("cancel", help="Cancel a scheduled message")
    schedule_cancel_p.add_argument("schedule_id", help="Schedule ID to cancel")
    schedule_cancel_p.add_argument("--group", help="Group ID")
    schedule_cancel_p.add_argument("--user", help="User ID")

    # --- get_cookies ---
    gc = subparsers.add_parser("get_cookies", help="Get QQ web cookies")
    gc.add_argument("domain", nargs="?", default="qq.com", help="Cookie domain (default: qq.com)")

    gsi = subparsers.add_parser("get_stranger_info", help="Get stranger info by QQ number")
    gsi.add_argument("user_id")
    gsi.add_argument("--no-cache", action="store_true", default=False, help="Force fresh query")

    # --- fs ---
    subparsers.add_parser("fs", help="Show skills-fs directory tree and workflow")

    # --- wake ---
    wake_p = subparsers.add_parser("wake", help="Wake the configured agent (HTTP/CLI, auto-fallback)")
    wake_p.add_argument("--reason", "-r", default="manual", help="Wake reason (default: manual)")
    wake_p.add_argument("--prompt", "-p", default=None, help="Prompt text to send (default: a contextual prompt)")
    wake_p.add_argument("--transport", choices=["auto", "http", "cli"], default=None,
                        help="Force a transport for this wake (default: from config / auto)")
    wake_p.add_argument("--timeout", dest="wake_timeout", type=float, default=None,
                        help="Per-wake timeout in seconds (default: 120)")
    wake_p.add_argument("--dry-run", action="store_true", help="Render what would fire without executing")
    wake_sub = wake_p.add_subparsers(dest="wake_subcommand")
    wake_sub.add_parser("test", help="Probe each configured transport (configured + reachable)")
    wake_sub.add_parser("sessions", help="List agent sessions (requires HTTP backend)")

    # --- setup ---
    setup_p = subparsers.add_parser("setup", help="Interactive setup wizard")
    setup_p.add_argument("--non-interactive", action="store_true")
    setup_p.add_argument("--yes", "-y", action="store_true")
    setup_p.add_argument("--force", action="store_true")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Apply overrides
    if args.api_url:
        os.environ["NAPCAT_API_URL"] = args.api_url
    if args.token:
        os.environ["NAPCAT_TOKEN"] = args.token
    if args.data_dir:
        os.environ["NAPCAT_DATA_DIR"] = args.data_dir

    # Init API client
    api = NapCatAPI(timeout=args.timeout)

    # Dispatch
    commands = {
        "api": cmd_api,
        "send": cmd_send,
        "recall": cmd_recall,
        "group": cmd_group,
        "friend": cmd_friend,
        "file": cmd_file,
        "daemon": cmd_daemon,
        "events": cmd_events,
        "alerts": cmd_alerts,
        "config": cmd_config,
        "status": cmd_status,
        "ocr": cmd_ocr,
        "translate": cmd_translate,
        "phone": cmd_phone,
        "like": cmd_like,
        "react": cmd_react,
        "search": cmd_search,
        "msg": cmd_msg,
        "batch": cmd_batch,
        "message": cmd_message,
        "schema": cmd_schema,
        "fs": cmd_fs,
        "send_forward": cmd_send_forward,
        "send_poke": cmd_send_poke,
        "reply": cmd_reply,
        "create_schedule": cmd_create_schedule,
        "schedule": cmd_schedule,
        "get_cookies": cmd_get_cookies,
        "get_stranger_info": cmd_get_stranger_info,
        "wake": cmd_wake,
        "setup": lambda a, api: __import__("napcat_cli.setup_wizard", fromlist=["run_setup"]).run_setup(a.non_interactive, a.yes, a.force),
    }

    handler = commands.get(args.command)
    if handler:
        return handler(args, api)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
