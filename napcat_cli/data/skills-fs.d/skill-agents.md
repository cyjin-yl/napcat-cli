# NapCat CLI Skill — Agent Guide

This skill exposes a NapCat QQ bot through `skills-fs`, a virtual filesystem engine.
Before the filesystem is available, an external daemon must be started.

## Daemon requirements

- `skills-fs` Go binary (can be shipped with this skill)
- `watch.py` NapCat WebSocket listener / HTTP provider (Python daemon)
- NapCat server reachable via HTTP and WebSocket

## How to start

```bash
# 1. Start NapCat (e.g. via Docker)
docker compose -f NapcatDocker/docker-compose.yml up -d

# 2. Start the NapCat HTTP/WebSocket provider daemon
python3 napcat-cli/daemon/watch.py ~/.napcat-data/daemon.json

# 3. Start skills-fs FUSE
skills-fs fuse   --config ~/.napcat-cli/skills-fs.json   --mountpoint ~/.napcat-cli/skills/napcat-cli   --allow-other   --log-file ~/.napcat-cli/skills-fuse.log
```

## Filesystem layout

- `/SKILL.md` — skill documentation
- `/AGENTS.md` — this guide
- `/persona.md` — bot persona / system prompt
- `/napcat/` — API endpoints and data directories
  - `/napcat/send_group`, `/napcat/send_private` — write JSON to send messages
  - `/napcat/events/`, `/napcat/alerts/` — real-time events and notifications
  - `/napcat/groups/{group_id}/{recent,1days,7days,30days,90days}/{message_id}` — browse group messages; write to `/napcat/groups/{group_id}/send` to reply
  - `/napcat/friends/{user_id}/{recent,1days,7days,30days,90days}/{message_id}` — browse private messages; write to `/napcat/friends/{user_id}/send` to reply

Each directory contains an `AGENTS.md` explaining its purpose and parameters.

## Shipped-with-skills-fs option

This skill can be bundled with a pre-built `skills-fs` binary. Set the binary path in
`~/.napcat-data/daemon.json` under `skillsFs.binary` and start the daemon with
`napcat daemon start`.
