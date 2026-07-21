# NapCat CLI Skill (skills-fs mounted)

> ⚙️ **Mount state: skills-fs IS mounted.** This is the *generated* post-mount SKILL.md
> (skills-fs overwrote the static one when it mounted the `/napcat/` tree). You now have
> **two equivalent interfaces**: the `napcat` CLI below, and the `/napcat/` virtual
> filesystem. Either works; pick whichever fits. If the mount ever disappears, the CLI
> keeps working — see `references/mounting.md` for the FUSE D-state hazard & recovery.

Access QQ bot capabilities through the `napcat` CLI **or** the `/napcat/` FUSE tree.

## When to Use
- Send private or group messages, read events/alerts, manage groups/friends/message lifecycle.
- Query group/friend metadata; poke members; call raw API endpoints.

## Prerequisites
- NapCat daemon running, HTTP `/invoke` at `http://127.0.0.1:18821`, connected to NapCat WS (`:18800`).
- Bot online (`napcat status`).
- `skills-fs` FUSE mounted at the skill dir (this state) for the filesystem interface.

---

# Interface A — the `napcat` CLI (no mount required)

All operations are CLI commands. Most useful when you don't want to touch the FUSE tree.

```bash
napcat status                      # bot online?
napcat events                      # recent events (JSON)
napcat alerts                      # pending alerts
napcat send private <uid> -m "hi"
napcat send group <gid> -m "hi" --at <uid>
napcat reply <msg_id> -m "回复"
napcat group list | members <gid> | info <gid>
napcat friend list
napcat daemon status               # daemon + recent log
```

### Read-only
`napcat status` · `napcat events [--since T] [--type X]` · `napcat alerts [--clear]` · `napcat msg <id>` · `napcat group list/members/info/essence <gid>` · `napcat friend list` · `napcat file list-group <gid>`

### Sending
`napcat send group|private <id> -m "..." [--at <uid>] [--image <path>] [--file <path>]` · `napcat reply <id> -m "..."` · `napcat recall <id> [--group <gid>]`

### Group ops
`napcat group {poke|mute|unmute|kick|admin|rename|remark|announce|set_essence} <gid> ...`

### Friend ops / lifecycle / raw
`napcat friend {list|remark|info|delete} ...` · `napcat send_poke` · `napcat send_forward` · `napcat api <endpoint> [-d JSON]`

### Wake / debug
`napcat wake test` · `napcat wake --dry-run` · `napcat wake --reason AT_ME --prompt "..."`

### Persona
On QQ, reply in persona — load `~/.napcat-data/skills/persona.md` first. Do **not** say "我是AI".

---

# Interface B — the `/napcat/` FUSE tree (this mount)

Read an API file to fetch data; write JSON to a write-enabled file to act. Written JSON is
forwarded as provider params.

```bash
cat napcat/events                       # read recent events
echo '{"group_id":123456,"message":"hi"}' > napcat/send_group   # send group msg
echo '{"alert_name":"NEW_MESSAGE"}' > napcat/clear_alert         # clear an alert
```

### Send / message mgmt
`send_group`=send_group_msg · `send_private`=send_private_msg · `delete`=delete_msg · `get_events` · `get_alerts` · `clear_alert` · `clear_all_alerts`

### Group management (write JSON params)
`group_kick` · `group_ban` · `group_admin` · `group_name` · `group_leave` · `group_essence` · `group_members`(read) · `group_info`(read) · `group_honor`(read) · `group_forward`

### Friend / system
`friend_list` · `friend_add` · `friend_info` · `status` · `cookie` · `bkn` · `get_image` · `ocr` · `approve_request` · `reject_request`

### Per-conversation directories
`napcat/groups/{group_id}/{recent|1days|7days|30days|90days}/{message_id}/` — browse messages;
write to `napcat/groups/{group_id}/send` to reply. Same shape under `napcat/friends/{user_id}/`.

## Procedure (FUSE)
1. Pick the action file (e.g. `napcat/send_group`).
2. Read for data; write JSON params for actions. Read back to verify.
3. For group/friend message browse, use the per-conversation directories above.

## Pitfalls
- **CLI vs FUSE poke**: `napcat group poke` works; the HTTP `send_group_poke` often doesn't. Prefer the CLI for poke.
- **Offline**: check `napcat status` / read `napcat/status` before API calls. If offline, `napcat daemon start`.
- **Rate limits**: `send_like` → error 1400 = rate-limited; wait ≥30s. >~10 msg/min to one target may throttle.
- **Recall**: own messages ≤2 min; others' ≤20 s (admin/owner exempt).
- **events file**: only recent ~50 in memory; `alerts` keeps fuller history. Newest-first; use `?limit=N` (e.g. `cat napcat/events?limit=20`) — note the daemon defaults to a small result set.
- **Files/images**: local paths need `file:///` prefix; `http(s)://` and `base64://` also OK.
- **Write payload**: write JSON params only (`{"group_id":"123","message":"hi"}`), NOT the full OneBot envelope — the provider wraps it. Non-JSON writes fail.
- **Unsupported APIs**: `retcode:200` + "不支持的Api" → feature unavailable on this NapCat build (e.g. `send_group_notice`, `get_essence_list`).
- **Kernel errors**: `NodeIKernel...` = kernel timeout (bot may be offline); `ERR_NEED_MAKEUP` = QQ risk control; `ERR_NOT_GROUP_ADMIN` / `ERR_NOT_IN_GROUP` / `ERR_SEND_MSG_FREQ_LIMIT` are self-explanatory.
- **Mount lost (ENOTCONN)**: fall back to the CLI; remount via `napcat daemon restart` (skills-fs remounts).

## Verification
- `napcat status` / `cat napcat/status` → online.
- `cat napcat/events` → events returned.
- Write `napcat/send_group` → message appears in the target group.
