# napcat-cli — Agent Guide

## Critical Lessons (DO NOT REPEAT)

These three bugs each caused a **silent total failure of the wake system** —
the bot stopped responding to all messages. They were introduced by edits that
looked correct but had subtle defects. The prevention measures below are now
enforced by `tests/test_lint.py`.

### 1. UnboundLocalError in event handler kills all events

**Bug**: An edit to the reply-detection code referenced a variable `data` that
was never assigned in that scope. When a message with a `reply` segment arrived,
Python raised `UnboundLocalError`. Because `process()` had no exception
handling, the error propagated up and **killed the WebSocket event loop** —
every subsequent event was silently dropped. The bot appeared dead until
manually restarted.

**Prevention**:
- `process()` now wraps all event handling in `try/except` with full traceback
  logging. A handler crash logs the error but does NOT kill the event loop.
- `tests/test_lint.py::test_no_undefined_names` runs pyflakes on every file —
  undefined names are caught at test time, not runtime.
- **Rule**: When editing a code block that uses variables, always trace every
  variable back to its assignment. If you copy/paste/restructure, re-verify all
  referenced names are defined in scope.

### 2. `hermes -z -` does NOT read from stdin

**Bug**: The wake command template used `-z -` thinking `-` meant "read prompt
from stdin" (like many Unix tools). Hermes's `-z PROMPT` flag takes a **literal
string** — `-` was sent as the actual prompt text. Hermes received literally
"-" instead of the contextual wake prompt, so it had no idea what to do and
replied with generic acknowledgments.

**Prevention**:
- The template now uses `-z "$(cat {prompt_file})"` which reads the temp file
  into a shell variable and passes it as a literal argument.
- `CliWakeBackend._render_file()` writes the prompt to a temp file and
  substitutes `{prompt_file}` — never pipes via stdin.
- **Rule**: Always verify external CLI flag semantics before assuming stdin
  support. Test the actual command manually before committing the template.

### 3. Undefined `file_count` in list_message_content

**Bug**: `file_count` was used in `+= 1` but never initialized (only
`image_count` was initialized to 0). Any message containing a `file` segment
would crash with `NameError` when listing message content via skills-fs.

**Prevention**:
- `file_count = 0` added alongside `image_count = 0`.
- `tests/test_lint.py::test_no_undefined_names` catches undefined names.
- **Rule**: When adding a new counter or accumulator, always initialize it to
  0/empty **in the same edit** that introduces the usage.

## Static Analysis Guard

`tests/test_lint.py` enforces two checks as part of the test suite:

1. **`test_no_undefined_names`** — Runs `pyflakes` on all `.py` files in
   `napcat_cli/`. Fails on any undefined name, undefined import, or real error.
   Filters out warnings (unused imports, unused locals, f-string placeholders).

2. **`test_all_modules_compile`** — Runs `py_compile` on every `.py` file.
   Catches syntax errors that would crash on import.

These run automatically with `pytest`. They would have caught all three bugs
above. **Never disable or skip these tests.**

## Development Rules

1. **Run `pytest` before every commit** — the lint tests catch most issues.
2. **Test external CLI commands manually** before embedding them in templates.
3. **Initialize all variables** at the point of introduction.
4. **Add `try/except` to event handlers** — a single bad event must never kill
   the event loop.
5. **When editing a function, re-read the full function body** after the edit to
   verify no variables were orphaned or duplicated.

## Message ID-only paths (no group_id needed)

You can operate on messages using just the message_id. New in v2.1.0:

### skills-fs (FUSE mount)

Write to these paths directly with `echo '{"text": "..."}' > napcat/messages/:message_id/reply/text`:

| Path | Action | Description |
|------|--------|-------------|
| messages/:mid | read | Get message content by message_id only |
| messages/:mid/reply/text | write | Reply with smart text (auto-parses CQ codes) |
| messages/:mid/reply/text_raw | write | Reply with raw plain text |
| messages/:mid/reply/image | write | Reply with an image |
| messages/:mid/reply/cqcode | write | Reply with CQ code string |
| messages/:mid/reply/at | write | Reply with @-mention |
| messages/:mid/reply/json | write | Reply with full segments JSON |
| messages/:mid/image | read | Get image URL/file info from message |

### CLI equivalents

- `napcat get_message <mid>` - get message content
- `napcat get_image <url>` - download image by URL
- `napcat group <gid> get_message <mid>` - get message in group context
- `napcat reply group <gid> <mid> -m "text"` / `napcat reply private <uid> <mid> -m "text"` - reply to message

### HTTP provider invoke

```
POST /invoke {"action": "reply_by_mid_text", "params": {"message_id": "...", "text": "..."}}
GET  /invoke?action=get_message_by_mid&message_id=...
GET  /invoke?action=get_image_by_mid&message_id=...
```

### Notes
- QQ image URLs have hotlinking protection. Always use `napcat get_image <url>` or skills-fs `/napcat/get_image` to download images before analyzing them. Direct URL access (e.g. vision_analyze) will fail.
- The reply_by_mid_* actions auto-resolve whether the message is from a group or private chat.
- If you already know the group_id, the groups/:gid/:range/:mid/ paths are equivalent but faster (no DB lookup).
