"""Interactive setup wizard for napcat-cli configuration."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

from napcat_cli.lib.config import NapCatConfig, DATA_DIR
from napcat_cli.lib.api import NapCatAPI


_DEFAULT_HERMES_PROMPT = (
    "收到新的 QQ 消息。请使用 napcat-cli 查看当前未处理收件箱，"
    "结合已有上下文决定是否需要回复，并发送回复。"
)


def _write_daemon_json(cfg: NapCatConfig, data_dir: str | Path) -> None:
    """Write a complete daemon.json with all fields consumed by watch.py."""
    cfg_dict = {
        "self_id": cfg.self_id or "",
        "wake_command": cfg.wake_command,
        "wake_on_event": cfg.wake_on_event,
        "ws_port": cfg.ws_port,
        "http_port": cfg.http_port,
        "group_trigger_word": cfg.group_trigger_word,
        "private_trigger": cfg.private_trigger,
        "skills_fs_enabled": cfg.skills_fs_enabled,
        "skills_fs_mountpoint": cfg.skills_fs_mountpoint,
        "skills_fs_binary": cfg.skills_fs_binary,
        "skills_fs_config": cfg.skills_fs_config,
    }
    daemon_path = Path(data_dir) / "daemon.json"
    daemon_path.write_text(json.dumps(cfg_dict, indent=2))


def _prompt_token_with_validation(api_url: str, non_interactive: bool = False) -> str:
    """Validate a token against NapCat; re-prompt on failure."""
    try:
        token = os.environ.get("NAPCAT_TOKEN", "")
    except Exception:
        token = ""

    if non_interactive:
        # Try once, never block
        try:
            api = NapCatAPI(api_url=api_url, token=token, timeout=5)
            r = api.call("get_login_info")
            if r.get("retcode") == 0:
                print(f"  Token validated OK")
            else:
                print(f"  Token validation skipped in non-interactive mode")
        except Exception:
            pass
        return token

    # Interactive: validate, re-prompt on failure
    try:
        api = NapCatAPI(api_url=api_url, token=token, timeout=5)
        r = api.call("get_login_info")
        if r.get("retcode") == 0:
            print(f"  Token validated OK")
            return token
    except Exception:
        pass

    # Validation failed — re-prompt
    print(f"  Token 校验失败。重新输入 token（直接回车则忽略校验继续）：", file=sys.stderr)
    while True:
        try:
            new_token = input("  > ")
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            return token
        if not new_token:
            print(f"  Keeping current token, skipping validation.")
            return token
        token = new_token
        try:
            api = NapCatAPI(api_url=api_url, token=token, timeout=5)
            r = api.call("get_login_info")
            if r.get("retcode") == 0:
                print(f"  Token validated OK")
                return token
        except Exception:
            pass
        print(f"  Token 校验失败。重新输入 token（直接回车则忽略校验继续）：", file=sys.stderr)


def _check_skills_fs_binary(cfg: NapCatConfig) -> tuple[str, str]:
    """Check for skills-fs binary. Returns (path_or_empty, how)."""
    binary = cfg.skills_fs_binary
    if binary:
        p = Path(binary)
        if p.exists() and os.access(p, os.X_OK):
            return binary, "configured"
        print(f"  Configured binary '{binary}' not found or not executable.", file=sys.stderr)

    # Try shipped binary next to repo
    from napcat_cli.daemon.watch import _resolve_shipped_binary
    shipped = _resolve_shipped_binary()
    if shipped and Path(shipped).exists():
        return shipped, "shipped"

    # Search PATH
    import shutil
    found = shutil.which("skills-fs")
    if found:
        return found, "PATH"

    return "", "missing"


def _install_hermes_skill(cfg: NapCatConfig, force: bool = False) -> None:
    """Copy SKILL.md and persona.md into ~/.hermes/skills/napcat-cli/."""
    hermes_dir = Path.home() / ".hermes" / "skills" / "napcat-cli"

    if hermes_dir.exists() and not force:
        print(f"  Hermes skill already installed at {hermes_dir}. Use --force to overwrite.")
        return

    try:
        import importlib.resources as pkg_resources
        skill_data = pkg_resources.files("napcat_cli.data")
        skill_md = skill_data.joinpath("SKILL.md").read_text()
        persona_md = skill_data.joinpath("persona.md").read_text()
    except Exception:
        print("  Could not read bundled skill files, skipping Hermes skill install.", file=sys.stderr)
        return

    hermes_dir.mkdir(parents=True, exist_ok=True)
    (hermes_dir / "SKILL.md").write_text(skill_md)
    (hermes_dir / "persona.md").write_text(persona_md)
    print(f"  Hermes skill installed at {hermes_dir}")


def _check_cli_symlink(yes: bool = False) -> None:
    """Check if napcat is available on PATH; offer symlink if not."""
    import shutil
    if shutil.which("napcat"):
        print("  'napcat' is already on PATH.")
        return

    local_bin = Path.home() / ".local" / "bin"
    target = local_bin / "napcat"

    if target.exists():
        print(f"  Symlink exists at {target}")
        return

    # Check if installed via pip/uv (entry point)
    try:
        import importlib.metadata
        info = importlib.metadata.distribution("napcat-cli")
        if info:
            print("  'napcat' is installed via pip/uv — should be on PATH.")
            return
    except Exception:
        pass

    # Source tree — offer symlink
    print(f"  'napcat' not found on PATH.")
    print(f"  Create symlink: ln -sf <path-to-napcat> {target}")


def _prompt_str(label: str, default: str, current: str = "") -> str:
    """Interactive prompt showing current value; bare Enter keeps current."""
    display = current if current else default
    if not current:
        print(f"  {label} [{display}]: ", end="", file=sys.stderr)
    else:
        print(f"  {label} (current: {current}) [{default}]: ", end="", file=sys.stderr)
    try:
        val = input()
    except (EOFError, KeyboardInterrupt):
        print(file=sys.stderr)
        val = ""
    return val if val else current if current else default


def _prompt_str_ni(default: str) -> str:
    """Non-interactive: return env var or default."""
    return default


def run_setup(non_interactive: bool = False, yes: bool = False, force: bool = False) -> int:
    """Run the interactive setup wizard."""
    print("=== napcat-cli Setup ===")
    print()

    cfg = NapCatConfig()

    # --- 1. NapCat connection ---
    print("[1] NapCat connection")
    if non_interactive:
        api_url = os.environ.get("NAPCAT_API_URL", "http://127.0.0.1:18801")
        token = _prompt_token_with_validation(api_url, non_interactive=True)
    else:
        api_url = _prompt_str("API URL", "http://127.0.0.1:18801")
        token = _prompt_token_with_validation(api_url)

    cfg.api_url = api_url
    cfg.token = token
    print()

    # --- 2. Data dir ---
    print("[2] Data directory")
    if non_interactive:
        data_dir = os.environ.get("NAPCAT_DATA_DIR", str(Path.home() / ".napcat-data"))
    else:
        data_dir = _prompt_str("Data directory", str(Path.home() / ".napcat-data"))
    print(f"  Data dir: {data_dir}")
    print()

    # --- 3. skills-fs ---
    print("[3] skills-fs configuration")
    if non_interactive:
        cfg.skills_fs_mountpoint = os.environ.get("NAPCAT_SKILLSFS_MOUNTPOINT",
            str(Path.home() / ".napcat-data" / "skills"))
        cfg.skills_fs_config = os.environ.get("NAPCAT_SKILLSFS_CONFIG",
            str(Path.home() / ".napcat-data" / "skills-fs.json"))
    else:
        cfg.skills_fs_mountpoint = _prompt_str(
            "skills-fs mountpoint", str(Path.home() / ".napcat-data" / "skills"))
        cfg.skills_fs_config = _prompt_str(
            "skills-fs config path", str(Path.home() / ".napcat-data" / "skills-fs.json"))
    cfg.skills_fs_enabled = True

    # Check binary
    binary_path, how = _check_skills_fs_binary(cfg)
    if how == "missing":
        print("  Go binary not found. Build it: cd skills-fs && make build (needs Go).", file=sys.stderr)
    else:
        print(f"  skills-fs binary found: {binary_path} ({how})")
    if binary_path:
        cfg.skills_fs_binary = binary_path
    print()

    # --- 4. Wake agent preset ---
    print("[4] Wake agent configuration")
    if non_interactive:
        preset = "hermes"
    else:
        print("  Choose wake agent: [H]ermes / [C]ustom / [N]one", file=sys.stderr)
        try:
            choice = input("  > ").lower().strip()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            choice = "h"
        if choice in ("h", "hermes"):
            preset = "hermes"
        elif choice in ("c", "custom"):
            preset = "custom"
        else:
            preset = "none"

    if preset == "hermes":
        if non_interactive:
            session = "napcat-qq"
            prompt = _DEFAULT_HERMES_PROMPT
        else:
            session = _prompt_str("Hermes session", "napcat-qq")
            prompt = _prompt_str("Wake prompt", _DEFAULT_HERMES_PROMPT)
        cfg.wake_command = (
            f"hermes -c {shlex.quote(session)} "
            f"-z {shlex.quote(prompt)} "
            f"-s napcat-cli --yolo"
        )
        cfg.wake_on_event = True
        print(f"  wake_command = {cfg.wake_command[:80]}...")
    elif preset == "custom":
        if non_interactive:
            cfg.wake_command = ""
        else:
            cfg.wake_command = _prompt_str("Custom wake command", "")
        cfg.wake_on_event = bool(cfg.wake_command)
    else:
        cfg.wake_command = ""
        cfg.wake_on_event = False
        print("  Wake disabled.")
    print()

    # --- 5. Install skill into Hermes ---
    print("[5] Hermes skill installation")
    if non_interactive or yes:
        _install_hermes_skill(cfg, force=force)
    else:
        print(f"  Install skill to ~/.hermes/skills/napcat-cli/? [Y/n]", file=sys.stderr)
        try:
            choice = input("  > ").lower().strip()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            choice = "y"
        if choice in ("", "y", "yes"):
            _install_hermes_skill(cfg, force=force)
    print()

    # --- 6. Check CLI symlink ---
    print("[6] CLI availability")
    _check_cli_symlink(yes=yes)
    print()

    # --- Persist ---
    print("Writing configuration...")
    os.environ["NAPCAT_DATA_DIR"] = data_dir
    cfg.save()
    _write_daemon_json(cfg, data_dir)
    print(f"  config.json written to {data_dir}/config.json")
    print(f"  daemon.json written to {data_dir}/daemon.json")
    print()
    print("=== Setup complete ===")
    print("Start the daemon with: napcat daemon start")
    print("Test wake with: napcat wake --dry-run")

    return 0
