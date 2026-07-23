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
    "\n[重要] 若在 /text 或 /text_raw 中写入 CQ 码字符串（如 [CQ:at,qq=123]），/text 会被正确解析为段落，/text_raw 会原样发送（不解析）并返回警告提示你改用 /text。"
    "\n\nSchema 位置：skills-fs 挂载点 `/napcat/schemas/` 下有 `reply_text.schema.json` `reply_text_raw.schema.json` `reply_image.schema.json` `reply_cqcode.schema.json` `reply_at.schema.json` `reply_json.schema.json`"
    "\n                        `send_text.schema.json` `send_text_raw.schema.json` `send_image.schema.json` `send_cqcode.schema.json` `send_at.schema.json` `send_json.schema.json`"
    "\n可直接读取 schema 确认字段要求。"
    "\n图片处理: 使用 `napcat get_image <url>` (CLI) 或 `/napcat/get_image` (skills-fs) 下载图片; "
    "PaddleOCR 已集成，自动识别文字（结果在图片信息中已提供），也可直接用多模态视觉能力阅读图片(URL在图片信息中已提供)。"
    "\n合并转发: 使用 `napcat group <gid> get_message <mid>` (CLI) 或 "
    "`/napcat/groups/:group_id/:time_range/:message_id/:content` (skills-fs) 获取完整内容。"
    "\n群管理: `napcat group list` / `napcat group info <gid>`"
    "\n[唤醒队列机制] 同一 session 的唤醒请求会被串行化队列处理。如果你发送了多条消息或有多个事件触发唤醒，它们会按顺序依次处理，不会并发。"
    "若你已在某个唤醒轮次中处理了相关内容，后续唤醒若无新内容可选择不再回复。"
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
            if sub_type:
                details.append(f"sub_type: {sub_type}")
            if file_size:
                details.append(f"size: {file_size}")

            if details:
                parts.append("[图片: " + ", ".join(details) + "]")

    return "; ".join(parts) if parts else ""


def _extract_reply_meta(event: dict) -> str:
    """Extract reply chain metadata from event with full context."""
    msg = event.get("message") if isinstance(event, dict) else None
    if not isinstance(msg, list):
        return ""
    
    for seg in msg:
        if not isinstance(seg, dict):
            continue
        seg_type = seg.get("type", "")
        data = seg.get("data", {})
        
        if seg_type == "reply":
            reply_id = data.get("id", "")
            if reply_id:
                # Build detailed reply context
                parts = [f"回复消息ID: {reply_id}"]
                
                # Add group/private context
                group_id = event.get("group_id", "")
                sender = event.get("sender", {})
                sender_name = ""
                sender_uid = ""
                if isinstance(event.get("sender"), dict):
                    sender_name = sender.get("nickname") or ""
                    sender_uid = str(sender.get("user_id", ""))
                    card = sender.get("card", "")
                    if card:
                        sender_name = f"{sender_name}({card})"
                
                if group_id:
                    parts.append(f"群组: {group_id}")
                else:
                    parts.append("私聊")
                
                if sender_name or sender_uid:
                    parts.append(f"发送者: {sender_name}({sender_uid})")
                
                # Add message context
                msg_text = _event_text(event)
                if msg_text:
                    parts.append(f"消息内容: {msg_text[:50]}")
                
                # Image hints
                msg = event.get("message") if isinstance(event, dict) else None
                if isinstance(msg, list):
                    has_image = any(isinstance(seg, dict) and seg.get("type") == "image" for seg in msg)
                    if has_image:
                        parts.append("[含图片] 请使用多模态视觉查看图片，或用 /napcat/get_image 下载，/napcat/groups/.../:message_id/:content 获取媒体内容")
                    
                    # Check for merge/forward messages
                    has_forward = any(isinstance(seg, dict) and seg.get("type") == "forward" for seg in msg)
                    if has_forward:
                        parts.append("[合并转发] 请使用 /napcat/groups/:group_id/:time_range/:message_id/:content 获取完整转发内容")
                return "; ".join(parts)
    return ""


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
    """Extract sender identity string for wake prompt (nickname + user_id)."""
    sender = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    nick = sender.get("nickname") or event.get("user_id") or "?"
    uid = sender.get("user_id") or event.get("user_id") or "?"
    return f"{nick}({uid})"


