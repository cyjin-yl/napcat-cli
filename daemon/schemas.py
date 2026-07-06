#!/usr/bin/env python3
"""NapCat action schemas — single source of truth for all write action parameters.

Used by:
  - watch.py: describe_action, structured error responses
  - gen_mounts.py: generating .schema blob data for skills-fs mounts
  - CLI napcat schema: printing action documentation
"""
from __future__ import annotations

ACTION_SCHEMAS: dict[str, dict] = {
    # ------------------------------------------------------------------
    # Custom internal actions (handled directly in _dispatch)
    # ------------------------------------------------------------------
    "send_group_message": {
        "params": ["group_id", "message"],
        "example": {"group_id": "123456", "message": "hello world"},
        "required": ["group_id", "message"],
        "description": "Send a message to a QQ group.",
    },
    "send_private_message": {
        "params": ["user_id", "message"],
        "example": {"user_id": "12345678", "message": "hello"},
        "required": ["user_id", "message"],
        "description": "Send a private message to a QQ user.",
    },
    "clear_alert": {
        "params": ["name"],
        "example": {"name": "NEW_MESSAGE"},
        "required": ["name"],
        "description": "Clear alerts of a specific name.",
    },
    "clear_all_alerts": {
        "params": [],
        "example": {},
        "required": [],
        "description": "Clear all pending alerts.",
    },
    # ------------------------------------------------------------------
    # Proxied NapCat API actions (napcat_ prefix → API action)
    # ------------------------------------------------------------------
    "napcat_send_group_msg": {
        "params": ["group_id", "message"],
        "example": {"group_id": "123456", "message": "hello"},
        "required": ["group_id", "message"],
        "description": "Send a group message (proxied to send_msg with message_type=group).",
    },
    "napcat_send_private_msg": {
        "params": ["user_id", "message"],
        "example": {"user_id": "12345678", "message": "hello"},
        "required": ["user_id", "message"],
        "description": "Send a private message (proxied to send_msg with message_type=private).",
    },
    "napcat_delete_msg": {
        "params": ["message_id"],
        "example": {"message_id": "987654321"},
        "required": ["message_id"],
        "description": "Delete/recall a sent message.",
    },
    "napcat_set_group_kick": {
        "params": ["group_id", "user_id", "reject_add_request"],
        "example": {"group_id": "123456", "user_id": "12345678"},
        "required": ["group_id", "user_id"],
        "description": "Kick a member from a group. Set reject_add_request to prevent rejoining.",
    },
    "napcat_set_group_ban": {
        "params": ["group_id", "user_id", "duration"],
        "example": {"group_id": "123456", "user_id": "12345678", "duration": 600},
        "required": ["group_id", "user_id", "duration"],
        "description": "Ban a group member. Duration is in seconds; 0 means unban.",
    },
    "napcat_set_essence": {
        "params": ["group_id", "message_id"],
        "example": {"group_id": "123456", "message_id": "987654321"},
        "required": ["group_id", "message_id"],
        "description": "Set or unset a message as group essence message.",
    },
    "napcat_group_sign": {
        "params": ["group_id"],
        "example": {"group_id": "123456"},
        "required": ["group_id"],
        "description": "Sign in to a group (daily check-in).",
    },
    "napcat_set_group_admin": {
        "params": ["group_id", "user_id", "enable"],
        "example": {"group_id": "123456", "user_id": "12345678", "enable": True},
        "required": ["group_id", "user_id", "enable"],
        "description": "Set or unset group admin for a member.",
    },
    "napcat_set_group_entire_title": {
        "params": ["group_id", "title"],
        "example": {"group_id": "123456", "title": "My Group Title"},
        "required": ["group_id", "title"],
        "description": "Set the group owner's special title for the entire group.",
    },
    "napcat_set_group_name": {
        "params": ["group_id", "group_name"],
        "example": {"group_id": "123456", "group_name": "New Group Name"},
        "required": ["group_id", "group_name"],
        "description": "Set the group name.",
    },
    "napcat_set_group_portrait": {
        "params": ["group_id", "file"],
        "example": {"group_id": "123456", "file": "file:///path/to/image.png"},
        "required": ["group_id", "file"],
        "description": "Set the group portrait/avatar. File can be a local path (file:///), URL, or base64.",
    },
    "napcat_create_group": {
        "params": ["group_name", "member_ids"],
        "example": {"group_name": "My Group", "member_ids": [12345678, 87654321]},
        "required": ["group_name"],
        "description": "Create a new QQ group. Optionally invite members via member_ids.",
    },
    "napcat_group_leave": {
        "params": ["group_id", "reject"],
        "example": {"group_id": "123456"},
        "required": ["group_id"],
        "description": "Leave a group. Set reject to block future join requests.",
    },
    "napcat_send_like": {
        "params": ["user_id", "times"],
        "example": {"user_id": "12345678", "times": 1},
        "required": ["user_id"],
        "description": "Send like(s) to a user. Times defaults to 1, max 10 per call.",
    },
    "napcat_forward_msg": {
        "params": ["group_id", "message_id"],
        "example": {"group_id": "123456", "message_id": "987654321"},
        "required": ["group_id", "message_id"],
        "description": "Forward a message to another group.",
    },
    "napcat_send_friend_request": {
        "params": ["user_id", "remark"],
        "example": {"user_id": "12345678", "remark": "Hey there"},
        "required": ["user_id"],
        "description": "Send a friend request to a user. Remark is optional.",
    },
    "napcat_get_stranger_info": {
        "params": ["user_id", "no_cache"],
        "example": {"user_id": "12345678"},
        "required": ["user_id"],
        "description": "Get information about a stranger (read-only).",
    },
    "napcat_delete_friend": {
        "params": ["user_id"],
        "example": {"user_id": "12345678"},
        "required": ["user_id"],
        "description": "Remove a user from the friend list.",
    },
    "napcat_get_credentials": {
        "params": [],
        "example": {},
        "required": [],
        "description": "Get bot credentials (SSO cookies, etc.). Read-only.",
    },
    "napcat_get_cookies": {
        "params": ["domain"],
        "example": {"domain": "qq.com"},
        "required": [],
        "description": "Get cookies for a specific domain. Domain is optional.",
    },
    "napcat_get_bkn": {
        "params": ["cookies"],
        "example": {"cookies": "bkn=..."},
        "required": [],
        "description": "Calculate BKN token from cookies.",
    },
    "napcat_get_status": {
        "params": [],
        "example": {},
        "required": [],
        "description": "Get bot online status and health. Read-only.",
    },
    "napcat_get_config": {
        "params": [],
        "example": {},
        "required": [],
        "description": "Get bot configuration. Read-only.",
    },
    "napcat_get_image": {
        "params": ["url"],
        "example": {"url": "https://example.com/image.png"},
        "required": ["url"],
        "description": "Download an image by URL.",
    },
    "napcat_ocr": {
        "params": ["image"],
        "example": {"image": "file:///path/to/image.png"},
        "required": ["image"],
        "description": "Perform OCR on an image. Image can be a file path, URL, or base64.",
    },
    "napcat_goon": {
        "params": [],
        "example": {},
        "required": [],
        "description": "Continue/resume a pending task (NapCat-specific).",
    },
    "napcat_approve_request": {
        "params": ["request_id", "request_type"],
        "example": {"request_id": "111111111", "request_type": "group"},
        "required": ["request_id", "request_type"],
        "description": 'Approve an incoming request. request_type: "group" or "friend".',
    },
    "napcat_reject_request": {
        "params": ["request_id", "request_type"],
        "example": {"request_id": "111111111", "request_type": "group"},
        "required": ["request_id", "request_type"],
        "description": 'Reject an incoming request. request_type: "group" or "friend".',
    },
    "napcat_send_group_notice": {
        "params": ["group_id", "title", "content", "hints"],
        "example": {"group_id": "123456", "title": "Notice", "content": "Hello everyone"},
        "required": ["group_id", "content"],
        "description": "Send a group notice/announcement.",
    },
    "napcat_set_group_card": {
        "params": ["group_id", "user_id", "card"],
        "example": {"group_id": "123456", "user_id": "12345678", "card": "Nickname"},
        "required": ["group_id", "user_id", "card"],
        "description": "Set a member's group card (nickname in group).",
    },
    "napcat_set_group_remark": {
        "params": ["group_id", "remark"],
        "example": {"group_id": "123456", "remark": "My Remark"},
        "required": ["group_id", "remark"],
        "description": "Set the bot's remark for a group.",
    },
    "napcat_set_friend_remark": {
        "params": ["user_id", "remark"],
        "example": {"user_id": "12345678", "remark": "Friend Remark"},
        "required": ["user_id", "remark"],
        "description": "Set the bot's remark for a friend.",
    },
    "napcat_upload_group_file": {
        "params": ["group_id", "file", "name", "folder"],
        "example": {"group_id": "123456", "file": "file:///path/to/file.txt", "name": "file.txt"},
        "required": ["group_id", "file"],
        "description": "Upload a file to a group. Folder is optional.",
    },
    "napcat_upload_private_file": {
        "params": ["user_id", "file", "name"],
        "example": {"user_id": "12345678", "file": "file:///path/to/file.txt", "name": "file.txt"},
        "required": ["user_id", "file"],
        "description": "Upload a file to a private chat.",
    },
    # ------------------------------------------------------------------
    # Bare OneBot API action names (used by per-group/per-friend mounts)
    # ------------------------------------------------------------------
    "set_group_kick": {
        "params": ["group_id", "user_id", "reject_add_request"],
        "example": {"group_id": "123456", "user_id": "12345678"},
        "required": ["group_id", "user_id"],
        "description": "Kick a member from a group (OneBot action name).",
    },
    "set_group_ban": {
        "params": ["group_id", "user_id", "duration"],
        "example": {"group_id": "123456", "user_id": "12345678", "duration": 600},
        "required": ["group_id", "user_id", "duration"],
        "description": "Ban/unban a group member (OneBot action name).",
    },
    "set_group_admin": {
        "params": ["group_id", "user_id", "enable"],
        "example": {"group_id": "123456", "user_id": "12345678", "enable": True},
        "required": ["group_id", "user_id", "enable"],
        "description": "Set/unset group admin (OneBot action name).",
    },
    "set_group_card": {
        "params": ["group_id", "user_id", "card"],
        "example": {"group_id": "123456", "user_id": "12345678", "card": "Nickname"},
        "required": ["group_id", "user_id", "card"],
        "description": "Set a member's group card (OneBot action name).",
    },
    "set_group_name": {
        "params": ["group_id", "group_name"],
        "example": {"group_id": "123456", "group_name": "New Group Name"},
        "required": ["group_id", "group_name"],
        "description": "Set group name (OneBot action name).",
    },
    "group_leave": {
        "params": ["group_id", "reject"],
        "example": {"group_id": "123456"},
        "required": ["group_id"],
        "description": "Leave a group (OneBot action name).",
    },
    "send_group_notice": {
        "params": ["group_id", "title", "content", "hints"],
        "example": {"group_id": "123456", "title": "Notice", "content": "Hello everyone"},
        "required": ["group_id", "content"],
        "description": "Send a group notice/announcement (OneBot action name).",
    },
    "send_poke": {
        "params": ["group_id", "user_id"],
        "example": {"group_id": "123456", "user_id": "12345678"},
        "required": ["group_id", "user_id"],
        "description": "Poke a group member (OneBot action name).",
    },
    "set_friend_remark": {
        "params": ["user_id", "remark"],
        "example": {"user_id": "12345678", "remark": "Friend Remark"},
        "required": ["user_id", "remark"],
        "description": "Set a friend's remark (OneBot action name).",
    },
    # New internal actions (message content browsing + schema description)
    # ------------------------------------------------------------------
    "list_message_content": {
        "params": ["message_id", "group_id", "user_id"],
        "example": {"message_id": "987654321", "group_id": "123456"},
        "required": ["message_id"],
        "description": "List available content types for a message (metadata, text, image, file, etc.).",
    },
    "get_message_content": {
        "params": ["message_id", "content", "group_id", "user_id"],
        "example": {"message_id": "987654321", "content": "image", "group_id": "123456"},
        "required": ["message_id", "content"],
        "description": "Get specific content from a message. Content: metadata, text, image, file, video, record, forward.",
    },
    "describe_action": {
        "params": ["action"],
        "example": {"action": "napcat_send_group_msg"},
        "required": ["action"],
        "description": "Get schema description for an action (params, example, required, description).",
    },
    # ------------------------------------------------------------------
    # Multi-type send/reply actions (send/reply text|image|file|cqcode|at|json)
    # ------------------------------------------------------------------
    "send_group_text": {
        "params": ["group_id"],
        "example": {"group_id": "123456", "text": "hello"},
        "required": ["group_id"],
        "description": "Send plain text to a group. Write raw text to the file.",
    },
    "send_group_image": {
        "params": ["group_id"],
        "example": "/path/to/image.jpg",
        "required": ["group_id"],
        "description": "Send an image to a group. Write a local file path.",
    },
    "send_group_file": {
        "params": ["group_id"],
        "example": "/path/to/file.txt",
        "required": ["group_id"],
        "description": "Upload a file to a group. Write a local file path.",
    },
    "send_group_cqcode": {
        "params": ["group_id"],
        "example": "[CQ:at,qq=123] hello",
        "required": ["group_id"],
        "description": "Send CQ code string to a group. NapCat parses CQ codes.",
    },
    "send_group_at": {
        "params": ["group_id", "qq"],
        "example": {"group_id": "123456", "qq": "456", "text": "hello"},
        "required": ["group_id", "qq"],
        "description": "@someone in a group with optional text.",
    },
    "send_group_json": {
        "params": ["group_id", "message"],
        "example": {"group_id": "123456", "message": [{"type": "text", "data": {"text": "hi"}}]},
        "required": ["group_id", "message"],
        "description": "Send full message segments JSON to a group.",
    },
    "send_private_text": {
        "params": ["user_id"],
        "example": {"user_id": "12345678", "text": "hello"},
        "required": ["user_id"],
        "description": "Send plain text to a friend. Write raw text to the file.",
    },
    "send_private_image": {
        "params": ["user_id"],
        "example": "/path/to/image.jpg",
        "required": ["user_id"],
        "description": "Send an image to a friend. Write a local file path.",
    },
    "send_private_file": {
        "params": ["user_id"],
        "example": "/path/to/file.txt",
        "required": ["user_id"],
        "description": "Upload a file to a friend. Write a local file path.",
    },
    "send_private_cqcode": {
        "params": ["user_id"],
        "example": "[CQ:face,id=14]",
        "required": ["user_id"],
        "description": "Send CQ code string to a friend.",
    },
    "send_private_at": {
        "params": ["user_id", "qq"],
        "example": {"user_id": "12345678", "qq": "456", "text": "hello"},
        "required": ["user_id", "qq"],
        "description": "@someone in a private chat with optional text.",
    },
    "send_private_json": {
        "params": ["user_id", "message"],
        "example": {"user_id": "12345678", "message": [{"type": "text", "data": {"text": "hi"}}]},
        "required": ["user_id", "message"],
        "description": "Send full message segments JSON to a friend.",
    },
    "reply_group_text": {
        "params": ["group_id", "message_id"],
        "example": {"group_id": "123456", "message_id": "987654321", "text": "reply"},
        "required": ["group_id", "message_id"],
        "description": "Reply to a group message with plain text.",
    },
    "reply_group_image": {
        "params": ["group_id", "message_id"],
        "example": {"group_id": "123456", "message_id": "987654321"},
        "required": ["group_id", "message_id"],
        "description": "Reply to a group message with an image. Write a local file path.",
    },
    "reply_group_file": {
        "params": ["group_id", "message_id"],
        "example": {"group_id": "123456", "message_id": "987654321"},
        "required": ["group_id", "message_id"],
        "description": "Upload a file as reply (same as send file). Write a local file path.",
    },
    "reply_group_cqcode": {
        "params": ["group_id", "message_id"],
        "example": {"group_id": "123456", "message_id": "987654321"},
        "required": ["group_id", "message_id"],
        "description": "Reply to a group message with CQ code string.",
    },
    "reply_group_at": {
        "params": ["group_id", "message_id", "qq"],
        "example": {"group_id": "123456", "message_id": "987654321", "qq": "456", "text": "hi"},
        "required": ["group_id", "message_id", "qq"],
        "description": "Reply to a group message by @-ing someone.",
    },
    "reply_group_json": {
        "params": ["group_id", "message_id", "message"],
        "example": {"group_id": "123456", "message_id": "987654321", "message": [{"type": "text", "data": {"text": "hi"}}]},
        "required": ["group_id", "message_id", "message"],
        "description": "Reply to a group message with full segments JSON.",
    },
    "reply_private_text": {
        "params": ["user_id", "message_id"],
        "example": {"user_id": "12345678", "message_id": "987654321", "text": "reply"},
        "required": ["user_id", "message_id"],
        "description": "Reply to a friend message with plain text.",
    },
    "reply_private_image": {
        "params": ["user_id", "message_id"],
        "example": {"user_id": "12345678", "message_id": "987654321"},
        "required": ["user_id", "message_id"],
        "description": "Reply to a friend message with an image.",
    },
    "reply_private_file": {
        "params": ["user_id", "message_id"],
        "example": {"user_id": "12345678", "message_id": "987654321"},
        "required": ["user_id", "message_id"],
        "description": "Upload a file as reply (same as send file). Write a local file path.",
    },
    "reply_private_cqcode": {
        "params": ["user_id", "message_id"],
        "example": {"user_id": "12345678", "message_id": "987654321"},
        "required": ["user_id", "message_id"],
        "description": "Reply to a friend message with CQ code string.",
    },
    "reply_private_at": {
        "params": ["user_id", "message_id", "qq"],
        "example": {"user_id": "12345678", "message_id": "987654321", "qq": "456"},
        "required": ["user_id", "message_id", "qq"],
        "description": "Reply to a friend message by @-ing someone.",
    },
    "reply_private_json": {
        "params": ["user_id", "message_id", "message"],
        "example": {"user_id": "12345678", "message_id": "987654321", "message": [{"type": "text", "data": {"text": "hi"}}]},
        "required": ["user_id", "message_id", "message"],
        "description": "Reply to a friend message with full segments JSON.",
    },
}
