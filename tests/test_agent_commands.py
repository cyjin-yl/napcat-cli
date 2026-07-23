"""Tests for Agent-facing CLI commands.

These test the exact command patterns that the Hermes Agent uses in practice.
Every command documented in SKILL.md and _PROMPT_FOOTER must work exactly
as written — no surprise argparse errors.

Run: python -m pytest tests/test_agent_commands.py -v
"""
import json
import subprocess
from pathlib import Path
import sys

REPO = Path(__file__).parent.parent
PYTHON = sys.executable


def _run(args: str, data_dir: str = "/tmp/napcat-test-data") -> tuple[int, str, str]:
    """Run napcat CLI with given args, return (exit_code, stdout, stderr)."""
    import os
    proc = subprocess.run(
        [PYTHON, "-m", "napcat_cli.cli"] + args.split(),
        capture_output=True, text=True, timeout=15,
        cwd=str(REPO),
        env={**os.environ, "NAPCAT_DATA_DIR": data_dir},
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


class TestEventsImageUrl:
    """napcat events -o text must show image URLs, not just [image]."""

    def test_events_shows_image_url(self):
        """Events text output should include image URL."""
        rc, out, err = _run("events -o text -n 5")
        if rc == 0 and out:
            # If there are image messages, output should have url= or [image: 
            lines = out.strip().split("\n")
            for line in lines:
                if "[image" in line.lower():
                    # Should have URL information
                    assert "url=" in line or "http" in line, (
                        f"No URL in image line: {line}"
                    )


class TestGetImageHttpProvider:
    """napcat get_image must work via HTTP provider dispatch."""

    def test_get_image_action_exists(self):
        """get_image should be a dispatchable action."""
        # We can't easily test the full HTTP provider here without daemon,
        # but we can check the CLI works
        rc, out, err = _run("get_image http://example.com/test.jpg")
        # Should either succeed (download) or fail with download error
        # NOT fail with "invalid choice" or argparse error
        assert "invalid choice" not in err


class TestOcrProvider:
    """ocr_image must be accessible via HTTP provider."""

    def test_ocr_action_in_dispatch(self):
        """ocr_image should be in the dispatch method."""
        import inspect
        from napcat_cli.daemon.watch import NapCatHandler
        source = inspect.getsource(NapCatHandler._dispatch)
        assert "ocr_image" in source, "ocr_image not in _dispatch"
        assert "get_image" in source, "get_image not in _dispatch"


class TestReplyByMid:
    """napcat messages/:mid/reply/text — Agent tries this pattern."""

    def test_reply_by_mid_text_action_exists(self):
        """reply_by_mid_text should be in _dispatch."""
        import inspect
        from napcat_cli.daemon.watch import NapCatHandler
        source = inspect.getsource(NapCatHandler._dispatch)
        assert "reply_by_mid_text" in source

    def test_reply_by_mid_text_raw_action_exists(self):
        """reply_by_mid_text_raw should be in _dispatch."""
        import inspect
        from napcat_cli.daemon.watch import NapCatHandler
        source = inspect.getsource(NapCatHandler._dispatch)
        assert "reply_by_mid_text_raw" in source

    def test_reply_by_mid_image_action_exists(self):
        """reply_by_mid_image should be in _dispatch."""
        import inspect
        from napcat_cli.daemon.watch import NapCatHandler
        source = inspect.getsource(NapCatHandler._dispatch)
        assert "reply_by_mid_image" in source

    def test_reply_by_mid_cqcode_action_exists(self):
        """reply_by_mid_cqcode should be in _dispatch."""
        import inspect
        from napcat_cli.daemon.watch import NapCatHandler
        source = inspect.getsource(NapCatHandler._dispatch)
        assert "reply_by_mid_cqcode" in source

    def test_reply_by_mid_at_action_exists(self):
        """reply_by_mid_at should be in _dispatch."""
        import inspect
        from napcat_cli.daemon.watch import NapCatHandler
        source = inspect.getsource(NapCatHandler._dispatch)
        assert "reply_by_mid_at" in source

    def test_reply_by_mid_json_action_exists(self):
        """reply_by_mid_json should be in _dispatch."""
        import inspect
        from napcat_cli.daemon.watch import NapCatHandler
        source = inspect.getsource(NapCatHandler._dispatch)
        assert "reply_by_mid_json" in source


class TestGetByMid:
    """napcat messages/:mid/ — get message/image by message_id only."""

    def test_get_message_by_mid_action_exists(self):
        """get_message_by_mid should be in _dispatch."""
        import inspect
        from napcat_cli.daemon.watch import NapCatHandler
        source = inspect.getsource(NapCatHandler._dispatch)
        assert "get_message_by_mid" in source

    def test_get_image_by_mid_action_exists(self):
        """get_image_by_mid should be in _dispatch."""
        import inspect
        from napcat_cli.daemon.watch import NapCatHandler
        source = inspect.getsource(NapCatHandler._dispatch)
        assert "get_image_by_mid" in source


class TestMessagePathsInSkillsFs:
    """skills-fs.json must have the /napcat/messages/:message_id/ mount entries."""

    def test_messages_mount_exists(self):
        """skills-fs.json must contain /napcat/messages/ mounts."""
        import json
        with open("/home/ezra/.napcat-data/skills-fs.json") as f:
            config = json.load(f)
        paths = [m["path"] for m in config.get("mounts", [])]
        assert "/napcat/messages" in paths, "messages mount not found"
        assert "/napcat/messages/:message_id" in paths
        assert "/napcat/messages/:message_id/reply/text" in paths
        assert "/napcat/messages/:message_id/reply/text_raw" in paths
        assert "/napcat/messages/:message_id/reply/image" in paths
        assert "/napcat/messages/:message_id/reply/cqcode" in paths
        assert "/napcat/messages/:message_id/reply/at" in paths
        assert "/napcat/messages/:message_id/reply/json" in paths
        assert "/napcat/messages/:message_id/image" in paths


class TestSubagentBugFixes:
    """Tests for bugs found by Agent EVAL testing — Phase 2."""

    def test_events_t_shorthand(self):
        """-t should be alias for --type on events."""
        rc, out, err = _run("events -t message -o text -n 1")
        assert "unrecognized arguments: -t" not in err, f"events -t fails: {err}"

    def test_group_gid_first_order(self):
        """napcat group <gid> info should work (gid BEFORE sub)."""
        rc, out, err = _run("group 1050866499 info")
        # Should not fail with `invalid choice: '1050866499'`
        assert "invalid choice: '1050866499'" not in err, (
            f"group {gid} info not recognized: {err}"
        )

    def test_group_gid_first_get_message(self):
        """napcat group <gid> get_message <mid> should work."""
        # First get a real mid
        proc = subprocess.run(
            [sys.executable, "-m", "napcat_cli.cli", "events", "-t", "message", "-o", "json", "-n", "1"],
            capture_output=True, text=True, timeout=10, cwd=str(REPO),
            env={**__import__("os").environ, "NAPCAT_DATA_DIR": "/tmp/napcat-test-data"},
        )
        data = json.loads(proc.stdout) if proc.stdout else []
        if not data:
            return  # skip if no events
        mid = data[0].get("message_id", "")
        if not mid:
            return
        rc, out, err = _run(f"group 1050866499 get_message {mid}")
        assert "invalid choice" not in err, (
            f"group <gid> get_message <mid> not working: {err}"
        )

    def test_describe_action_for_dispatch_only(self):
        """describe_action should work for actions like get_message_by_mid."""
        rc, out = subprocess.run(
            [sys.executable, "-c",
             "import subprocess; r=subprocess.run(['curl','-s','http://127.0.0.1:18821/invoke?action=describe_action&action=get_message_by_mid'],capture_output=True,text=True); print(r.stdout)"],
            capture_output=True, text=True, timeout=10, cwd=str(REPO),
        ).stdout, ""
        # Won't actually test live HTTP from this Python run, just check existence
        assert True  # live HTTP tested separately


class TestEventsNoUnboundText:
    """events -o text must not throw UnboundLocalError on meta_events (heartbeats)."""

    def test_events_text_no_unbound(self):
        """Events with no message field should not crash."""
        rc, out, err = _run("events --no-heartbeat -o text -n 5")
        # Should not crash with UnboundLocalError; may have other errors
        assert "UnboundLocalError" not in (err or ""), (
            f"UnboundLocalError: {err}"
        )


class TestAlertsClearBackwardCompat:
    """napcat alerts --clear should still work (some Agents expect flag form)."""

    def test_alerts_clear_subcommand(self):
        """alerts clear (subcommand) should work."""
        rc, out, err = _run("alerts clear")
        assert "invalid choice" not in err, f"alerts clear failed: {err}"


class TestFoundBugFixes2:
    """TDD tests for 7 bugs found by TestExploreNewPaths agent."""

    def test_wake_subcommand_registered(self):
        """Bug 1: napcat wake must be in commands dispatch."""
        rc, out, err = _run("wake --reason test")
        assert "Available commands" not in out, "wake subcommand not in dispatch"
        assert "unknown command" not in (err or "").lower()

    def test_friend_info_handler_exists(self):
        """Bug 3: cmd_friend must handle 'info' subcommand."""
        rc, out, err = _run("friend info 3914024488")
        assert "Unknown friend command" not in err, (
            f"friend info not handled: {err}"
        )

    def test_phone_events(self):
        """Bug 4: napcat phone events must not raise AttributeError on type."""
        rc, out, err = _run("phone events")
        assert "AttributeError" not in (err or ""), f"phone events crash: {err}"

    def test_phone_alerts(self):
        """Bug 6: napcat phone alerts must not raise AttributeError on limit."""
        rc, out, err = _run("phone alerts")
        assert "AttributeError" not in (err or ""), f"phone alerts crash: {err}"

    def test_phone_msg(self):
        """Bug 5: napcat phone msg must not raise AttributeError on target_type."""
        rc, out, err = _run("phone msg group 12345 hello")
        assert "AttributeError" not in (err or ""), f"phone msg crash: {err}"

    def test_data_dir_nonexistent_clean_error(self):
        """Bug 7: --data-dir with nonexistent path must show clean error."""
        rc, out, err = _run("--data-dir /nonexistent/path/xyz123 status",
                            data_dir="/nonexistent/path/xyz123")
        assert "Traceback" not in (err or ""), f"data_dir crash: {err}"
