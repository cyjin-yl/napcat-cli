"""Tests for NapCatConfig and daemon.json helper."""
from __future__ import annotations

import json
import os
from pathlib import Path

from napcat_cli.lib.config import NapCatConfig, get_config
from napcat_cli.setup_wizard import _write_daemon_json


class TestNapCatConfig:
    def test_roundtrip(self, tmp_path):
        os.environ["NAPCAT_DATA_DIR"] = str(tmp_path)
        cfg = NapCatConfig()
        cfg.api_url = "http://test.example.com:9999"
        cfg.token = "test-token-123"
        cfg.skills_fs_mountpoint = "/custom/mount"
        cfg.skills_fs_enabled = False
        cfg.save()

        reloaded = get_config()
        assert reloaded.api_url == "http://test.example.com:9999"
        assert reloaded.token == "test-token-123"
        assert reloaded.skills_fs_mountpoint == "/custom/mount"
        assert reloaded.skills_fs_enabled is False

    def test_set_int(self, tmp_path):
        os.environ["NAPCAT_DATA_DIR"] = str(tmp_path)
        cfg = NapCatConfig()
        cfg.set("ws_port", "9999")
        assert cfg.ws_port == 9999
        assert isinstance(cfg.ws_port, int)

    def test_set_bool(self, tmp_path):
        os.environ["NAPCAT_DATA_DIR"] = str(tmp_path)
        cfg = NapCatConfig()
        cfg.set("skills_fs_enabled", "true")
        assert cfg.skills_fs_enabled is True


class TestWriteDaemonJson:
    def test_emits_all_11_keys(self, tmp_path):
        os.environ["NAPCAT_DATA_DIR"] = str(tmp_path)
        cfg = NapCatConfig()
        cfg.skills_fs_mountpoint = "/test/mount"
        cfg.skills_fs_binary = "/test/bin"
        cfg.skills_fs_config = "/test/config.json"
        cfg.save()

        _write_daemon_json(cfg, str(tmp_path))

        daemon_file = tmp_path / "daemon.json"
        assert daemon_file.exists()

        data = json.loads(daemon_file.read_text())
        expected_keys = {
            "self_id", "wake_command", "wake_on_event",
            "ws_port", "http_port",
            "group_trigger_word", "private_trigger",
            "skills_fs_enabled", "skills_fs_mountpoint",
            "skills_fs_binary", "skills_fs_config",
        }
        assert set(data.keys()) == expected_keys
        assert data["skills_fs_mountpoint"] == "/test/mount"
        assert data["skills_fs_binary"] == "/test/bin"
        assert data["skills_fs_config"] == "/test/config.json"

    def test_skills_fs_fields_survive_reload(self, tmp_path):
        os.environ["NAPCAT_DATA_DIR"] = str(tmp_path)
        cfg = NapCatConfig()
        cfg.skills_fs_mountpoint = "/persistent/mount"
        cfg.skills_fs_enabled = True
        cfg.save()
        _write_daemon_json(cfg, str(tmp_path))

        daemon_file = tmp_path / "daemon.json"
        data = json.loads(daemon_file.read_text())
        assert data["skills_fs_mountpoint"] == "/persistent/mount"
        assert data["skills_fs_enabled"] is True
