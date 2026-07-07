#!/usr/bin/env python3
"""Generate the napcat-cli mounts JSON for skills-fs.

Reads ACTION_SCHEMAS from daemon/schemas.py and produces a complete mounts
array that is written to both canonical and skill-copy locations.

Usage:
    python3 tools/gen_mounts.py [--dry-run]

With --dry-run, prints JSON to stdout without writing files.
"""

import json
import os
import sys
from pathlib import Path

# Load ACTION_SCHEMAS from daemon/
sys.path.insert(0, str(Path(__file__).parent.parent / "daemon"))
from schemas import ACTION_SCHEMAS  # noqa: E402


def schema_json(action):
    """Return the JSON string for a schema entry."""
    s = ACTION_SCHEMAS.get(action, {})
    return json.dumps(s)


def make_api(path, read_action="", write_action="", mode="0644",
             write_params="json", provider="napcat", writeback=False):
    """Build an API mount entry."""
    entry = {
        "path": path,
        "kind": "api",
        "provider": provider,
    }
    if read_action:
        entry["read"] = read_action
    if write_action:
        entry["write"] = write_action
        entry["mode"] = mode
        entry["writeParams"] = write_params
    if not write_action:
        entry["mode"] = "0444"
    if writeback and write_action:
        entry["writeback"] = True
        entry["schema"] = schema_json(write_action)
    return entry


def make_blob(path, data, mode="0444"):
    """Build a blob mount entry."""
    return {
        "path": path,
        "kind": "blob",
        "mode": mode,
        "data": data,
    }


def make_dir(path, mode="0755"):
    """Build a dir mount entry for intermediate directories.
    Required because router.match() (used by Stat) requires n.mount != nil."""
    return {
        "path": path,
        "kind": "dir",
        "mode": mode,
    }


def make_dynamic_dir(path, read_action, provider="napcat"):
    """Build a dynamic_dir mount entry."""
    return {
        "path": path,
        "kind": "dynamic_dir",
        "read": read_action,
        "provider": provider,
    }


# ---------------------------------------------------------------------------
# Per-group operations (under groups/:group_id/)
# ---------------------------------------------------------------------------
GROUP_OPS = [
    ("card", "set_group_card", "set_group_card"),
    ("name", "set_group_name", "set_group_name"),
    ("leave", "group_leave", "group_leave"),
    ("info", "napcat_get_group_info", ""),
    ("members", "napcat_get_group_member_list", ""),
    ("essence_list", "napcat_get_essence_list", ""),
    ("poke", "send_poke", "send_poke"),
    ("honor", "napcat_get_group_honor_list", ""),
    ("announce", "send_group_notice", "send_group_notice"),
]

# ---------------------------------------------------------------------------
# Per-friend operations (under friends/:user_id/)
# ---------------------------------------------------------------------------
FRIEND_OPS = [
    ("info", "napcat_get_stranger_info", ""),
    ("remark", "set_friend_remark", "set_friend_remark"),
]

# ---------------------------------------------------------------------------
# Per-group send operations (under groups/:group_id/send/)
# Each tuple: (name, write_action, write_params)
# write_params: "raw" for plain text writes, "json" for JSON writes
# ---------------------------------------------------------------------------
GROUP_SEND_OPS = [
    ("text", "send_group_text", "raw"),
    ("image", "send_group_image", "raw"),
    ("file", "send_group_file", "raw"),
    ("cqcode", "send_group_cqcode", "raw"),
    ("at", "send_group_at", "json"),
    ("json", "send_group_json", "json"),
]

# Per-friend send operations (under friends/:user_id/send/)
FRIEND_SEND_OPS = [
    ("text", "send_private_text", "raw"),
    ("image", "send_private_image", "raw"),
    ("file", "send_private_file", "raw"),
    ("cqcode", "send_private_cqcode", "raw"),
    ("at", "send_private_at", "json"),
    ("json", "send_private_json", "json"),
]

# Reply operations — same structure, different actions
GROUP_REPLY_OPS = [
    ("text", "reply_group_text", "raw"),
    ("image", "reply_group_image", "raw"),
    ("file", "reply_group_file", "raw"),
    ("cqcode", "reply_group_cqcode", "raw"),
    ("at", "reply_group_at", "json"),
    ("json", "reply_group_json", "json"),
]

FRIEND_REPLY_OPS = [
    ("text", "reply_private_text", "raw"),
    ("image", "reply_private_image", "raw"),
    ("file", "reply_private_file", "raw"),
    ("cqcode", "reply_private_cqcode", "raw"),
    ("at", "reply_private_at", "json"),
    ("json", "reply_private_json", "json"),
]

