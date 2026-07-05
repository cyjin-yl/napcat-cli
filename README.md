# NapCat CLI

Standalone CLI and daemon for NapCat QQ bot management with skills-fs integration.

## Overview

NapCat CLI provides two interfaces for interacting with your QQ bot:

1. **CLI Tools** — Direct command-line access to all NapCat API features
2. **skills-fs Integration** — Filesystem-based API exposure for AI agents

### Architecture

```
┌─────────────┐         WebSocket         ┌──────────────┐
│   NapCat    │◄─────────────────────────┤   Daemon     │
│  Server     │                          │  (watch.py)  │
└─────────────┘                           └──────────────┘
       │ HTTP API                                │
       │                                         │ Events + Alerts
       ▼                                         ▼
┌─────────────┐                          ┌──────────────┐
│  napcat     │◄─────────────────────────┤ Filesystem  │
│    CLI      │   HTTP Provider (18821)  │   Bridge     │
└─────────────┘                          └──────────────┘
                                                  │
                                                  ▼
                                         ┌──────────────────┐
                                         │   skills-fs      │
                                         │  Virtual Files   │
                                         └──────────────────┘
                                                  │
                                                  ▼
                                         ┌──────────────────┐
                                         │  AI Agent        │
                                         │  (Hermes)        │
                                         └──────────────────┘
```

### Components

- **`napcat` CLI** — Command-line interface for all QQ bot operations
- **`daemon/watch.py`** — WebSocket listener + HTTP provider server
- **`lib/`** — Core API client and event handling
- **`skills-fs/`** — Virtual filesystem engine (submodule)

## skills-fs Integration

The skills-fs integration exposes QQ bot capabilities as an intuitive virtual filesystem:

### Filesystem Structure

```
~/.hermes/skills/napcat-cli/          # FUSE mountpoint
├── SKILL.md                          # Auto-generated skill documentation
├── AGENTS.md                         # Agent guide: daemon setup and navigation
├── persona.md                        # Bot persona / system prompt artifact
├── napcat/                           # API endpoints as files
│   ├── events/                       # Real-time event stream
│   ├── alerts/                       # Pending notifications
│   ├── groups/                       # Dynamic directory: group IDs
│   │   └── {group_id}/             # Dynamic directory: time ranges + send file
│   │       ├── AGENTS.md
│   │       ├── send                # Write message JSON to send to this group
│   │       └── {recent,1days,...}/ # Dynamic directory: message IDs
│   │           └── {message_id}    # API file: read message metadata
│   ├── friends/                      # Dynamic directory: user IDs
│   │   └── {user_id}/              # Dynamic directory: time ranges + send file
│   │       ├── AGENTS.md
│   │       ├── send                # Write message JSON to send to this friend
│   │       └── {recent,1days,...}/ # Dynamic directory: message IDs
│   │           └── {message_id}    # API file: read message metadata
│   ├── send_group                    # Legacy group send endpoint
│   ├── send_private                  # Legacy private send endpoint
│   └── ...                           # Other API endpoints
```

### How It Works

1. **Browse Intuitively** — Navigate `napcat/` to explore endpoints.
2. **Read Operations** — Files contain data (JSON, text, etc.).
3. **Write Operations** — Writing JSON to a write-enabled file triggers the corresponding NapCat API call. The JSON payload is forwarded as provider parameters (requires `writeParams: "json"`).
4. **Dynamic Directories** — `napcat/groups/{group_id}/...` and `napcat/friends/{user_id}/...` are provider-backed directories that render IDs, time ranges, and message IDs on demand. Each directory also exposes a `send` file scoped to that group or friend.
5. **Auto-Generated Docs** — `SKILL.md` is generated from the skill definition and exposed at the FUSE root via `exposeAtRoot: true`.
6. **Persona Artifact** — `persona.md` is mounted as a blob at the FUSE root for agents to read the bot persona.
7. **Agent Guidance (AGENTS.md)** — Per-directory `AGENTS.md` files describe what each directory contains and what parameters are available, so agents can navigate without guessing.

### Example: Agent Workflow

