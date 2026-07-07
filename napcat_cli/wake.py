"""Wake-command builder — shared by daemon and CLI."""
from __future__ import annotations

import shlex


def build_wake_command(wake_command: str, reason: str) -> str:
    """Render a wake_command template, substituting $REASON/${REASON}/{reason}
    with shlex-quoted reason. Empty wake_command -> ''."""
    if not wake_command:
        return ""
    safe = shlex.quote(reason)
    return (wake_command.replace("$REASON", safe)
                        .replace("${REASON}", safe)
                        .replace("{reason}", safe))
