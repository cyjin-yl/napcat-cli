"""REPLY_TO_ME detection: pure quote/reply to bot without @ must wake."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from napcat_cli.daemon.watch import EventProcessor


def _reasons(orch: MagicMock) -> list[str]:
    return [c.args[0] for c in orch.submit.call_args_list]


def _reply_event(*, reply_to: str = "999", at_bot: bool = False, self_id: str = "10001",
                 message_type: str = "group", group_id: str = "201644592",
                 msg_id: str = "1332767306", text: str = "早说了说点漂亮话") -> dict:
    segs = [{"type": "reply", "data": {"id": str(reply_to)}}]
    raw = f"[CQ:reply,id={reply_to}]"
    if at_bot:
        segs.append({"type": "at", "data": {"qq": str(self_id)}})
        raw += f"[CQ:at,qq={self_id}] "
    segs.append({"type": "text", "data": {"text": text}})
    raw += text
    return {
        "post_type": "message",
        "message_type": message_type,
        "group_id": group_id,
        "user_id": 1293883574,
        "sender": {"user_id": 1293883574, "nickname": "復州河野鳥"},
        "message": segs,
        "raw_message": raw,
        "message_id": int(msg_id) if str(msg_id).isdigit() else msg_id,
        "self_id": int(self_id) if str(self_id).isdigit() else self_id,
        "time": 0,
    }


def test_parse_reply_target_from_segment():
    ev = _reply_event(reply_to="824539191")
    assert EventProcessor._parse_reply_target_id(ev) == "824539191"


def test_parse_reply_target_from_cq_only():
    ev = {
        "message": [{"type": "text", "data": {"text": "hi"}}],
        "raw_message": "[CQ:reply,id=42]hi",
    }
    assert EventProcessor._parse_reply_target_id(ev) == "42"


def test_parse_reply_target_empty():
    assert EventProcessor._parse_reply_target_id({"message": [], "raw_message": "hi"}) == ""


def test_old_bug_would_compare_to_self_message_id():
    """Document the bug: reply id must NOT equal this message's id."""
    ev = _reply_event(reply_to="824539191", msg_id="1332767306")
    assert EventProcessor._parse_reply_target_id(ev) != str(ev["message_id"])


def test_reply_to_bot_wakes_reply_to_me(tmp_path):
    orch = MagicMock()
    proc = EventProcessor(tmp_path, self_id="3914024488", orchestrator=orch)
    ev = _reply_event(reply_to="824539191", self_id="3914024488")
    with patch.object(proc, "_message_sender_id", return_value="3914024488"):
        proc._handle_message(ev)
    assert "REPLY_TO_ME" in _reasons(orch)
    assert "AT_ME" not in _reasons(orch)


def test_reply_to_other_user_does_not_wake(tmp_path):
    orch = MagicMock()
    proc = EventProcessor(tmp_path, self_id="3914024488", orchestrator=orch)
    ev = _reply_event(reply_to="111", self_id="3914024488")
    with patch.object(proc, "_message_sender_id", return_value="1293883574"):
        proc._handle_message(ev)
    assert "REPLY_TO_ME" not in _reasons(orch)


def test_reply_plus_at_prefers_at_me_not_double(tmp_path):
    orch = MagicMock()
    proc = EventProcessor(tmp_path, self_id="3914024488", orchestrator=orch)
    ev = _reply_event(reply_to="824539191", at_bot=True, self_id="3914024488")
    with patch.object(proc, "_message_sender_id", return_value="3914024488") as msi:
        proc._handle_message(ev)
    reasons = _reasons(orch)
    assert "AT_ME" in reasons
    assert "REPLY_TO_ME" not in reasons
    # should not need lookup when @ already matched
    msi.assert_not_called()


def test_message_sender_id_uses_get_msg(tmp_path):
    orch = MagicMock()
    proc = EventProcessor(tmp_path, self_id="3914024488", orchestrator=orch)
    with patch("napcat_cli.lib.api.NapCatAPI") as API:
        inst = API.return_value
        inst.call.return_value = {
            "retcode": 0,
            "data": {"sender": {"user_id": 3914024488}, "user_id": 3914024488},
        }
        assert proc._message_sender_id("824539191") == "3914024488"
        inst.call.assert_called()
        assert inst.call.call_args.args[0] == "get_msg"


def test_emoji_like_on_bot_message_wakes(tmp_path):
    orch = MagicMock()
    proc = EventProcessor(tmp_path, self_id="3914024488", orchestrator=orch)
    ev = {
        "notice_type": "group_msg_emoji_like",
        "user_id": 1293883574,
        "group_id": 201644592,
        "message_id": 824539191,
        "likes": [{"emoji_id": "128077"}],
        "is_add": True,
    }
    with patch.object(proc, "_message_sender_id", return_value="3914024488"):
        proc._handle_group_emoji_like(ev)
    assert "REPLY_TO_ME" in _reasons(orch)


def test_emoji_like_on_other_message_no_wake(tmp_path):
    orch = MagicMock()
    proc = EventProcessor(tmp_path, self_id="3914024488", orchestrator=orch)
    ev = {
        "notice_type": "group_msg_emoji_like",
        "user_id": 1,
        "group_id": 1,
        "message_id": 2,
        "likes": [{"emoji_id": "1"}],
        "is_add": True,
    }
    with patch.object(proc, "_message_sender_id", return_value="999"):
        proc._handle_group_emoji_like(ev)
    assert _reasons(orch) == []