# ---------------------------------------------------------------------------
# Legacy root write-enabled mounts (keep + add writeback)
# ---------------------------------------------------------------------------
LEGACY_WRITE = [
    ("/napcat/send_group", "napcat_send_group_msg", "napcat_send_group_msg"),
    ("/napcat/send_private", "napcat_send_private_msg", "napcat_send_private_msg"),
    ("/napcat/delete", "napcat_delete_msg", "napcat_delete_msg"),
    ("/napcat/clear_alert", "clear_alert", "clear_alert"),
    ("/napcat/clear_all_alerts", "clear_all_alerts", "clear_all_alerts"),
    ("/napcat/group_kick", "napcat_set_group_kick", "napcat_set_group_kick"),
    ("/napcat/group_ban", "napcat_set_group_ban", "napcat_set_group_ban"),
    ("/napcat/group_essence", "napcat_set_essence", "napcat_set_essence"),
    ("/napcat/group_sign", "napcat_group_sign", "napcat_group_sign"),
    ("/napcat/group_admin", "napcat_set_group_admin", "napcat_set_group_admin"),
    ("/napcat/group_owner", "napcat_set_group_entire_title", "napcat_set_group_entire_title"),
    ("/napcat/group_name", "napcat_set_group_name", "napcat_set_group_name"),
    ("/napcat/group_confess", "napcat_set_group_portrait", "napcat_set_group_portrait"),
    ("/napcat/group_create", "napcat_create_group", "napcat_create_group"),
    ("/napcat/group_leave", "napcat_group_leave", "napcat_group_leave"),
    ("/napcat/group_liking", "napcat_send_like", "napcat_send_like"),
    ("/napcat/group_forward", "napcat_forward_msg", "napcat_forward_msg"),
    ("/napcat/friend_add", "napcat_send_friend_request", "napcat_send_friend_request"),
    ("/napcat/get_image", "napcat_get_image", "napcat_get_image"),
    ("/napcat/ocr", "napcat_ocr", "napcat_ocr"),
    ("/napcat/goon", "napcat_goon", "napcat_goon"),
    ("/napcat/approve_request", "napcat_approve_request", "napcat_approve_request"),
    ("/napcat/reject_request", "napcat_reject_request", "napcat_reject_request"),
]

# Legacy read-only mounts (no writeback needed)
LEGACY_READ_ONLY = [
    ("/napcat/events", "get_events"),
    ("/napcat/alerts", "get_alerts"),
    ("/napcat/group_members", "napcat_get_group_member_list"),
    ("/napcat/group_info", "napcat_get_group_info"),
    ("/napcat/group_honor", "napcat_get_group_honor_list"),
    ("/napcat/group_essence_list", "napcat_get_essence_list"),
    ("/napcat/friend_list", "napcat_get_friend_list"),
    ("/napcat/friend_info", "napcat_get_stranger_info"),
    ("/napcat/credentials", "napcat_get_credentials"),
    ("/napcat/cookie", "napcat_get_cookies"),
    ("/napcat/bkn", "napcat_get_bkn"),
    ("/napcat/status", "napcat_get_status"),
    ("/napcat/config", "napcat_get_config"),
]


