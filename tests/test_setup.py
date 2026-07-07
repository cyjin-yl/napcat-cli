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
        """Non-interactive setup generates a Hermes wake command."""
        os.environ["NAPCAT_DATA_DIR"] = str(tmp_path)

        with patch("napcat_cli.lib.api.NapCatAPI.call", return_value={"retcode": 0}):
            run_setup(non_interactive=True, yes=True)

        cfg = get_config()
        assert cfg.wake_command.startswith("hermes -c")
        assert "-z " in cfg.wake_command
        assert "-s napcat-cli --yolo" in cfg.wake_command
        assert cfg.wake_on_event is True

    def test_daemon_json_has_11_keys(self, tmp_path):
        """daemon.json after setup has all 11 keys."""
        os.environ["NAPCAT_DATA_DIR"] = str(tmp_path)

        with patch("napcat_cli.lib.api.NapCatAPI.call", return_value={"retcode": 0}):
            run_setup(non_interactive=True, yes=True)

        daemon_data = json.loads((tmp_path / "daemon.json").read_text())
        expected = {
            "self_id", "wake_command", "wake_on_event",
            "ws_port", "http_port", "group_trigger_word", "private_trigger",
            "skills_fs_enabled", "skills_fs_mountpoint",
            "skills_fs_binary", "skills_fs_config",
        }
        assert set(daemon_data.keys()) == expected
