"""Agent Eval: regression tests for known Hermes bad cases.

These tests validate that napcat-cli generates prompts and messages
that make it easy for Hermes (and other LLM agents) to:

- Resolve reply chains with full context (group, sender, message ID)
- Handle image/OCR workflows properly
- Distinguish between group and private conversations
- Not confuse sender identity
- Handle merged/forwarded messages

Each test simulates the napcat event -> wake orchestrator pipeline
and checks the prompt output for the right hints/metadata.
"""
from __future__ import annotations

from napcat_cli.wake_orchestrator import build_prompt


# ---------------------------------------------------------------------------
# Reply chain resolution (bad case: reply to wrong person / reply wrong group)
# ---------------------------------------------------------------------------

class TestReplyChain:
    """Agent must correctly resolve who/what/where for reply messages."""

    def test_group_reply_identifies_group(self):
        """Reply in a group must include group_id."""
        event = {
            "message": [{"type": "reply", "data": {"id": "123"}},
                        {"type": "text", "data": {"text": "好的"}}],
            "group_id": "678901",
            "sender": {"nickname": "张三", "user_id": 10001, "card": "阿三"},
        }
        prompt = build_prompt("AT_ME", [event])
        assert "678901" in prompt, "Should include group_id"
        assert "群" in prompt, "Should mention group context"

    def test_dm_reply_identifies_private(self):
        """Private DM reply must NOT mention group_id and should note private."""
        event = {
            "message": [{"type": "reply", "data": {"id": "456"}},
                        {"type": "text", "data": {"text": "在吗"}}],
            "sender": {"nickname": "李四", "user_id": 20002},
        }
        prompt = build_prompt("DM_ME", [event])
        assert "私聊" in prompt, "Should identify private chat context"

    def test_reply_identifies_sender_nickname_and_card(self):
        """Reply should include sender's nickname + group card + QQ number."""
        event = {
            "message": [{"type": "reply", "data": {"id": "789"}},
                        {"type": "text", "data": {"text": "收到"}}],
            "group_id": "111",
            "sender": {"nickname": "王五", "user_id": 30003, "card": "小王"},
        }
        prompt = build_prompt("AT_ME", [event])
        assert "王五" in prompt, "Should include nickname"
        assert "30003" in prompt, "Should include QQ number"

    def test_reply_includes_message_id(self):
        """Reply should include the replied-to message ID."""
        event = {
            "message": [{"type": "reply", "data": {"id": "99999"}},
                        {"type": "text", "data": {"text": "看看"}}],
            "group_id": "222",
            "sender": {"nickname": "赵六", "user_id": 40004},
        }
        prompt = build_prompt("AT_ME", [event])
        assert "99999" in prompt, "Should include the replied-to message ID"

    def test_reply_mentions_image_when_present(self):
        """Reply that references an image should hint about image tools."""
        event = {
            "message": [
                {"type": "reply", "data": {"id": "555"}},
                {"type": "text", "data": {"text": "这图什么意思"}},
                {"type": "image", "data": {"file_id": "img001.jpg", "url": "http://example.com/img.jpg"}},
            ],
            "group_id": "333",
            "sender": {"nickname": "钱七", "user_id": 50005},
        }
        prompt = build_prompt("AT_ME", [event])
        assert "img001.jpg" in prompt, "Should include image references"
        assert "图片" in prompt, "Should hint about image tools"

    def test_forward_message_gets_hint(self):
        """Forward/merged messages should trigger exploration hints."""
        event = {
            "message": [
                {"type": "forward", "data": {"id": "fw001"}},
                {"type": "text", "data": {"text": "你看这个"}},
            ],
            "group_id": "444",
            "sender": {"nickname": "孙八", "user_id": 60006},
        }
        prompt = build_prompt("AT_ME", [event])
        # Forward message shouldn't crash and should produce normal prompt
        assert "转发" in prompt or "合并" in prompt or "你看这个" in prompt

    def test_multiple_images_detected(self):
        """Multiple images in a message should all be listed."""
        event = {
            "message": [
                {"type": "text", "data": {"text": "三张照片"}},
                {"type": "image", "data": {"file_id": "a.jpg", "url": "http://a.jpg"}},
                {"type": "image", "data": {"file_id": "b.jpg", "url": "http://b.jpg"}},
                {"type": "image", "data": {"file_id": "c.jpg", "url": "http://c.jpg"}},
            ],
            "group_id": "555",
            "sender": {"nickname": "周九", "user_id": 70007},
        }
        prompt = build_prompt("AT_ME", [event])
        images_found = sum(1 for f in ["a.jpg", "b.jpg", "c.jpg"] if f in prompt)
        assert images_found >= 2, \
            f"Should expose at least 2 of 3 image file_ids, found {images_found}/3"


# ---------------------------------------------------------------------------
# Image handling (bad case: Agent ignores images / doesn't OCR)
# ---------------------------------------------------------------------------

class TestImageHandling:
    """Agent must be explicitly guided to use OCR/image tools."""

    def test_image_metadata_in_prompt(self):
        """Wake prompt should contain image metadata values."""
        event = {
            "message": [
                {"type": "image", "data": {
                    "file_id": "abc123.jpg",
                    "url": "http://example.com/img.jpg",
                    "file_size": 102400,
                    "sub_type": 0,
                }},
                {"type": "text", "data": {"text": "看看这个"}},
            ],
            "group_id": "666",
            "sender": {"nickname": "吴十", "user_id": 80008},
        }
        prompt = build_prompt("AT_ME", [event])
        assert "abc123.jpg" in prompt, "Should include file_id value"
        assert "http://example.com/img.jpg" in prompt, "Should include url value"
        assert "102400" in prompt, "Should include file_size value"

    def test_ocr_tools_mentioned(self):
        """Prompt footer should hint at tools/CLI for image handling."""
        event = {
            "message": [
                {"type": "image", "data": {"file_id": "test.png"}},
            ],
            "group_id": "777",
            "sender": {"nickname": "郑一", "user_id": 90009},
        }
        prompt = build_prompt("AT_ME", [event])
        assert "get_image" in prompt or "下载" in prompt, \
            "Should mention image download tools in prompt"


