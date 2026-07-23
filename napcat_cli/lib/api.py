"""NapCat HTTP API client."""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

from .config import get_config, DATA_DIR


class NapCatAPI:
    """HTTP client for NapCat OneBot 11 API."""

    def __init__(self, api_url: str | None = None, token: str | None = None, timeout: int | None = None):
        cfg = get_config()
        self.api_url = (api_url or os.environ.get("NAPCAT_API_URL") or cfg.api_url).rstrip("/")
        self.token = token or os.environ.get("NAPCAT_TOKEN") or cfg.token
        self.timeout = timeout if timeout is not None else 30
        self.echo_counter = 0
        self._online_cache: dict = {}

        # Load or create API availability cache

    def _load_api_cache(self) -> None:
        """Load API availability cache from disk. No probe — cache is built lazily."""
        self._unsupported_apis: set[str] = set()
        cache_file = DATA_DIR / "napcat_api_cache.json"
        try:
            if cache_file.exists():
                data = json.loads(cache_file.read_text())
                ts = data.get("timestamp", 0)
                import time
                if time.time() - ts < 3600:  # 1 hour TTL
                    self._unsupported_apis = set(data.get("unsupported", []))
        except Exception:
            pass

    def _save_api_cache(self) -> None:
        """Persist API availability cache to disk."""
        try:
            import time
            cache_file = DATA_DIR / "napcat_api_cache.json"
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps({
                "timestamp": int(time.time()),
                "unsupported": list(self._unsupported_apis),
            }))
        except Exception:
            pass

    def _next_echo(self) -> str:
        """Generate unique echo ID for request tracking."""
        import secrets
        return secrets.token_hex(6)

    def request(self, endpoint: str, method: str = "POST", json_body: dict | None = None, timeout: int | None = None) -> dict:
        """Make a raw API request.

        Args:
            endpoint: API endpoint name (e.g., "get_login_info", ".send_poke")
            method: HTTP method
            json_body: JSON body for POST/PUT requests
            timeout: Per-call timeout in seconds. Falls back to self.timeout (default 30).

        Returns:
            Parsed JSON response as dict.
        """
        url = f"{self.api_url}/{endpoint}"
        call_timeout = timeout if timeout is not None else self.timeout

        body = b""
        if method.upper() in ("POST", "PUT", "PATCH") and json_body is not None:
            body = json.dumps(json_body).encode("utf-8")

        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")

        try:
            with urllib.request.urlopen(req, timeout=call_timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            try:
                err = json.loads(raw)
            except json.JSONDecodeError:
                err = {"status": "error", "message": raw, "retcode": e.code}
            print(f"API error {e.code}: {err}", file=sys.stderr)
            return err
        except urllib.error.URLError as e:
            err = {
                "status": "error",
                "message": f"Connection failed: {e.reason}",
                "retcode": -1,
                "hint": self._connection_hint(),
            }
            print(f"Connection error: {err['message']}", file=sys.stderr)
            return err
        except Exception as e:
            err = {
                "status": "error",
                "message": str(e),
                "retcode": -1,
                "hint": self._connection_hint(),
            }
            print(f"Error: {err['message']}", file=sys.stderr)
            return err
    def _normalize(self, params: dict) -> dict:
        """Normalize params for NapCat 4.x - convert string IDs to int."""
        numeric_fields = {
            "group_id", "user_id", "message_id", "flag",
            "duration", "count", "status", "ext_status", "battery_status",
            "font", "busid", "page", "page_size", "role",
        }
        bool_fields = {
            "no_cache", "enable", "approve", "reject_add_request",
            "auto_escape", "enable_force_push", "upload_file",
        }
        result: dict = {}
        for k, v in params.items():
            if k in numeric_fields and isinstance(v, str):
                try:
                    result[k] = int(v)
                except ValueError:
                    result[k] = v
            elif k in bool_fields and isinstance(v, str):
                result[k] = v.lower() in ("true", "1", "yes")
            else:
                result[k] = v
        return result

    def is_online(self, _cache_ttl: int = 30) -> bool:
        """Check if NapCat bot is currently online.

        Caches result for _cache_ttl seconds to avoid excessive polling.
        """
        import time
        now = time.time()
        if self._online_cache.get("ts") and (now - self._online_cache["ts"]) < _cache_ttl:
            return self._online_cache["online"]

        result = self.request("get_status", method="POST", json_body={}, timeout=5)
        data = result.get("data", {})
        online = bool(data.get("online", False))
        self._online_cache = {"ts": now, "online": online}
        return online

    @staticmethod
    def friendly_error(raw_message: str) -> str:
        """Map NapCat kernel error messages to user-friendly descriptions."""
        mapping: dict[str, str] = {
            "NTEvent serviceAndMethod:NodeIKernelMsgService/sendMsgInfoListener/onMsgInfoListener": "NapCat 内核超时，bot 可能离线",
            "ERR_NEED_MAKEUP": "QQ 风控限制，操作被拒绝",
            "ERR_NOT_GROUP_ADMIN": "不是群管理员",
            "ERR_NOT_IN_GROUP": "不在该群中",
            "ERR_REQUEST_COOLDOWN": "请求冷却中，请稍后再试",
            "ERR_SEND_MSG_FREQ_LIMIT": "发送频率限制，请稍后再试",
            "ERR_GROUP_NOT_FOUND": "群不存在或已退群",
        }
        for pattern, friendly in mapping.items():
            if pattern in raw_message:
                return friendly
        # If message contains kernel internal path, simplify it
        if "NodeIKernel" in raw_message:
            return "NapCat 内核响应超时"
        return raw_message

    # Cache of known unsupported APIs (populated on first use)
    _unsupported_apis: set[str] | None = None

    def is_api_supported(self, action: str) -> bool | None:
        """Check if a NapCat API action is supported.

        Returns True if known supported, False if known unsupported,
        None if unknown (hasn't been tested yet).
        """
        if self._unsupported_apis is None:
            self._unsupported_apis = set()
        if action in self._unsupported_apis:
            return False
        return None  # unknown

    def mark_api_unsupported(self, action: str) -> None:
        """Record that an API action is not supported by this NapCat instance."""
        if self._unsupported_apis is None:
            self._unsupported_apis = set()
        self._unsupported_apis.add(action)
        self._save_api_cache()


    def call(self, action: str, timeout: int | None = None, **params: Any) -> dict:
        """Call an API action with parameters.

        Args:
            action: Action name (e.g., "send_msg", "get_group_info")
            timeout: Per-call timeout in seconds. Falls back to self.timeout.
            **params: Action parameters

        Returns:
            Parsed JSON response.
        """
        if self.is_api_supported(action) is False:
            return {
                "status": "failed",
                "retcode": 200,
                "data": None,
                "message": f"API '{action}' is not supported by this NapCat instance. Check OneBot 11 spec or NapCat extensions.",
                "wording": f"API '{action}' unsupported",
            }

        normalized = self._normalize(params)
        result = self.request(action, json_body=normalized, timeout=timeout)

        # Detect unsupported API responses and cache them
        if result.get("message", "") == "不支持的Api" or "不支持的Api" in str(result.get("wording", "")):
            self.mark_api_unsupported(action)
            result["message"] = f"API '{action}' is not supported by this NapCat instance. Check OneBot 11 spec or NapCat extensions."
            result["wording"] = f"API '{action}' unsupported"
        # Map kernel errors to friendly messages
        raw_msg = result.get("message", "")
        if raw_msg and result.get("retcode", 0) != 0:
            friendly = self.friendly_error(raw_msg)
            if friendly != raw_msg:
                result["message"] = friendly

        return result

    def _connection_hint(self) -> str:
        """Generate helpful hint when connection fails."""
        hints = []
        # Check if container is running
        try:
            import subprocess
            result = subprocess.run(
                ["docker", "ps", "--filter", "name=napcat", "--format", "{{.Status}}"],
                capture_output=True, text=True, timeout=5,
            )
            if not result.stdout.strip():
                hints.append("NapCat Docker container is not running. Start with: docker compose up -d")
            elif "Exited" in result.stdout:
                hints.append("NapCat container has exited. Check logs: docker logs napcat")
        except Exception:
            pass

        # Check URL
        if not self.api_url:
            hints.append("NAPCAT_API_URL not set. Default: http://127.0.0.1:18801")

        # Check if logged in
        hints.append("Make sure you have logged in via WebUI (http://127.0.0.1:6099/webui)")

        return " | ".join(hints) if hints else "Check NapCat container and login status"
