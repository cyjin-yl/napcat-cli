---
name: napcat-cli
description: NapCat QQ bot messaging — send messages, read events, manage groups, handle friends via CLI
version: 2.0.0
author: Ezra
license: MIT
platforms: [linux]
allowed-tools:
  - terminal
  - read_file
  - write_file
metadata:
  category: messaging
  source: napcat-cli
---

# NapCat CLI Skill

> ⚙️ **Mount state: skills-fs NOT mounted (you are reading the static/pre-mount SKILL.md).**
> The `napcat` CLI below works **fully without any mount** — it talks directly to the
> NapCat daemon. Optionally, `skills-fs` can overlay this skill directory with a
> virtual `/napcat/` filesystem (read events/send messages as files). That mount is
> **currently disabled for stability** and has known gotchas (FUSE can wedge the
> daemon into uninterruptible D-state). See `references/mounting.md` before enabling it.
> When mounted, skills-fs serves its own (post-mount) SKILL.md describing the `/napcat/` tree.

Access QQ bot capabilities via the `napcat` CLI tool (Python-based, installed on PATH). No FUSE mount required — commands talk directly to the NapCat daemon.

## When to Use

Use this skill when you need to interact with a QQ bot instance:
- Send private or group messages.
- Read recent bot events, pending alerts, or status.
- Manage groups, friends, and message lifecycle.
- Query group or friend metadata.
- Poke members, manage group files, or call raw API endpoints.

## Prerequisites

- `napcat` CLI is installed (on PATH after `uv tool install napcat-cli` or pip install).
- NapCat daemon is running (check with `napcat daemon status`).
- NapCat Docker container is up (WS on `:18800`, HTTP on `:18801`).
- Bot is logged in and online (check with `napcat status`).

## How to Run

All operations are CLI commands. No FUSE mount needed.

```bash
# Check status
napcat status

# Read events and alerts
napcat events
napcat alerts

# Send messages
napcat send private <user_id> -m "message"
napcat send group <group_id> -m "message"

# Poke a group member
napcat group poke <group_id> <user_id>

# Read group/friend lists
napcat group list
napcat group members <group_id>
napcat friend list

# Manage daemon
napcat daemon start
napcat daemon stop
napcat daemon status
```

## Quick Reference

### Read-Only Commands

| Command | Description |
|---------|-------------|
| `napcat status` | Bot login status |
| `napcat events` | Recent 50 events from cache (JSON array) |
| `napcat alerts` | Pending alert categories |
| `napcat friend list` | Friend list with metadata |
| `napcat group list` | Bot's group membership |
| `napcat group members <gid>` | Members of a group |
| `napcat group info <gid>` | Group info |
| `napcat group essence <gid>` | Essence messages |
| `napcat file list-group <gid>` | Group file list |

### Sending Messages

| Command | Description |
|---------|-------------|
| `napcat send private <uid> -m "msg"` | Send private message |
| `napcat send group <gid> -m "msg"` | Send group message |
| `napcat send group <gid> --at <uid> -m "msg"` | @ someone in group |
| `napcat send <type> <target> --image <path>` | Send image (PIL-generated, local file, or URL) |
| `napcat send <type> <target> --file <path>` | Send file |

### Group Operations

| Command | Description |
|---------|-------------|
| `napcat group poke <gid> <uid>` | Poke a member ✅ |
| `napcat group mute <gid> <uid> [seconds]` | Mute a member |
| `napcat group unmute <gid> <uid>` | Unmute |
| `napcat group kick <gid> <uid>` | Kick a member |
| `napcat group admin <gid> <uid> [set/remove]` | Set/remove admin |
| `napcat group rename <gid> <uid> <card>` | Set group card |
| `napcat group remark <gid> <remark>` | Set group remark |
| `napcat group announce <gid> -m "msg"` | Send announcement |

### Friend Operations

| Command | Description |
|---------|-------------|
| `napcat friend info <uid>` | User info |
| `napcat friend remark <uid> <remark>` | Set remark |
| `napcat friend add <uid>` | Send friend request |
| `napcat friend delete <uid>` | Remove friend |

### Message Lifecycle

| Command | Description |
|---------|-------------|
| `napcat recall <msg_id> --group <gid>` | Recall group message |
| `napcat recall <msg_id>` | Recall private message |

### Raw API Access

| Command | Description |
|---------|-------------|
| `napcat api <endpoint>` | Call raw NapCat HTTP API |
| `napcat api <endpoint> -o value` | Extract value field |
| `napcat api <endpoint> -o raw` | Raw JSON output |

### Utility

| Command | Description |
|---------|-------------|
| `napcat daemon start/stop/status` | Manage watch daemon |
| `napcat config get <key>` | Read config key |
| `napcat config set <key> <value>` | Set config key |
| `napcat translate --from zh --to en "text"` | QQ translation (may not work) |
| `napcat ocr <image_path>` | OCR on image |
| `napcat fs` | Show skills-fs FUSE mount status |

