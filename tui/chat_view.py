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
    ]

    def __init__(
        self, *, chat_id: str, chat_name: str, chat_type: str,
    ) -> None:
        super().__init__()
        self.chat_id = chat_id
        self.chat_name = chat_name
        self.chat_type = chat_type

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
            sender = m.get("sender", {})
            sender_name = sender.get("nickname") or sender.get("card") or str(sender.get("user_id", "?"))
            sender_uid = str(sender.get("user_id", ""))
            is_self = sender_uid == self_id

            time_str = ""
            t = m.get("time", 0)
            if t:
                dt = datetime.fromtimestamp(t)
                time_str = dt.strftime("%H:%M")

            content = ""
            raw = m.get("message", [])
            if isinstance(raw, list):
                content = " ".join(seg.get("text", "") for seg in raw if seg.get("type") == "text")
            elif isinstance(raw, str):
                content = raw

            prefix = "[green]我[/green]" if is_self else f"[blue]{sender_name}[/blue]"
            rich_log.write(Text.from_markup(line))

        rich_log.scroll_to(y=1.0)

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

        rich_log = self.query_one("#messages", RichLog)
        now = datetime.now().strftime("%H:%M")
        rich_log.write(Text.from_markup(f"[green]我[/green] {now}\n{text}"), scroll_end=True)

        chat = self._app().chats.get(self.chat_id)
        if chat:
            chat.last_message = text
            chat.last_time = int(datetime.now().timestamp())

    def _app(self) -> "NapCatApp":
        return self.app  # type: ignore[return-value]
