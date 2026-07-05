"""Chat list screen - main conversation list."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from textual.screen import Screen
from textual.widgets import ListView, ListItem, Label
from textual.containers import Vertical

if TYPE_CHECKING:
    from .app import NapCatApp



class ChatListScreen(Screen):
    """Main screen showing sorted list of conversations."""

    CSS = """
    ChatListScreen {
        layout: vertical;
    }
    #header-label {
        height: 2;
        dock: top;
        background: $primary;
        color: $text;
        text-align: center;
    }
    #chat-listview {
        height: 1fr;
    }
    ListItem {
        height: 4;
        padding: 0 1;
    }
    ListItem:hover {
        background: $accent;
    }
    ListItem.-unread {
        background: $accent-lighten-2;
    }
    """

    BINDINGS = [
        ("escape", "back", "Back"),
        ("/", "slash", "Command"),
        ("ctrl+q", "quit", "Quit"),
    ]

    def compose(self) -> None:
        yield Label("  QQ 消息", id="header-label")
        yield ListView(id="chat-listview")

    def on_mount(self) -> None:
        self._refresh_list()

    def action_back(self) -> None:
        self._app().exit()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Open chat when a list item is selected (Enter key via ListView)."""
        item = event.item
        chat_id = getattr(item, "chat_id", None)
        chat_name = getattr(item, "chat_name", None)
        chat_type = getattr(item, "chat_type", None)
        if chat_id:
            self._open_chat(chat_id, chat_name or "", chat_type or "private")

    def _open_chat(self, chat_id: str, chat_name: str, chat_type: str) -> None:
        """Decrement unread count and push the chat view screen."""
        app = self._app()
        chat_item = app.chats.get(chat_id)
        if chat_item:
            chat_item.unread = 0
        from .chat_view import ChatViewScreen
        app.push_screen(
            ChatViewScreen(
                chat_id=chat_id,
                chat_name=chat_name,
                chat_type=chat_type,
            )
        )

    async def action_slash(self) -> None:
        self._app().push_screen("slash")

    def _refresh_list(self) -> None:
        """Refresh the chat list from app state."""
        app = self._app()

        # Show only new alerts — deduplicate by stable signature
        for alert in app.alerts:
            sig = app._alert_signature(alert)
            if sig not in app._seen_alerts:
                app._seen_alerts.add(sig)
                summary = alert.get("summary", alert.get("message", "Unknown"))
                self.app.notify(f"\u26a0 {summary}", severity="warning")

        listview = self.query_one("#chat-listview", ListView)

        # Save current selection index before clearing
        old_index = listview.index if listview.index is not None else 0

        sorted_items = sorted(app.chats.values(), key=lambda c: c.last_time, reverse=True)

        items: list[ListItem] = []
        for chat in sorted_items:
            name_label, msg_label = self._format_item(chat)
            li = ListItem(Vertical(name_label, msg_label, classes="item-vbox"))
            li.chat_id = chat.id  # type: ignore[attr-defined]
            li.chat_name = chat.name  # type: ignore[attr-defined]
            li.chat_type = chat.kind  # type: ignore[attr-defined]
            if chat.unread > 0:
                li.add_class("-unread")
            items.append(li)

        listview.clear()
        listview.extend(items)
        listview.index = min(old_index, max(0, len(items) - 1))

    def _format_item(self, chat) -> tuple[Label, Label]:
        """Return (name_label, msg_label) for a chat item."""
        if chat.kind == "group":
            name = f"群[{chat.name}]"
        else:
            remark = chat.remark
            qq = chat.id
            name = f"{qq} [{remark}]" if remark else f"{qq}"
        badge = f" [{chat.unread}]" if chat.unread > 0 else ""
        name_label = Label(f"{name}{badge}")
        # Second line: time + sender + last message
        parts: list[str] = []
        if chat.last_time:
            dt = datetime.fromtimestamp(chat.last_time)
            parts.append(dt.strftime("%H:%M"))
        sender = chat.last_sender
        if sender:
            parts.append(f"{sender}:")
        msg = (chat.last_message or "")[:40]
        if msg:
            parts.append(msg)
        msg_label = Label(" ".join(parts))
        return name_label, msg_label

    def _app(self) -> "NapCatApp":
        return self.app  # type: ignore[return-value]
