# napcat-cli configuration help

Operator guide for `~/.napcat-data/config.json`, `daemon.json`, OneBot11, wake,
OCR, and skills-fs. Secrets stay out of git — only examples ship in-repo.

```bash
napcat config help          # this guide (also: napcat config --help)
napcat config show          # current values (token/key redacted as "(set)")
napcat config get <key>
napcat config set <key> <value>
# After changing connection/wake keys that the daemon reads:
napcat daemon stop && napcat daemon start
```

User-state files (gitignored via `.napcat-data/`):

| File | Role |
|------|------|
| `~/.napcat-data/config.json` | `NapCatAPI` + CLI + source for `napcat daemon start` rewrite |
| `~/.napcat-data/daemon.json` | Runtime input for `watch.py` (rewritten on every `daemon start`) |
| `~/.napcat-data/skills-fs.json` | FUSE map (often a symlink to the package template) |
| `~/.hermes/.env` | Hermes `API_SERVER_*` (wake HTTP bearer) |

In-repo examples (placeholders only):

- `napcat_cli/data/config.json.example`
- `napcat_cli/data/daemon.json.example`
- `napcat_cli/data/onebot11.example.json`

---

## 1. OneBot11 must expose HTTP + WebSocket

NapCat can be **QQ-online** while OneBot11 has **empty** `httpServers` /
`websocketServers`. Then host ports accept TCP but handshake/API returns
`Connection reset` / `token验证失败` / CLI prints `Not logged in`.

Container file: `/app/napcat/config/onebot11_<self_id>.json`

Minimum (see `onebot11.example.json`):

- HTTP server `0.0.0.0:3000` with a non-empty `token`
- WebSocket server `0.0.0.0:3001` with the **same** `token`
- Optional: `webui.json` → `"autoLoginAccount": "<self_id>"` so container
  restart uses quick login instead of a new QR

After editing, restart the NapCat container and confirm logs show both servers
started.

---

## 2. Docker host↔container port cross-mapping

Many installs publish:

```text
host 18800 → container 3000  (OneBot11 HTTP)
host 18801 → container 3001  (OneBot11 WebSocket)
```

That is the **opposite** of older napcat-cli mental defaults
(`api_url …18801`, `ws_port 18800`). Always check:

```bash
docker port napcat
# or: ss -ltnp | grep -E '18800|18801'
```

Then:

```bash
napcat config set api_url  http://127.0.0.1:18800   # host HTTP
napcat config set ws_url   ws://127.0.0.1:18801     # host WS (overrides ws_port)
napcat config set token    <same as OneBot11 token>
napcat daemon stop && napcat daemon start
```

`ws_url` is preferred when set; otherwise the daemon builds
`ws://127.0.0.1:{ws_port}`.

---

## 3. WebSocket auth is `?access_token=` (not a connect frame)

NapCat validates the token on the **upgrade URL**:

```text
ws://HOST:PORT/?access_token=TOKEN
```

Sending a client JSON frame `{"post_type":"connect","token":…}` is **too late**.
Without the query param you get retcode `1403` / `token验证失败` and a ~5s
reconnect loop — events may appear once per reconnect, but **AT_ME / DM_ME
never stay connected long enough for a reliable wake**.

The daemon appends `access_token` automatically from `token` /
`NAPCAT_TOKEN` and redacts it in logs (`access_token=***`).

---

## 4. Agent wake: HTTP recommended, CLI legacy

### Reply policy by wake reason

| Reason | Must send on QQ? |
|--------|------------------|
| `AT_ME`, `REPLY_TO_ME`, `DM_ME` | **Yes** — Hermes session text is **invisible** to the QQ user; refuse/refute only counts if sent via napcat-cli or skills-fs |
| backlog / poke / friend / notices / etc. | Optional — may ignore |


| Transport | When | Notes |
|-----------|------|--------|
| **HTTP** | **Default / recommended** (`wake_primary=http`) | `POST {wake_http_url}/api/sessions/{id}/chat` with Bearer key. Synchronous agent turn (often 30–60s). |
| **CLI** | **LEGACY / 不推荐** | `hermes --continue … -z …`. Can `exit=0` without a real reply (`Input is not a terminal`). |
| `auto` | HTTP first if configured, else CLI fallback | Prefer explicit `http` in production. |

