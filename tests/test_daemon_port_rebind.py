"""D-state-aware http_port rebind on daemon start (no live FUSE required)."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from napcat_cli.cli import (
    _find_free_http_port,
    _is_dstate,
    _port_in_use,
    _proc_state,
    _rebind_http_port_if_needed,
)
from napcat_cli.lib.config import NapCatConfig


def test_port_in_use_false_for_closed_port():
    # bind an ephemeral then close — port should be free afterward
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert _port_in_use(port) is False


def test_find_free_http_port_returns_preferred_when_free():
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    free = s.getsockname()[1]
    s.close()
    assert _find_free_http_port(free, span=1) == free


def test_proc_state_self_not_d():
    st = _proc_state(os.getpid())
    assert st is not None
    assert not st.startswith("D")
    assert _is_dstate(os.getpid()) is False


def test_proc_state_missing_pid():
    assert _proc_state(2_000_000_000) is None
    assert _is_dstate(2_000_000_000) is False


def test_rebind_noop_when_port_free(tmp_path, monkeypatch):
    monkeypatch.setenv("NAPCAT_DATA_DIR", str(tmp_path))
    cfg = NapCatConfig(http_port=39999)
    # ensure free
    if _port_in_use(39999):
        pytest.skip("39999 unexpectedly busy")
    assert _rebind_http_port_if_needed(cfg) == 39999
    assert cfg.http_port == 39999


def test_rebind_when_dstate_holds_port(tmp_path, monkeypatch):
    monkeypatch.setenv("NAPCAT_DATA_DIR", str(tmp_path))
    cfg = NapCatConfig(http_port=40000)
    # pretend 40000 busy, held only by D-state pid 9; 40001 free
    def fake_in_use(port):
        return int(port) == 40000

    with patch("napcat_cli.cli._port_in_use", side_effect=fake_in_use), \
         patch("napcat_cli.cli._listener_pids_for_port", return_value=[9]), \
         patch("napcat_cli.cli._is_dstate", return_value=True), \
         patch("napcat_cli.cli._find_free_http_port", return_value=40001):
        got = _rebind_http_port_if_needed(cfg)
    assert got == 40001
    assert cfg.http_port == 40001
    # persisted
    from napcat_cli.lib.config import get_config
    assert get_config().http_port == 40001


def test_rebind_raises_when_healthy_holder(tmp_path, monkeypatch):
    monkeypatch.setenv("NAPCAT_DATA_DIR", str(tmp_path))
    cfg = NapCatConfig(http_port=40010)

    def fake_in_use(port):
        return int(port) == 40010

    with patch("napcat_cli.cli._port_in_use", side_effect=fake_in_use), \
         patch("napcat_cli.cli._listener_pids_for_port", return_value=[os.getpid()]), \
         patch("napcat_cli.cli._is_dstate", return_value=False), \
         patch("napcat_cli.cli._proc_state", return_value="S"):
        with pytest.raises(RuntimeError, match="healthy process"):
            _rebind_http_port_if_needed(cfg)
