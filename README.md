# napcat-cli

Standalone CLI and daemon for NapCat QQ bot management with skills-fs integration.

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
| `napcat wake [--reason R] [--dry-run]` | Trigger agent wake |
| `napcat setup` | Interactive setup wizard |
| `napcat phone` | Textual phone-style TUI |

---

## Setup

```bash
uv tool install napcat-cli
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

Events can trigger a wake command that runs a shell command (e.g. `hermes -z`).

Configure via:

```bash
napcat config set wake_on_event true
napcat config set wake_command 'hermes -c session -z "new QQ message" -s napcat-cli --yolo'
```

The `$REASON`, `${REASON}`, and `{reason}` placeholders in `wake_command` are
replaced with the event reason (e.g. `AT_ME`, `NEW_MESSAGE`).

Supported triggers: `AT_ME`, `REPLY_TO_ME`, `GROUP_TRIGGER`, `PRIVATE_TRIGGER`,
`NEW_POKE`, `NEW_FRIEND_REQUEST`, `NEW_GROUP_REQUEST`, `NEW_MESSAGE`.

Manual trigger:

```bash
napcat wake                        # reason: manual
napcat wake --reason NEW_MESSAGE   # custom reason
napcat wake --dry-run              # print command without executing
```

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
│   ├── wake.py          # Wake command builder
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
