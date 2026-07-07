"""Tests for cmd_fs de-traversal (no iterdir on FUSE mount)."""
from __future__ import annotations

import json
import os
from io import StringIO
from pathlib import Path as PyPath
from unittest.mock import patch, MagicMock

from napcat_cli.lib.config import NapCatConfig
from napcat_cli.cli import main


class TestCmdFsNoTraversal:
    def test_no_iterdir_called(self, tmp_path):
        """cmd_fs should not call iterdir on the mountpoint."""
        os.environ["NAPCAT_DATA_DIR"] = str(tmp_path)

        cfg = NapCatConfig()
        cfg.save()

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "skills_fs": {"status": "mounted", "mountpoint": "/fake/mount", "pid": 1234}
        }).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        out = StringIO()
        err = StringIO()

        iterdir_called = False
        original_iterdir = PyPath.iterdir

        def raise_on_iterdir(self):
            nonlocal iterdir_called
            iterdir_called = True
            raise AssertionError("iterdir should not be called on FUSE mount")

        with patch.object(__import__("sys"), "argv", ["napcat", "fs"]):
            with patch("sys.stdout", out), patch("sys.stderr", err):
                with patch("napcat_cli.cli.get_config", return_value=cfg):
                    with patch("urllib.request.urlopen", return_value=mock_resp):
                        with patch.object(PyPath, "iterdir", raise_on_iterdir):
                            rc = main()

        assert not iterdir_called, "cmd_fs still calls iterdir on FUSE mount!"

    def test_output_has_no_traversal_notice(self, tmp_path):
        """cmd_fs output should contain the no-traversal notice."""
        os.environ["NAPCAT_DATA_DIR"] = str(tmp_path)

        cfg = NapCatConfig()
        cfg.save()

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "skills_fs": {"status": "mounted", "mountpoint": "/fake/mount"}
        }).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)

        out = StringIO()
        err = StringIO()

        with patch.object(__import__("sys"), "argv", ["napcat", "fs"]):
            with patch("sys.stdout", out), patch("sys.stderr", err):
                with patch("napcat_cli.cli.get_config", return_value=cfg):
                    with patch("urllib.request.urlopen", return_value=mock_resp):
                        rc = main()

        output = out.getvalue()
        assert "does not traverse" in output or "traverse" in output.lower()