### Setup, Wake, and Configuration

| Command | Description |
|---------|-------------|
| `napcat setup` | Interactive wizard: configure NapCat connection, skills-fs, and wake agent |
| `napcat setup --non-interactive` | Non-interactive setup with all defaults (useful in scripts) |
| `napcat setup --yes` | Auto-accept all prompts without confirmation |
| `napcat wake [--reason R] [--prompt P] [--transport T]` | Wake the agent (HTTP/CLI, auto-fallback); daemon also calls it automatically on events |
| `napcat wake test` / `napcat wake sessions` | Probe transports / list Hermes sessions |
| `napcat wake --dry-run` | Render the HTTP request + CLI command without executing |
| `napcat config get <key>` | Read a config key (api_url, token, wake_command, etc.) |
| `napcat config set <key> <value>` | Set a config key |

## Setup Command (`napcat setup`)

Runs an interactive wizard that configures:
1. **NapCat connection** — API URL (default `http://127.0.0.1:18801`) and token (validated against the running instance).
2. **Data directory** — default `~/.napcat-data`.
3. **skills-fs** — mountpoint, config path, binary detection. Guides you to build or download the Go binary if missing.
4. **Wake agent** — choose `hermes` (default), `custom`, or `none`. Hermes uses the CLI one-shot transport (`hermes --continue <session> -z …`) by default; the HTTP API server is opt-in. Wake is pluggable — any HTTP endpoint or shell command works.
5. **Install skill** — copies this SKILL.md into `~/.hermes/skills/napcat-cli/` for Hermes to discover.

Use `--non-interactive` to skip all prompts (uses defaults). Use `--yes` to auto-confirm actions.

## Agent Wake (`napcat wake`)

