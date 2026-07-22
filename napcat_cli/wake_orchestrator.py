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
from typing import Any, Callable

from .wake_backend import Waker
from .wake import render_wake_command

# Reasons that should wake near-immediately and ignore cooldown.
_IMMEDIATE = {"AT_ME", "REPLY_TO_ME", "DM_ME"}
# Message-class reasons — a wake for any of these counts as "the agent read the inbox".
_MESSAGE_REASONS = {"AT_ME", "REPLY_TO_ME", "DM_ME", "NEW_MESSAGE", "NEW_MESSAGE_BACKLOG",
                    "GROUP_TRIGGER", "PRIVATE_TRIGGER"}

_PROMPT_FOOTER = (
    "你可以用 `napcat events` / `napcat alerts` 查看详情，用 `napcat send`/`napcat reply` 回复。"
    "\n可用技能: `/napcat/ocr` (OCR识图), `/napcat/get_image` (下载图片), "
    "`/napcat/groups/:group_id/:time_range/:message_id/:content` (获取媒体内容)。"
)


# ---------------------------------------------------------------------------
# Prompt construction helpers
# ---------------------------------------------------------------------------

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
    s = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    nick = s.get("nickname") or event.get("user_id") or "?"
    return f"{nick}({event.get('user_id', '?')})"


def _where(event: dict) -> str:
    g = event.get("group_id")
    return f"群{g}" if g else "私聊"


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
        
        if seg_type == "image":
            details = []
            summary = data.get("summary", "").strip()
            file_id = data.get("file", "").strip() or data.get("file_id", "").strip()
            url = data.get("url", "").strip()
            sub_type = data.get("sub_type", "").strip()
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
    """Extract reply chain metadata from event."""
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
                return f"回复消息ID: {reply_id}"
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
    s = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    nick = s.get("nickname") or event.get("user_id") or "?"
    return f"{nick}({event.get('user_id', '?')})"


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
            action = "请尽快查看并回复。"
        elif reason == "REPLY_TO_ME":
            verb = "被回复"
            action = "请查看并酌情回复。"
        else:  # DM_ME
            verb = "收到私聊"
            action = "请尽快查看并回复。"
        
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
        
        return f"【QQ {reason}】{head}{body}{meta}。请尽快查看并回复。\n{_PROMPT_FOOTER}"

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


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

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

    def submit(self, reason: str, event: dict | None = None) -> None:
        """Queue a wake for ``reason`` (debounced). Non-blocking."""
        with self._lock:
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
            self._timers[reason] = t
            t.start()
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
            since_wake = now - self._last_message_wake
            if idle < self.new_message_idle_seconds:
                return False
            if since_wake < self.new_message_idle_seconds:
                return False
            count = self._unread_count
            # consume the unread batch — the agent will read them
            self._unread_since = None
            self._unread_count = 0
            self._last_message_wake = now
        self.log(f"[WAKE] backlog reason=NEW_MESSAGE_BACKLOG unread={count} "
                 f"idle={int(idle)}s>={self.new_message_idle_seconds}s")
        # synthesize a backlog reason via submit/cooldown-bypass
        with self._lock:
            self._pending.setdefault("NEW_MESSAGE_BACKLOG", []).extend([{}] * count)
        self._fire_now("NEW_MESSAGE_BACKLOG", count)
        return True

    def stop(self) -> None:
        self._queue.put(None)
        with self._lock:
            for t in list(self._timers.values()):
                t.cancel()
            self._timers.clear()

    # -- internals ---------------------------------------------------------

    def _flush(self, reason: str) -> None:
        """Timer callback: apply cooldown, then enqueue a coalesced wake."""
        with self._lock:
            self._timers.pop(reason, None)
            events = self._pending.pop(reason, [])
            if not events:
                return
            now = time.time()
            if reason not in _IMMEDIATE:
                last = self._last_wake.get(reason, 0)
                if now - last < self.cooldown_seconds:
                    return
            self._last_wake[reason] = now
            self._unread_count = 0
        self._enqueue(reason, events)

    def _enqueue(self, reason: str, events: list[dict]) -> None:
        """Enqueue a wake for the worker thread."""
        self._queue.put((reason, "", events))

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
                # Build prompt
                prompt = build_prompt(reason, events)
                # Delegate to waker
                self.waker.wake(prompt, reason, {})
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

    def _fire_now(self, reason: str, count: int) -> None:
        """Immediately fire a wake (bypassing debounce/cooldown) for backlog."""
        events = [{}] * count
        self._enqueue(reason, events)


# Module exports
__all__ = [
    "WakeOrchestrator",
    "build_prompt",
    "_IMMEDIATE",
    "_MESSAGE_REASONS",
]