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
    wake_on_event: bool = True
    wake_command: str = ""
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

    def save(self) -> None:
        """Save config to file atomically via temp file then rename."""
        import os
        data_dir = _get_data_dir()
        config_file = data_dir / "config.json"
        tmp_path = config_file.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))
        os.replace(str(tmp_path), str(config_file))

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
