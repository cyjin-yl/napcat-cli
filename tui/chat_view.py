"""Chat view screen — detailed conversation with messages."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from textual.screen import Screen
from textual.widgets import Input, Button, RichLog, Label
from rich.text import Text
from textual.containers import Horizontal

if TYPE_CHECKING:
    from .app import NapCatApp


class ChatViewScreen(Screen):
    """Detailed chat view for a single conversation."""

    CSS = """
    ChatViewScreen {
        layout: vertical;
    }
    #view-header {
        height: 2;
        dock: top;
        background: $primary;
        color: $primary-foreground;
        text-align: center;
    }
    #messages {
        height: 1fr;
        overflow-y: scroll;
        padding: 1;
    }
    #input-bar {
        height: 3;
        dock: bottom;
        border: solid $accent;
        background: $surface;
    }
    #input-bar Input {
        width: 80%;
    }
    #input-bar Button {
        width: 15%;
    }
    """

    BINDINGS = [
        ("escape", "back", "Back"),
        ("up", "scroll_up", "Up"),
        ("down", "scroll_down", "Down"),
        ("pageup", "page_up", "PgUp"),
        ("pagedown", "page_down", "PgDn"),
        ("/", "slash", "Command"),
    ]

    def __init__(
        self, *, chat_id: str, chat_name: str, chat_type: str,
    ) -> None:
        super().__init__()
        self.chat_id = chat_id
        self.chat_name = chat_name
        self.chat_type = chat_type
        self._last_message_time: float = 0

    def compose(self) -> None:
        yield Label(f"  {self.chat_name}", id="view-header")
        yield RichLog(id="messages")
        yield Horizontal(
            Input(placeholder="输入消息...", id="msg-input"),
            Button("发送", id="send-btn"),
            id="input-bar",
        )

    def on_mount(self) -> None:
        self._load_messages()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-btn":
            self._send_message()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "msg-input":
            self._send_message()

    def action_back(self) -> None:
        self._app().pop_screen()

    def _load_messages(self) -> None:
        self.run_worker(self._load_messages_worker())

    async def _load_messages_worker(self) -> None:
        client = self._app().client
        msgs = await client.get_message_history(self.chat_type, self.chat_id, count=100)

        rich_log = self.query_one("#messages", RichLog)
        rich_log.clear()

        try:
            from ..lib.config import get_config
            cfg = get_config()
            self_id = str(cfg.self_id or "")
        except Exception:
            self_id = ""

        sorted_msgs = sorted(msgs, key=lambda m: m.get("time", 0))

        for m in sorted_msgs:
            self._write_message_line(rich_log, m, self_id)

        rich_log.scroll_end()
        if sorted_msgs:
            self._last_message_time = max(m.get("time", 0) for m in sorted_msgs)

    def _send_message(self) -> None:
        input_widget = self.query_one("#msg-input", Input)
        text = input_widget.value.strip()
        if not text:
            return
        input_widget.value = ""
        self.run_worker(self._send_async(text))

    async def _send_async(self, text: str) -> None:
        client = self._app().client
        result = await client.send_message(self.chat_type, self.chat_id, text)

        # Single timestamp for display, dedup cutoff, and chat metadata.
        ts = datetime.now().timestamp()
        now_str = datetime.fromtimestamp(ts).strftime("%H:%M")

        rich_log = self.query_one("#messages", RichLog)
        rich_log.write(Text.from_markup(f"[green]我[/green] {now_str}\n{text}"), scroll_end=True)

        self._last_message_time = ts

        chat = self._app().chats.get(self.chat_id)
        if chat:
            chat.last_message = text
            chat.last_time = int(ts)
    def _refresh_messages(self) -> None:
        """Append new messages from daemon when polling detects changes."""
        self.run_worker(self._append_new_messages())

    async def _append_new_messages(self) -> None:
        """Fetch fresh messages and append only those newer than last load."""
        client = self._app().client
        msgs = await client.get_message_history(self.chat_type, self.chat_id, count=100)

        rich_log = self.query_one("#messages", RichLog)
        at_end = rich_log.at_bottom

        # Filter to only messages newer than last load
        new_msgs = [m for m in msgs if m.get("time", 0) > self._last_message_time]

        if not new_msgs:
            return

        # Sort by time so ordering is preserved
        new_msgs = sorted(new_msgs, key=lambda m: m.get("time", 0))

        try:
            from ..lib.config import get_config
            cfg = get_config()
            self_id = str(cfg.self_id or "")
        except Exception:
            self_id = ""

        for m in new_msgs:
            self._write_message_line(rich_log, m, self_id)
            self._last_message_time = max(self._last_message_time, m.get("time", 0))

        if at_end:
            rich_log.scroll_end()

    def _write_message_line(self, rich_log: RichLog, m: dict, self_id: str, *, scroll_end: bool = False) -> None:
        """Render a single message line into the RichLog."""
        sender = m.get("sender", {})
        sender_name = ""
        sender_uid = ""
        if isinstance(sender, dict):
            sender_name = sender.get("nickname") or sender.get("card") or ""
            sender_uid = str(sender.get("user_id", ""))

        t = m.get("time", 0)
        time_str = ""
        if t:
            time_str = datetime.fromtimestamp(t).strftime("%H:%M")

        raw = m.get("message", [])
        if isinstance(raw, list):
            content = " ".join(seg.get("text", "") for seg in raw if seg.get("type") == "text")
        elif isinstance(raw, str):
            content = raw
        if not content:
            content = "[media]"

        is_self = sender_uid == self_id
        prefix = "[green]我[/green]" if is_self else f"[blue]{sender_name}[/blue]"
        line = f"{prefix} {time_str}\n{content}"
        rich_log.write(Text.from_markup(line), scroll_end=scroll_end)

    def action_scroll_down(self) -> None:
        self.query_one("#messages", RichLog).scroll_down()

    def action_scroll_up(self) -> None:
        self.query_one("#messages", RichLog).scroll_up()

    def action_page_up(self) -> None:
        self.query_one("#messages", RichLog).page_up()

    def action_page_down(self) -> None:
        self.query_one("#messages", RichLog).page_down()

    async def action_slash(self) -> None:
        self._app().push_screen("slash")

    def _app(self) -> "NapCatApp":
        return self.app  # type: ignore[return-value]