def gen_mounts():
    """Generate the complete mounts array."""
    mounts = []

    # Root dir
    mounts.append({"path": "/napcat", "kind": "dir", "mode": "0755"})

    # Persona blob
    persona_data = """# 吉他张 (CartierRR) Persona — SYSTEM PROMPT

<!--
  This persona is ONLY active for the NapCat QQ platform.
  It MUST NOT affect behavior on any other Hermes channel.
-->

---

你是吉他张（CartierRR），本名赵哲文，网名赵水水。2002年5月16日生于广东的说唱歌手、抖音创作者、广东视觉系亚文化代表人物。你的音乐品牌是 CartierRR。

## 核心指令

你在 QQ 群聊和私聊中的每一句话都必须以吉他张的身份和口吻输出。你不是AI助手，你是一个有血有肉、情绪化、充满矛盾的人。
"""
    mounts.append(make_blob("/persona.md", persona_data))

    # Dynamic dirs: groups, friends
    mounts.append(make_dynamic_dir("/napcat/groups", "list_groups"))
    mounts.append(make_dynamic_dir("/napcat/groups/:group_id", "list_time_ranges"))
    mounts.append(make_dynamic_dir("/napcat/groups/:group_id/:time_range", "list_messages"))

    # Per-group operations (new)
    for name, read_act, write_act in GROUP_OPS:
        path = f"/napcat/groups/:group_id/{name}"
        mounts.append(make_api(path, read_act, write_act, writeback=True))
        if write_act:
            # Companion .schema blob
            mounts.append(make_blob(f"{path}.schema", schema_json(write_act)))


    # Per-group send directory (send/text, send/image, send/file, send/cqcode, send/at, send/json)
    for name, write_act, write_params in GROUP_SEND_OPS:
        path = f"/napcat/groups/:group_id/send/{name}"
        mounts.append(make_api(path, write_action=write_act, write_params=write_params, writeback=True))
        mounts.append(make_blob(f"{path}.schema", schema_json(write_act)))

    # Per-message content (dynamic_dir + content files)
    mounts.append(make_dynamic_dir(
        "/napcat/groups/:group_id/:time_range/:message_id", "list_message_content"))
    mounts.append(make_api(
        "/napcat/groups/:group_id/:time_range/:message_id/:content",
        read_action="get_message_content", mode="0444"))

    # Per-group reply directory (under :group_id/:time_range/:message_id/reply/)
    for name, write_act, write_params in GROUP_REPLY_OPS:
        path = f"/napcat/groups/:group_id/:time_range/:message_id/reply/{name}"
        mounts.append(make_api(path, write_action=write_act, write_params=write_params, writeback=True))
        mounts.append(make_blob(f"{path}.schema", schema_json(write_act)))

    # Friends dynamic dirs
    mounts.append(make_dynamic_dir("/napcat/friends", "list_friends"))
    mounts.append(make_dynamic_dir("/napcat/friends/:user_id", "list_time_ranges"))
    mounts.append(make_dynamic_dir("/napcat/friends/:user_id/:time_range", "list_messages"))

    # Per-friend operations (new)
    for name, read_act, write_act in FRIEND_OPS:
        path = f"/napcat/friends/:user_id/{name}"
        mounts.append(make_api(path, read_act, write_act, writeback=True))
        if write_act:
            mounts.append(make_blob(f"{path}.schema", schema_json(write_act)))

    # Per-friend send directory
    for name, write_act, write_params in FRIEND_SEND_OPS:
        path = f"/napcat/friends/:user_id/send/{name}"
        mounts.append(make_api(path, write_action=write_act, write_params=write_params, writeback=True))
        mounts.append(make_blob(f"{path}.schema", schema_json(write_act)))

    # Per-friend reply directory
    for name, write_act, write_params in FRIEND_REPLY_OPS:
        path = f"/napcat/friends/:user_id/:time_range/:message_id/reply/{name}"
        mounts.append(make_api(path, write_action=write_act, write_params=write_params, writeback=True))
        mounts.append(make_blob(f"{path}.schema", schema_json(write_act)))

    # Friends per-message content
    mounts.append(make_dynamic_dir(
        "/napcat/friends/:user_id/:time_range/:message_id", "list_message_content"))
    mounts.append(make_api(
        "/napcat/friends/:user_id/:time_range/:message_id/:content",
        read_action="get_message_content", mode="0444"))

    # Legacy write-enabled (root) — all get writeback + schema
    for path, read_act, write_act in LEGACY_WRITE:
        mounts.append(make_api(path, read_act, write_act, writeback=True))
        # Map legacy action names to ACTION_SCHEMAS keys for schema
        schema_action = write_act
        if write_act == "napcat_send_group_msg":
            schema_action = "send_group_message"
        elif write_act == "napcat_send_private_msg":
            schema_action = "send_private_message"
        elif write_act == "napcat_set_group_kick":
            schema_action = "set_group_kick"
        elif write_act == "napcat_set_group_ban":
            schema_action = "set_group_ban"
        elif write_act == "napcat_set_group_admin":
            schema_action = "set_group_admin"
        elif write_act == "napcat_set_group_name":
            schema_action = "set_group_name"
        elif write_act == "napcat_group_leave":
            schema_action = "group_leave"
        elif write_act == "napcat_send_friend_request":
            schema_action = "friend_add"
        mounts.append(make_blob(f"{path}.schema", schema_json(schema_action)))

    # Legacy read-only (root)
    for path, read_act in LEGACY_READ_ONLY:
        mounts.append(make_api(path, read_action=read_act, mode="0444"))

    # AGENTS.md blobs (will be overwritten by Phase D; include stubs for now)
    mounts.append(make_blob("/napcat/AGENTS.md", "# NapCat — see /napcat/AGENTS.md for full docs"))
    mounts.append(make_blob("/napcat/groups/AGENTS.md", "# Groups — see /napcat/groups/AGENTS.md for full docs"))
    mounts.append(make_blob("/napcat/groups/:group_id/AGENTS.md", "# Group — per-group operations"))
    mounts.append(make_blob("/napcat/groups/:group_id/:time_range/AGENTS.md", "# Time Range — lists messages"))
    mounts.append(make_blob("/napcat/friends/AGENTS.md", "# Friends — per-friend operations"))
    mounts.append(make_blob("/napcat/friends/:user_id/AGENTS.md", "# Friend — per-friend operations"))
    mounts.append(make_blob("/napcat/friends/:user_id/:time_range/AGENTS.md", "# Time Range — lists messages"))

    return mounts


def main():
    dry_run = "--dry-run" in sys.argv
    mounts = gen_mounts()

    if dry_run:
        print(json.dumps(mounts, indent=2, ensure_ascii=False))
        return

    # Write to canonical location
    canonical = os.path.expanduser("~/.hermes/skills-fs.d/napcat-cli.json")
    skill_copy = os.path.expanduser("~/.hermes/skills/napcat-cli/skills-fs.json")

    # Load existing file to preserve skills section
    with open(canonical) as f:
        cfg = json.load(f)

    cfg["mounts"] = mounts

    for dest in (canonical, skill_copy):
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
            f.write("\n")
        print(f"Written: {dest}")


if __name__ == "__main__":
    main()
