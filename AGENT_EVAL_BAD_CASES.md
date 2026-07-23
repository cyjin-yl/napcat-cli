# Agent Eval: Hermes Bad Cases Report

## Overview

This document catalogues known failure modes ("bad cases") where Hermes (or other
LLM agents) misbehaves when interacting with napcat-cli. Each case has been
identified through log analysis, reproduced in test fixtures, and addressed with
fixes in the napcat-cli harness.

## 1. Reply Chain Resolution Failures

### 1.1 Replying to Wrong Person

**Symptom:** Agent replies to the first person it sees rather than the actual
reply target. This happens when the wake prompt lacks sender identity metadata.

**Root Cause:** `_who()` in `wake_orchestrator.py` did not include `user_id`
in the sender identity string. The prompt showed only `昵称(?)`, making it
impossible for the agent to distinguish between users with the same nickname.

**Fix:** `_who()` now returns `昵称(QQ号)` — includes both nickname and QQ number
as source-grounded identifiers.

### 1.2 Reply Wrong Group

**Symptom:** When a message replies to an earlier message in a different group,
the agent replies in the wrong group. The wake prompt lacked `group_id` context.

**Root Cause:** `_extract_reply_meta()` returned only the reply message ID without
group context. The prompt said "回复消息ID: xxx" but didn't say where.

**Fix:** Reply metadata now includes group_id (or "私聊" for DMs), sender identity,
and message context — plus explicit hints for images and forward messages.

### 1.3 Merged/Forward Message Blindness

**Symptom:** Agent ignores forwarded/merged messages entirely — no exploration.

**Root Cause:** `format_message()` in `message.py` had no handler for `forward`
segment type, rendering `[forward]` as unknown. No CLI/FS hints were provided
for exploring forward content.

**Fix:** `format_message()` now renders `[合并转发: <id>]`. Prompt footer includes
CLI (`napcat group <gid> get_message <mid>`) and FS
(`/napcat/groups/:group_id/:time_range/:message_id/:content`) exploration hints.

## 2. Image Handling Failures

### 2.1 Agent Doesn't OCR Images

**Symptom:** When a message contains an image, the agent ignores it — doesn't
try to read text or describe the image.

**Root Cause:** The wake prompt included only `[图片]` with no metadata, and the
prompt footer referenced `/napcat/ocr` which is non-functional. The agent had no
way to discover image URLs or know about alternative access methods.

**Fix:** Prompt now includes full image metadata (file_id, url, file_size, sub_type).
Prompt footer clearly states NapCat OCR is unavailable and recommends:
- Using multimodal vision capabilities with the provided image URL
- `napcat get_image <url>` CLI or `/napcat/get_image` FS path for download

### 2.2 format_message Integer Crash

**Symptom:** `format_message()` raises `AttributeError: 'int' object has no attribute 'strip'`
when image fields like `sub_type` or `file_size` contain integers instead of strings.

**Root Cause:** Direct `.strip()` calls on values from `data.get()` without type checking.
OneBot message data can contain integers for numeric fields.

**Fix:** All field accesses now handle non-string values defensively.

### 2.3 extract_files Returns Empty

**Symptom:** `extract_files()` returns empty list despite image/video segments having
`url` or `file` fields.

**Root Cause:** The function required `Path.exists()` to be True, which filters out
remote URLs and non-existent local paths.

**Fix:** `extract_files()` now returns Path objects for ANY file/url/path field
without requiring local filesystem existence. The agent can use its own HTTP client
to download URLs.

## 3. Identity Resolution Failures

### 3.1 Sender Identity Missing from DM Prompts

**Symptom:** For private messages (DM_ME), the prompt showed "最近一条来自 陈二(?)"
with the QQ number missing.

**Root Cause:** `_who()` used `event.get('user_id', '?')` but sender's `user_id`
lives inside `sender` dict, not at the top-level event.

**Fix:** `_who()` now checks `sender.get('user_id')` first, then falls back to
`event.get('user_id')`.

### 3.2 Empty Sender Crashes

**Symptom:** Events without a `sender` dict cause crashes or empty prompts.

**Root Cause:** `_who()` assumed `event.get("sender")` is always a dict.

**Fix:** Added `isinstance` guard before accessing `sender` fields.

## 4. Wake Storm Patterns

### 4.1 CLI Backend Timeout (120s)

**Bad Case:** All wake deliveries fail with `cli: timeout after 120.0s`.
When Hermes gateway is unreachable, the CLI backend is tried and waits 120s
before giving up. This causes a 2-minute queue backlog.

**Root Cause:** CLI wake backend hardcodes 120s timeout with no config override
and no faster fallback when Hermes is down.

**Fix (in progress):** The `wake_new_message_idle_seconds` was reduced from 600 to 300
to prevent backlog pileup. Future: CLI timeout should be configurable.

### 4.2 MY_MESSAGE_RECALLED Wake Storms

**Bad Case:** Each message recall event triggers an unnecessary wake. These
events have no actionable content but bypass cooldown.

**Root Cause:** All events are treated equally in `watch.py` — no filtering for
non-actionable event types.

**Fix (proposed):** Add an exclusion list for low-value event types
(MY_MESSAGE_RECALLED, BOT_BANNED, etc.) that should not trigger agent wakes.

### 4.3 BOT_BANNED Notifications

**Bad Case:** BOT_BANNED events wake the agent but the agent has no ability
to unban itself (requires manual intervention).

**Fix (proposed):** Convert BOT_BANNED to a notification-only event that logs
to daemon.log but does not trigger agent wake.

### 4.4 Backlog Idle Threshold

**Bad Case:** Backlog fires when `idle >= 600s` with unread messages. This
means even 1 unread message triggers a backlog wake every 10 minutes.

**Fix (applied):** `new_message_idle_seconds` default reduced from 600 to 300.
The backlog sweep task checks both idle time AND unread count.

## 5. Prompt Footer Gaps

### 5.1 Missing CLI Alternatives

**Bad Case:** Prompt footer mentioned only skills-fs paths (e.g., `/napcat/ocr`,
`/napcat/get_image`). When skills-fs mount is unavailable, the agent has no
fallback path.

**Fix:** Footer now includes both CLI commands (`napcat get_image <url>`) AND
FS paths (`/napcat/get_image`) for every action.

### 5.2 OCR Reference to Broken Feature

**Bad Case:** Footer said `/napcat/ocr` (OCR识图) but NapCat built-in OCR is
not functional.

**Fix:** Footer now states "NapCat 内置 OCR 不可用，请直接用多模态视觉能力阅读图片"
and suggests multimodal vision instead.

## 6. TUI Rendering Gaps

### 6.1 Image Invisibility in TUI

**Bad Case:** The TUI shows plain text names for users without image context.
Images appear as `[图片]` with no metadata.

**Fix:** Added `tui_show_images` config option (default: True) that appends
image file_id, url, and size info to messages in the TUI.

## Test Coverage

20 Agent Eval tests cover the above bad cases, organized as:

- `TestReplyChain` (7 tests): Reply resolution, group/DM context, forward hints
- `TestImageHandling` (2 tests): Image metadata, tool hints
- `TestIdentityResolution` (3 tests): Sender identification, empty sender
- `TestPromptFormat` (2 tests): Footer completeness, reason inclusion
- `TestMessageFormatting` (5 tests): Format_message correctness
- `TestFileExtraction` (1 test): extract_files output

Run with: `pytest tests/test_agent_eval.py -v`
