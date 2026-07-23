"""Interactive setup wizard for napcat-cli configuration."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from napcat_cli.lib.config import NapCatConfig, DATA_DIR
from napcat_cli.lib.api import NapCatAPI


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
        "wake_enabled": cfg.wake_enabled,
        "wake_preset": cfg.wake_preset,
        "wake_primary": cfg.wake_primary,
        "wake_session": cfg.wake_session,
        "wake_http_url": cfg.wake_http_url,
        "wake_http_session_id": cfg.wake_http_session_id,
        "wake_cli_command": cfg.wake_cli_command,
        "wake_debounce_seconds": cfg.wake_debounce_seconds,
        "wake_cooldown_seconds": cfg.wake_cooldown_seconds,
        "wake_new_message_idle_seconds": cfg.wake_new_message_idle_seconds,
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
    """Copy SKILL.md, persona.md, and references/ into ~/.hermes/skills/napcat-cli/."""
    hermes_dir = Path.home() / ".hermes" / "skills" / "napcat-cli"

    if hermes_dir.exists() and not force:
        print(f"  Hermes skill already installed at {hermes_dir}. Use --force to overwrite.")
        return

    try:
        import importlib.resources as pkg_resources
        skill_data = pkg_resources.files("napcat_cli.data")
        skill_md = skill_data.joinpath("SKILL.md").read_text()
        persona_md = skill_data.joinpath("persona.md").read_text()
        ref_files: dict[str, str] = {}
        try:
            for child in skill_data.joinpath("references").iterdir():
                if str(child).endswith(".md"):
                    ref_files[child.name] = child.read_text()
        except (FileNotFoundError, NotADirectoryError, OSError):
            pass
    except Exception:
        print("  Could not read bundled skill files, skipping Hermes skill install.", file=sys.stderr)
        return

    hermes_dir.mkdir(parents=True, exist_ok=True)
    (hermes_dir / "SKILL.md").write_text(skill_md)
    (hermes_dir / "persona.md").write_text(persona_md)
    if ref_files:
        ref_dir = hermes_dir / "references"
        ref_dir.mkdir(exist_ok=True)
        for name, content in ref_files.items():
            (ref_dir / name).write_text(content)
    print(f"  Hermes skill installed at {hermes_dir}")


HERMES_GATEWAY_UNIT = "hermes-gateway.service"


def _enable_hermes_api_server(cfg: NapCatConfig) -> bool:
    """Opt-in: enable the Hermes API server and restart the gateway.

    Appends ``API_SERVER_ENABLED=true`` + a generated key to ``~/.hermes/.env``
    (append-only, never rewrites), restarts the systemd unit (passwordless sudo),
    and wires ``cfg.wake_http_url`` / ``cfg.wake_http_key``. Returns True on success.
    """
    import secrets as _secrets
    import subprocess
    hermes_env = Path.home() / ".hermes" / ".env"
    key = _secrets.token_hex(32)
    try:
        with open(hermes_env, "a") as f:
            f.write("\n# Added by napcat-cli setup (agent wake)\n")
            f.write("API_SERVER_ENABLED=true\n")
            f.write(f"API_SERVER_KEY={key}\n")
    except Exception as e:
        print(f"  Could not write {hermes_env}: {e}", file=sys.stderr)
        return False
    print(f"  Appended API_SERVER_ENABLED=true + key to {hermes_env}")
    try:
        subprocess.run(["sudo", "-n", "systemctl", "restart", HERMES_GATEWAY_UNIT],
                       check=True, timeout=60)
        print(f"  Restarted {HERMES_GATEWAY_UNIT}")
    except Exception as e:
        print(f"  WARNING: could not restart {HERMES_GATEWAY_UNIT}: {e}", file=sys.stderr)
        print(f"  Restart manually: sudo systemctl restart {HERMES_GATEWAY_UNIT}", file=sys.stderr)
    cfg.wake_http_url = "http://127.0.0.1:8642"
    cfg.wake_http_key = key
    return True


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
    _skill_dir = str(Path.home() / ".hermes" / "skills" / "napcat-cli")
    if non_interactive:
        cfg.skills_fs_mountpoint = os.environ.get("NAPCAT_SKILLSFS_MOUNTPOINT", _skill_dir)
        cfg.skills_fs_config = os.environ.get("NAPCAT_SKILLSFS_CONFIG",
            str(Path.home() / ".napcat-data" / "skills-fs.json"))
    else:
        cfg.skills_fs_mountpoint = _prompt_str(
            "skills-fs mountpoint (overlay target = the skill dir)", _skill_dir)
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
    print("  Wake is pluggable: Hermes is the default preset, but any HTTP endpoint or")
    print("  shell command works. Default transport = CLI one-shot (zero infra); HTTP is opt-in.")
    if non_interactive:
        preset = "hermes"
    else:
        print("  Choose wake agent: [H]ermes (default) / [C]ustom / [N]one", file=sys.stderr)
        try:
            choice = input("  > ").lower().strip()
        except (EOFError, KeyboardInterrupt):
            print(file=sys.stderr)
            choice = "h"
        preset = "custom" if choice in ("c", "custom") else "none" if choice in ("n", "none") else "hermes"

    cfg.wake_enabled = preset != "none"
    cfg.wake_preset = preset
    cfg.wake_primary = "auto"

    if preset == "hermes":
        session = "napcat-qq" if non_interactive else _prompt_str("Agent session name", "napcat-qq")
        cfg.wake_session = session
        cfg.wake_cli_command = ""  # hermes preset fills the default CLI command at wake time
        cfg.wake_command = ""      # clear any legacy (broken) wake command
        cfg.wake_on_event = True
        print(f"  preset=hermes session={session} primary=auto (CLI one-shot — LEGACY / NOT RECOMMENDED — prefer HTTP)")
        enable_http = False
        if not non_interactive:
            print(f"  Enable the Hermes HTTP API server now? Appends to ~/.hermes/.env and runs\n"
                  f"    sudo systemctl restart {HERMES_GATEWAY_UNIT}\n"
                  f"  (briefly interrupts your messaging platforms). [y/N]", file=sys.stderr)
            try:
                enable_http = input("  > ").lower().strip() in ("y", "yes")
            except (EOFError, KeyboardInterrupt):
                print(file=sys.stderr)
        if enable_http:
            _enable_hermes_api_server(cfg)
        else:
            print("  HTTP API server not enabled — CLI one-shot transport (LEGACY / not recommended) will be used.\n"
                  "  (Run `napcat setup` again or set wake_http_* to switch to the recommended HTTP transport.)")
    elif preset == "custom":
        cfg.wake_session = "napcat-qq" if non_interactive else _prompt_str("Session name", "napcat-qq")
        if not non_interactive:
            cfg.wake_http_url = _prompt_str("HTTP base URL (blank = skip http)", "")
            if cfg.wake_http_url:
                cfg.wake_http_key = _prompt_str("HTTP bearer key", "")
            cfg.wake_cli_command = _prompt_str("CLI command template (blank = skip cli)", "")
        cfg.wake_command = ""
        cfg.wake_on_event = True
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
