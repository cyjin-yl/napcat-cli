"""Tests for napcat_cli.wake_backend (HTTP/CLI transports, Waker auto-fallback)."""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import urllib.error

from napcat_cli.wake_backend import (
    CliWakeBackend, HttpWakeBackend, Waker, WakeResult, extract_reply,
)


# ---------------------------------------------------------------------------
# extract_reply
# ---------------------------------------------------------------------------

class TestExtractReply:
    def test_string_response(self):
        assert extract_reply("hello world") == "hello world"

    def test_dict_output_key(self):
        assert extract_reply({"output": "hi"}) == "hi"

    def test_dict_content_array(self):
        assert extract_reply({"content": [{"type": "text", "text": "a"}, {"text": "b"}]}) == "ab"

    def test_nested_message_content(self):
        assert extract_reply({"message": {"content": "deep"}}) == "deep"

    def test_no_reply(self):
        assert extract_reply({"foo": "bar"}) == ""
        assert extract_reply(None) == ""


# ---------------------------------------------------------------------------
# HttpWakeBackend
# ---------------------------------------------------------------------------

def _ok_response(body):
    """Build a fake urlopen context manager returning status 200 + body bytes."""
    cm = MagicMock()
    cm.__enter__ = MagicMock(return_value=cm)
    cm.__exit__ = MagicMock(return_value=False)
    cm.status = 200
    cm.read = MagicMock(return_value=json.dumps(body).encode())
    return cm


class TestHttpWakeBackend:
    def test_configured_requires_url_and_key(self):
        assert not HttpWakeBackend("", "").configured()
        assert not HttpWakeBackend("http://x", "").configured()
        assert not HttpWakeBackend("", "k").configured()
        assert HttpWakeBackend("http://x", "k").configured()

    def test_dry_run_renders_request(self):
        b = HttpWakeBackend("http://127.0.0.1:8642", "k", session_id="s1")
        res = b.wake("hello @me", "AT_ME", {}, "idem-1", dry_run=True)
        assert res.ok is True
        assert "/api/sessions/s1/chat" in res.detail
        assert "idem-1" in res.detail
        assert "input" in res.detail

    def test_success_extracts_reply(self):
        b = HttpWakeBackend("http://x", "k", session_id="s1")
        with patch("urllib.request.urlopen", return_value=_ok_response({"output": "在的"})):
            res = b.wake("hi", "AT_ME", {}, "k1")
        assert res.ok is True
        assert res.http_status == 200
        assert res.extra["reply"] == "在的"
        assert res.extra["session_id"] == "s1"

    def test_http_error_not_ok(self):
        b = HttpWakeBackend("http://x", "k", session_id="s1")
        err = urllib.error.HTTPError("http://x", 401, "no auth", {}, None)
        with patch("urllib.request.urlopen", side_effect=err):
            res = b.wake("hi", "AT_ME", {}, "k1")
        assert res.ok is False
        assert res.http_status == 401

    def test_url_error_not_ok(self):
        b = HttpWakeBackend("http://x", "k", session_id="s1")
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            res = b.wake("hi", "AT_ME", {}, "k1")
        assert res.ok is False
        assert res.http_status is None


# ---------------------------------------------------------------------------
# CliWakeBackend
# ---------------------------------------------------------------------------

class TestCliWakeBackend:
    def test_render_quotes_prompt_and_session(self):
        b = CliWakeBackend("hermes --continue {session} -z {prompt} --yolo", session="napcat-qq")
        assert b.render("hi there", "AT_ME") == "hermes --continue napcat-qq -z 'hi there' --yolo"

    def test_not_configured_without_template(self):
        assert not CliWakeBackend("").configured()

    def test_dry_run_returns_rendered(self):
        b = CliWakeBackend("echo {prompt}", session="s")
        res = b.wake("hi there", "AT_ME", {}, "k", dry_run=True)
        assert res.ok is True
        assert "echo 'hi there'" in res.detail

    def test_success_captures_reply(self):
        b = CliWakeBackend("echo {prompt}")
        r = MagicMock(returncode=0, stdout="reply text", stderr="")
        with patch("napcat_cli.wake_backend.subprocess.run", return_value=r):
            res = b.wake("hi", "AT_ME", {}, "k")
        assert res.ok is True
        assert res.extra["reply"] == "reply text"

    def test_nonzero_exit_not_ok(self):
        b = CliWakeBackend("false {prompt}")
        r = MagicMock(returncode=1, stdout="", stderr="boom")
        with patch("napcat_cli.wake_backend.subprocess.run", return_value=r):
            res = b.wake("hi", "AT_ME", {}, "k")
        assert res.ok is False
        assert "boom" in res.detail


# ---------------------------------------------------------------------------
# Waker auto-fallback
# ---------------------------------------------------------------------------

class _FakeBackend:
    """Minimal fake backend for Waker tests."""

    def __init__(self, name, ok=True, reply=""):
        self.name = name
        self._ok = ok
        self._reply = reply
        self.calls = 0

    def configured(self):
        return True

    def wake(self, prompt, reason, ctx, idem_key, *, dry_run=False, timeout=30.0):
        self.calls += 1
        return WakeResult(self._ok, self.name, "fake", extra={"reply": self._reply})


class TestWaker:
    def test_empty_waker_returns_none_result(self):
        w = Waker([], "auto")
        assert w.empty
        res = w.wake("p", "AT_ME")
        assert res.ok is False and res.transport == "none"

    def test_primary_http_tried_first(self):
        http = _FakeBackend("http", ok=True)
        cli = _FakeBackend("cli", ok=True)
        w = Waker([http, cli], primary="http")
        w.wake("p", "AT_ME")
        assert http.calls == 1 and cli.calls == 0

    def test_auto_fallback_on_http_failure(self):
        http = _FakeBackend("http", ok=False)
        cli = _FakeBackend("cli", ok=True, reply="recovered")
        w = Waker([http, cli], primary="auto")
        res = w.wake("p", "AT_ME")
        assert res.ok is True
        assert res.transport == "cli"
        assert res.extra["reply"] == "recovered"
        assert http.calls == 1 and cli.calls == 1

    def test_all_fail_reports_last(self):
        http = _FakeBackend("http", ok=False)
        cli = _FakeBackend("cli", ok=False)
        w = Waker([http, cli], primary="auto")
        res = w.wake("p", "AT_ME")
        assert res.ok is False
        assert "all backends failed" in res.detail

    def test_dry_run_returns_primary_only(self):
        http = _FakeBackend("http", ok=True)
        cli = _FakeBackend("cli", ok=True)
        w = Waker([http, cli], primary="http")
        w.wake("p", "AT_ME", dry_run=True)
        assert http.calls == 1 and cli.calls == 0