text, counts), so read the prompt before re-querying.
179:
180:- `AT_ME` / `REPLY_TO_ME` / `DM_ME` wake you near-immediately and bypass cooldown —
181:  these expect a prompt reply (`DM_ME` = someone sent you a private/DM message).
182:  Use `napcat reply <id>` or `napcat send`/`napcat reply` 回复。
183:- `NEW_MESSAGE_BACKLOG` means unread messages piled up — scan `napcat events` /
184:  `napcat alerts` 和回复。
185:
186:## 图片与 OCR 技能使用指南
186:
187:### 图片消息识别
187:当收到图片消息时，Wake prompt 会自动包含图片元数据：
187:- `file_id`: 图片文件 ID，可用于下载
187:- `url`: 图片下载链接
187:- `sub_type`: 图片类型（0=普通图片, 1=动画表情, 7=赞/点赞等）
187:- `file_size`: 文件大小（字节）
187:- `summary`: 图片摘要/描述（如有）
187:- `url`: 图片下载链接
187:
187:### 可用技能：图片处理
187:- **下载图片**: 写入 `/napcat/get_image` 文件，内容为 JSON `{"url": "图片URL"}`，即可下载图片到本地
187:- **OCR 识图**: 写入 `/napcat/ocr` 文件，内容为 JSON `{"image": "file:///path/to/image.png"}` 或 `{"image": "https://example.com/img.jpg"}` 或 `{"image": "base64数据"}`，即可进行 OCR 识别
187:- **获取消息媒体内容**: 读取 `/napcat/groups/:group_id/:time_range/:message_id/:content` 可获取消息中的媒体内容（图片/视频/文件）
187:- **回复图片消息**: 写入 `/napcat/groups/:group_id/:time_range/:message_id/reply/image`，内容为本地图片路径
187:- **发送图片**: 写入 `/napcat/groups/:group_id/send/image` 或 `/napcat/friends/:user_id/send/image`，内容为本地图片路径
187:
187:### 回复链追踪
187:当消息包含 `reply` 段时，Wake prompt 会包含 `[回复链] 回复消息ID: xxx`。
187:可通过 `/napcat/groups/:group_id/:time_range/:message_id` 查看被回复消息的详细内容。
187:
187:### 最佳实践
187:1. **收到图片消息**: 先读取 `url` 或 `file_id`，如需识别文字请用 `/napcat/ocr`
187:2. **追踪回复链**: 看到 `[回复链] 回复消息ID: xxx` 时，可读取对应消息上下文
187:3. **下载图片**: 写入 `/napcat/get_image` 配合 `url` 参数
187:4. **OCR 识图**: 支持本地文件 `file:///path`、URL、base64 格式
187:4. **发送/回复图片**: 使用 `/napcat/groups/:group_id/send/image` 或 `/napcat/friends/:user_id/send/image`
187:
187:### Wake Prompt 中的图片信息格式
187:Wake prompt 中会自动包含类似信息：
187:`[图片信息] [图片: 摘要: 测试, file_id: abc.jpg, url: http://example.com/img.jpg, sub_type: 0, size: 12345]`
187:
187:### 回复链信息格式
187:回复链信息会显示：
187:`[回复链] 回复消息ID: 12345`
187:
187:Agent 遇到图片消息时，应主动考虑：
187:1. 是否需要 OCR 识别文字内容？
187:2. 是否需要下载图片进行视觉分析？
187:3. 是否有回复链需要追踪上下文？
187:4. 是否需要下载图片进行进一步处理？
187:
187:---
187:
187:## TUI 图片显示（实验性）
187:配置 `tui.show_images: true` 可在 TUI 中显示图片预览（需终端支持图片协议，如 Kitty、iTerm2、WezTerm）。
187:配置示例：
187:```yaml
187:tui:
187:  show_images: true
187:```
187:
187:---
187:
187:## TUI 快捷键
187:| 快捷键 | 功能 |
187:|--------|------|
187:| `Escape` | 退出/返回 |
187:| `F5` | 刷新 |
187:| `Up/Down` | 上/下翻页 |
187:| `PageUp/PageDown` | 翻页 |
187:| `Enter` | 打开聊天/发送消息 |
187:| `Tab` | 命令补全 |
187:
187:## 技能开发
187:技能目录结构：
187:```
187:skill_name/
187:├── SKILL.md        # 技能说明（必需）
187:├── main.py         # 入口脚本（可选）
187:└── requirements.txt # 依赖（可选）
187:```
187:
187:技能通过 `skills-fs` 挂载到 `/napcat/skills/` 目录，Agent 可直接读取和执行。
187:
187:---
187:
187:## 常见问题
187:Q: 为什么收到图片消息只显示 `[图片]`？
187:A: 旧版本只显示 `[图片]`，新版本会自动包含 `file_id`、`url`、`sub_type`、`file_size` 等元数据。请确保使用最新版本。
187:
187:Q: 如何识别图片中的文字？
187:A: 使用 `/napcat/ocr` 技能，写入 JSON `{"image": "file:///path/to/image.png"}` 或 `{"image": "https://example.com/img.jpg"}`。
187:
187:Q: 如何下载图片？
187:A: 写入 `/napcat/get_image`，内容为 `{"url": "图片URL"}`。
187:
187:Q: 如何追踪回复链？
187:A: Wake prompt 中会显示 `[回复链] 回复消息ID: xxx`，可通过 skills-fs 读取对应消息。
187:
187:Q: 为什么会有大量 wake up？
187:A: AT_ME/DM_ME/REPLY_TO_ME 绕过冷却，可能导致频繁唤醒。可在配置中调整 `debounce_seconds` 和 `cooldown_seconds`。
187:
187:---
187:
187:## 版本历史
187:- v2.1.0: 新增 DM_ME 唤醒理由，支持私聊立即唤醒
187:- v2.1.0: Wake prompt 包含图片元数据、回复链、技能提示
187:- v2.1.0: 新增 skills-fs 挂载点 `/napcat/get_image` (下载)、`/napcat/ocr` (OCR)
187:
- `NEW_MESSAGE_BACKLOG` means unread messages piled up — scan `napcat events` /
  `napcat alerts` and reply to anything worth replying to.
- Other events (`NEW_FRIEND`, `NEW_REQUEST`, `BOT_BANNED`, `NEW_POKE`, …) are
  debounced; you'll perceive them within a reasonable window.

Wake is **pluggable** (HTTP API server or CLI one-shot, auto-fallback); Hermes is
the default preset but not required. Manual / debug:

```bash
napcat wake                            # manual wake, contextual default prompt
napcat wake --reason AT_ME --prompt "…"
napcat wake --dry-run                  # render HTTP request + CLI command
napcat wake test                       # per-transport reachability
napcat wake sessions                   # list Hermes sessions (HTTP backend)
grep '\[WAKE\]' ~/.napcat-data/daemon.log   # when/why/how wakes fired + your replies
```

### Proactive speaking (replying to QQ)

When a wake tells you to act, reply using the CLI (no FUSE mount needed):

```bash
napcat reply <message_id> -m "回复内容"             # quote-reply in the same chat
napcat send group <group_id> -m "…" --at <user_id>  # @ someone in a group
napcat send private <user_id> -m "…"
```

Load `~/.napcat-data/skills/persona.md` first to reply in persona on QQ.

## Config Command (`napcat config`)

Reads and writes configuration stored in `~/.napcat-data/config.json`.

- `napcat config get <key>` — prints the current value of a config key.
- `napcat config set <key> <value>` — updates a config key.

