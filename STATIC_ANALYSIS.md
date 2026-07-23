# Static Analysis Report — napcat-cli

Generated 2026-07-24 via `pyflakes` + `pylint --disable=all --enable=W,E`.

## 1. Active Bugs (must fix)

### E0203: Access to `_online_cache` before definition — `lib/api.py:145-146`

```python
# Line 145-146 (in is_online()):
if hasattr(self, "_online_cache") and (now - self._online_cache["ts"]) < _cache_ttl:
    return self._online_cache["online"]
# Line 151:
self._online_cache = {"ts": now, "online": online}
```

`_online_cache` is never initialized in `__init__`. The `hasattr` guard
makes it not crash, but it's fragile — if anyone removes the guard, it's
an `AttributeError`. Also, pylint flags it as a code smell.

**Fix**: Add `self._online_cache: dict = {}` in `__init__`.

### Duplicate function definitions — `wake_orchestrator.py`

- `_event_text` defined at line 71 **and** line 192 — the first is dead code.
- `_where` defined at line 87 **and** line 214 — the first is dead code.

**Fix**: Remove the dead copies at lines 71 and 87.

## 2. Code Smells (low priority)

### Unused imports (14 instances across 7 files)

- `setup_wizard.py`: `subprocess`, `Any`, `DATA_DIR`
- `wake_orchestrator.py`: `Any`, `render_wake_command`
- `daemon/watch.py`: `get_config`
- `lib/api.py`: `Path`
- `lib/message.py`: `json`
- `lib/ocr.py`: `tempfile`, `urllib.request`, `Path` (+ re-imports inside functions)
- `tui/chat_view.py`: `Select`, `reactive`, `Container`, `Binding`

**Impact**: None at runtime, but noisy and hides real issues.

### f-string without placeholders (15 instances)

Files: `setup_wizard.py` (8), `wake_orchestrator.py` (1), `cli.py` (2),
`watch.py` (3).

**Impact**: None — just `f"hello"` that should be `"hello"`.

### Unused local variables (8 instances)

- `cli.py`: `ac`, `phone_status`, `phone_alerts`, `phone_config`
- `lib/events.py`: `now` (x2), `placeholders`
- `tui/chat_view.py`: `sub_type`

**Impact**: Dead code, minor memory waste.

### `eval()` usage — `cli.py:1149`

```python
result = eval(...)
```

Used in `cmd_schema` to evaluate JSON-like expressions. This is a **security
risk** if user input reaches it. Should use `json.loads` or `ast.literal_eval`.

### Broad `except Exception` (56 instances)

Most are in setup wizard and CLI command handlers — acceptable for a CLI tool
that must not crash. The ones in `lib/api.py` and `daemon/watch.py` should be
narrowed.

### `subprocess.run` without `check=True` — `cli.py:1321`

Silently ignores non-zero exit codes.

## 3. File Size Analysis

```
2193 lines  daemon/watch.py     ← needs splitting
1869 lines  cli.py              ← needs splitting
 546 lines  wake_orchestrator.py
 508 lines  daemon/schemas.py
 485 lines  tui/chat_view.py
 400 lines  wake_backend.py
```

### Recommended split for `daemon/watch.py` (2193 lines)

Current structure mixes 5 distinct responsibilities in one file:

| Lines | Responsibility | Proposed file |
|-------|---------------|---------------|
| 47-93 | Utilities (_make_rotating_logger, _run_with_timeout) | `daemon/utils.py` |
| 95-600 | EventProcessor (event handling, alerting, wake triggers) | `daemon/processor.py` |
| 600-627 | EventCache (in-memory cache) | `daemon/cache.py` |
| 627-695 | ws_daemon (WebSocket client) | `daemon/ws_client.py` |
| 694-1019 | SkillsFsManager + monitor | `daemon/skillsfs.py` |
| 1019-1990 | NapCatHandler (HTTP provider, _dispatch with ~40 actions) | `daemon/http_provider.py` |
| 1989-end | run_http_server, health_check, backlog_sweep, run_daemon, main | `daemon/__init__.py` or `daemon/main.py` |

### Recommended split for `cli.py` (1869 lines)

| Lines | Responsibility | Proposed file |
|-------|---------------|---------------|
| 40-255 | Core commands: api, send, reply, recall | `cli/messaging.py` |
| 256-485 | Group/friend/file commands | `cli/social.py` |
| 379-485 | Daemon management | `cli/daemon.py` |
| 486-640 | Events, alerts, batch, config, status | `cli/info.py` |
| 680-857 | OCR, translate, like, forward, poke, schedule | `cli/actions.py` |
| 857-985 | Misc commands (cookies, search, msg, react) | `cli/actions.py` |
| 986-1042 | Phone/TUI subcommands | `cli/phone.py` |
| 1043-1277 | Message/schema commands | `cli/schema.py` |
| 1278-1450 | Wake commands | `cli/wake.py` |
| 1450-end | argparse setup + main() | `cli/__init__.py` or `cli/main.py` |

### Other files — OK as-is

- `wake_orchestrator.py` (546 lines): cohesive, single responsibility
- `wake_backend.py` (400 lines): cohesive, single responsibility
- `daemon/schemas.py` (508 lines): pure data, splitting adds no value
- `tui/chat_view.py` (485 lines): cohesive UI component
- `lib/*` files: all under 340 lines, well-scoped

## 4. Priority Recommendations

1. **Immediate**: Fix E0203 (`_online_cache` init) and remove dead duplicate
   functions in `wake_orchestrator.py`.
2. **Short-term**: Clean unused imports and unused locals (mechanical, safe).
3. **Medium-term**: Replace `eval()` with `ast.literal_eval()` in `cli.py:1149`.
4. **Medium-term**: Split `watch.py` into `processor.py` + `http_provider.py`
   + `skillsfs.py` + `ws_client.py`. The HTTP provider `_dispatch` method alone
   is ~1000 lines and should be its own module.
5. **Lower priority**: Split `cli.py` into subcommands package. This is a big
   refactor and touches the argparse setup.
