"""Tests for napcat_cli.wake module."""
from __future__ import annotations

import shlex
from io import StringIO
from unittest.mock import patch

from napcat_cli.wake import build_wake_command, render_wake_command


class TestBuildWakeCommand:
    def test_empty_command_returns_empty(self):
        assert build_wake_command("", "ANY") == ""

    def test_dollar_reason_safe_value(self):
        """Safe reason like 'hello' is not quoted by shlex.quote."""
        result = build_wake_command("echo $REASON", "hello")
        assert result == "echo hello"  # shlex.quote("hello") == "hello"

    def test_dollar_reason_dangerous_value(self):
        """Dangerous reason gets quoted."""
        reason = "a; rm -rf /"
        result = build_wake_command("echo $REASON", reason)
        assert shlex.quote(reason) in result

    def test_brace_reason(self):
        result = build_wake_command("echo ${REASON}", "hello")
        assert result == "echo hello"

    def test_curly_reason(self):
        result = build_wake_command("echo {reason}", "hello")
        assert result == "echo hello"

    def test_surrounding_text_preserved(self):
        tmpl = "hermes -c 'session' -z '$REASON' -s napcat-cli --yolo"
        result = build_wake_command(tmpl, "NEW_MESSAGE")
        assert "hermes -c 'session'" in result
        assert "-s napcat-cli --yolo" in result
        assert "NEW_MESSAGE" in result


class TestRenderWakeCommand:
    def test_prompt_and_session_substituted(self):
        result = render_wake_command(
            "hermes --continue {session} -z {prompt} --yolo",
            prompt="hi there", session="napcat-qq",
        )
        assert result == "hermes --continue napcat-qq -z 'hi there' --yolo"

    def test_dangerous_prompt_quoted(self):
        result = render_wake_command("echo {prompt}", prompt="a; rm -rf /")
        assert shlex.quote("a; rm -rf /") in result

    def test_empty_values_render_empty_quotes(self):
        assert render_wake_command("x {prompt} y", prompt="", session="") == "x '' y"

    def test_reason_still_supported(self):
        assert render_wake_command("echo $REASON", reason="AT_ME") == "echo AT_ME"
