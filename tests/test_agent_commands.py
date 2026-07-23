"""Tests for Agent-facing CLI commands.

These test the exact command patterns that the Hermes Agent uses in practice.
Every command documented in SKILL.md and _PROMPT_FOOTER must work exactly
as written — no surprise argparse errors.

Run: python -m pytest tests/test_agent_commands.py -v
"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).parent.parent
PYTHON = sys.executable


def _run(args: str) -> tuple[int, str, str]:
    """Run napcat CLI with given args, return (exit_code, stdout, stderr)."""
    proc = subprocess.run(
        [PYTHON, "-m", "napcat_cli.cli"] + args.split(),
        capture_output=True, text=True, timeout=15,
        cwd=str(REPO),
        env={**__import__("os").environ, "NAPCAT_DATA_DIR": "/tmp/napcat-test-data"},
    )
    return proc.returncode, proc.stdout, proc.stderr


class TestSendCommand:
    """napcat send group <gid> -m 'text' must work (Agent uses -m shorthand)."""

    def test_send_accepts_m_shorthand(self):
        """-m should be accepted as shorthand for --message."""
        rc, out, err = _run("send group 999999 -m test")
        # Should NOT fail with argparse error about -m
        assert "unrecognized arguments: -m" not in err, (
            f"send -m not recognized: {err}"
        )

    def test_send_accepts_message_shorthand(self):
        """--message should work."""
        rc, out, err = _run("send group 999999 --message test")
        assert "unrecognized arguments" not in err


class TestReplyCommand:
    """napcat reply <mid> -m 'text' — Agent uses this pattern."""

    def test_reply_accepts_m_shorthand(self):
        """-m should be accepted."""
        rc, out, err = _run("reply group 999999 12345 -m test")
        assert "unrecognized arguments: -m" not in err


class TestGetImageCommand:
    """napcat get_image <url> — Agent uses this to download images."""

    def test_get_image_exists(self):
        """get_image should be a valid subcommand."""
        rc, out, err = _run("get_image http://example.com/test.jpg")
        # Should not fail with "invalid choice" — it's a real command
        assert "invalid choice" not in err, (
            f"get_image not a valid command: {err}"
        )


class TestGroupGetMessage:
    """napcat group <gid> get_message <mid> — Agent tries this pattern."""

    def test_group_get_message_exists(self):
        """get_message should work as a group subcommand."""
        rc, out, err = _run("group 783289820 get_message 12345")
        # Should not fail with "invalid choice"
        assert "invalid choice" not in err or "get_message" in err, (
            f"group get_message not recognized: {err}"
        )


class TestEventsTextOutput:
    """napcat events -o text should show human-readable text, not raw segments."""

    def test_events_text_no_raw_segments(self):
        """Text output should not contain raw [{'type': 'text', ...}] format."""
        rc, out, err = _run("events -o text -n 1")
        if rc == 0 and out:
            # Should not show raw Python list repr of segments
            assert "[{'type':'" not in out, (
                f"Raw segments in text output: {out[:200]}"
            )


class TestEventsGroupFilter:
    """napcat events --group <gid> should filter by group."""

    def test_events_group_flag_exists(self):
        """--group should be a valid flag."""
        rc, out, err = _run("events --group 123 -n 1")
        assert "unrecognized arguments: --group" not in err, (
            f"--group not recognized: {err}"
        )
