"""Tests for napcat_cli.wake_orchestrator (debounce, cooldown, backlog, prompts)."""
from __future__ import annotations

import threading
import time

from napcat_cli.wake_backend import WakeResult
from napcat_cli.wake_orchestrator import WakeOrchestrator, build_prompt


class FakeWaker:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        self.primary = "auto"
        self.empty = False

    def wake(self, prompt, reason, ctx=None, *, idem_key="", dry_run=False, timeout=30.0):
        with self._lock:
            self.calls.append((reason, prompt))
        return WakeResult(True, "cli", "fake", extra={"reply": "ok"})


# ---------------------------------------------------------------------------
# build_prompt
# ---------------------------------------------------------------------------

class TestBuildPrompt:
    def test_at_me_includes_who_where_text(self):
        events = [{
            "user_id": 123, "sender": {"nickname": "Alice"}, "group_id": 456,
            "message": [{"type": "text", "data": {"text": "在吗"}}],
        }]
        p = build_prompt("AT_ME", events)
        assert "AT_ME" in p
        assert "Alice" in p
        assert "456" in p
        assert "在吗" in p

    def test_backlog_mentions_count(self):
        p = build_prompt("NEW_MESSAGE_BACKLOG", [{}, {}, {}])
        assert "3" in p

    def test_new_friend_lists_ids(self):
        p = build_prompt("NEW_FRIEND", [{"user_id": 111}, {"user_id": 222}])
        assert "111" in p and "222" in p

    def test_generic_fallback(self):
        p = build_prompt("SOMETHING_NEW", [{"summary": "boom"}])
        assert "SOMETHING_NEW" in p and "boom" in p


# ---------------------------------------------------------------------------
# Debounce / cooldown / backlog (timing-based; small intervals, generous waits)
# ---------------------------------------------------------------------------

class TestOrchestration:
    def test_debounce_coalesces_burst_into_one_wake(self):
        fk = FakeWaker()
        o = WakeOrchestrator(fk, log=lambda m: None, debounce_seconds=0.05, cooldown_seconds=100)
        for i in range(3):
            o.submit("GROUP_TRIGGER", {"user_id": i, "message": [{"type": "text", "data": {"text": "hi"}}]})
        time.sleep(0.3)
        assert len(fk.calls) == 1
        reason, prompt = fk.calls[0]
        assert reason == "GROUP_TRIGGER"
        assert "3" in prompt  # three events coalesced

    def test_cooldown_suppresses_repeat(self):
        fk = FakeWaker()
        o = WakeOrchestrator(fk, log=lambda m: None, debounce_seconds=0.05, cooldown_seconds=100)
        o.submit("GROUP_TRIGGER", {})
        time.sleep(0.3)
        assert len(fk.calls) == 1
        o.submit("GROUP_TRIGGER", {})  # still within 100s cooldown
        time.sleep(0.3)
        assert len(fk.calls) == 1  # suppressed

    def test_at_me_bypasses_cooldown(self):
        fk = FakeWaker()
        o = WakeOrchestrator(fk, log=lambda m: None, debounce_seconds=0.05, cooldown_seconds=100)
        o.submit("AT_ME", {"user_id": 1})
        time.sleep(0.3)
        o.submit("AT_ME", {"user_id": 2})  # would be suppressed for non-immediate reasons
        time.sleep(0.3)
        assert len(fk.calls) == 2

    def test_backlog_fires_when_unread_stale(self):
        fk = FakeWaker()
        o = WakeOrchestrator(fk, log=lambda m: None, new_message_idle_seconds=1)
        o.note_new_message(time.time() - 200)  # 200s-old unread
        assert o.maybe_backlog_sweep() is True
        time.sleep(0.3)  # let the worker process the synthesized wake
        assert any(r == "NEW_MESSAGE_BACKLOG" for r, _ in fk.calls)

    def test_backlog_does_not_refire_immediately(self):
        fk = FakeWaker()
        o = WakeOrchestrator(fk, log=lambda m: None, new_message_idle_seconds=1)
        o.note_new_message(time.time() - 200)
        assert o.maybe_backlog_sweep() is True
        assert o.maybe_backlog_sweep() is False  # unread consumed + recent message wake

    def test_backlog_idle_below_threshold_no_fire(self):
        fk = FakeWaker()
        o = WakeOrchestrator(fk, log=lambda m: None, new_message_idle_seconds=600)
        o.note_new_message(time.time())  # fresh
        assert o.maybe_backlog_sweep() is False
        assert fk.calls == []
