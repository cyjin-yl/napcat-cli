"""Generic, pluggable agent wake backends.

Agent-agnostic transports for delivering a wake prompt + reason + context to an
external agent (Hermes by default, but any HTTP endpoint or shell command works).
A :class:`Waker` holds an ordered list of backends and tries each until one
succeeds — this is the "dual transport + auto-fallback" behaviour.

Two built-in backends:

- :class:`HttpWakeBackend` — generic HTTP POST with Bearer auth and an optional
  ``Idempotency-Key`` header. The Hermes API Server preset targets
  ``POST /api/sessions/{id}/chat`` (verified working per the Hermes API docs).
- :class:`CliWakeBackend` — **LEGACY / not recommended** — generic shell command rendered via
  :func:`napcat_cli.wake.render_wake_command`. The Hermes preset targets
  ``hermes --continue <session> -z <prompt> --yolo --pass-session-id``.
  Prefer :class:`HttpWakeBackend` for all production use — CLI has known
  unreliability (process-spawn latency, quoting hazards, no idempotency,
  new-session drift).

Backends use only the standard library (``urllib``, ``subprocess``).
"""
from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .wake import render_wake_command


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class WakeResult:
    """Outcome of a single wake attempt."""

    ok: bool
    transport: str  # "http" | "cli" | "none"
    detail: str = ""
    elapsed: float = 0.0
    http_status: int | None = None
    extra: dict = field(default_factory=dict)

    def __bool__(self) -> bool:  # convenience: `if result:`
        return self.ok


# ---------------------------------------------------------------------------
# Backend interface
# ---------------------------------------------------------------------------

class WakeBackend(ABC):
    """Abstract wake transport."""

    name: str = "backend"

    @abstractmethod
    def configured(self) -> bool:
        """True if this backend has enough config to attempt a wake."""

    @abstractmethod
    def wake(
        self,
        prompt: str,
        reason: str,
        ctx: dict,
        idem_key: str,
        *,
        dry_run: bool = False,
        timeout: float = 30.0,
    ) -> WakeResult:
        """Deliver (or render, when dry_run) a wake. Never raises."""

    # optional: connectivity probe + session enumeration (http-only by default)
    def probe(self, timeout: float = 3.0) -> bool:
        return self.configured()

    def list_sessions(self, timeout: float = 5.0) -> list[dict] | None:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_escape(value: str) -> str:
    """Escape a string for safe interpolation into a JSON string literal."""
    return json.dumps(value, ensure_ascii=False)[1:-1]


def extract_reply(resp: Any, limit: int = 2000) -> str:
    """Best-effort extract the agent's textual reply from an HTTP response body."""
    if resp is None:
        return ""
    if isinstance(resp, str):
        return resp[:limit]
    if isinstance(resp, dict):
        # try common shapes across agent HTTP APIs
        for key in ("reply", "output", "response", "text", "content", "message", "answer"):
            v = resp.get(key)
            if isinstance(v, str) and v.strip():
                return v[:limit]
            if isinstance(v, list):
                # OpenAI-style content arrays
                parts = []
                for item in v:
                    if isinstance(item, dict):
                        t = item.get("text") or item.get("content") or ""
                        if isinstance(t, str):
                            parts.append(t)
                    elif isinstance(item, str):
                        parts.append(item)
                joined = "".join(parts).strip()
                if joined:
                    return joined[:limit]
        # nested message.content
        msg = resp.get("message") or resp.get("data") or {}
        if isinstance(msg, dict):
            c = msg.get("content") or msg.get("text")
            if isinstance(c, str) and c.strip():
                return c[:limit]
    return ""


# ---------------------------------------------------------------------------
# HTTP backend
# ---------------------------------------------------------------------------

