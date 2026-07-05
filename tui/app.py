"""napcat TUI — root application."""
from __future__ import annotations

from typing import Any

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header

from .api import ChatItem, DaemonClient, get_client
from .slash_command import SlashCommandModal


class NapCatApp(App):
    """Root application for the napcat TUI."""
    SCREENS: dict[str, type] = {"slash": SlashCommandModal}

    CSS_PATH = "styles.tcss"

    BINDINGS = [
        ("escape", "quit", "Quit"),
        ("f5", "refresh", "Refresh"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.client: DaemonClient = get_client()
        self.chats: dict[str, ChatItem] = {}
        self.alerts: list[dict[str, Any]] = []
        self._seen_alerts: set[str] = set()
        self._seen_message_ids: set[str] = set()

    @staticmethod
    def _alert_signature(alert: dict[str, Any]) -> str:
        import json
        return json.dumps(alert, sort_keys=True)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()

    def on_mount(self) -> None:
        from .chat_list import ChatListScreen
        self.set_interval(2, self._poll_loop)
        self.push_screen(ChatListScreen())

    async def _poll_loop(self) -> None:
        await self.refresh_data()
        screen = self.screen
        if hasattr(screen, "_refresh_list"):
            screen._refresh_list()
        if hasattr(screen, "_refresh_messages"):
            screen._refresh_messages()

    async def refresh_data(self) -> None:
        """Fetch fresh data from the daemon and merge into app state."""
        try:
            events = await self.client.get_events(limit=200)
            friends = await self.client.get_friends()
            groups = await self.client.get_groups()
            self.alerts = await self.client.get_alerts()

            # Build/update chat items from friends and groups
            for f in friends:
                fid = str(f.get("user_id", ""))
                self.chats.setdefault(fid, ChatItem(
                    id=fid,
                    name=f.get("nickname", fid),
                    kind="private",
                    remark=f.get("remark", ""),
                ))
                self.chats[fid].name = f.get("nickname", fid)
                self.chats[fid].remark = f.get("remark", "")

            for g in groups:
                gid = str(g.get("group_id", ""))
                self.chats.setdefault(gid, ChatItem(
                    id=gid,
                    name=g.get("group_name", gid),
                    kind="group",
                ))
                self.chats[gid].name = g.get("group_name", gid)

            # Update last message / unread from events
            for ev in events:
                post_type = ev.get("post_type", "")
                if post_type == "message":
                    target_id = str(ev.get("group_id") or ev.get("user_id", ""))
                    sender_name = ""
                    sender_info = ev.get("sender", {})
                    if sender_info:
                        sender_name = sender_info.get("nickname") or sender_info.get("card") or ""
                    content = ""
                    raw = ev.get("raw_message", "") or ev.get("message", [])
                    if isinstance(raw, str):
                        content = raw
                    elif isinstance(raw, list):
                        content = " ".join(m.get("data", {}).get("text", "") for m in raw if m.get("type") == "text")
                    if not target_id:
                        continue
                    if target_id not in self.chats:
                        is_group = bool(ev.get("group_id"))
                        self.chats.setdefault(target_id, ChatItem(
                            id=target_id,
                            name=(sender_name or target_id) if not is_group else target_id,
                            kind="group" if is_group else "private",
                        ))
                    item = self.chats[target_id]
                    item.last_message = content
                    item.last_sender = sender_name
                    item.last_time = ev.get("time", 0)
                    mid = str(ev.get("message_id", "")) or str(ev.get("message_seq", ""))
                    if mid and mid not in self._seen_message_ids:
                        self._seen_message_ids.add(mid)
                        item.unread += 1
                elif post_type == "notice":
                    notice_type = ev.get("notice_type", "")
                    if notice_type == "friend_increase":
                        fid = str(ev.get("user_id", ""))
                        self.chats.setdefault(fid, ChatItem(
                            id=fid, name=fid, kind="private",
                        ))

        except Exception:
            pass

    async def action_refresh(self) -> None:
        await self.refresh_data()