def _where(event: dict) -> str:
    g = event.get("group_id")
    return f"群{g}" if g else "私聊"


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
            context_hint += (
                "\n[图片处理提示] 此消息包含图片。图片 URL 已在上方提供。"
                "如需下载本地分析，可用 `napcat get_image <url>` (CLI) 或写入 `/napcat/get_image` (skills-fs)。"
            )
        # Encourage proactive context gathering
        explore_hint = (
            "\n[建议] 收到消息后，建议："
            "1) 先读取上下文（用 napcat events/alerts 或 skills-fs 查看最近 10-20 条）"
            "2) 有图片 -> 直接用多模态视觉看 URL，或用 CLI `napcat get_image <url>` / FS `/napcat/get_image` 下载后用 PaddleOCR/多模态识别"
            "3) 有合并转发/回复链 -> 用 CLI `napcat group <gid> get_message <mid>` 或 FS `/napcat/groups/:gid/:range/:mid/:content` 拉取完整内容"
            "4) 回复方式：CLI `napcat reply <mid> -m \"内容\"` / `napcat send group <gid> -m \"内容\"` / `napcat send private <uid> -m \"内容\"`；FS 写入 `/napcat/groups/:gid/:range/:mid/reply/text` (智能文本，自动识别 CQ 码/at/图片) 或 `/reply/text_raw` (纯文本) / `/reply/image` / `/reply/json` 等"
            "5) 再决定如何回复"
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
        
        return f"【QQ {reason}】{head}{body}{meta}{context_hint}{explore_hint}\n{_PROMPT_FOOTER}"

    if reason == "NEW_MESSAGE_BACKLOG":
        return f"【QQ 未读积压】有约 {n} 条未读新消息积压了一段时间，请扫一眼收件箱，酌情回复需要回复的。\n{_PROMPT_FOOTER}"

    if reason in ("NEW_MESSAGE", "GROUP_TRIGGER", "PRIVATE_TRIGGER"):
        text = _event_text(events[-1]) if events else ""
        return f"【QQ 新消息】收到 {n} 条新消息。最近：{_where(events[-1]) if events else ''} {_who(events[-1]) if events else ''}：{text}\n{_PROMPT_FOOTER}"

    if reason == "NEW_FRIEND":
        ids = sorted({str(e.get("user_id", "")) for e in events if e.get("user_id")})
        return f"【QQ 新好友】新增好友 {n} 个：{', '.join(ids)}。可酌情打招呼或忽略。\n{_PROMPT_FOOTER}"

    if reason == "NEW_REQUEST":
        reqs = []
        for e in events:
            rt = e.get("request_type", "?")
            sub = e.get("sub_type", "")
            comment = str(e.get("comment", ""))[:40]
            reqs.append(f"{rt}/{sub} from {e.get('user_id','?')}" + (f"「{comment}」" if comment else ""))
        return f"【QQ 请求】收到 {n} 个加好友/加群请求：{'; '.join(reqs)}。请决定是否同意（用 napcat api set_friend_add_request/set_group_add_request）。\n{_PROMPT_FOOTER}"

    if reason == "BOT_BANNED":
        e = events[-1] if events else {}
        return f"【QQ 被禁言】你在{e.get('group_id','?')}被禁言，操作者 {e.get('operator_id','?')}，时长 {e.get('duration','?')}s。请知悉。"

    if reason == "BOT_KICKED_FROM_GROUP":
        return f"【QQ 被踢出群】你被踢出/移除了 {n} 个群。请知悉。"

    if reason == "GROUP_ADMIN_CHANGE":
        return f"【QQ 管理员变动】你的群管理员权限发生变动。请知悉。"

    if reason in ("NEW_POKE", "PROFILE_LIKE"):
        e = events[-1] if events else {}
        return f"【QQ 戳一戳】{e.get('sender_id') or e.get('operator_id','?')} 戳了你/赞了你 {n} 次。可酌情互动。"

    if reason == "NEW_GROUP_MEMBER":
        ids = sorted({str(e.get("user_id", "")) for e in events if e.get("user_id")})
        return f"【QQ 新群成员】{n} 个新成员加入：{', '.join(ids)}。可酌情欢迎。\n{_PROMPT_FOOTER}"

    if reason == "BOT_OFFLINE":
        return "【QQ 掉线】NapCat bot 连接丢失/离线。请检查容器与登录状态。"

    # generic fallback
    summaries = "; ".join(str(e.get("summary", ""))[:60] for e in events if e.get("summary"))
    return f"【QQ 事件 {reason}】{summaries or f'{n} 个事件'}。请查看 napcat events。\n{_PROMPT_FOOTER}"
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