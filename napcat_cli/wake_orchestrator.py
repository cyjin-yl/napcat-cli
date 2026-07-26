"""Wake orchestrator — debounce, cooldown, backlog sweep, contextual prompts.

Sits between :class:`napcat_cli.daemon.watch.EventProcessor` and the
:class:`napcat_cli.wake_backend.Waker`. Events arrive on the daemon's asyncio
loop; this module offloads the blocking wake (HTTP/subprocess) to a worker
thread so the loop never blocks, and adds:

- **Debounce**: a burst of same-reason events within ``debounce_seconds``
  coalesces into one wake.
- **Cooldown**: per-reason ``cooldown_seconds`` suppresses repeats. ``AT_ME``,
  ``REPLY_TO_ME`` and ``DM_ME`` bypass cooldown (near-immediate wake) so direct
  mentions and private (DM) messages are answered promptly.
- **NEW_MESSAGE backlog sweep**: if unread messages accumulate longer than
  ``new_message_idle_seconds`` without a wake, fire a ``NEW_MESSAGE_BACKLOG``
  wake so the agent scans the inbox.
- **Contextual prompts**: the wake prompt summarizes *what* happened (who, where,
  text, counts, image metadata, reply chains) instead of a generic "new message".
- **Legacy fallback**: if no backend is configured but a ``wake_command`` is set,
  it is run as-is (back-compat for ``echo … >> .agent-wake`` configs).
"""
from __future__ import annotations

import queue
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

from .wake_backend import Waker

if TYPE_CHECKING:
    from napcat_cli.lib.events import EventsReader


# Reasons that should wake near-immediately and ignore cooldown.
_IMMEDIATE = {"AT_ME", "REPLY_TO_ME", "DM_ME"}
# Message-class reasons — a wake for any of these counts as "the agent read the inbox".
_MESSAGE_REASONS = {"AT_ME", "REPLY_TO_ME", "DM_ME", "NEW_MESSAGE", "NEW_MESSAGE_BACKLOG",
                    "GROUP_TRIGGER", "PRIVATE_TRIGGER"}
# Must produce a real QQ outbound message (napcat send/reply). Content may refuse
# the request, but silent internal text-only "我不接" without QQ send is NOT allowed.
_MUST_REPLY = frozenset(_IMMEDIATE)

