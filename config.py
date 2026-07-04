"""Configuration for napcat-cli."""

import os
import json
from pathlib import Path
from dataclasses import dataclass, field


@dataclass
class NapCatConfig:
    """NapCat connection settings."""
    http_url: str = "http://127.0.0.1:18801"
    ws_url: str = "ws://127.0.0.1:18800"
    token: str = ""
    qq_account: int = 0  # auto-detected


@dataclass
class HTTPServerConfig:
    """HTTP server for skills-fs provider."""
    host: str = "127.0.0.1"
    port: int = 6099
    # Overridden by WebUI port; use separate port for skills-fs provider
    provider_host: str = "127.0.0.1"
    provider_port: int = 18802


@dataclass
class WatchConfig:
    """Watch daemon settings."""
    signal_dir: Path = field(default_factory=lambda: Path.home() / ".hermes" / "napcat-signals")
    event_cache: Path = field(default_factory=lambda: Path.home() / ".hermes" / "napcat-events.json")
    poll_interval_ms: int = 500


@dataclass
class SkillsFsConfig:
    """Skills-fs mount configuration."""
    mount_point: Path = field(default_factory=lambda: Path.home() / ".hermes" / "skills" / "napcat-cli")
    lib_path: Path = field(default_factory=lambda: Path(__file__).parent / "skills-fs" / "binding" / "python" / "lib" / "libgobridge.so")


@dataclass
class Config:
    """Root configuration."""
    napcat: NapCatConfig = field(default_factory=NapCatConfig)
    http_server: HTTPServerConfig = field(default_factory=HTTPServerConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)
    skills_fs: SkillsFsConfig = field(default_factory=SkillsFsConfig)
    log_level: str = "INFO"

    @classmethod
    def from_file(cls, path: str | Path) -> "Config":
        """Load config from YAML/JSON file."""
        path = Path(path)
        if not path.exists():
            return cls()
        data = json.loads(path.read_text()) if path.suffix == ".json" else {}
        # Simple YAML subset: parse key-value pairs
        cfg = cls()
        if "napcat" in data:
            d = data["napcat"]
            cfg.napcat.http_url = d.get("http_url", cfg.napcat.http_url)
            cfg.napcat.ws_url = d.get("ws_url", cfg.napcat.ws_url)
            cfg.napcat.token = d.get("token", cfg.napcat.token)
        if "http_server" in data:
            d = data["http_server"]
            cfg.http_server.provider_port = d.get("provider_port", cfg.http_server.provider_port)
        if "watch" in data:
            d = data["watch"]
            cfg.watch.poll_interval_ms = d.get("poll_interval_ms", cfg.watch.poll_interval_ms)
        return cfg

    @classmethod
    def load(cls) -> "Config":
        """Load from env, config file, or defaults."""
        cfg = cls()
        env_url = os.environ.get("NAPCAT_HTTP_URL")
        if env_url:
            cfg.napcat.http_url = env_url
        env_ws = os.environ.get("NAPCAT_WS_URL")
        if env_ws:
            cfg.napcat.ws_url = env_ws
        env_token = os.environ.get("NAPCAT_TOKEN")
        if env_token:
            cfg.napcat.token = env_token
        env_port = os.environ.get("NAPCAT_PROVIDER_PORT")
        if env_port:
            cfg.http_server.provider_port = int(env_port)

        # Check for config file
        for p in [Path.cwd() / "napcat-cli.json", Path.home() / ".config" / "napcat-cli.json"]:
            if p.exists():
                cfg = cls.from_file(p)
                break
        return cfg


def get_config() -> Config:
    """Get global configuration."""
    return Config.load()
