"""Chat list screen — main conversation list."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from textual.screen import Screen
from textual.widgets import ListView, ListItem, Label

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
        height: 3;
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
        ("enter", "select", "Open"),
        ("/", "slash", "Command"),
    ]

    def compose(self) -> None:
        yield Label("  QQ 消息", id="header-label")
        yield ListView(id="chat-listview")

    def on_mount(self) -> None:
        self._refresh_list()

    def action_back(self) -> None:
        self._app().exit()

    def action_select(self) -> None:
        listview = self.query_one("#chat-listview", ListView)
        idx = listview.index
        if idx >= 0:
            children = list(listview.children)
            if idx < len(children):
                item = children[idx]
                chat_id = getattr(item, "chat_id", None)
                chat_name = getattr(item, "chat_name", None)
                chat_type = getattr(item, "chat_type", None)
                if chat_id:
                    from .chat_view import ChatViewScreen
                    self._app().push_screen(
                        ChatViewScreen(
                            chat_id=chat_id,
                            chat_name=chat_name or "",
                            chat_type=chat_type or "private",
                        )
                    )

    async def action_slash(self) -> None:
        self._app().push_screen("slash")

    def _refresh_list(self) -> None:
        """Refresh the chat list from app state."""
        listview = self.query_one("#chat-listview", ListView)
        app = self._app()

        sorted_items = sorted(app.chats.values(), key=lambda c: c.last_time, reverse=True)

        items: list[ListItem] = []
        for chat in sorted_items:
            label = self._format_label(chat)
            li = ListItem(Label(label))
            li.chat_id = chat.id  # type: ignore[attr-defined]
            li.chat_name = chat.name  # type: ignore[attr-defined]
            li.chat_type = chat.kind  # type: ignore[attr-defined]
            if chat.unread > 0:
                li.add_class("-unread")
            items.append(li)

        listview.clear()
        # ListView.extend is async; use run_worker
        self.run_worker(self._extend_listview(listview, items))

    async def _extend_listview(self, listview: ListView, items: list[ListItem]) -> None:
        await listview.extend(items)

    def _format_label(self, chat) -> str:
        prefix = "群" if chat.kind == "group" else ""
        name = f"{prefix}{chat.name}"
        time_str = ""
        if chat.last_time:
            dt = datetime.fromtimestamp(chat.last_time)
            time_str = dt.strftime("%H:%M")
        msg = chat.last_message[:30] if chat.last_message else ""
        badge = f" [{chat.unread}]" if chat.unread > 0 else ""
        return f"{name:<20} {time_str}  {msg}{badge}"

    def _app(self) -> "NapCatApp":
        return self.app  # type: ignore[return-value]
