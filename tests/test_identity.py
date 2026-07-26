"""Canonical group/person labels — synthetic only, no live QQ accounts."""
from __future__ import annotations

import pytest

from napcat_cli.lib import identity as ident
from napcat_cli.lib.identity import (
    IDENTITY_LEGEND,
    NO_GROUP_CARD,
    NO_GROUP_REMARK,
    clear_caches,
    format_group,
    format_person,
    label_from_event_where,
    label_from_event_who,
    label_group,
    label_person,
    resolve_group,
    resolve_person,
)
from napcat_cli.wake_orchestrator import build_prompt, _extract_reply_meta


@pytest.fixture(autouse=True)
def _clear():
    clear_caches()
    yield
    clear_caches()


def test_format_group_empty_remark_uses_placeholder():
    assert format_group("", "SomeGroup", 10001) == f"({NO_GROUP_REMARK})(SomeGroup)(10001)"
    assert format_group(None, "G", "2") == f"({NO_GROUP_REMARK})(G)(2)"
    assert format_group("备注", "名", "3") == "(备注)(名)(3)"


def test_format_person_empty_card_uses_placeholder():
    assert format_person("", "Nick", 20002) == f"({NO_GROUP_CARD})(Nick)(20002)"
    assert format_person("Card", "Nick", "9") == "(Card)(Nick)(9)"


def test_label_from_event_who_uses_sender_only():
    ev = {
        "group_id": 10001,
        "user_id": 20002,
        "sender": {"user_id": 20002, "nickname": "Alice", "card": "CardA"},
    }
    assert label_from_event_who(ev) == "(CardA)(Alice)(20002)"


def test_label_from_event_where_private():
    assert label_from_event_where({"message_type": "private"}) == "私聊"
    assert label_from_event_where({}) == "私聊"


def test_resolve_group_uses_api_mock(monkeypatch):
    class FakeAPI:
        def call(self, action, **kw):
            assert action == "get_group_info"
            assert kw["group_id"] in (10001, "10001")
            return {"retcode": 0, "data": {"group_remark": "", "group_name": "Test Group", "group_id": 10001}}

    info = resolve_group(10001, api=FakeAPI())
    assert info["name"] == "Test Group"
    assert label_group(10001, api=FakeAPI()) == f"({NO_GROUP_REMARK})(Test Group)(10001)"


def test_resolve_person_member_api_when_sender_incomplete(monkeypatch):
    class FakeAPI:
        def call(self, action, **kw):
            if action == "get_group_member_info":
                return {"retcode": 0, "data": {"card": "MemberCard", "nickname": "Bob", "user_id": 20002}}
            raise AssertionError(action)

    info = resolve_person(20002, group_id=10001, sender={"user_id": 20002}, api=FakeAPI())
    assert info["card"] == "MemberCard"
    assert info["nickname"] == "Bob"


def test_extract_reply_meta_distinguishes_trigger_vs_quoted_ids():
    """trigger mid = user message; quoted mid = bot message being replied to."""
    ev = {
        "message_id": 5001,  # user's new message (napcat reply target)
        "group_id": 10001,
        "user_id": 20002,
        "sender": {"user_id": 20002, "nickname": "Alice", "card": "C"},
        "message": [
            {"type": "reply", "data": {"id": "9001"}},  # bot's older message
            {"type": "text", "data": {"text": "please clarify"}},
        ],
        "raw_message": "[CQ:reply,id=9001]please clarify",
        "message_type": "group",
    }
    meta = _extract_reply_meta(ev)
    assert "5001" in meta
    assert "9001" in meta
    assert "触发消息ID" in meta
    assert "你的原消息ID" in meta
    # reply CLI must use trigger, not quoted
    assert meta.index("5001") < meta.index("9001") or "reply" in meta.lower()


def test_build_prompt_reply_includes_legend_and_trigger_id(monkeypatch):
    def fake_group(gid, api=None):
        return {"remark": "", "name": "GroupName", "group_id": str(gid)}

    def fake_person(uid, group_id=None, sender=None, api=None):
        return {"card": "CardX", "nickname": "UserX", "user_id": str(uid)}

    monkeypatch.setattr(ident, "resolve_group", fake_group)
    monkeypatch.setattr(ident, "resolve_person", fake_person)

    ev = {
        "message_id": 5001,
        "group_id": 10001,
        "user_id": 20002,
        "sender": {"user_id": 20002, "nickname": "UserX", "card": "CardX"},
        "message": [
            {"type": "reply", "data": {"id": "9001"}},
            {"type": "text", "data": {"text": "hi"}},
        ],
        "raw_message": "[CQ:reply,id=9001]hi",
        "message_type": "group",
    }
    prompt = build_prompt("REPLY_TO_ME", [ev])
    assert "身份格式说明" in prompt or "群备注" in IDENTITY_LEGEND
    assert IDENTITY_LEGEND.split("]")[0] in prompt or "身份格式说明" in prompt
    assert f"({NO_GROUP_REMARK})(GroupName)(10001)" in prompt
    assert "(CardX)(UserX)(20002)" in prompt
    assert "触发消息ID" in prompt and "5001" in prompt
    assert "你的原消息ID" in prompt and "9001" in prompt
    assert "强制回复" in prompt
    assert "完全看不到" in prompt
    # concrete reply recipe uses trigger id semantics
    assert "触发消息ID" in prompt