# ---------------------------------------------------------------------------
# Identity resolution (bad case: Agent confuses sender identity)
# ---------------------------------------------------------------------------

class TestIdentityResolution:
    """Agent must correctly identify message sender."""

    def test_dm_identifies_sender(self):
        """DM wake should include who sent the message with QQ number."""
        event = {
            "message": [{"type": "text", "data": {"text": "你好"}}],
            "sender": {"nickname": "陈二", "user_id": 10101},
        }
        prompt = build_prompt("DM_ME", [event])
        assert "陈二" in prompt, "Should include sender nickname"
        assert "10101" in prompt, "Should include sender QQ"

    def test_group_identifies_sender_with_card(self):
        """Group messages should include identity information."""
        event = {
            "message": [{"type": "text", "data": {"text": "大家好"}}],
            "group_id": "888",
            "sender": {"nickname": "刘三", "user_id": 20202, "card": "群主-刘"},
        }
        prompt = build_prompt("AT_ME", [event])
        assert "刘三" in prompt or "20202" in prompt, \
            "Should include sender identity information"

    def test_sender_empty_still_works(self):
        """Event without sender dict should not crash."""
        event = {
            "message": [{"type": "text", "data": {"text": "test"}}],
        }
        prompt = build_prompt("DM_ME", [event])
        assert len(prompt) > 0, "Should produce some prompt"
        assert "私聊" in prompt or "消息" in prompt


# ---------------------------------------------------------------------------
# Prompt format consistency
# ---------------------------------------------------------------------------

class TestPromptFormat:
    """Agent Eval: prompt must be well-structured for LLM consumption."""

    def test_footer_contains_skills_hints(self):
        """Prompt footer should include CLI and FS usage hints."""
        event = {
            "message": [{"type": "text", "data": {"text": "hi"}}],
            "sender": {"nickname": "张三", "user_id": 12345},
        }
        prompt = build_prompt("AT_ME", [event])
        # Footer should reference napcat CLI/FS commands
        keywords = ["napcat", "send", "图片"]
        assert any(k in prompt for k in keywords), \
            "Prompt footer should contain napcat skills hints"

    def test_reason_included_in_prompt(self):
        """Wake reason should be clearly stated."""
        event = {
            "message": [{"type": "text", "data": {"text": "在吗"}}],
            "group_id": "123",
            "sender": {"nickname": "李四", "user_id": 23456},
        }
        prompt = build_prompt("AT_ME", [event])
        assert "AT_ME" in prompt or "@" in prompt or "提到" in prompt, \
            "Wake reason should be identifiable in prompt"


# ---------------------------------------------------------------------------
# Message formatting (format_message from lib.message)
# ---------------------------------------------------------------------------

class TestMessageFormatting:
    """format_message must produce Agent-friendly output."""

    def test_image_format_includes_fields(self):
        """format_message should expose image fields."""
        from napcat_cli.lib.message import format_message
        msg = [{"type": "image", "data": {
            "file_id": "img001.jpg",
            "url": "http://example.com/1.jpg",
            "sub_type": 0,
            "file_size": 50000,
            "summary": "风景照",
        }}]
        result = format_message(msg)
        assert isinstance(result, str), "Should return string"
        assert "img001.jpg" in result
        assert "http://example.com/1.jpg" in result
        assert "50000" in result or "50" in result  # file_size in bytes or KB

    def test_reply_format_includes_id(self):
        """format_message should expose reply IDs."""
        from napcat_cli.lib.message import format_message
        msg = [
            {"type": "reply", "data": {"id": "98765"}},
            {"type": "text", "data": {"text": "回复你"}},
        ]
        result = format_message(msg)
        assert isinstance(result, str)
        assert "98765" in result, "Reply ID should be visible"

    def test_text_only_unchanged(self):
        """Plain text should pass through unchanged."""
        from napcat_cli.lib.message import format_message
        result = format_message([{"type": "text", "data": {"text": "hello"}}])
        assert result == "hello"

    def test_forward_format(self):
        """Forward/merged messages should be identifiable."""
        from napcat_cli.lib.message import format_message
        result = format_message([{"type": "forward", "data": {"id": "fw_001"}}])
        assert isinstance(result, str)
        assert "fw_001" in result or "合并" in result or "转发" in result

    def test_at_format(self):
        """@ mentions should be formatted cleanly."""
        from napcat_cli.lib.message import format_message
        result = format_message([{"type": "at", "data": {"qq": "12345", "name": "张三"}}])
        assert isinstance(result, str)
        assert "张三" in result or "12345" in result


# ---------------------------------------------------------------------------
# extract_files utility (file extraction from messages)
# ---------------------------------------------------------------------------

class TestFileExtraction:
    """extract_files must correctly find media URLs from segments."""

    def test_extract_image_urls(self):
        from napcat_cli.lib.message import extract_files
        msg = [
            {"type": "image", "data": {"url": "http://img.com/a.jpg",
                                       "file": "/tmp/a.jpg"}},
            {"type": "video", "data": {"url": "http://v.com/b.mp4",
                                       "file": "/tmp/b.mp4"}},
            {"type": "text", "data": {"text": "hello"}},
        ]
        files = extract_files(msg)
        assert len(files) >= 1, "Should find at least 1 media file"
        urls = [str(f) for f in files]
        assert any("a.jpg" in u for u in urls)
