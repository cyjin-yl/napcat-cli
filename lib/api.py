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

    def __init__(self, api_url: str | None = None, token: str | None = None):
        cfg = get_config()
        self.api_url = (api_url or os.environ.get("NAPCAT_API_URL") or cfg.api_url).rstrip("/")
        self.token = token or os.environ.get("NAPCAT_TOKEN") or cfg.token
        self.echo_counter = 0

    def _next_echo(self) -> str:
        """Generate unique echo ID for request tracking."""
        import secrets
        return secrets.token_hex(6)

    def request(self, endpoint: str, method: str = "POST", json_body: dict | None = None) -> dict:
        """Make a raw API request.

        Args:
            endpoint: API endpoint name (e.g., "get_login_info", ".send_poke")
            method: HTTP method
            json_body: JSON body for POST/PUT requests

        Returns:
            Parsed JSON response as dict.
        """
        url = f"{self.api_url}/{endpoint}"

        body = b""
        if method.upper() in ("POST", "PUT", "PATCH") and json_body is not None:
            body = json.dumps(json_body).encode("utf-8")

        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("Content-Type", "application/json")
        if self.token:
            req.add_header("Authorization", f"Bearer {self.token}")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
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


    def call(self, action: str, **params: Any) -> dict:
        """Call an API action with parameters.

        Args:
            action: Action name (e.g., "send_msg", "get_group_info")
            **params: Action parameters

        Returns:
            Parsed JSON response.
        """
        normalized = self._normalize(params)
        return self.request(action, json_body=normalized)

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
