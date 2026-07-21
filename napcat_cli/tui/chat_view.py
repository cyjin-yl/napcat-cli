"""Chat view screen — detailed conversation with messages."""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Input, Button, RichLog, Label, Select
from textual.widgets import ListItem, ListView
from textual.reactive import reactive
from rich.text import Text
from textual.containers import Horizontal, Container
from textual.binding import Binding
from napcat_cli.lib.message import format_message

if TYPE_CHECKING:
    from .app import NapCatApp

# NapCat CLI command catalog for autocomplete
_NAPCAT_COMMANDS: list[tuple[str, str]] = [
    ("api", "Raw API access"),
    ("send", "Send a message"),
    ("recall", "Recall a message"),
    ("group", "Group management"),
    ("friend", "Friend management"),
    ("file", "File operations"),
    ("daemon", "Manage watch daemon"),
    ("events", "Read events"),
    ("alerts", "Check alerts"),
    ("config", "Manage configuration"),
    ("status", "Check bot login status"),
    ("ocr", "OCR an image"),
    ("translate", "QQ translation"),
    ("like", "Like a message"),
    ("react", "React to a message"),
    ("search", "Search messages"),
    ("batch", "Batch operations"),
]

_NAPCAT_GROUP_SUB: list[str] = [
    "info", "members", "member", "mute", "unmute", "kick",
    "admin", "rename", "remark", "announce", "list", "essence", "poke",
]