```bash
# Check bot status
cat ~/.hermes/skills/napcat-cli/napcat/status

# Read recent events
cat ~/.hermes/skills/napcat-cli/napcat/events

# Browse group messages dynamically
ls ~/.hermes/skills/napcat-cli/napcat/groups
ls ~/.hermes/skills/napcat-cli/napcat/groups/123456
cat ~/.hermes/skills/napcat-cli/napcat/groups/123456/AGENTS.md
ls ~/.hermes/skills/napcat-cli/napcat/groups/123456/recent
cat ~/.hermes/skills/napcat-cli/napcat/groups/123456/recent/1001

# Send to a specific group by writing to its send file
echo '{"message": "Hello group!"}' > ~/.hermes/skills/napcat-cli/napcat/groups/123456/send

# Browse private friend messages
ls ~/.hermes/skills/napcat-cli/napcat/friends
ls ~/.hermes/skills/napcat-cli/napcat/friends/987654
cat ~/.hermes/skills/napcat-cli/napcat/friends/987654/recent/2001

# Send to a specific friend
echo '{"message": "Hi!"}' > ~/.hermes/skills/napcat-cli/napcat/friends/987654/send

# Clear a specific alert
echo '{"name": "NEW_MESSAGE"}' > ~/.hermes/skills/napcat-cli/napcat/clear_alert
```

## CLI Usage

### Basic Commands

```bash
# Check bot status
napcat status

# List groups
napcat group list

# List group members
napcat group members <group_id>

# Send messages
napcat send group <group_id> <message>
napcat send private <user_id> <message>

# Message management
napcat recall <message_id> [--group <group_id>]

# Friend operations
napcat friend list
napcat friend info <user_id>

# Events and alerts
napcat events --type message --limit 10
napcat alerts --clear
```

### API Access

```bash
# Raw API access (like `gh api`)
napcat api get_login_info
napcat api send_group_msg '{"group_id": 123456, "message": "Hello"}'

# Custom API endpoint
napcat api .send_poke '{"user_id": 123456}'
```

### Daemon Management

```bash
# Start the daemon (WebSocket + HTTP provider)
napcat daemon start

# Check daemon status
napcat daemon status

# Stop the daemon
napcat daemon stop
```

## Installation

### Prerequisites

- Python 3.10+
- NapCat server running (Docker or native)
- Go compiler (for skills-fs build)

### Setup

1. **Clone repository**
   ```bash
   git clone https://github.com/255doesnotexist/napcat-cli.git
   cd napcat-cli
   ```

2. **Initialize submodules**
   ```bash
   git submodule update --init --recursive
   ```

3. **Build skills-fs**
   ```bash
   cd skills-fs
   make build
   cd ..
   ```

4. **Install napcat CLI**
   ```bash
   chmod +x napcat
   sudo ln -s $(pwd)/napcat ~/.local/bin/napcat
   ```

5. **Configure**
   ```bash
   # Edit config if needed
   cat > ~/.config/napcat-cli.json << EOF
   {
     "napcat": {
       "http_url": "http://127.0.0.1:18801",
       "ws_url": "ws://127.0.0.1:18800",
       "token": ""
     }
   }
   EOF
   ```

## Configuration

### Environment Variables

- `NAPCAT_API_URL` — NapCat HTTP API endpoint (default: `http://127.0.0.1:18801`)
- `NAPCAT_TOKEN` — API authentication token
- `NAPCAT_DATA_DIR` — Data directory (default: `~/.napcat-data`)

### skills-fs Configuration

A sample `skills-fs-config.json` is shipped in the repository root. Copy it to the runtime location and adjust the home path if needed:

```bash
cp skills-fs-config.json ~/.hermes/skills-fs.json
```

Key configuration points:

- **FUSE mountpoint** — Start `skills-fs` with `--mountpoint ~/.hermes/skills/napcat-cli` (no `-fs` suffix). The generated `SKILL.md` is exposed at the FUSE root via `exposeAtRoot: true`.
- **Payload forwarding** — Every write-enabled API mount uses `"writeParams": "json"` so that the JSON written to the file is forwarded as provider parameters.
- **Persona artifact** — `persona.md` is mounted as a blob at `/persona.md`.

Note: because `skillsRoot` is `~/.hermes/skills`, the skill generator writes `SKILL.md` to `~/.hermes/skills/napcat-cli/SKILL.md` before the FUSE daemon mounts. After FUSE mounts, that on-disk file is hidden and replaced by the virtual `/SKILL.md` exposed by `exposeAtRoot: true`. This is expected.

### Running skills-fs

