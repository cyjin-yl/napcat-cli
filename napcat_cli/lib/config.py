"""Configuration management for napcat-cli."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields, asdict
from pathlib import Path


def _get_data_dir() -> Path:
    """Return the data directory, re-computed from env each call."""
    return Path(os.environ.get("NAPCAT_DATA_DIR", str(Path.home() / ".napcat-data")))


class _LazyPath:
    """Lazy proxy: re-reads NAPCAT_DATA_DIR on each access."""
    def __truediv__(self, other):
        return _get_data_dir() / other
    def __str__(self):
        return str(_get_data_dir())
    def __repr__(self):
        return f"DATA_DIR({_get_data_dir()!s})"


DATA_DIR = _LazyPath()


@dataclass
class NapCatConfig:
    """napcat-cli configuration."""
    api_url: str = "http://127.0.0.1:18801"
    token: str = ""
    self_id: str | None = None
    webhook_port: int = 18820
    ws_port: int = 18800
    http_port: int = 18821
    wake_on_event: bool = True          # deprecated alias of wake_enabled (back-compat)
    wake_command: str = ""              # legacy shell escape hatch (used only if no backend configured)

    # --- generic, pluggable agent wake (Hermes is the default preset, not required) ---
    wake_enabled: bool = True
    wake_preset: str = "hermes"         # hermes | custom | none
    wake_primary: str = "auto"          # auto | http | cli   (cli is LEGACY / not recommended; auto = http if configured+reachable, else legacy cli fallback)
    wake_session: str = "napcat-qq"     # session name (cli --continue / http session lookup)
    wake_http_url: str = ""             # e.g. http://127.0.0.1:8642  (hermes preset default)
    wake_http_key: str = ""             # bearer token (env: NAPCAT_WAKE_HTTP_KEY)
    wake_http_session_id: str = ""      # explicit session id; else resolved by name via GET /api/sessions
    wake_cli_command: str = ""          # rendered template (LEGACY / not recommended — prefer HTTP); hermes preset fills it
    wake_debounce_seconds: float = 3.0
    wake_cooldown_seconds: float = 30.0
    wake_new_message_idle_seconds: int = 600
    wake_timeout: float = 300.0         # wake backend timeout (seconds)
    event_dir: str = "events"
    alert_dir: str = "alerts"
    log_file: str = "daemon.log"
    group_trigger_word: str = ""
    private_trigger: str = "*"

    # skills-fs integration
    skills_fs_enabled: bool = True
    skills_fs_binary: str = ""
    skills_fs_mountpoint: str = ""
    skills_fs_config: str = ""
    # TUI settings
    tui_show_images: bool = True

    def save(self) -> None:
        """Save config to file atomically via temp file then rename."""
        import os
        data_dir = _get_data_dir()
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise FileNotFoundError(f"Cannot create data directory {data_dir}: {e}") from e
        config_file = data_dir / "config.json"
        tmp_path = config_file.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))
            os.replace(str(tmp_path), str(config_file))
        except FileNotFoundError:
            # Bubble up cleanly so callers can show a friendly error.
            raise

    def set(self, key: str, value: str) -> None:
        """Set a config value by key name."""
        if hasattr(self, key):
            attr = getattr(self, key)
            if isinstance(attr, bool):
                value = value.lower() in ("true", "1", "yes")
            elif isinstance(attr, int):
                value = int(value)
            setattr(self, key, value)
        else:
            raise ValueError(f"Unknown config key: {key}. Available: {', '.join(f.name for f in fields(NapCatConfig))}")


def get_config() -> NapCatConfig:
    """Load config from file or create default."""
    data_dir = _get_data_dir()
    config_file = data_dir / "config.json"
    if config_file.exists():
        try:
            data = json.loads(config_file.read_text())
            return NapCatConfig(**data)
        except Exception:
            pass
    return NapCatConfig()


def ensure_dirs() -> None:
    """Ensure events and alerts directories exist."""
    cfg = get_config()
    data_dir = _get_data_dir()
    (data_dir / cfg.event_dir).mkdir(exist_ok=True)
    (data_dir / cfg.alert_dir).mkdir(exist_ok=True)
