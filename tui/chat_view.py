"""Chat view screen — detailed conversation with messages."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Input, Button, RichLog, Label
from rich.text import Text
from textual.containers import Horizontal, Container
from textual.binding import Binding

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
        color: $text;
        text-align: center;
    }
    #messages {
        height: 1fr;
        overflow: scroll;
        padding: 1;
    }
    #input-bar {
        dock: bottom;
        height: auto;
        border: solid $accent;
        background: $surface;
    }
    #cmd-row, #msg-row {
        height: 1;
        width: 1fr;
    }
    #send-btn {
        width: 4;
    }
    #msg-input {
        width: 1fr;
        margin: 0 1;
    }
    #cmd-input {
        width: 1fr;
        margin: 0 1;
    }
    #cmd-btn {
        width: 3;
    }
    """

    BINDINGS = [
        ("escape", "back", "Back"),
        ("up", "scroll_up", "Up"),
        ("down", "scroll_down", "Down"),
        ("pageup", "page_up", "PgUp"),
        ("pagedown", "page_down", "PgDn"),
        Binding("/", "slash", "Cmd", priority=True),
    ]

    def __init__(
        self, *, chat_id: str, chat_name: str, chat_type: str,
    ) -> None:
        super().__init__()
        self.chat_id = chat_id
        self.chat_name = chat_name
        self.chat_type = chat_type
        self._last_message_time: float = 0
        self._loaded_message_ids: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Label(f"  {self.chat_name}", id="view-header")
        yield RichLog(id="messages")
        yield Container(
            Horizontal(
                Input(placeholder="napcat 命令...", id="cmd-input"),
                Button("执行", id="cmd-btn"),
                id="cmd-row",
            ),
            Horizontal(
                Input(placeholder="输入消息...", id="msg-input"),
                Button("发送", id="send-btn"),
                id="msg-row",
            ),
            id="input-bar",
        )

    def on_mount(self) -> None:
        self._load_messages()
        self.query_one("#msg-input", Input).focus()

    def action_slash(self) -> None:
        """Focus the command input bar."""
        self.query_one("#cmd-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-btn":
            self._send_message()
        elif event.button.id == "cmd-btn":
            self._execute_command()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "msg-input":
            self._send_message()
        elif event.input.id == "cmd-input":
            self._execute_command()

    def action_back(self) -> None:
        self._app().pop_screen()

    def _load_messages(self) -> None:
        self.run_worker(self._load_messages_worker())

    async def _load_messages_worker(self) -> None:
        client = self._app().client
        msgs = await client.get_message_history(self.chat_type, self.chat_id, count=100)

        rich_log = self.query_one("#messages", RichLog)
        rich_log.clear()
        self._loaded_message_ids.clear()

        try:
            from ..lib.config import get_config
            cfg = get_config()
            self_id = str(cfg.self_id or "")
        except Exception:
            self_id = ""

        sorted_msgs = sorted(msgs, key=lambda m: m.get("time", 0))

        # Track loaded message IDs for dedup
        for m in sorted_msgs:
            mid = self._get_msg_id(m)
            if mid:
                self._loaded_message_ids.add(mid)

        # Batch write all messages at once for performance
        parts = []
        for m in sorted_msgs:
            line = self._format_message_line(m, self_id)
            parts.append(line)

        if parts:
            rich_log.write(Text.from_markup("\n".join(parts)), scroll_end=True)

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

        # Track server-assigned message ID if available
        if isinstance(result, dict):
            server_id = str(result.get("message_id", "")) or str(result.get("message_seq", ""))
            if server_id:
                self._loaded_message_ids.add(server_id)

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
        """Fetch fresh messages and append only those newer than last load, deduped by ID."""
        client = self._app().client
        msgs = await client.get_message_history(self.chat_type, self.chat_id, count=100)

        rich_log = self.query_one("#messages", RichLog)
        at_end = rich_log.at_bottom

        # Filter: newer than last load AND not already loaded by ID
        new_msgs = [
            m for m in msgs
            if m.get("time", 0) > self._last_message_time
            and self._get_msg_id(m) not in self._loaded_message_ids
        ]

        if not new_msgs:
            return

        new_msgs = sorted(new_msgs, key=lambda m: m.get("time", 0))

        for m in new_msgs:
            mid = self._get_msg_id(m)
            if mid:
                self._loaded_message_ids.add(mid)
            self._last_message_time = max(self._last_message_time, m.get("time", 0))

        try:
            from ..lib.config import get_config
            cfg = get_config()
            self_id = str(cfg.self_id or "")
        except Exception:
            self_id = ""

        # Batch write all new messages at once
        parts = []
        for m in new_msgs:
            line = self._format_message_line(m, self_id)
            parts.append(line)

        if parts:
            rich_log.write(Text.from_markup("\n".join(parts)))
            if at_end:
                rich_log.scroll_end()

    def _get_msg_id(self, m: dict) -> str:
        """Extract a stable message ID for deduplication."""
        return str(m.get("message_id", "")) or str(m.get("message_seq", ""))

    def _format_message_line(self, m: dict, self_id: str) -> str:
        """Format a single message into a Rich markup line."""
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
        return f"{prefix} {time_str}\n{content}"

    def _execute_command(self) -> None:
        input_widget = self.query_one("#cmd-input", Input)
        cmd = input_widget.value.strip()
        if not cmd:
            return
        input_widget.value = ""
        self.run_worker(self._execute_command_worker(cmd))

    async def _execute_command_worker(self, cmd: str) -> None:
        """Execute a napcat CLI command via API client and show output."""
        stdout, stderr, _ = await self._app().client.run_napcat_cli(cmd.split())
        output = stdout or stderr or "(no output)"
        rich_log = self.query_one("#messages", RichLog)
        rich_log.write(Text.from_markup(f"[bold cyan]⚡ {cmd}[/bold cyan]\n{output}"), scroll_end=True)

    def action_scroll_down(self) -> None:
        self.query_one("#messages", RichLog).scroll_down()

    def action_scroll_up(self) -> None:
        self.query_one("#messages", RichLog).scroll_up()

    def action_page_up(self) -> None:
        self.query_one("#messages", RichLog).page_up()

    def action_page_down(self) -> None:
        self.query_one("#messages", RichLog).page_down()

    def _app(self) -> "NapCatApp":
        return self.app  # type: ignore[return-value]