class HttpWakeBackend(WakeBackend):
    """Generic HTTP POST wake.

    Targets an OpenAI-ish / agent endpoint. For the Hermes API Server the path
    template is ``/api/sessions/{session}/chat`` and the body field is ``input``.
    Session resolution: if ``session_id`` is set it is used directly; otherwise
    the name is resolved via ``GET {base}/api/sessions`` (Hermes Sessions API).
    """

    name = "http"

    def __init__(
        self,
        base_url: str,
        key: str,
        *,
        session: str = "",
        session_id: str = "",
        path_template: str = "/api/sessions/{session}/chat",
        body_field: str = "input",
        body_extra: dict | None = None,
        health_path: str = "/health",
        sessions_path: str = "/api/sessions",
        label: str = "http",
    ):
        self.base_url = (base_url or "").rstrip("/")
        self.key = key or ""
        self.session = session or ""
        self.session_id = session_id or ""
        self.path_template = path_template
        self.body_field = body_field
        self.body_extra = body_extra or {}
        self.health_path = health_path
        self.sessions_path = sessions_path
        self.label = label

    # -- configuration -----------------------------------------------------

    def configured(self) -> bool:
        return bool(self.base_url and self.key)

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        timeout: float = 5.0,
        idem_key: str = "",
    ) -> tuple[int | None, dict | str]:
        """Issue an authenticated request. Returns (http_status, parsed_body_or_text)."""
        url = f"{self.base_url}{path}"
        data = None
        headers = {
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if idem_key:
            headers["Idempotency-Key"] = idem_key
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                status = resp.status
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            status = e.code
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return None, f"connection failed: {e.reason if hasattr(e, 'reason') else e}"
        try:
            return status, json.loads(raw)
        except json.JSONDecodeError:
            return status, raw

    # -- session resolution ------------------------------------------------

    def _resolve_session(self, timeout: float = 5.0) -> tuple[str, str]:
        """Return (session_id_for_path, note). Uses explicit id, else lookup by name."""
        if self.session_id:
            return self.session_id, f"explicit id {self.session_id}"
        if not self.session:
            return "", "no session configured"
        status, body = self._request("GET", f"{self.sessions_path}?limit=200", timeout=timeout)
        if status != 200 or not isinstance(body, dict):
            return "", f"session lookup failed (status={status})"
        entries = body.get("sessions") or body.get("data") or body.get("items") or []
        # match by id, title, or name (case-insensitive substring on title)
        target = self.session.lower()
        for s in entries:
            if not isinstance(s, dict):
                continue
            sid = str(s.get("id") or s.get("session_id") or "")
            title = str(s.get("title") or s.get("name") or "")
            if sid == self.session or title.lower() == target or target in title.lower():
                return sid, f"resolved by name '{self.session}' -> {sid}"
        return "", f"session '{self.session}' not found in {len(entries)} sessions"

    def probe(self, timeout: float = 3.0) -> bool:
        if not self.configured():
            return False
        status, _ = self._request("GET", self.health_path, timeout=timeout)
        return status == 200

    def list_sessions(self, timeout: float = 5.0) -> list[dict] | None:
        if not self.configured():
            return None
        status, body = self._request("GET", f"{self.sessions_path}?limit=200", timeout=timeout)
        if status != 200 or not isinstance(body, dict):
            return None
        entries = body.get("sessions") or body.get("data") or body.get("items") or []
        out = []
        for s in entries:
            if isinstance(s, dict):
                out.append({
                    "id": s.get("id") or s.get("session_id") or "",
                    "title": s.get("title") or s.get("name") or "",
                    "last_active": s.get("last_active") or s.get("updated_at") or "",
                })
        return out

    # -- wake --------------------------------------------------------------

    def wake(self, prompt, reason, ctx, idem_key, *, dry_run=False, timeout=30.0) -> WakeResult:
        if not self.configured():
            return WakeResult(False, "http", "not configured (base_url+key required)")
        start = time.monotonic()
        sid, note = self._resolve_session() if "{session}" in self.path_template else (self.session_id or "", "static path")
        if "{session}" in self.path_template and not sid:
            return WakeResult(False, "http", f"session resolution failed: {note}",
                              elapsed=time.monotonic() - start)
        path = self.path_template.replace("{session}", urllib.parse.quote(sid, safe=""))
        body = {**self.body_extra, self.body_field: prompt}
        if dry_run:
            return WakeResult(True, "http", f"[dry-run] POST {self.base_url}{path} "
                              f"body={{{self.body_field}: <prompt>}} idem={idem_key} ({note})",
                              elapsed=time.monotonic() - start)
        status, resp = self._request("POST", path, body=body, timeout=timeout, idem_key=idem_key)
        ok = status == 200 and not (isinstance(resp, dict) and resp.get("error"))
        detail = f"POST {path} -> {status}"
        if not ok:
            detail += f" :: {resp if isinstance(resp, str) else json.dumps(resp, ensure_ascii=False)[:300]}"
        return WakeResult(ok, "http", detail, elapsed=time.monotonic() - start,
                          http_status=status,
                          extra={"session_id": sid, "note": note, "reply": extract_reply(resp)})


# ---------------------------------------------------------------------------
# CLI backend
# ---------------------------------------------------------------------------

class CliWakeBackend(WakeBackend):
    """Generic shell-command wake (rendered, shlex-safe)."""

    name = "cli"

    def __init__(self, command_template: str, *, session: str = "", label: str = "cli"):
        self.command_template = command_template or ""
        self.session = session or ""
        self.label = label

    def configured(self) -> bool:
        return bool(self.command_template)

    def render(self, prompt: str, reason: str) -> str:
        """Render for dry-run/backward compat - uses {prompt} placeholder."""
        return render_wake_command(self.command_template, reason=reason, prompt=prompt, session=self.session)

    def _render_file(self, prompt: str, reason: str) -> str:
        """Render for actual execution - uses {prompt_file} placeholder with temp file."""
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(prompt)
            prompt_file = f.name
        return render_wake_command(self.command_template, reason=reason, prompt_file=prompt_file, session=self.session)

    def wake(self, prompt, reason, ctx, idem_key, *, dry_run=False, timeout=30.0) -> WakeResult:
        if not self.configured():
            return WakeResult(False, "cli", "not configured (command_template required)")
        cmd = self._render_file(prompt, reason) if not dry_run else self.render(prompt, reason)
        if dry_run:
            return WakeResult(True, "cli", f"[dry-run] {cmd}")
        start = time.monotonic()
        try:
            r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
            ok = r.returncode == 0
            detail = f"exit={r.returncode}"
            if r.stderr:
                detail += f" err={r.stderr.strip()[:200]}"
            return WakeResult(ok, "cli", detail, elapsed=time.monotonic() - start,
                              extra={"reply": r.stdout or "", "stderr": r.stderr or ""})
        except subprocess.TimeoutExpired:
            return WakeResult(False, "cli", f"timeout after {timeout}s", elapsed=time.monotonic() - start)
        except Exception as e:
            return WakeResult(False, "cli", f"failed: {e}", elapsed=time.monotonic() - start)


# ---------------------------------------------------------------------------
# Waker — ordered backends with auto-fallback
# ---------------------------------------------------------------------------

class Waker:
    """Tries each configured backend in priority order until one succeeds.

    Args:
        backends: available backends (any unconfigured ones are dropped).
        primary: "http" | "cli" | "auto". ``auto`` orders http before cli but
            still falls back; an explicit primary puts that transport first.
    """

    def __init__(self, backends: list[WakeBackend], primary: str = "auto"):
        self.primary = primary
        configured = [b for b in backends if b.configured()]
        by_name = {b.name: b for b in configured}

        order: list[WakeBackend] = []
        if primary == "http" and "http" in by_name:
            order.append(by_name["http"])
        elif primary == "cli" and "cli" in by_name:
            order.append(by_name["cli"])
        # append the rest in stable [http, cli] order
        for name in ("http", "cli"):
            b = by_name.get(name)
            if b and b not in order:
                order.append(b)
        self.backends = order

    @property
    def empty(self) -> bool:
        return not self.backends

    def wake(self, prompt: str, reason: str, ctx: dict | None = None, *, idem_key: str = "", dry_run: bool = False, timeout: float = 120.0) -> WakeResult:
        ctx = ctx or {}
        if not idem_key:
            idem_key = f"napcat-{reason}-{int(time.time())}"
        if self.empty:
            return WakeResult(False, "none", "no wake backend configured (run `napcat setup`)")
        attempts: list[str] = []
        for b in self.backends:
            res = b.wake(prompt, reason, ctx, idem_key, dry_run=dry_run, timeout=timeout)
            attempts.append(f"{b.name}: {res.detail}")
            if dry_run:
                return res  # render only the first (primary) backend
            if res.ok:
                return res
        # all failed
        return WakeResult(False, self.backends[-1].name if self.backends else "none",
                          "all backends failed :: " + " | ".join(attempts))

    def test(self) -> list[dict]:
        """Per-backend connectivity/configuration status."""
        out = []
        for b in self.backends:
            out.append({
                "transport": b.name,
                "configured": b.configured(),
                "reachable": b.probe() if b.configured() else False,
                "label": getattr(b, "label", b.name),
            })
        return out

    def list_sessions(self) -> list[dict] | None:
        for b in self.backends:
            if b.name == "http":
                return b.list_sessions()
        return None