_PROMPT_FOOTER = (
    "你可以用 `napcat events` / `napcat alerts` 查看详情，用 `napcat send`/`napcat reply` 回复。"
    "\n[Alerts 处理建议] `napcat alerts` 返回未读/提醒列表，包含概要（截断 ~100 字）。"
    "建议先扫一眼：若提到你（@你/回复你/私聊）或关键词 -> 处理并回复；"
    "若全是无关噪音 -> 用 `napcat alerts --clear` 一键标记已读。"
    "\n[防无限循环] 你的回复如果触发「戳自己/回复自己」，会再次拉起 wakeup。"
    "**除非明确需要，否则不要回复自己的戳一戳或回复消息**，否则会陷入无限循环。"
    "若需确认收到，建议用表情回应（like/react）而非文字回复。"
    "\n[回复/发送 API] 两个文本端点（按需取用）："
    "\n- `/text` (智能) 与 `/text_raw` (纯文本)，其余端点 (/image /cqcode /at /json) 保持不变。"
    "\nFS:  写入 `/napcat/groups/:gid/:range/:mid/reply/text` (智能文本，自动识别 CQ 码/at/图片路径) / `/reply/text_raw` (纯文本，不处理 CQ) / `/reply/image` / `/reply/cqcode` / `/reply/at` / `/reply/json`"
    "\n     写入 `/napcat/groups/:gid/send/text` (智能文本) / `/send/text_raw` (纯文本) / `/send/image` / `/send/cqcode` / `/send/at` / `/send/json`"
    "\n     写入 `/napcat/friends/:uid/send/text` (智能文本) / `/send/text_raw` (纯文本) / `/send/image` / `/send/cqcode` / `/send/at` / `/send/json`"
    "\n     新路径（无需 group_id，仅需 message_id）：`/napcat/messages/:mid/reply/text` / `/reply/text_raw` / `/reply/image` / `/reply/cqcode` / `/reply/at` / `/reply/json`"
    "\n     读取 `/napcat/messages/:mid` 获取消息内容，`/napcat/messages/:mid/image` 获取图片信息"
    "\n[重要] 若在 /text 或 /text_raw 中写入 CQ 码字符串（如 [CQ:at,qq=123]），/text 会被正确解析为段落，/text_raw 会原样发送（不解析）并返回警告提示你改用 /text。"
    "\n\nSchema 位置：skills-fs 挂载点 `/napcat/schemas/` 下有 `reply_text.schema.json` `reply_text_raw.schema.json` `reply_image.schema.json` `reply_cqcode.schema.json` `reply_at.schema.json` `reply_json.schema.json`"
    "\n                        `send_text.schema.json` `send_text_raw.schema.json` `send_image.schema.json` `send_cqcode.schema.json` `send_at.schema.json` `send_json.schema.json`"
    "\n可直接读取 schema 确认字段要求。"
    "\n图片处理: QQ图片URL有防盗链限制，不可直接访问。必须用 `napcat get_image <url>` (CLI) 或 `/napcat/get_image` (skills-fs) 下载到本地后再识别; "
    "PaddleOCR 已集成，自动识别文字（结果在图片信息中已提供）。"
    "\n合并转发: 使用 `napcat group <gid> get_message <mid>` (CLI) 或 "
    "`/napcat/groups/:group_id/:time_range/:message_id/:content` (skills-fs) 获取完整内容。"
    "也可直接用 `/napcat/messages/:mid` 或 `napcat get_message <mid>`（无需 group_id）。"
    "\n群管理: `napcat group list` / `napcat group info <gid>`（也支持 `napcat group <gid> info`）"
    "\n[唤醒队列机制] 同一 session 的唤醒请求会被串行化队列处理。如果你发送了多条消息或有多个事件触发唤醒，它们会按顺序依次处理，不会并发。"
    "若你已在某个唤醒轮次中处理了相关内容，后续唤醒若无新内容可选择不再回复（仅限非 AT/DM/REPLY 类唤醒）。"
)

_MUST_REPLY_POLICY = (
    "\n[强制回复策略 — AT_ME / REPLY_TO_ME / DM_ME]"
    "\n本轮是 **@ / 被回复 / 私聊** 唤醒。"
    "\n**可见性（必读）**：你在本 agent/Hermes 会话里输出的任何文字（包括「我不接」「拒绝」），"
    "**触发此次唤醒的那位 QQ 用户完全看不到**。只有调用 napcat-cli 或 skills-fs 发出的消息才会出现在 QQ。"
    "\n因此：**必须** 调用下列之一，把回复（含拒绝/反驳）真正送到 QQ："
    "\n  - CLI：`napcat send private <对方QQ号user_id> --message \"...\"` / `napcat send group <群号group_id> --message \"...\"` / `napcat reply group <群号group_id> <触发消息IDmessage_id> --message \"...\"` / `napcat reply private <对方QQ号user_id> <触发消息IDmessage_id> --message \"...\"`"
    "\n  - skills-fs：写入对应 `.../send/text` 或 `.../reply/text`（或 text_raw/image/json 等）"
    "\n- 允许：拒绝帮忙、反驳、吐槽、嘲讽、说「不接这活」——但这些话也必须 **经 napcat-cli/skills-fs 发到 QQ**。"
    "\n- 禁止：只在 agent 会话里写「我不接 / 不回复 / 已处理」却 **不调用** send/reply（QQ 侧=已读不回）。"
    "\n- 禁止：用「策略不允许 / 内容违规」当借口完全沉默；至少用 napcat 回一句人格内的拒绝或反驳。"
    "\n- 成功标准：CLI 出现 `Sent message_id=...` 或 skills-fs 写入成功，而不是仅有会话 assistant 文本。"
    "\n- 仍须遵守防无限循环：不要回复自己的戳/自己的消息。"
)

