# napcat-cli

Standalone CLI and daemon for NapCat QQ bot management with skills-fs integration.

[![PyPI](https://img.shields.io/pypi/v/napcat-cli.svg?label=PyPI)](https://pypi.org/project/napcat-cli/)
[![Python versions](https://img.shields.io/pypi/pyversions/napcat-cli.svg)](https://pypi.org/project/napcat-cli/)

> 📝 **介绍博文：** [napcat-cli 群配置与管理速查](https://yvxi.pages.dev/blog/napcat-cli-group-config/) — 从零配置、群消息收发、Agent Wake 的完整链路。

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

### Two transports, auto-fallback

| Transport | When it's used | Needs |
|-----------|----------------|-------|
| **CLI one-shot** (default) | Always available | `hermes` on PATH |
| **HTTP API server** | Opt-in (best latency, idempotent, in-session) | `wake_http_url` + `wake_http_key` |

`wake_primary=auto` tries HTTP first (if configured + reachable), else falls back
to the CLI one-shot. The Hermes CLI backend runs
`hermes --continue <session> -z "<prompt>" --yolo --pass-session-id`; the HTTP
backend POSTs to `POST /api/sessions/{id}/chat` (verified per the Hermes API
docs), with `Authorization: Bearer <key>` and an `Idempotency-Key` header.

### Event routing

| Trigger | Behavior |
|---------|----------|
| `AT_ME`, `REPLY_TO_ME` | Near-immediate wake (cooldown bypassed), with who/where/text in the prompt |
| `GROUP_TRIGGER`, `PRIVATE_TRIGGER` | Debounced wake |
| `NEW_MESSAGE` (not @) | Tracked, not woken; if unread longer than `wake_new_message_idle_seconds` → a `NEW_MESSAGE_BACKLOG` wake |
| `NEW_FRIEND`, `NEW_REQUEST`, `BOT_BANNED`, `NEW_POKE`, `GROUP_ADMIN_CHANGE`, `NEW_GROUP_MEMBER`, `BOT_OFFLINE`, … | Debounced + cooldown-bounded wake so the agent perceives them within a reasonable window |

Debounce (`wake_debounce_seconds`, default 3) coalesces a burst into one wake;
cooldown (`wake_cooldown_seconds`, default 30) suppresses repeats. Every wake is
logged to `daemon.log` as `[WAKE] trigger / queued / deliver / reply` lines
(including transport, elapsed time, and the agent's reply), and `daemon.log` is
size-rotated (2 MB × 5) so it can't fill the disk.

### Configure

The easiest path is the wizard:

```bash
napcat setup        # choose Hermes preset; CLI one-shot by default, HTTP opt-in
```

Or set keys directly:

```bash
napcat config set wake_enabled true
napcat config set wake_preset hermes        # hermes | custom | none
napcat config set wake_session napcat-qq
napcat config set wake_primary auto         # auto | http | cli
# HTTP (optional). Key also readable from NAPCAT_WAKE_HTTP_KEY / HERMES_API_KEY:
napcat config set wake_http_url http://127.0.0.1:8642
napcat config set wake_http_key <API_SERVER_KEY>
napcat config set wake_new_message_idle_seconds 600
```

To enable the Hermes HTTP API server (appends to `~/.hermes/.env` and restarts
the `hermes-gateway.service` systemd unit), answer "y" during `napcat setup`, or
set `API_SERVER_ENABLED=true` + `API_SERVER_KEY` in `~/.hermes/.env` yourself and
`sudo systemctl restart hermes-gateway.service`.

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
path) — see `napcat_cli/data/SKILL.md`. Legacy `wake_command` shell strings still
run as a last-resort escape hatch when no backend is configured.

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

> ⚙️ **Distribution note:** `skills-fs` is a **compiled Go binary** (git submodule
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