Common keys: `api_url`, `token`, `wake_command`, `wake_on_event`, `ws_port`, `http_port`, `group_trigger_word`, `private_trigger`, `skills_fs_enabled`, `skills_fs_mountpoint`, `skills_fs_binary`, `skills_fs_config`.

## Platform Identity — Persona (吉他张 CartierRR)

When replying to QQ messages via the napcat-cli, **load the persona file first**:

```
~/.napcat-data/skills/persona.md
```

You become 吉他张 (CartierRR) — NOT an AI assistant. Use the persona's tone, slang (法, 中英混杂), and emotional style. Do NOT say "我是AI", "请问", "谢谢" on QQ.

**Activate persona when:**
- Replying to messages received via NapCat platform
- User asks you to interact in QQ groups in a joking/cosplay capacity from another platform
- There are @噗噗 interactive messages in group chat
- Someone makes a joke, teases, or plays with the bot — respond in persona

**Do NOT activate persona on:** Weixin, Discord, Web, and other non-QQ Hermes channels.

## Procedure

1. **Check status first** — `napcat status` to verify the bot is online.
2. **Check daemon** — `napcat daemon status` to verify the daemon is running.
3. **Execute the command** — use the appropriate `napcat` subcommand.
4. **Verify** — read events or alerts to confirm the action succeeded.

## Pitfalls

### Recall Messages Time Out

`napcat recall` may return "NapCat 内核响应超时" (kernel timeout). This is a NapCat kernel issue, not a bot-offline issue. The bot stays online (verify with `napcat status`). Workaround: retry after a delay or use the raw API directly.

### Translate Not Supported

`napcat translate` fails with "API 'qq_translate' is not supported by this NapCat instance". NapCat needs a translation plugin for this. Do not retry — it's a feature gap, not a bug.

### Poke via CLI Works, HTTP API Doesn't

`napcat group poke` works. The same operation via HTTP API (`send_group_poke`) fails. Always use the CLI for poke operations.

### Daemon Subcommands

`napcat daemon` only supports `start`, `stop`, and `status`. There is no `log`, `restart`, or `reload`. To restart, stop then start.

### Config Keys

`napcat config get/set` works with internal NapCat config keys, not environment variables. `NAPCAT_API_URL` etc. are env vars, not config keys.

### Events Cache

`napcat events` returns the last 50 cached events from memory. Most are heartbeat meta_events. Filter them:

```python
events = json.loads(subprocess.check_output(["napcat", "events"]))
messages = [e for e in events if e.get('post_type') != 'meta_event']
```

### Alerts

`napcat alerts` shows pending alert categories. To read actual alert data, read the alert files directly from `~/.napcat-data/alerts/`:
- `NAPCAT_CLI_NEW_MESSAGE.alert` — new messages
- `NAPCAT_CLI_NEW_POKE.alert` — poke events
- `NAPCAT_CLI_NOTICE.alert` — system notices
- `NAPCAT_CLI_NEED_WAKE_UP.alert` — wake-up triggers

### Message Sending

- `napcat send` returns the `message_id` on success (e.g., `Sent message_id=123456`).
- Use `--at <uid>` to @ someone in group messages.
- Use `--image` or `--file` for media. For local paths, the CLI handles them directly (no `file:///` prefix needed via CLI).

### Rate Limits

- `send_like` (via `napcat group liking`) is rate-limited by QQ. Error 1400 = rate limit.
- Rapid message sending may be throttled. Space out messages.

### Message Length Limits

QQ has a maximum message length (~200 chars). Long text (e.g., command output, logs) will be truncated or fail to send. Workarounds:
- Generate an image with PIL and send via `--image`
- Save to file and send via `--file`
- Split into multiple shorter messages

### Error Messages

- "NapCat 内核响应超时" = kernel-level timeout. Bot is likely online but the kernel is slow to respond. Retry or move on.
- "NodeIKernel" in error = kernel-level issue. Check daemon and bot status.

### Offline Detection

Before issuing API calls, check `napcat status`. If offline:
1. Check daemon: `napcat daemon status`
2. If daemon not running, start it: `napcat daemon start`
3. If daemon running but bot offline, NapCat Docker container may need restart

### skills-fs FUSE Mount

A FUSE mount is available at `~/.napcat-data/skills` (check with `napcat fs`).
It provides a virtual filesystem view of all napcat operations, alerts, events,
and per-group/per-friend message directories. The CLI is the primary interface;
the FUSE mount is supplementary and useful for Agent filesystem access patterns.

## Verification

- `napcat status` — confirms bot is online.
- `napcat daemon status` — confirms daemon is running.
- `napcat events` — shows recent events (filter out heartbeats).
- `napcat alerts` — shows pending alerts.
- After sending, check the returned `message_id`.
- After group operations, re-read `napcat group members <gid>` to verify changes.
