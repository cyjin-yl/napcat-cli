"""Slash command modal — inline napcat CLI terminal."""
from __future__ import annotations

from pathlib import Path

from textual.screen import ModalScreen
from textual.widgets import Input, Label, RichLog, ListView, ListItem
from rich.text import Text


_NAPCAT_SUBCOMMANDS = [
    "api", "send", "recall", "group", "friend", "file",
    "daemon", "events", "alerts", "config", "status",
    "ocr", "translate", "phone",
]

_NAPCAT_GROUP_SUB = ["info", "members", "member", "mute", "unmute", "kick", "admin", "rename", "remark", "announce", "list", "essence", "poke"]
_NAPCAT_FRIEND_SUB = ["list", "info", "remark", "add", "delete"]
_NAPCAT_FILE_SUB = ["upload-group", "upload-private", "list-group", "list-folder", "info", "download"]


class SlashCommandModal(ModalScreen[str | None]):
    """Modal for executing napcat CLI commands with autocomplete."""

    CSS = """
    SlashCommandModal {
        width: 90%;
        height: 60%;
        border: thick $warning;
        background: $surface;
        layout: vertical;
    }
    #slash-title {
        height: 2;
        dock: top;
        background: $warning;
        color: $text;
        text-align: center;
    }
    #slash-input {
        height: 3;
        dock: top;
        border: solid $warning;
    }
    #slash-completion {
        height: auto;
        max-height: 8;
        dock: top;
        background: $surface;
    }
    #slash-output {
        height: 1fr;
        overflow: auto;
        background: $surface;
        color: $text;
        padding: 1;
    }
    """

    def compose(self) -> None:
        yield Label("  / napcat CLI 命令终端", id="slash-title")
        yield Input(placeholder="输入 napcat 命令,如: status / group list", id="slash-input")
        yield ListView(id="slash-completion")
        yield RichLog(id="slash-output")

    def on_mount(self) -> None:
        self.query_one("#slash-input", Input).focus()
        self._completions: list[str] = []
        self._selected_completion = 0

    def on_key(self, event) -> None:
        input_widget = self.query_one("#slash-input", Input)

        if event.key == "tab":
            event.prevent_default()
            self._cycle_completion()
        elif event.key == "enter":
            event.prevent_default()
            cmd = input_widget.value.strip()
            if cmd:
                self._execute_command(cmd)
        elif event.key == "escape":
            self.dismiss(None)
        elif event.key == "up":
            if self._has_completions():
                event.prevent_default()
                self._selected_completion = max(0, self._selected_completion - 1)
                self._highlight_completion()
            else:
                self.query_one("#slash-output", RichLog).scroll_up()
        elif event.key == "down":
            if self._has_completions():
                event.prevent_default()
                self._selected_completion = min(len(self._completions) - 1, self._selected_completion + 1)
                self._highlight_completion()
            else:
                self.query_one("#slash-output", RichLog).scroll_down()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "slash-input":
            cmd = event.input.value.strip()
            if cmd:
                self._execute_command(cmd)

    def on_input_changed(self, event: Input.Changed) -> None:
        if not event.value.strip():
            self._completions = []
            self._refresh_completions()
            return

        parts = event.value.strip().split()
        if len(parts) >= 2:
            sub = parts[1].lower()
            if parts[0] == "group" and sub:
                self._completions = [f"group {c}" for c in _NAPCAT_GROUP_SUB if c.startswith(sub)]
            elif parts[0] == "friend" and sub:
                self._completions = [f"friend {c}" for c in _NAPCAT_FRIEND_SUB if c.startswith(sub)]
            elif parts[0] == "file" and sub:
                self._completions = [f"file {c}" for c in _NAPCAT_FILE_SUB if c.startswith(sub)]
            else:
                self._completions = [s for s in _NAPCAT_SUBCOMMANDS if s.startswith(parts[0])]
        elif len(parts) == 1:
            self._completions = [s for s in _NAPCAT_SUBCOMMANDS if s.startswith(parts[0])]
        else:
            self._completions = _NAPCAT_SUBCOMMANDS[:]

        self._selected_completion = 0
        self._refresh_completions()

    def _has_completions(self) -> bool:
        return bool(self._completions)

    def _cycle_completion(self) -> None:
        if not self._completions:
            return
        self._selected_completion = (self._selected_completion + 1) % len(self._completions)
        input_widget = self.query_one("#slash-input", Input)
        input_widget.value = self._completions[self._selected_completion]
        self._highlight_completion()

    def _refresh_completions(self) -> None:
        listview = self.query_one("#slash-completion", ListView)
        listview.clear()
        if not self._completions:
            return
        self.run_worker(self._fill_completions(listview))

    async def _fill_completions(self, listview: ListView) -> None:
        items = [ListItem(Label(c)) for c in self._completions]
        await listview.extend(items)
        if self._completions:
            self._highlight_completion()

    def _highlight_completion(self) -> None:
        if not self._completions:
            return
        listview = self.query_one("#slash-completion", ListView)
        if listview.index >= 0:
            old = listview.highlighted_child
            if old:
                old.remove_class("-selected")
        listview.index = self._selected_completion
        if listview.index >= 0:
            new = listview.highlighted_child
            if new:
                new.add_class("-selected")

    def _execute_command(self, cmd: str) -> None:
        self.run_worker(self._execute_command_worker(cmd))

    async def _execute_command_worker(self, cmd: str) -> None:
        output = self.query_one("#slash-output", RichLog)
        output.write(f"$ {cmd}\n")

        parts = cmd.split()
        self._completions = []
        self._refresh_completions()

        stdout, stderr, code = await self._app().client.run_napcat_cli(parts)

        if stdout:
            output.write(stdout)
        if stderr:
            output.write(Text.from_markup(f"[red]{stderr}[/]"))
        output.write(Text.from_markup(f"\n[bold]exit: {code}[/]\n"), scroll_end=True)

        self.query_one("#slash-input", Input).value = ""

    def _app(self):
        return self.app
