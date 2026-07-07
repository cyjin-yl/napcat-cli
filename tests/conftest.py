"""Pytest configuration for napcat-cli tests."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def pytest_configure(config) -> None:
    """Set up isolated NAPCAT_DATA_DIR per test session."""
    tmp = Path(config.rootdir) / ".test-data"
    tmp.mkdir(exist_ok=True)
    os.environ["NAPCAT_DATA_DIR"] = str(tmp)

    # Ensure repo root is on sys.path for editable/source imports
    repo_root = str(Path(__file__).parent.parent)
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


def pytest_unconfigure(config) -> None:
    """Clean up test data directory."""
    tmp = Path(config.rootdir) / ".test-data"
    import shutil
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
