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
_NAPCAT_FRIEND_SUB: list[str] = ["list", "info", "remark", "add", "delete"]
_NAPCAT_FILE_SUB: list[str] = [
    "upload-group", "upload-private", "list-group", "list-folder", "info", "download",
]


class CommandInput(Input):
    """Message input with inline ``/command`` autocomplete.

    Completions render in the screen's static ``#cmd-overlay`` ListView (part of
    the layout, directly above this input) instead of a dynamically-mounted
    floating widget — robust under Textual 8.x. ``/cmd`` + Enter runs a napcat
    CLI command; plain text + Enter sends a message (both are routed through
    ``ChatViewScreen._handle_submit`` via ``Input.Submitted``).
    """

    def __init__(self, *, placeholder: str = "输入消息...", **kwargs) -> None:
        super().__init__(placeholder=placeholder, **kwargs)
        self._completions: list[str] = []

    # --- overlay (the static #cmd-overlay ListView in the screen) --------
    def _overlay(self) -> ListView:
        return self.screen.query_one("#cmd-overlay", ListView)

    def _overlay_visible(self) -> bool:
        return self._overlay().has_class("-visible")

    def _hide_overlay(self) -> None:
        self._completions = []
        overlay = self._overlay()
        overlay.remove_class("-visible")
        overlay.clear()
        self.remove_class("-cmd-mode")

    # --- completions -----------------------------------------------------
    def _get_completions(self) -> list[str]:
        # Preserve trailing whitespace: "/group " must still read as "command +
        # space" so the subcommand list is offered (stripping it would collapse
        # "/group " back to the top-level "group" match).
        raw = self.value.lstrip("/")
        if " " in raw:
            cmd, _, rest = raw.partition(" ")
            cmd = cmd.strip()
            rest = rest.strip()
            if cmd == "group":
                return [f"group {s}" for s in _NAPCAT_GROUP_SUB if s.startswith(rest)]
            if cmd == "friend":
                return [f"friend {s}" for s in _NAPCAT_FRIEND_SUB if s.startswith(rest)]
            if cmd == "file":
                return [f"file {s}" for s in _NAPCAT_FILE_SUB if s.startswith(rest)]
            return []
        return [c for c, _ in _NAPCAT_COMMANDS if c.startswith(raw.strip())]

    async def _refresh_overlay(self) -> None:
        overlay = self._overlay()
        completions = self._get_completions()
        if not self.value.startswith("/") or not completions:
            self._hide_overlay()
            return
        self._completions = completions
        overlay.clear()
        await overlay.extend(ListItem(Label(c)) for c in completions)
        overlay.index = 0
        overlay.add_class("-visible")
        self.add_class("-cmd-mode")

    def _cycle(self, direction: int) -> None:
        if not self._completions:
            return
        overlay = self._overlay()
        idx = overlay.index if overlay.index is not None else 0
        overlay.index = (idx + direction) % len(self._completions)

    def _apply_highlighted(self) -> None:
        """Insert the highlighted suggestion into the value (menu-complete)."""
        if not self._completions:
            return
        overlay = self._overlay()
        idx = overlay.index if overlay.index is not None else 0
        if 0 <= idx < len(self._completions):
            self.value = "/" + self._completions[idx] + " "

    # --- events ----------------------------------------------------------
    async def _on_key(self, event) -> None:
        # Override Input's private key handler (runs before its default action)
        # so command-mode keys are intercepted first. Shell-like semantics:
        #   Tab        menu-complete the highlighted suggestion
        #   Up/Down    move the highlight through suggestions
        #   Enter      submit the input as typed (run /command or send message)
        #   Escape     dismiss the suggestion list
        if self.value.startswith("/"):
            if event.key == "tab":
                if not self._completions:
                    await self._refresh_overlay()
                self._apply_highlighted()
                await self._refresh_overlay()  # show subcommands for the completion
                event.stop()
                event.prevent_default()
                return
            if event.key in ("down", "up"):
                if not self._overlay_visible():
                    await self._refresh_overlay()
                self._cycle(1 if event.key == "down" else -1)
                event.stop()
                event.prevent_default()
                return
            if event.key == "escape":
                self._hide_overlay()
                event.stop()
                event.prevent_default()
                return
            # Enter and ordinary characters fall through to Input's default:
            # Enter fires Input.Submitted -> ChatViewScreen._handle_submit, which
            # runs the /command or sends the message exactly as typed.
        # Let Input process the key (insert/delete/cursor) ...
        await super()._on_key(event)
        # ... then refresh the overlay against the now-current value. Doing it
        # here (synchronously in the key handler, not via a background worker)
        # is deterministic: the list updates as part of handling the keypress,
        # so it can never lag behind or be dropped while the user is typing.
        if self.value.startswith("/"):
            await self._refresh_overlay()
        elif self._overlay_visible():
            self._hide_overlay()


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
        overflow-y: scroll;
        padding: 1 2;
        border: solid $accent;
    }
    /* /command autocomplete dropdown — in-layout, directly above the input
       bar; hidden unless the input is in command mode. */
    #cmd-overlay {
        height: auto;
        max-height: 6;
        display: none;
        background: $surface-lighten-2;
        border: solid $warning;
    }
    #cmd-overlay.-visible {
        display: block;
    }
    #cmd-overlay > ListItem {
        height: 1;
        padding: 0 1;
    }
    #cmd-overlay > ListItem.-highlight,
    #cmd-overlay > ListItem:hover {
        background: $warning;
        color: $text;
    }
    /* Bottom input bar: children keep their natural height (3, bordered) so
       the typed text and the button label are actually visible. */
    #input-bar {
        dock: bottom;
        height: auto;
        padding: 0 1;
        background: $surface;
    }
    #msg-input {
        width: 1fr;
    }
    #send-btn {
        width: auto;
        min-width: 8;
    }
    CommandInput.-cmd-mode {
        border: solid $warning;
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
        yield ListView(id="cmd-overlay")
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
        input_widget._hide_overlay()

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

        # Append image paths when config enabled
        show_images = True
        try:
            from napcat_cli.lib.config import get_config
            cfg = get_config()
            show_images = bool(getattr(cfg, 'tui_show_images', True))
        except Exception:
            pass

        if show_images and isinstance(msg, list):
            for seg in msg:
                if isinstance(seg, dict) and seg.get("type") == "image":
                    data = seg.get("data", {}) or {}
                    url = data.get("url", "")
                    file_id = data.get("file_id", "")
                    sub_type = data.get("sub_type", "")
                    file_size = data.get("file_size", "")
                    summary = data.get("summary", "")
                    extras = [s for s in (url, file_id) if s]
                    if file_size:
                        try:
                            extras.append(f"{int(file_size) // 1024}KB")
                        except (ValueError, TypeError):
                            pass
                    if summary:
                        extras.append(summary)
                    if extras:
                        content += f"\n [图片: {' | '.join(extras)}]"

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