class CommandInput(Input):
    """Input widget with inline command autocomplete overlay."""

    CSS = """
    CommandInput {
        width: 1fr;
    }
    CommandInput .-cmd-mode {
        border: solid $warning;
    }
    #cmd-overlay {
        height: auto;
        max-height: 6;
        dock: bottom;
        background: $surface;
        border: solid $warning;
    }
    #cmd-overlay > ListItem {
        height: 1;
        padding: 0 1;
    }
    #cmd-overlay > ListItem:hover,
    #cmd-overlay > ListItem.-selected {
        background: $primary;
        color: $text;
    }
    """

    def __init__(self, *, placeholder: str = "输入消息...", **kwargs) -> None:
        super().__init__(placeholder=placeholder, **kwargs)

    async def _on_key(self, event) -> None:
        # Override Input's private key handler (runs before the public on_key),
        # so special keys can be intercepted ahead of Input's default behaviour.
        if self.value.startswith("/"):
            if event.key == "tab":
                self._tab_next()
                event.stop()
                event.prevent_default()
                return
            if event.key == "up":
                self._tab_prev()
                event.stop()
                event.prevent_default()
                return
            if event.key == "down":
                self._tab_next()
                event.stop()
                event.prevent_default()
                return
            if event.key == "enter":
                self._submit_command()
                event.stop()
                event.prevent_default()
                return
            if event.key == "escape":
                self._clear_overlay()
                event.stop()
                event.prevent_default()
                return
        await super()._on_key(event)

    def _get_completions(self) -> list[str]:
        """Get command completions based on current prefix."""
        prefix = self.value.lstrip("/").strip()
        # Match against top-level commands
        results = [cmd for cmd, _ in _NAPCAT_COMMANDS if cmd.startswith(prefix)]
        # If prefix contains space, try group sub-commands
        if " " in prefix:
            parts = prefix.split(None, 1)
            if parts[0] == "group" and parts[1]:
                results += [f"group {s}" for s in _NAPCAT_GROUP_SUB if s.startswith(parts[1])]
        return results

    def _update_overlay(self) -> None:
        """Show or hide the autocomplete overlay."""
        completions = self._get_completions()
        screen = self.app.screen

        if not completions:
            self._clear_overlay()
            self.remove_class("-cmd-mode")
            return

        self.add_class("-cmd-mode")

        # Check if overlay already exists
        if hasattr(self, "_overlay") and self._overlay:
            # Update existing overlay
            items = [c for c in completions]
            self._overlay.clear()
            self._overlay.update([ListItem(Label(c)) for c in items])
            self._selected_idx = 0
            self._overlay.set_offset(self._selected_idx)
            self._completions = completions
            return

        # Create new overlay
        self._completions = completions
        self._selected_idx = 0
        self._overlay = ListView([ListItem(Label(c)) for c in completions], id="cmd-overlay")
        screen.mount(self._overlay)

    def _clear_overlay(self) -> None:
        """Remove the autocomplete overlay."""
        if hasattr(self, "_overlay") and self._overlay:
            self._overlay.remove()
            self._overlay = None
        self.remove_class("-cmd-mode")

    def _tab_next(self) -> None:
        """Cycle to next completion."""
        if not hasattr(self, "_completions") or not self._completions:
            self._update_overlay()
            return
        self._selected_idx = (self._selected_idx + 1) % len(self._completions)
        self._overlay.set_offset(self._selected_idx)

    def _tab_prev(self) -> None:
        """Cycle to previous completion."""
        if not hasattr(self, "_completions") or not self._completions:
            self._update_overlay()
            return
        self._selected_idx = (self._selected_idx - 1) % len(self._completions)
        self._overlay.set_offset(self._selected_idx)

    def _submit_command(self) -> None:
        """Accept selected completion and submit as command."""
        if hasattr(self, "_completions") and self._completions:
            self.value = "/" + self._completions[self._selected_idx] + " "
            self._clear_overlay()


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
        height: 3;
        border: solid $accent;
        background: $surface;
        padding: 0 1;
    }
    #msg-input {
        height: 1;
        border: solid $accent;
    }
    #send-btn {
        width: 6;
        height: 1;
    }
    """

    BINDINGS = [
        ("escape", "back", "Back"),
        ("up", "scroll_up", "Up"),
        ("down", "scroll_down", "Down"),
        ("pageup", "page_up", "PgUp"),
        ("pagedown", "page_down", "PgDn"),
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
        yield Horizontal(
            CommandInput(placeholder="输入消息 /命令...", id="msg-input"),
            Button("发送", id="send-btn"),
            id="input-bar",
        )

    def on_mount(self) -> None:
        self._load_messages()
        self.query_one("#msg-input", CommandInput).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send-btn":
            self._handle_submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "msg-input":
            self._handle_submit()

    def _handle_submit(self) -> None:
        """Handle input submission — command or message."""
        input_widget = self.query_one("#msg-input", CommandInput)
        text = input_widget.value.strip()
        if not text:
            return
        input_widget.value = ""
        input_widget.remove_class("-cmd-mode")
        if hasattr(input_widget, "_overlay") and input_widget._overlay:
            input_widget._overlay.remove()
            input_widget._overlay = None

        if text.startswith("/"):
            cmd = text[1:].strip()
            self._execute_command(cmd)
        else:
            self._send_message(text)

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
            from napcat_cli.lib.config import get_config
            cfg = get_config()
            self_id = str(cfg.self_id or "")
        except Exception:
            self_id = ""

        sorted_msgs = sorted(msgs, key=lambda m: m.get("time", 0))

        for m in sorted_msgs:
            mid = self._get_msg_id(m)
            if mid:
                self._loaded_message_ids.add(mid)

        combined = Text()
        for i, m in enumerate(sorted_msgs):
            msg_text = self._format_message_line(m, self_id)
            combined.append_text(msg_text)
            if i < len(sorted_msgs) - 1:
                combined.append("\n", style="dim")
        if sorted_msgs:
            rich_log.write(combined, scroll_end=True)

        if sorted_msgs:
            self._last_message_time = max(m.get("time", 0) for m in sorted_msgs)

    def _send_message(self, text: str) -> None:
        self.run_worker(self._send_async(text))

    async def _send_async(self, text: str) -> None:
        client = self._app().client
        result = await client.send_message(self.chat_type, self.chat_id, text)

        if isinstance(result, dict):
            server_id = str(result.get("message_id", "")) or str(result.get("message_seq", ""))
            if server_id:
                self._loaded_message_ids.add(server_id)

        ts = datetime.now().timestamp()
        now_str = datetime.fromtimestamp(ts).strftime("%H:%M")

        rich_log = self.query_one("#messages", RichLog)
        rich_log.write(Text.assemble(("[我] ", "green"), (f"{now_str}\n", ""), (text, "")), scroll_end=True)

        self._last_message_time = ts

        chat = self._app().chats.get(self.chat_id)
        if chat:
            chat.last_message = text
            chat.last_time = int(ts)

    def _refresh_messages(self) -> None:
        self.run_worker(self._append_new_messages())

    async def _append_new_messages(self) -> None:
        client = self._app().client
        msgs = await client.get_message_history(self.chat_type, self.chat_id, count=100)

        rich_log = self.query_one("#messages", RichLog)
        at_end = rich_log.scroll_y >= rich_log.max_scroll_y

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
            from napcat_cli.lib.config import get_config
            cfg = get_config()
            self_id = str(cfg.self_id or "")
        except Exception:
            self_id = ""

        combined = Text()
        for i, m in enumerate(new_msgs):
            msg_text = self._format_message_line(m, self_id)
            combined.append_text(msg_text)
            if i < len(new_msgs) - 1:
                combined.append("\n", style="dim")
        if new_msgs:
            rich_log.write(combined)
            if at_end:
                rich_log.scroll_end()

    def _get_msg_id(self, m: dict) -> str:
        return str(m.get("message_id", "")) or str(m.get("message_seq", ""))

    def _format_message_line(self, m: dict, self_id: str) -> Text:
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
        msg = m.get("message", [])
        if isinstance(msg, list):
            content = format_message(msg)
        elif isinstance(msg, str):
            content = msg
        else:
            content = ""
        if not content:
            content = "[media]"

        is_self = sender_uid == self_id
        prefix_name = "我" if is_self else sender_name
        prefix_style = "green" if is_self else "blue"

        return Text.assemble(
            (f"[{prefix_name}] ", prefix_style),
            (time_str, ""),
            ("\n", ""),
            (content, ""),
        )

    def _execute_command(self, cmd: str) -> None:
        self.run_worker(self._execute_command_worker(cmd))

    async def _execute_command_worker(self, cmd: str) -> None:
        stdout, stderr, _ = await self._app().client.run_napcat_cli(cmd.split())
        output = stdout or stderr or "(no output)"
        rich_log = self.query_one("#messages", RichLog)
        rich_log.write(Text.assemble((f"⚡ {cmd}\n", "bold cyan"), (output, "")), scroll_end=True)

    def action_scroll_down(self) -> None:
        self.query_one("#messages", RichLog).scroll_down()

    def action_scroll_up(self) -> None:
        self.query_one("#messages", RichLog).scroll_up()

    def action_page_up(self) -> None:
        self.query_one("#messages", RichLog).scroll_page_up()

    def action_page_down(self) -> None:
        self.query_one("#messages", RichLog).scroll_page_down()

    def _app(self) -> "NapCatApp":
        return self.app  # type: ignore[return-value]