```bash
skills-fs fuse --config ~/.hermes/skills-fs.json \
  --mountpoint ~/.hermes/skills/napcat-cli \
  --allow-other \
  --log-file ~/.hermes/skills-fuse.log
```

If you need to validate or regenerate the config while FUSE is already mounted, unmount it first (e.g. `fusermount3 -u ~/.hermes/skills/napcat-cli`), then validate and remount.

Example minimal config snippet:

```json
{
  "providers": [
    { "id": "napcat", "url": "http://127.0.0.1:18821/invoke" }
  ],
  "skillsRoot": "$HOME/.hermes/skills",
  "skills": [
    {
      "name": "napcat-cli",
      "description": "NapCat QQ bot messaging — send messages, read events, manage groups, handle friends",
      "enabled": true,
      "version": "1.0.0",
      "author": "Ezra",
      "license": "MIT",
      "platforms": ["linux"],
      "metadata": { "source": "napcat-cli", "category": "messaging" },
      "allowedTools": ["read_file", "write_file", "list_directory"],
      "bodyTemplate": "# NapCat CLI Skill\n...",
      "exposeAtRoot": true
    }
  ],
  "mounts": [
    { "path": "/napcat", "kind": "dir", "mode": "0755" },
    { "path": "/persona.md", "kind": "blob", "mode": "0444", "data": "..." },
    {
      "path": "/napcat/send_group",
      "kind": "api",
      "provider": "napcat",
      "read": "napcat_send_group_msg",
      "write": "napcat_send_group_msg",
      "mode": "0644",
      "writeParams": "json"
    }
  ]
}
```

## Daemon Features

The daemon (`daemon/watch.py`) provides:

### Real-time Event Processing

- **Messages** — Group and private messages
- **Notices** — Poke, recall, ban, admin changes, member join/leave
- **Requests** — Friend and group requests
- **Meta** — Connection status, heartbeat

### Alert System

The daemon generates alert files for important events:

- `NAPCAT_CLI_NEW_MESSAGE` — Any new message
- `NAPCAT_CLI_AT_ME` — Bot was @mentioned
- `NAPCAT_CLI_REPLY_TO_ME` — Reply to bot's message
- `NAPCAT_CLI_NEW_POKE` — Poke received
- `NAPCAT_CLI_NEW_REQUEST` — Friend/group request
- `NAPCAT_CLI_NEED_WAKE_UP` — Composite alert for agent attention

### HTTP Provider

Daemon runs HTTP server implementing skills-fs provider contract:

- **Endpoint**: `http://127.0.0.1:18821`
- **Actions**: `get_events`, `get_alerts`, `clear_alert`, `list_groups`, `list_friends`, `list_time_ranges`, `list_messages`, `get_message`, `send_group_message`, `send_private_message`, `napcat_*` (API proxy)
- **Format**: JSON request/response

Dynamic directory actions return entries in the format `{"entries": [{"name": "...", "kind": "..."}]}` so skills-fs can render them as directories.

## Development

### Project Structure

```
napcat-cli/
├── napcat              # Main CLI script
├── config.py           # Configuration management
├── skills-fs-config.json  # Sample skills-fs configuration (shipped with repo)
├── persona.md         # Bot persona configuration (mounted as FUSE artifact)
├── lib/
│   ├── api.py         # NapCat HTTP API client
│   ├── events.py      # Event filesystem bridge
│   └── config.py      # Data directory paths
├── daemon/
│   └── watch.py       # WebSocket daemon + HTTP provider
├── skills-fs/         # Virtual filesystem (submodule)
└── README.md          # This file
```

### Running Tests

```bash
# Test CLI commands
napcat status
napcat group list

# Test daemon
python3 daemon/watch.py ~/.config/napcat-daemon.json

# Test skills-fs
cd skills-fs && make test
```

## Contributing

Contributions welcome! Proudly powered by [skills-fs](https://github.com/yandu-app/skills-fs).
## License

Same as parent project.

## Related Projects

- [NapCat](https://github.com/NapNeko/NapCatQQ-Docker) — NapCat OneBot 11 implementation
- [skills-fs](https://github.com/yandu-app/skills-fs) — Virtual filesystem engine
- [Hermes](https://github.com/yandu-app/hermes) — AI agent framework (if applicable)

---

**Note**: This project is designed for integration with AI agent frameworks. The filesystem-based API design makes it intuitive for agents to explore and interact with QQ bot capabilities through familiar file operations.
