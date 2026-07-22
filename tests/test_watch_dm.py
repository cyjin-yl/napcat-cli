"""Tests for EventProcessor private-message → DM_ME wake wiring.

A private (DM) message must wake the agent at AT_ME level (reason ``DM_ME``):
near-immediate, cooldown bypassed. An @-mention inside a DM still wakes via
``AT_ME`` (not ``DM_ME``) to avoid a duplicate immediate wake.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from napcat_cli.daemon.watch import EventProcessor


def _event(text: str = "在吗", *, at: bool = False, self_id: str = "100",
           message_type: str = "private", group_id: str = "") -> dict:
    raw = f"[CQ:at,qq={self_id}] {text}" if at else text
    ev = {
        "post_type": "message",
        "message_type": message_type,
        "user_id": 123,
        "sender": {"user_id": 123, "nickname": "Alice"},
        "message": [{"type": "text", "data": {"text": raw}}],
        "raw_message": raw,
        "message_id": 1,
        "time": 0,
    }
    if group_id:
        ev["group_id"] = group_id
    return ev


def _reasons(orch: MagicMock) -> list[str]:
    return [c.args[0] for c in orch.submit.call_args_list]


def test_private_message_wakes_dm_me(tmp_path):
    orch = MagicMock()
    proc = EventProcessor(tmp_path, self_id="100", orchestrator=orch)
    proc._handle_message(_event())
    assert "DM_ME" in _reasons(orch)


def test_private_at_mention_wakes_at_me_not_dm_me(tmp_path):
    orch = MagicMock()
    proc = EventProcessor(tmp_path, self_id="100", orchestrator=orch)
    proc._handle_message(_event(at=True))
    reasons = _reasons(orch)
    assert "AT_ME" in reasons
    assert "DM_ME" not in reasons


def test_group_message_does_not_wake_dm_me(tmp_path):
    orch = MagicMock()
    proc = EventProcessor(tmp_path, self_id="100", orchestrator=orch)
    proc._handle_message(_event(message_type="group", group_id="456"))
    assert "DM_ME" not in _reasons(orch)