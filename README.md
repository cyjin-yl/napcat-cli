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
~/.hermes/skills/napcat-cli/
├── skills/              # Auto-generated skill documentation
│   └── napcat-cli.md   # Complete API reference
└── napcat/             # API endpoints as files
    ├── groups/         # Browse groups by ID
    │   ├── 123456/    # Group-specific data
    │   │   ├── messages/   # Message history
    │   │   ├── members/    # Member list
    │   │   └── info        # Group information
    │   └── 789012/
    ├── events/        # Real-time event stream
    ├── alerts/        # Pending notifications
    ├── send_group     # Send group messages
    ├── send_private   # Send private messages
    └── ...            # Other API endpoints
```

### How It Works

1. **Browse Intuitively** — Navigate directories to explore groups and messages
2. **Read Operations** — Files contain data (JSON, text, etc.)
3. **Write Operations** — Writing to files triggers API calls
4. **Auto-Generated Docs** — `skill.md` files generated from endpoint definitions

### Example: Agent Workflow

```bash
# Check available groups
ls ~/.hermes/skills/napcat-cli/napcat/groups/

# Read messages from specific group
cat ~/.hermes/skills/napcat-cli/napcat/groups/1050866499/messages/

# Send a message
echo "Hello group!" > ~/.hermes/skills/napcat-cli/napcat/send_group
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

The daemon uses skills-fs config at `~/.hermes/skills-fs.json`:

```json
{
  "providers": [
    {
      "id": "napcat",
      "url": "http://127.0.0.1:18821"
    }
  ],
  "skillsRoot": "/home/ezra/.hermes/skills",
  "skills": [
    {
      "name": "napcat-cli",
      "description": "NapCat QQ bot messaging",
      "enabled": true,
      "bodyTemplate": "# NapCat CLI Skill\n..."
    }
  ],
  "mounts": [
    {
      "path": "/napcat/send_group",
      "kind": "api",
      "provider": "napcat",
      "read": "napcat_send_group_msg",
      "write": "napcat_send_group_msg"
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
- **Actions**: `get_events`, `get_alerts`, `clear_alert`, `napcat_*` (API proxy)
- **Format**: JSON request/response

## Development

### Project Structure

```
napcat-cli/
├── napcat              # Main CLI script
├── config.py           # Configuration management
├── lib/
│   ├── api.py         # NapCat HTTP API client
│   ├── events.py      # Event filesystem bridge
│   └── config.py      # Data directory paths
├── daemon/
│   └── watch.py       # WebSocket daemon + HTTP provider
├── skills-fs/         # Virtual filesystem (submodule)
└── persona.md         # Bot persona configuration
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
