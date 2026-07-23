# napcat-cli

Standalone CLI and daemon for NapCat QQ bot management with skills-fs integration.

[![PyPI](https://img.shields.io/pypi/v/napcat-cli.svg?label=PyPI)](https://pypi.org/project/napcat-cli/)
[![Python versions](https://img.shields.io/pypi/pyversions/napcat-cli.svg)](https://pypi.org/project/napcat-cli/)

> 介绍博文： [napcat-cli 群配置与管理速查](https://yvxi.pages.dev/blog/napcat-cli-group-config/) — 从零配置、群消息收发、Agent Wake 的完整链路。

---

napcat-cli provides a CLI for all NapCat API operations, a WebSocket daemon
that bridges to a skills-fs HTTP provider, and an agent wake-up mechanism for
integrating with Hermes or other AI agents.

```bash
napcat send group 123456 -m "Hello"
napcat send private 987654 -m "Hi"
napcat recall 1001
napcat events --limit 10
napcat daemon start
napcat wake --reason NEW_MESSAGE
```

---

## Commands

| Command | Description |
|---------|-------------|
| `napcat api <endpoint>` | Raw API access (like `gh api`) |
| `napcat send group <id> -m "msg"` | Send group message |
| `napcat send private <id> -m "msg"` | Send private message |
| `napcat reply <id> -m "msg"` | Reply to a message |
| `napcat recall <msg_id>` | Recall a message |
| `napcat group <sub>` | Group list, members, settings |
| `napcat friend <sub>` | Friend list, info |
| `napcat file <sub>` | File upload, download, list |
| `napcat events` | Read events from SQLite |
| `napcat alerts [--clear]` | Pending alerts |
| `napcat status` | Bot online status |
| `napcat config get/set` | Configuration management |
| `napcat daemon start/stop/status/restart` | Watch daemon |
| `napcat fs tree` | Skills-fs directory tree |
| `napcat wake [--reason R] [--prompt P] [--transport T] [--dry-run]` | Wake the configured agent (HTTP/CLI, auto-fallback) |
| `napcat setup` | Interactive setup wizard |
| `napcat phone` | Textual phone-style TUI |

---

## Setup

```bash
uv tool install napcat-cli  # recommended — isolated install via uv (alternative: pip install napcat-cli)
napcat setup          # interactive — guides token, data dir, skills-fs, wake
napcat daemon start
```

The setup wizard writes two config files:

- `<data_dir>/config.json` — API URL, token, self_id, ports, triggers
- `<data_dir>/daemon.json` — All fields consumed by `watch.py` including `skills_fs_*` settings

Non-interactive mode uses defaults:

```bash
napcat setup --non-interactive  # no prompts, validates token
napcat setup --yes              # skip token validation
napcat setup --force            # overwrite existing config
```

---

## Agent Wake

When a notable QQ event arrives, the daemon wakes an external agent (Hermes by
default) carrying a **contextual prompt** so it can read the inbox and reply.
The wake mechanism is **generic and pluggable** — Hermes is just the default
preset; any HTTP endpoint or shell command works.

### How it works

```
QQ Event (WS) --> EventProcessor --> WakeOrchestrator (debounce/cooldown/queue)
                                          |
                                     Waker (auto-fallback)
                                    /              \
                          HTTP API (preferred)    CLI (fallback)
                          POST /chat              hermes -z "..."
                          fixed session_id        --continue <session>
```

- **Debounce** (`wake_debounce_seconds`, default 3): coalesces a burst of same-reason events into one wake.
- **Cooldown** (`wake_cooldown_seconds`, default 30): suppresses repeat wakes for non-urgent reasons.
- **Serial queue**: `WakeOrchestrator` has a single worker thread — wakes to the same session are processed **one at a time**, preventing concurrent "split personality" responses.
- **Timeout**: `wake_timeout` (default 300s) — how long to wait for the agent to respond.

Every wake is logged to `daemon.log`:
```
[WAKE] trigger reason=AT_ME who=Alice where=group123 text='hello'
[WAKE] queued reason=AT_ME pending=1 debounce=1.0s primary=auto
[WAKE] delivered reason=AT_ME transport=http detail=POST /api/sessions/.../chat -> 200
```

### Event routing

| Trigger | Behavior |
|---------|----------|
| `AT_ME`, `REPLY_TO_ME`, `DM_ME` | Near-immediate wake (cooldown bypassed). Prompt includes who/where/text, image metadata, reply chain, and skill hints. |
| `GROUP_TRIGGER` | Debounced wake (group trigger-word match) |
| `NEW_MESSAGE` (not @) | Tracked, not woken; if unread longer than `wake_new_message_idle_seconds` -> `NEW_MESSAGE_BACKLOG` wake |
| `NEW_FRIEND`, `NEW_REQUEST`, `BOT_BANNED`, `NEW_POKE`, `GROUP_ADMIN_CHANGE`, `NEW_GROUP_MEMBER`, `BOT_OFFLINE`, ... | Debounced + cooldown-bounded wake |

AT_ME detection supports all NapCat message formats: CQ code (`[CQ:at,qq=...]`), display name (`@name (qq)`), and message segments (`{"type":"at","data":{"qq":"..."}}`).

### Two transports, auto-fallback

| Transport | When it's used | Needs |
|-----------|----------------|-------|
| **HTTP API server** (preferred) | When `wake_http_url` + `wake_http_key` are set | Agent's HTTP API endpoint |
| **CLI one-shot** (fallback) | Always available | Agent CLI on PATH |

`wake_primary=auto` tries HTTP first (if configured + reachable), else falls back to CLI.

### Configuring for Hermes Agent

#### Option A: HTTP API Server (recommended)

The HTTP API server is the recommended wake transport. It provides:
- **Session continuity**: all wakes go to the same session (no "split personality")
- **Idempotency**: `Idempotency-Key` header prevents duplicate processing on retry
- **Persistence**: session history is stored in `~/.hermes/state.db`
- **Lower latency**: no CLI process spawn overhead

**Step 1: Enable Hermes API Server**

Add to `~/.hermes/.env`:
```bash
API_SERVER_ENABLED=true
API_SERVER_KEY=$(openssl rand -hex 32)   # generate a random key
```

Restart the gateway:
```bash
sudo systemctl restart hermes-gateway.service
# Verify API server is listening:
curl http://127.0.0.1:8642/health
```

**Step 2: Create a dedicated QQ session**

```bash
curl -X POST http://127.0.0.1:8642/api/sessions \
  -H "Authorization: Bearer <API_SERVER_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"title": "napcat-qq"}'
# Response: {"session": {"id": "api_1784824379_59dd9495", ...}}
```

**Step 3: Configure napcat-cli**

```bash
napcat config set wake_enabled true
napcat config set wake_preset hermes
napcat config set wake_primary auto              # try HTTP first, CLI fallback
napcat config set wake_http_url http://127.0.0.1:8642
napcat config set wake_http_key <API_SERVER_KEY>
napcat config set wake_http_session_id <SESSION_ID>  # from Step 2
napcat config set wake_session napcat-qq         # CLI fallback session name
napcat config set wake_timeout 300               # seconds
```

Or edit `~/.napcat-data/daemon.json` directly:
```json
{
  "wake_enabled": true,
  "wake_preset": "hermes",
  "wake_primary": "auto",
  "wake_http_url": "http://127.0.0.1:8642",
  "wake_http_key": "<API_SERVER_KEY>",
  "wake_http_session_id": "<SESSION_ID>",
  "wake_session": "napcat-qq",
  "wake_timeout": 300.0,
  "wake_cli_command": "hermes --continue {session} -z \"$(cat {prompt_file})\" --yolo --pass-session-id"
}
```

Key env vars (also checked by Hermes preset, in priority order):
- `NAPCAT_WAKE_HTTP_KEY` — HTTP API bearer token
- `HERMES_API_KEY` — alias for the above

#### Option B: CLI one-shot (simpler, less reliable)

If you don't want to run the API server, napcat-cli can invoke the Hermes CLI
directly. The CLI backend writes the prompt to a temp file and passes it via
`$(cat {prompt_file})` to avoid shell quoting issues.

```bash
napcat config set wake_primary cli
napcat config set wake_session napcat-qq
napcat config set wake_cli_command 'hermes --continue {session} -z "$(cat {prompt_file})" --yolo --pass-session-id'
```

Limitations of CLI mode:
- Each invocation may create a **new session** if the name doesn't match an existing one
- No idempotency — retries may cause duplicate processing
- ~30-60s per invocation (CLI process startup + model inference)
- Large prompts with special characters require the `$(cat {prompt_file})` pattern (not `-z -`, which Hermes treats as literal text "-")

### Configuring for other agents

napcat-cli's wake system is agent-agnostic. Any HTTP endpoint or shell command works.

#### Custom HTTP endpoint

```bash
napcat config set wake_preset custom
napcat config set wake_primary http
napcat config set wake_http_url http://127.0.0.1:9000
napcat config set wake_http_key my-secret-key
# The HTTP backend POSTs to: {wake_http_url}/api/sessions/{session}/chat
# Body: {"input": "<prompt>"}
# Headers: Authorization: Bearer <key>, Idempotency-Key: <unique-id>
# If your endpoint uses a different path/body format, see wake_backend.py HttpWakeBackend
```

#### Custom CLI command

```bash
napcat config set wake_preset custom
napcat config set wake_primary cli
napcat config set wake_cli_command 'my-agent --prompt "$(cat {prompt_file})"'
# Placeholders: {prompt_file} (temp file path), {session}, {prompt} (inline, shell-quoted)
```

#### Disable wake entirely

```bash
napcat config set wake_preset none
```

### Manual / debug

```bash
napcat wake                           # reason: manual, contextual default prompt
napcat wake --reason AT_ME --prompt "hello"
napcat wake --transport cli           # force a transport for this wake
napcat wake --dry-run                 # render the HTTP request + CLI command without executing
napcat wake test                      # per-transport configured + reachable probe
napcat wake sessions                  # list Hermes sessions (HTTP backend)
grep '\[WAKE\]' ~/.napcat-data/daemon.log    # see when/why/how wakes fired
```

The agent replies via `napcat send` / `napcat reply` (or the skills-fs write
path) — see `napcat_cli/data/SKILL.md`.

### Troubleshooting & verification

#### Verify the full wake chain end-to-end

1. **Check daemon is running:**
   ```bash
   ps aux | grep watch.py | grep -v grep
   tail -5 ~/.napcat-data/daemon.log   # should show recent events
   ```

2. **Check Hermes API server is listening:**
   ```bash
   curl -s http://127.0.0.1:8642/health
   # Expected: {"status": "ok", ...}
   ```

3. **Test HTTP wake manually:**
   ```bash
   curl -X POST http://127.0.0.1:8642/api/sessions/<SESSION_ID>/chat \
     -H "Authorization: Bearer <KEY>" \
     -H "Content-Type: application/json" \
     -H "Idempotency-Key: test-001" \
     -d '{"input": "ping"}'
   # Expected: {"message": {"content": "..."}}  with a response
   ```

4. **Test CLI wake manually:**
   ```bash
   echo "hello" > /tmp/prompt.txt
   hermes --continue napcat-qq -z "$(cat /tmp/prompt.txt)" --yolo --pass-session-id
   # Expected: agent responds within 30-60s
   # NOTE: -z - does NOT read stdin. Always use -z "$(cat file)" or -z "literal".
   ```

5. **Send a test @mention to the bot in a group, then check logs:**
   ```bash
   grep '\[WAKE\]' ~/.napcat-data/daemon.log | tail -10
   # Should show: trigger -> queued -> delivered (or failed)
   ```

#### Common issues

| Symptom | Cause | Fix |
|---------|-------|-----|
| Bot doesn't respond to @mentions | Daemon not running, or crash killed event loop | `ps aux \| grep watch.py`; check `daemon.log` for errors; restart daemon |
| `[WAKE] delivered` but no reply | Agent received prompt but didn't act | Check Hermes session via `hermes sessions list`; verify agent has napcat skills/tools |
| `[WAKE] failed ... timeout` | Agent took > `wake_timeout` seconds | Increase `wake_timeout` in daemon.json |
| `[WAKE] failed ... session resolution failed` | HTTP session ID not found | Run `napcat wake sessions` to list; recreate if needed |
| Multiple replies to one event ("split personality") | Concurrent wakes to same session | Ensure `wake_primary=auto` or `http`; the worker thread serializes wakes |
| `hermes -z -` sends literal "-" | `-z` takes literal string, not stdin | Use `-z "$(cat {prompt_file})"` template |
| Events stop after a bad message | Unhandled exception in event handler | Check `daemon.log` for `process() ERROR`; the handler now has try/except |

#### Static analysis (prevents regressions)

The test suite includes `tests/test_lint.py` which runs `pyflakes` and
`py_compile` on every file in `napcat_cli/`. This catches undefined names,
syntax errors, and other issues that would crash the daemon at runtime.

```bash
python -m pytest tests/test_lint.py -v
# Should pass: test_no_undefined_names, test_all_modules_compile
```

### Image & OCR

The wake prompt automatically includes image metadata when an image message is received:
- `file_id` — image file ID
- `url` — download URL
- `sub_type` — image type (0=normal, 1=sticker, 7=like)
- `file_size` — file size in bytes
- `summary` — PaddleOCR auto-recognized text (if available)
- `reply_id` — reply message ID (for tracking reply chains)

**PaddleOCR is integrated.** Image text is auto-recognized and included in the
`summary` field. The agent can also use multimodal vision to read the image URL
directly. NapCat's built-in OCR is unavailable — do not use `/napcat/ocr`.

| Operation | CLI | skills-fs |
|-----------|-----|-----------|
| Download image | `napcat get_image <url>` | `/napcat/get_image` |
| View message detail | `napcat group <gid> get_message <mid>` | `/napcat/groups/:gid/:range/:mid/:content` |
| Send image | `napcat send group <gid> -f <path>` | `/napcat/groups/:gid/send/image` |
---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `NAPCAT_API_URL` | `http://127.0.0.1:18801` | NapCat HTTP endpoint |
| `NAPCAT_TOKEN` | — | API auth token |
| `NAPCAT_DATA_DIR` | `~/.napcat-data` | Data directory |

---

## skills-fs Integration

The daemon runs an HTTP provider server (port 18820/18821) that skills-fs calls
to read/write NapCat API endpoints through the virtual filesystem.

When `skills_fs_enabled` is true in `daemon.json`, the daemon spawns skills-fs
automatically with the configured mountpoint and config file.

> Distribution note: `skills-fs` is a **compiled Go binary** (git submodule
> pinned at [`cb13f37`](https://github.com/yandu-app/skills-fs/tree/cb13f37)).
> It is **not bundled** in the pip/uv install of `napcat-cli`. If you need the
> FUSE mount (optional), build it separately:
>
> ```bash
> git clone https://github.com/yandu-app/skills-fs
> cd skills-fs && make build              # produces ./skills-fs
> napcat config set skills_fs_binary /path/to/skills-fs
> napcat daemon stop && napcat daemon start
> ```
>
> Or place `skills-fs` on your `PATH` and the daemon will find it. This feature
> is **optional** — the CLI, daemon, and wake system work without it.

Manual start:

```bash
skills-fs fuse --config ~/.napcat-data/skills-fs.json \
  --mountpoint ~/.napcat-data/skills/napcat-cli --allow-other
```

---

## Development

```
napcat-cli/
├── napcat_cli/          # Installable package
│   ├── cli.py           # CLI entry point
│   ├── wake.py          # Wake command-template renderer
│   ├── wake_backend.py  # Generic HTTP/CLI wake transports + Waker (auto-fallback)
│   ├── wake_presets.py  # Hermes/custom/none presets -> Waker
│   ├── wake_orchestrator.py  # Debounce, cooldown, backlog sweep, contextual prompts
│   ├── setup_wizard.py  # Setup wizard
│   ├── daemon/          # Watch daemon, schemas
│   ├── lib/             # API, config, events
│   ├── tui/             # Textual TUI
│   └── data/            # SKILL.md, persona.md
├── pyproject.toml
├── tests/
├── skills-fs/           # Go submodule
└── tools/               # Dev utilities
```

```bash
python -m pytest tests/ -v
python -m build --wheel
uv build && uv publish
```

---

## License

MIT
