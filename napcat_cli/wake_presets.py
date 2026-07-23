"""Wake presets — turn NapCatConfig into a :class:`Waker`.

Hermes is the default preset but is *not* required. ``custom`` lets you point the
HTTP/CLI backends at any agent; ``none`` disables wake. The HTTP key is read
from ``NAPCAT_WAKE_HTTP_KEY`` (or ``HERMES_API_KEY``) if not set in config, so it
does not have to live in plaintext ``config.json``.
"""
from __future__ import annotations

import os

from .lib.config import NapCatConfig
from .wake_backend import CliWakeBackend, HttpWakeBackend, Waker

HERMES_DEFAULT_HTTP_URL = "http://127.0.0.1:8642"
HERMES_DEFAULT_CLI = "hermes --continue {session} -z {prompt} --yolo --pass-session-id"
HERMES_PATH = "/api/sessions/{session}/chat"


def _http_key(cfg: NapCatConfig) -> str:
    return (
        cfg.wake_http_key
        or os.environ.get("NAPCAT_WAKE_HTTP_KEY", "")
        or os.environ.get("HERMES_API_KEY", "")
    )


def _http_backend(cfg: NapCatConfig, *, fill_defaults: bool) -> HttpWakeBackend | None:
    base = cfg.wake_http_url or (HERMES_DEFAULT_HTTP_URL if fill_defaults else "")
    key = _http_key(cfg)
    if not base or not key:
        return None
    return HttpWakeBackend(
        base_url=base,
        key=key,
        session=cfg.wake_session,
        session_id=cfg.wake_http_session_id,
        path_template=HERMES_PATH,
        body_field="input",
        label="hermes-http" if fill_defaults else "http",
    )


def _cli_backend(cfg: NapCatConfig, *, fill_defaults: bool) -> CliWakeBackend | None:
    tmpl = cfg.wake_cli_command or (HERMES_DEFAULT_CLI if fill_defaults else "")
    if not tmpl:
        return None
    return CliWakeBackend(tmpl, session=cfg.wake_session,
                          label="hermes-cli" if fill_defaults else "cli")


def build_waker(cfg: NapCatConfig) -> Waker:
    """Build a Waker from ``cfg.wake_preset`` and the wake_* config fields."""
    preset = (cfg.wake_preset or "hermes").lower()
    backends = []

    if preset == "hermes":
        # Hermes defaults fill empty fields; http is optional (needs key), cli is on by default.
        # CLI is LEGACY / not recommended — prefer HTTP.
        http = _http_backend(cfg, fill_defaults=True)
        cli = _cli_backend(cfg, fill_defaults=True)
        if http:
            backends.append(http)
        if cli:
            backends.append(cli)
    elif preset == "custom":
        http = _http_backend(cfg, fill_defaults=False)
        cli = _cli_backend(cfg, fill_defaults=False)
        if http:
            backends.append(http)
        if cli:
            backends.append(cli)
    # preset == "none": no backends

    primary = (cfg.wake_primary or "auto").lower()
    if primary not in ("http", "cli", "auto"):
        primary = "auto"
    return Waker(backends, primary=primary)