_OPTIONAL_REPLY_POLICY = (
    "\n[可选回复策略 — 非 AT/DM/REPLY]"
    "\n本轮是登记类/积压/通知类唤醒（如 NEW_MESSAGE_BACKLOG、NEW_POKE、NEW_FRIEND、群管理通知等）："
    "先扫一眼上下文，**可以不接、不回复**；只有你判断需要介入时再 send/reply。"
    "无新信息或纯噪音时直接 `napcat alerts --clear` 后结束本轮即可。"
)


# ---------------------------------------------------------------------------
# Prompt construction helpers
# ---------------------------------------------------------------------------





def _extract_image_meta(event: dict) -> str:
    """Extract image metadata from event for wake prompt."""
    msg = event.get("message") if isinstance(event, dict) else None
    if not isinstance(msg, list):
        return ""

    parts = []
    for seg in msg:
        if not isinstance(seg, dict):
            continue
        seg_type = seg.get("type", "")
        data = seg.get("data", {})
        if not isinstance(data, dict):
            data = {}

        if seg_type == "image":
            details = []
            summary = data.get("summary", "")
            if isinstance(summary, str):
                summary = summary.strip()
            file_id = data.get("file", "") or data.get("file_id", "")
            url = data.get("url", "")
            sub_type = data.get("sub_type", "")
            file_size = data.get("file_size", "")

            if summary:
                details.append(f"摘要: {summary}")
            if file_id:
                details.append(f"file_id: {file_id}")
            if url:
                details.append(f"url: {url}")
            if sub_type != "" and sub_type is not None:
                # OneBot image sub_type: 0 normal, 1 face/sticker, etc.
                details.append(f"sub_type(图片类型0普通/1表情等)={sub_type}")
            if file_size:
                details.append(f"file_size(字节)={file_size}")

            if details:
                parts.append("[图片: " + ", ".join(details) + "]")

    return "; ".join(parts) if parts else ""


def _extract_reply_meta(event: dict) -> str:
    """Reply-chain context with *labeled* message IDs.

    Critical: ``napcat reply <mid>`` must use the **triggering** user message
    id (this event's message_id), NOT the bot message being quoted.
    """
    from napcat_cli.lib.identity import label_from_event_where, label_from_event_who

    trigger_mid = str(event.get("message_id") or "")
    # quoted id = message the user is replying to (often the bot's earlier msg)
    quoted_mid = ""
    segs = event.get("message", [])
    if isinstance(segs, list):
        for seg in segs:
            if isinstance(seg, dict) and seg.get("type") == "reply":
                data = seg.get("data") or {}
                if isinstance(data, dict):
                    quoted_mid = str(data.get("id") or data.get("message_id") or "")
                elif isinstance(data, str) and data.strip().lstrip("-").isdigit():
                    quoted_mid = data.strip()
                break
    if not quoted_mid:
        import re
        m = re.search(r"\[CQ:reply,id=(-?\d+)\]", str(event.get("raw_message") or ""))
        if m:
            quoted_mid = m.group(1)
    if not trigger_mid and not quoted_mid:
        return ""

    parts: list[str] = []
    if trigger_mid:
        parts.append(
            f"触发消息ID(message_id，对方刚发来的这条，`napcat reply` 的 <mid> 必须用它): {trigger_mid}"
        )
    if quoted_mid:
        parts.append(
            f"你的原消息ID(对方引用/回复的那条，仅上下文，不要当作 napcat reply 的 <mid>): {quoted_mid}"
        )
    parts.append(f"群组: {label_from_event_where(event)}")
    parts.append(f"发送者: {label_from_event_who(event)}")
    msg_text = _event_text(event)
    if msg_text:
        parts.append(f"触发消息内容: {msg_text[:80]}")
    if isinstance(segs, list):
        if any(isinstance(seg, dict) and seg.get("type") == "image" for seg in segs):
            parts.append("[含图片] 请用多模态查看，或 /napcat/get_image 下载")
        if any(isinstance(seg, dict) and seg.get("type") == "forward" for seg in segs):
            parts.append("[合并转发] 用 get_message / skills-fs message 路径拉全量")
    return "; ".join(parts)



