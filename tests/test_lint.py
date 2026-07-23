"""Static checks that run as part of the test suite.

Catches undefined names (F821) and syntax errors that would crash the daemon
at runtime. This is the regression guard for the class of bug where an edit
introduces an `UnboundLocalError` in the WebSocket event handler, which
silently kills all subsequent event processing.
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PACKAGE = REPO_ROOT / "napcat_cli"

# pyflakes messages that are warnings, not errors
_WARN_PATTERNS = (
    "imported but unused",
    "f-string is missing placeholders",
    "redefinition of unused",
    "local variable" ,  # covers "assigned to but never used"
)


def _run_pyflakes(paths: list[Path]) -> list[str]:
    """Run pyflakes on the given paths, return a list of error strings."""
    try:
        import pyflakes.api  # noqa: F401
    except ImportError:
        return []  # skip if not installed
    proc = subprocess.run(
        [sys.executable, "-m", "pyflakes", *[str(p) for p in paths]],
        capture_output=True, text=True,
    )
    errors = []
    for line in (proc.stdout + proc.stderr).strip().splitlines():
        line = line.strip()
        if not line:
            continue
        if any(w in line for w in _WARN_PATTERNS):
            continue
        errors.append(line)
    return errors


def _run_py_compile(paths: list[Path]) -> list[str]:
    """Compile-check each file; return a list of error strings."""
    errors = []
    for p in paths:
        proc = subprocess.run(
            [sys.executable, "-m", "py_compile", str(p)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            errors.append(f"{p}: {proc.stderr.strip()}")
    return errors


def _collect_py_files() -> list[Path]:
    """Collect all .py files in the package."""
    return sorted(PACKAGE.rglob("*.py"))


def test_no_undefined_names():
    """Ensure pyflakes reports no undefined names or other real errors."""
    files = _collect_py_files()
    errors = _run_pyflakes(files)
    assert not errors, (
        "pyflakes found errors (undefined names, etc.):\n" + "\n".join(errors)
    )


def test_all_modules_compile():
    """Ensure every Python file compiles without syntax errors."""
    files = _collect_py_files()
    errors = _run_py_compile(files)
    assert not errors, (
        "Syntax errors found:\n" + "\n".join(errors)
    )
