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
    event_dir: str = "events"
    alert_dir: str = "alerts"
    log_file: str = "daemon.log"

    def save(self) -> None:
        """Save config to file."""
        # 🔴 问题：配置文件写入没有原子性保护
        # 问题1：直接写入可能导致配置文件在写入过程中被读取，得到不完整的配置
        # 问题2：多进程同时保存配置会导致数据丢失
        # 必须改进：使用原子性写入（写入临时文件后重命名）或文件锁
        CONFIG_FILE.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))  # ❌ 问题：非原子操作

    def set(self, key: str, value: str) -> None:
        """Set a config value by key name."""
        if hasattr(self, key):
            # Type conversion
            attr = getattr(self, key)
            if isinstance(attr, int):
                value = int(value)
            elif isinstance(attr, bool):
                value = value.lower() in ("true", "1", "yes")
            setattr(self, key, value)
        else:
            raise ValueError(f"Unknown config key: {key}. Available: {', '.join(f.name for f in fields(self))}")


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