def _event_text(event: dict) -> str:
    """Extract text content from event message segments."""
    msg = event.get("message") if isinstance(event, dict) else None
    if msg is None and isinstance(event, dict):
        msg = event.get("raw_message", "")
    if isinstance(msg, list):
        return "".join(
            (s.get("data") or {}).get("text", "")
            for s in msg
            if isinstance(s, dict) and s.get("type") == "text"
        ).strip()
    return str(msg or "").strip()


def _who(event: dict) -> str:
    """Sender as ``(群名片)(用户昵称)(QQ号)``."""
    from napcat_cli.lib.identity import label_from_event_who
    return label_from_event_who(event)


def _where(event: dict) -> str:
    """Group as ``(群备注)(群名)(群号)`` or ``私聊``."""
    from napcat_cli.lib.identity import label_from_event_where
    return label_from_event_where(event)


def build_prompt(reason: str, events: list[dict]) -> str:
    """Build a contextual wake prompt for a coalesced batch of events."""
    events = [e for e in events if isinstance(e, dict)]
    n = len(events)

    if reason in ("AT_ME", "REPLY_TO_ME", "DM_ME"):
        who = _who(events[-1]) if events else "?"
        where = _where(events[-1]) if events else "?"
        text = _event_text(events[-1]) if events else ""
        
        if reason == "AT_ME":
            verb = "被 @"
        elif reason == "REPLY_TO_ME":
            verb = "被回复"
        else:  # DM_ME
            verb = "收到私聊"
        
        head = f"你在{where}{verb}了" + (f"{n}次" if n > 1 else "")
        body = f"。最近一条来自 {who}：{text}" if text else ""
        # Explicit IDs for the *triggering* message (required for napcat reply)
        _ev = events[-1] if events else {}
        _trigger_mid = str(_ev.get("message_id") or "")
        _uid = str((_ev.get("sender") or {}).get("user_id") if isinstance(_ev.get("sender"), dict) else _ev.get("user_id") or "")
        _gid = str(_ev.get("group_id") or "")
        id_line = ""
        if _trigger_mid or _uid or _gid:
            bits = []
            if _trigger_mid:
                bits.append(f"触发消息ID(message_id，对方这条，reply 用它)={_trigger_mid}")
            if _uid:
                bits.append(f"对方QQ号(user_id)={_uid}")
            if _gid:
                bits.append(f"群号(group_id)={_gid}")
            else:
                bits.append("会话=私聊(无 group_id)")
            id_line = "\n[关键ID] " + "; ".join(bits)
        
        # Include image metadata if present
        image_meta = _extract_image_meta(events[-1]) if events else ""
        reply_meta = _extract_reply_meta(events[-1]) if events else ""
        
        meta_parts = []
        if image_meta:
            meta_parts.append(f"[图片信息] {image_meta}")
        if reply_meta:
            meta_parts.append(f"[回复链] {reply_meta}")
        
        meta = "\n" + "\n".join(meta_parts) if meta_parts else ""
        
        # Build context-aware prompt with exploration hints
        context_hint = ""
        if image_meta:
            context_hint = (
                "\n[图片处理提示] 此消息包含图片。注意：QQ图片URL有防盗链限制，不可直接访问。"
                "必须先用 `napcat get_image <url>` (CLI) 或写入 `/napcat/get_image` (skills-fs) 下载到本地，"
                "然后再进行 PaddleOCR 文字识别或多模态视觉分析。"
            )
        # Encourage proactive context gathering
        explore_hint = (
            "\n[建议] 收到消息后："
            "1) 先读取上下文（napcat events/alerts 或 skills-fs 最近 10-20 条）"
            "2) 有图片 -> 必须先 `napcat get_image` / `/napcat/get_image` 再 OCR/视觉"
            "3) 有合并转发/回复链 -> `napcat get_message` / skills-fs message 路径拉全量"
            "4) **必须** 调用 napcat-cli 或 skills-fs 向 QQ 发出回复（可反驳/拒绝）；若用 `napcat reply`，<mid> 必须是上方「触发消息ID」(对方刚发的那条)，不是「你的原消息ID」；"
            "仅在本会话打字 QQ 用户看不到"
            "5) 看到 `Sent message_id=...`（或 FS 写入成功）后再结束本轮"
        )
        # Include read event IDs and seen/read status
        read_event_ids = []
        seen_status = {}
        for e in events:
            eid = e.get("id")
            if eid:
                read_event_ids.append(str(eid))
                seen = e.get("seen")
                if seen is not None:
                    seen_status[str(eid)] = bool(seen)
                read_ts = e.get("read_timestamp")
                if read_ts is not None:
                    if str(eid) not in seen_status:
                        seen_status[str(eid)] = {}
                    seen_status[str(eid)]["read"] = True

        context_info = ""
        if read_event_ids:
            context_info += f"\n[已读事件ID] {', '.join(read_event_ids[:20])}" + ("..." if len(read_event_ids) > 20 else "")
        if seen_status:
            seen_count = sum(1 for v in seen_status.values() if (isinstance(v, bool) and v) or (isinstance(v, dict) and v.get("seen")))
            unread_count = len(seen_status) - seen_count
            context_info += f"\n[已读/未读] 已读 {seen_count} 条，未读 {unread_count} 条"

        from napcat_cli.lib.identity import IDENTITY_LEGEND
        return (
            f"{IDENTITY_LEGEND}\n"
            f"【QQ {reason}】{head}{body}{id_line}{meta}{context_hint}{explore_hint}"
            f"{context_info}{_MUST_REPLY_POLICY}\n{_PROMPT_FOOTER}"
        )


    if reason == "NEW_MESSAGE_BACKLOG":
        from napcat_cli.lib.identity import IDENTITY_LEGEND
        return (
            f"{IDENTITY_LEGEND}\n"
            f"【QQ 未读积压】有约 {n} 条未读新消息积压了一段时间，请扫一眼收件箱；"
            f"仅在需要时回复，可以不接。\n{_OPTIONAL_REPLY_POLICY}\n{_PROMPT_FOOTER}"
        )

    if reason in ("NEW_MESSAGE", "GROUP_TRIGGER", "PRIVATE_TRIGGER"):
        from napcat_cli.lib.identity import IDENTITY_LEGEND
        e = events[-1] if events else {}
        text = _event_text(e) if e else ""
        mid = str(e.get("message_id") or "")
        uid = str((e.get("sender") or {}).get("user_id") if isinstance(e.get("sender"), dict) else e.get("user_id") or "")
        gid = str(e.get("group_id") or "")
        id_bits = []
        if mid:
            id_bits.append(f"触发消息ID(message_id)={mid}")
        if uid:
            id_bits.append(f"对方QQ号(user_id)={uid}")
        if gid:
            id_bits.append(f"群号(group_id)={gid}")
        id_line = ("\n[关键ID] " + "; ".join(id_bits)) if id_bits else ""
        return (
            f"{IDENTITY_LEGEND}\n"
            f"【QQ 新消息/{reason}】收到 {n} 条。最近：{_where(e) if e else ''} "
            f"{_who(e) if e else ''}：{text}{id_line}\n"
            f"{_OPTIONAL_REPLY_POLICY}\n{_PROMPT_FOOTER}"
        )

    if reason == "NEW_FRIEND":
        people = []
        for e in events:
            uid = e.get("user_id")
            if uid:
                people.append(f"{_who({'user_id': uid, 'sender': e.get('sender') or {'user_id': uid}})} [user_id={uid}]")
        return (
            f"【QQ 新好友】新增好友 {n} 个：{'; '.join(people) or '(无明细)'}。"
            f"可酌情用 `napcat send private <user_id> --message` 打招呼，或忽略。"
            f"\n{_OPTIONAL_REPLY_POLICY}\n{_PROMPT_FOOTER}"
        )

    if reason == "NEW_REQUEST":
        reqs = []
        for e in events:
            rt = e.get("request_type", "?")
            sub = e.get("sub_type", "")
            comment = str(e.get("comment", ""))[:40]
            uid = e.get("user_id", "")
            who = _who({"user_id": uid, "group_id": e.get("group_id"), "sender": e.get("sender") or {"user_id": uid}})
            flag = e.get("flag", "")
            bit = f"类型={rt}/{sub or '-'} 申请人={who} [user_id={uid}]"
            if e.get("group_id"):
                bit += f" 目标群={_where(e)} [group_id={e.get('group_id')}]"
            if flag:
                bit += f" flag(审批用凭证)={flag}"
            if comment:
                bit += f" 验证信息「{comment}」"
            reqs.append(bit)
        return (
            f"【QQ 请求】收到 {n} 个加好友/加群请求：{' || '.join(reqs)}。"
            f"同意/拒绝用 napcat api set_friend_add_request / set_group_add_request（需要上面的 flag）。"
            f"\n{_OPTIONAL_REPLY_POLICY}\n{_PROMPT_FOOTER}"
        )

    if reason == "BOT_BANNED":
        e = events[-1] if events else {}
        op = e.get("operator_id") or e.get("user_id")
        dur = e.get("duration", "?")
        return (
            f"【QQ 被禁言】你在群 {_where(e)} [group_id={e.get('group_id','')}] 被禁言；"
            f"操作者 {_who({'user_id': op, 'group_id': e.get('group_id'), 'sender': {'user_id': op}})} [operator_id/user_id={op}]；"
            f"时长 duration={dur} 秒。请知悉（通常无需回复）。\n{_OPTIONAL_REPLY_POLICY}"
        )

    if reason == "BOT_KICKED_FROM_GROUP":
        places = []
        for e in events:
            places.append(f"{_where(e)} [group_id={e.get('group_id','')}] 操作者user_id={e.get('operator_id','?')}")
        return (
            f"【QQ 被踢出群】你被踢出/移除了 {n} 个群：{'; '.join(places) or '(无明细)'}。请知悉。\n{_OPTIONAL_REPLY_POLICY}"
        )

    if reason == "GROUP_ADMIN_CHANGE":
        bits = []
        for e in events:
            bits.append(
                f"{_where(e)} [group_id={e.get('group_id','')}] "
                f"对象={_who({'user_id': e.get('user_id'), 'group_id': e.get('group_id'), 'sender': {'user_id': e.get('user_id')}})} "
                f"[user_id={e.get('user_id','')}] sub_type={e.get('sub_type','?')}"
            )
        return (
            f"【QQ 管理员变动】{'; '.join(bits) or '你的群管理员权限发生变动'}。请知悉。\n{_OPTIONAL_REPLY_POLICY}"
        )

    if reason in ("NEW_POKE", "PROFILE_LIKE"):
        e = events[-1] if events else {}
        src = e.get("sender_id") or e.get("operator_id") or e.get("user_id")
        who = _who({
            "user_id": src,
            "group_id": e.get("group_id"),
            "sender": e.get("sender") if isinstance(e.get("sender"), dict) else {"user_id": src},
        })
        where = _where(e)
        extra = f" times(点赞次数)={e.get('times')}" if e.get("times") is not None else ""
        return (
            f"【QQ {'资料卡点赞' if reason=='PROFILE_LIKE' else '戳一戳'}】"
            f"{who} [user_id={src}] 在 {where}"
            + (f" [group_id={e.get('group_id')}]" if e.get("group_id") else "")
            + f" 戳了你/赞了你 共 {n} 次事件{extra}。"
            f"可酌情互动，可以不接。\n{_OPTIONAL_REPLY_POLICY}"
        )

    if reason == "NEW_GROUP_MEMBER":
        bits = []
        for e in events:
            uid = e.get("user_id")
            if not uid:
                continue
            bits.append(
                f"{_who({'user_id': uid, 'group_id': e.get('group_id'), 'sender': e.get('sender') or {'user_id': uid}})} "
                f"[user_id={uid}] 加入 {_where(e)} [group_id={e.get('group_id','')}]"
            )
        return (
            f"【QQ 新群成员】{n} 个新成员：{'; '.join(bits) or '(无明细)'}。可酌情欢迎。"
            f"\n{_OPTIONAL_REPLY_POLICY}\n{_PROMPT_FOOTER}"
        )

    if reason == "BOT_OFFLINE":
        return "【QQ 掉线】NapCat bot 连接丢失/离线。请检查容器与登录状态。\n" + _OPTIONAL_REPLY_POLICY

    # generic fallback
    summaries = "; ".join(str(e.get("summary", ""))[:60] for e in events if e.get("summary"))
    return (
        f"【QQ 事件 {reason}】{summaries or f'{n} 个事件'}。请查看 napcat events。"
        f"\n{_OPTIONAL_REPLY_POLICY}\n{_PROMPT_FOOTER}"
    )


