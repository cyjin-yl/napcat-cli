# Agent Wake — Hermes (and other agents)

napcat-cli wakes an external agent when notable QQ events arrive, carrying a
contextual prompt. **Hermes is the default preset, not a hard dependency** — the
wake layer is generic (HTTP or shell), so any agent works.

## How a wake flows

```
QQ event → EventProcessor._wake(reason, event)
         → WakeOrchestrator.submit()      [debounce + cooldown + backlog tracking]
         → Waker.wake(prompt)             [HTTP first if configured, else CLI]
         → agent turn runs in session `wake_session` (e.g. napcat-qq)
```

Each step is logged to `daemon.log` with a `[WAKE]` prefix:

```
[2026-07-22 00:12:03] [WAKE] trigger reason=AT_ME who=Alice(123) where=group456 text='在吗'
[2026-07-22 00:12:03] [WAKE] queued reason=AT_ME pending=1 debounce=1.0s primary=auto
[2026-07-22 00:12:05] [WAKE] deliver reason=AT_ME transport=cli ok=True elapsed=2.1s :: exit=0
[2026-07-22 00:12:05] [WAKE] reply reason=AT_ME transport=cli: 在的，怎么了？
```

`daemon.log` is size-rotated (2 MB × 5) so it can't fill the disk.

## Transports

| Transport | Hermes target | Needs | Notes |
|-----------|---------------|-------|-------|
| `http` | `POST /api/sessions/{id}/chat` | `wake_http_url` + `wake_http_key` | Best latency, idempotent, injects into a live session; requires the API server enabled |
| `cli` | `hermes --continue <session> -z "<prompt>" --yolo --pass-session-id` | `hermes` on PATH | Zero infrastructure; spawns a one-shot process per wake |

`wake_primary=auto` (default) tries HTTP first when configured + reachable,
otherwise falls back to CLI. Force one with `napcat wake --transport http|cli`.

## Enable the Hermes HTTP API server (opt-in)

The API server is off by default. To use the HTTP transport:

1. Append to `~/.hermes/.env` (append-only — don't rewrite):
   ```dotenv
   API_SERVER_ENABLED=true
   API_SERVER_KEY=<random 64-hex>
   ```
2. Restart the gateway (systemd, passwordless sudo):
   ```bash
   sudo systemctl restart hermes-gateway.service
   ```
   This briefly interrupts the messaging platforms the gateway serves.
3. Configure napcat-cli:
   ```bash
   napcat config set wake_http_url http://127.0.0.1:8642
   napcat config set wake_http_key <same API_SERVER_KEY>
   # optional: pin a specific session id instead of resolving by name
   napcat config set wake_http_session_id <session-id>
   ```
   `napcat setup` does all three interactively when you answer "y" to the
   HTTP-enable prompt.

### Verified request shape

```bash
curl -X POST http://127.0.0.1:8642/api/sessions/$SESSION_ID/chat \
  -H "Authorization: Bearer $API_SERVER_KEY" \
  -H "Idempotency-Key: napcat-AT_ME-1700000000" \
  -H "Content-Type: application/json" \
  -d '{"input":"你在群里被 @ 了…请查看并回复。"}'
```

If `wake_http_session_id` is unset, the backend resolves it by name via
`GET /api/sessions`. Docs: <https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server>

## Event routing & timing

| Reason | Wake? | Timing |
|--------|-------|--------|
| `AT_ME`, `REPLY_TO_ME` | yes | near-immediate (debounce ≤1 s), **cooldown bypassed** |
| `GROUP_TRIGGER`, `PRIVATE_TRIGGER` | yes | debounced |
| `NEW_MESSAGE` (not @) | tracked only | a `NEW_MESSAGE_BACKLOG` wake fires if unread > `wake_new_message_idle_seconds` (default 600) |
| `NEW_FRIEND`, `NEW_REQUEST`, `BOT_BANNED`, `NEW_POKE`, `GROUP_ADMIN_CHANGE`, `NEW_GROUP_MEMBER`, `MY_MESSAGE_RECALLED`, `BOT_KICKED_FROM_GROUP`, `GROUP_DISBANDED`, `BOT_OFFLINE`, `HEALTH_CHECK_OFFLINE` | yes | debounced + cooldown-bounded |

Tuning keys: `wake_debounce_seconds` (default 3), `wake_cooldown_seconds`
(default 30), `wake_new_message_idle_seconds` (default 600).

## Troubleshooting

- **`napcat wake test`** — reports per-transport configured/reachable and whether
  `hermes` is on PATH.
- **No wake fires** — check `wake_enabled` is true and `wake_preset` ≠ `none`;
  grep `[WAKE]` in `daemon.log`. `disabled, skip` means `wake_enabled=false`.
- **HTTP 401 / unreachable** — `auto` falls back to CLI automatically; to use
  HTTP, confirm `API_SERVER_ENABLED=true` and the gateway was restarted.
- **Legacy `wake_command`** still runs as a last-resort escape hatch when no
  backend is configured (back-compat for `echo … >> .agent-wake` configs).
