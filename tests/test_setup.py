"""Tests for napcat setup wizard (non-interactive mode)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

from napcat_cli.lib.config import NapCatConfig, get_config
from napcat_cli.lib.config import DATA_DIR
from napcat_cli.setup_wizard import run_setup


class TestSetupNonInteractive:
    def test_writes_config_and_daemon_json(self, tmp_path):
        """run_setup creates config.json and daemon.json in DATA_DIR."""
        os.environ["NAPCAT_DATA_DIR"] = str(tmp_path)

        with patch("napcat_cli.lib.api.NapCatAPI.call", return_value={"retcode": 0}):
            rc = run_setup(non_interactive=True, yes=True)

        assert rc == 0
        # setup_wizard writes to DATA_DIR, which we set via env
        assert (tmp_path / "config.json").exists()
        assert (tmp_path / "daemon.json").exists()

    def test_wake_command_has_hermes(self, tmp_path):
        """Non-interactive setup configures the Hermes wake preset."""
        os.environ["NAPCAT_DATA_DIR"] = str(tmp_path)

        with patch("napcat_cli.lib.api.NapCatAPI.call", return_value={"retcode": 0}):
            run_setup(non_interactive=True, yes=True)

        cfg = get_config()
        assert cfg.wake_preset == "hermes"
        assert cfg.wake_session == "napcat-qq"
        assert cfg.wake_enabled is True
        assert cfg.wake_primary == "auto"
        assert cfg.wake_command == ""  # legacy broken command cleared

    def test_daemon_json_has_all_keys(self, tmp_path):
        """daemon.json after setup has all keys (base + wake_*)."""
        os.environ["NAPCAT_DATA_DIR"] = str(tmp_path)

        with patch("napcat_cli.lib.api.NapCatAPI.call", return_value={"retcode": 0}):
            run_setup(non_interactive=True, yes=True)

        daemon_data = json.loads((tmp_path / "daemon.json").read_text())
        expected = {
            "self_id", "wake_command", "wake_on_event",
            "ws_port", "http_port", "group_trigger_word", "private_trigger",
            "skills_fs_enabled", "skills_fs_mountpoint",
            "skills_fs_binary", "skills_fs_config",
            "wake_enabled", "wake_preset", "wake_primary", "wake_session",
            "wake_http_url", "wake_http_session_id", "wake_cli_command",
            "wake_debounce_seconds", "wake_cooldown_seconds",
            "wake_new_message_idle_seconds",
        }
        assert set(daemon_data.keys()) == expected


class TestSkillsFsConfigInstall:
    def test_setup_installs_skills_fs_json(self, tmp_path):
        """Fresh install must ship a skills-fs.json with providers+mounts."""
        os.environ["NAPCAT_DATA_DIR"] = str(tmp_path)
        os.environ["NAPCAT_SKILLSFS_CONFIG"] = str(tmp_path / "skills-fs.json")
        os.environ["NAPCAT_HERMES_SKILL_DIR"] = str(tmp_path / "hermes-skill")

        with patch("napcat_cli.lib.api.NapCatAPI.call", return_value={"retcode": 0}):
            rc = run_setup(non_interactive=True, yes=True)

        assert rc == 0
        cfg_path = tmp_path / "skills-fs.json"
        assert cfg_path.is_file()
        data = json.loads(cfg_path.read_text())
        assert data.get("providers"), "providers required for FUSE mounts"
        assert any(p.get("id") == "napcat" for p in data["providers"])
        assert data.get("mounts"), "mounts required"

    def test_setup_keeps_existing_symlink(self, tmp_path):
        """A working symlink must not be replaced by setup."""
        os.environ["NAPCAT_DATA_DIR"] = str(tmp_path)
        os.environ["NAPCAT_HERMES_SKILL_DIR"] = str(tmp_path / "hermes-skill")
        real = tmp_path / "real-skills-fs.json"
        link = tmp_path / "skills-fs.json"
        real.write_text(json.dumps({
            "providers": [{"id": "napcat", "url": "http://127.0.0.1:18821/invoke"}],
            "mounts": [{"path": "/napcat", "kind": "dir", "mode": "0755", "agents": False}],
        }))
        link.symlink_to(real)
        os.environ["NAPCAT_SKILLSFS_CONFIG"] = str(link)

        with patch("napcat_cli.lib.api.NapCatAPI.call", return_value={"retcode": 0}):
            run_setup(non_interactive=True, yes=True, force=True)

        assert link.is_symlink()
        assert link.resolve() == real.resolve()