class WakeOrchestrator:
    def __init__(
        self,
        waker: Waker,
        *,
        log: Callable[[str], None] = lambda _msg: None,
        debounce_seconds: float = 3.0,
        cooldown_seconds: float = 30.0,
        new_message_idle_seconds: int = 300,
        legacy_command: str = "",
        legacy_session: str = "",
        wake_timeout: float = 120.0,
        max_concurrent_wakes: int = 3,
        immediate_min_interval: float = 5.0,
        self_id: str = "",
        events_reader: "EventsReader | None" = None,
    ):
        self.waker = waker
        self.log = log
        self.debounce_seconds = debounce_seconds
        self.cooldown_seconds = cooldown_seconds
        self.new_message_idle_seconds = new_message_idle_seconds
        self.legacy_command = legacy_command
        self.legacy_session = legacy_session
        self.wake_timeout = wake_timeout
        self.max_concurrent_wakes = max_concurrent_wakes
        self.immediate_min_interval = immediate_min_interval
        self.self_id = str(self_id) if self_id else ""
        self.events_reader = events_reader

        self._lock = threading.Lock()
        self._pending: dict[str, list[dict]] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._last_wake: dict[str, float] = {}
        self._last_immediate_wake: dict[str, float] = {}

        # unread-new-message tracking for backlog sweep (in-memory; best-effort)
        self._unread_since: float | None = None
        self._unread_count: int = 0
        self._last_message_wake: float = 0.0

        self._queue: "queue.Queue[tuple[str, str, list[dict]] | None]" = queue.Queue()
        self._worker = threading.Thread(target=self._run, name="napcat-wake-worker", daemon=True)
        self._worker.start()

        # active wake tracking for concurrency limit
        self._active_wakes: set[str] = set()
        self._active_wakes_lock = threading.Lock()
    # -- public API --------------------------------------------------------

    def submit(self, reason: str, event: dict | None = None, *, self_triggered: bool = False) -> None:
        """Queue a wake for ``reason`` (debounced). Non-blocking."""
        with self._lock:
            if self_triggered:
                self.log(f"[WAKE] submit self_triggered={self_triggered} reason={reason} (will be filtered in flush)")
            self._pending.setdefault(reason, []).append(event or {})
            n = len(self._pending[reason])
            # (re)start debounce timer
            old = self._timers.get(reason)
            if old:
                old.cancel()
            # near-immediate for direct mentions (coalesces sub-second bursts), debounce otherwise
            delay = min(self.debounce_seconds, 1.0) if reason in _IMMEDIATE else self.debounce_seconds
            t = threading.Timer(delay, self._flush, args=(reason,))
            t.daemon = True
            t.start()
            self._timers[reason] = t

            self.log(f"[WAKE] queued reason={reason} pending={n} debounce={delay:.1f}s "
                     f"primary={getattr(self.waker, 'primary', '?')}")


    def note_new_message(self, event_time: float) -> None:
        """Track an incoming NEW_MESSAGE for backlog detection (not a wake)."""
        with self._lock:
            if self._unread_since is None:
                self._unread_since = event_time or time.time()
            self._unread_count += 1

    def maybe_backlog_sweep(self, now: float | None = None) -> bool:
        """Called periodically. Fire a backlog wake if unread messages are stale.

        Returns True if a backlog wake was queued.
        """
        now = now or time.time()
        with self._lock:
            if self._unread_since is None or self._unread_count == 0:
                return False
            idle = now - self._unread_since
            if idle < self.new_message_idle_seconds:
                return False
            # Fire backlog wake - use empty events since we only care about count
            count = self._unread_count
            self._unread_count = 0
            self._unread_since = None
        self._enqueue("NEW_MESSAGE_BACKLOG", [{}] * count)
        return True

    def _flush(self, reason: str) -> None:
        """Timer callback: apply cooldown, then enqueue a coalesced wake."""
        with self._lock:
            self._timers.pop(reason, None)
            events = self._pending.pop(reason, [])
            if not events:
                return
            now = time.time()
            
            # Check for self-triggered events (bot's own actions)
            if self.self_id:
                filtered_events = []
                for event in events:
                    sender = event.get("sender") if isinstance(event, dict) else None
                    if isinstance(sender, dict):
                        sender_id = str(sender.get("user_id", ""))
                        if sender_id and sender_id == self.self_id:
                            # Self-triggered event - log but don't wake
                            self.log(f"[WAKE] skipped self-triggered reason={reason} sender={sender_id}")
                            continue
                    filtered_events.append(event)
                if not filtered_events:
                    return
                events = filtered_events
            
            if reason not in _IMMEDIATE:
                last = self._last_wake.get(reason, 0)
                if now - last < self.cooldown_seconds:
                    return
            self._last_wake[reason] = now
            self._unread_count = 0
        self._enqueue(reason, events)

    def _fire_now(self, reason: str, count: int) -> None:
        """Immediately fire a wake (bypassing debounce/cooldown) for backlog."""
        events = [{}] * count
        self._enqueue(reason, events)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is None:
                break
            reason, _ctx, events = item
            
            # Enforce concurrency limit for immediate reasons
            if reason in _IMMEDIATE:
                with self._active_wakes_lock:
                    if len(self._active_wakes) >= self.max_concurrent_wakes:
                        self.log(f"[WAKE] max concurrent wakes reached ({self.max_concurrent_wakes}), deferring {reason}")
                        # re-queue with a small delay
                        threading.Timer(0.5, lambda: self.submit(reason, events[0] if events else None)).start()
                        self._queue.task_done()
                        continue
                    self._active_wakes.add(reason)
            
            try:
                # Auto-mark seen: mark event IDs as seen when included in wake prompt
                if self.events_reader and events:
                    event_ids = [e.get("id") for e in events if isinstance(e, dict) and e.get("id")]
                    if event_ids:
                        self.events_reader.mark_seen(event_ids)
                
                # Add seen/read status to events for prompt
                if self.events_reader and events:
                    event_ids = [e.get("id") for e in events if isinstance(e, dict) and e.get("id")]
                    if event_ids:
                        seen_status = self.events_reader.get_seen_status(event_ids)
                        for e in events:
                            if isinstance(e, dict):
                                eid = e.get("id")
                                if eid and eid in seen_status:
                                    e["seen"] = seen_status[eid]
                                    # Also check if read
                                    from napcat_cli.lib.events_sqlite import get_connection
                                    conn = get_connection(self.events_reader.data_dir)
                                    cur = conn.execute("SELECT read_timestamp FROM events WHERE id = ?", (eid,))
                                    row = cur.fetchone()
                                    if row and row[0]:
                                        e["read"] = True
                                    conn.close()
                
                # Build prompt
                prompt = build_prompt(reason, events)
                # Delegate to waker
                result = self.waker.wake(prompt, reason, {}, timeout=self.wake_timeout)
                if result.ok:
                    self.log(f"[WAKE] delivered reason={reason} transport={result.transport} detail={result.detail[:100]}")
                else:
                    self.log(f"[WAKE] failed reason={reason} transport={result.transport} detail={result.detail}")
            except Exception as e:
                self.log(f"Wake error: {e}")
            finally:
                if reason in _IMMEDIATE:
                    with self._active_wakes_lock:
                        self._active_wakes.discard(reason)
                self._queue.task_done()

    def _enqueue(self, reason: str, events: list[dict]) -> None:
        """Enqueue a wake for the worker thread."""
        self._queue.put((reason, "", events))




# Module exports
__all__ = [
    "WakeOrchestrator",
    "build_prompt",
    "_IMMEDIATE",
    "_MESSAGE_REASONS",
]