Required for HTTP (Hermes API server):

```bash
# ~/.hermes/.env
API_SERVER_ENABLED=true
API_SERVER_KEY=<secret>

napcat config set wake_primary http
napcat config set wake_http_url http://127.0.0.1:8642
napcat config set wake_http_key <same API_SERVER_KEY>
napcat config set wake_session napcat-qq   # resolved via GET /api/sessions
napcat daemon stop && napcat daemon start

napcat wake test
# expect: primary=http and [OK] http reachable
```

`napcat daemon start` persists `wake_http_key` into `daemon.json` and also
exports `NAPCAT_WAKE_HTTP_KEY` / `HERMES_API_KEY` into the daemon process env
so the key need not live only on disk in one place.

**Do not** treat CLI `exit=0` as “bot replied on QQ”. Grep:

```bash
grep '\[WAKE\]' ~/.napcat-data/daemon.log | tail -20
# Good: transport=http … POST …/chat -> 200
# Bad:  transport=cli detail=exit=0   (legacy; often no QQ reply)
```

More detail: [`docs/HERMES_WAKE.md`](./HERMES_WAKE.md).

---

## 5. PaddleOCR “not installed” under the daemon

PaddleOCR is often installed only in a **project venv** (e.g.
`napcat-cli/.test-venv`, ~700MB+), while:

```bash
# ~/.local/bin/napcat
exec env PYTHONPATH=/home/ezra/napcat-cli /usr/bin/python3 -m napcat_cli.cli "$@"
```

runs the daemon on **system** `/usr/bin/python3` (PEP 668 — no system pip
install). Symptom in `daemon.log`:

```text
PaddleOCR not installed: No module named 'paddleocr'
OCR requested but PaddleOCR not available
```

Mitigations (first match wins in `napcat_cli/lib/ocr.py`):

1. `NAPCAT_OCR_SITE_PACKAGES=/path/to/site-packages`
2. `NAPCAT_VENV` / `VIRTUAL_ENV` pointing at a venv that has paddleocr
3. Auto-detect repo-local `.test-venv` / `.venv` next to the source tree

Verify with the same interpreter the daemon uses:

```bash
/usr/bin/python3 -c "import sys; sys.path.insert(0,'.'); from napcat_cli.lib.ocr import get_ocr_instance; print(bool(get_ocr_instance()))"
```

---

## 6. skills-fs provider URL (HTTP port)

The daemon HTTP provider listens on `http_port` (config key, default **18821**).
skills-fs calls that provider; the packaged `skills-fs.json` uses:

```json
"providers": [{ "id": "napcat", "url": "${NAPCAT_PROVIDER_URL}" }]
```

`napcat daemon start` exports:

- `NAPCAT_HTTP_PORT=<http_port>`
- `NAPCAT_PROVIDER_URL=http://127.0.0.1:<http_port>/invoke`

so changing the port is only:

```bash
napcat config set http_port 18824
napcat daemon stop && napcat daemon start
```

Do **not** hardcode host-only ports into the tracked template.

### D-state port holders (auto-rebind)

Linux processes stuck in **D-state** (uninterruptible sleep, often after a hung
FUSE op) cannot be `kill -9`'d and may keep a TCP listen forever until reboot.
`napcat daemon start` detects this without touching the D-state process or its
mounts:

1. If `http_port` is free → use it.
2. If held only by D-state / defunct napcat watchers → **auto-pick a free port**,
   `config set` it into `config.json`, export `NAPCAT_PROVIDER_URL`, start.
3. If held by a **healthy** process → refuse (stop that daemon first).

You should see:
```text
Warning: http_port 18823 is blocked (D-state PID(s) [67107] …). Auto-rebound to 18824 …
```
Live traffic on another port is not interrupted. Clearing D-state still needs a reboot.


Override either env var manually if the provider is remote.

## 7. skills-fs “degraded” while FUSE actually works

Healthy mount still had:

```text
LOOKUP "status" → no such file or directory
skills-fs: mount not healthy after spawn — killing child
exceeded max restarts (3), degraded
```

when the health probe looked for **root** `status`. The real status file is
**`napcat/status`**. Current probe prefers `/proc/mounts` (never blocks on
FUSE), then `stat(mountpoint)`, then optional `napcat/status`.

```bash
mountpoint ~/.hermes/skills/napcat-cli
ls ~/.hermes/skills/napcat-cli/napcat/status
grep 'skills-fs:' ~/.napcat-data/daemon.log | tail
```

---

## 8. Config keys (`napcat config get/set`)

These are **dataclass fields**, not env var names. Env overrides still work for
several connection fields (`NAPCAT_API_URL`, `NAPCAT_TOKEN`, …).

| Key | Meaning |
|-----|---------|
| `api_url` | OneBot11 HTTP base (host side) |
| `token` | OneBot11 access token (HTTP + WS) |
| `ws_port` / `ws_url` | WS host port or full URL |
| `http_port` | napcat-cli HTTP **provider** port (skills-fs), default historically 18821/18823 |
| `self_id` | Bot QQ; empty disables AT_ME until healed from login |
| `wake_primary` | `http` (default) \| `auto` \| `cli` (legacy) |
| `wake_http_url` / `wake_http_key` / `wake_http_session_id` | HTTP wake |
| `wake_session` | Session name for HTTP resolve / CLI `--continue` |
| `wake_cli_command` | Legacy CLI template (not recommended) |
| `group_trigger_word` / `private_trigger` | Wake routing |
| `skills_fs_*` | FUSE binary / mountpoint / config |

`napcat daemon start` rewrites `daemon.json` from `config.json` — set values
with `napcat config set` so they survive restarts.

---

## 9. Quick checklist after a broken night

```bash
# A. QQ + OneBot
napcat status                          # login + online
docker logs napcat 2>&1 | tail -50     # WS/HTTP "已启动"?

# B. Ports + token
docker port napcat
napcat config show | egrep 'api_url|ws_|token|wake_'

# C. Daemon + wake
napcat daemon status
grep '\[WAKE\]\|Connected to\|skills-fs:' ~/.napcat-data/daemon.log | tail -30
napcat wake test

# D. FUSE + OCR
mountpoint ~/.hermes/skills/napcat-cli
/usr/bin/python3 -c "import sys; sys.path.insert(0,'/path/to/napcat-cli'); from napcat_cli.lib.ocr import get_ocr_instance; print(bool(get_ocr_instance()))"
```

---

## 10. Security notes

- Never commit real `token`, `wake_http_key`, or `API_SERVER_KEY`.
- Examples use `REPLACE_ME` / empty strings only.
- Daemon logs redact WS `access_token`; `config show` prints `(set)` for keys.


## Identity labels in wake prompts

All wake/alert text that names a group or person uses one builder
(`napcat_cli.lib.identity`):

| Entity | Format | Example |
|--------|--------|---------|
| Group | `(群备注)(群名)(群号)` | `()(LUG @ YSU \| 那一刻就像看到原子弹爆炸)(201644592)` |
| Person | `(群名片)(用户昵称)(QQ号)` | `([hubot] --dangerously-skip-permissions)(復州河野鳥)(1293883574)` |

Empty fields keep the parentheses so the triple is always parseable.


## 11. napcat auth commands

| Command | Description |
|---------|-------------|
| `napcat auth` | Check login + online status |
| `napcat auth qr` | Fetch QR code (ASCII art in terminal + URL + image file) |
| `napcat auth quick-login <qq>` | Set autoLoginAccount in webui.json, restart NapCat |
| `napcat auth set-password <qq> <password>` | Save QQ password for auto quick-login (gitignored config) |
| `napcat auth recreate` | Recreate Docker container with ACCOUNT + NAPCAT_QUICK_PASSWORD env |

Password is stored only in `~/.napcat-data/config.json` (gitignored). It is
passed as a Docker env var at container creation time. Never written to
tracked files. `config show` redacts it as `(set)`.
