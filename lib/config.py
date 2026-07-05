"""Configuration management for napcat-cli."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, fields, asdict
from pathlib import Path

# Default data directory
_DEFAULT_DATA_DIR = Path(os.environ.get("NAPCAT_DATA_DIR", Path.home() / ".napcat-data"))

DATA_DIR = Path(os.environ.get("NAPCAT_DATA_DIR", str(_DEFAULT_DATA_DIR)))
DATA_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = DATA_DIR / "config.json"


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

    def save(self) -> None:
        """Save config to file atomically via temp file then rename."""
        import os
        tmp_path = CONFIG_FILE.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))
        os.replace(str(tmp_path), str(CONFIG_FILE))

    def set(self, key: str, value: str) -> None:
        """Set a config value by key name."""
        if hasattr(self, key):
            # Type conversion
            attr = getattr(self, key)
            if isinstance(attr, int):
                value = int(value)
            elif isinstance(attr, bool) and isinstance(value, str):
                value = value.lower() in ("true", "1", "yes")
            setattr(self, key, value)
        else:
            raise ValueError(f"Unknown config key: {key}. Available: {', '.join(f.name for f in fields(NapCatConfig))}")


def get_config() -> NapCatConfig:
    """Load config from file or create default."""
    if CONFIG_FILE.exists():
        try:
            data = json.loads(CONFIG_FILE.read_text())
            return NapCatConfig(**data)
        except Exception:
            pass
    return NapCatConfig()


def ensure_data_dirs() -> None:
    """Create required subdirectories."""
    cfg = get_config()
    (DATA_DIR / cfg.event_dir).mkdir(exist_ok=True)
    (DATA_DIR / cfg.alert_dir).mkdir(exist_ok=True)
