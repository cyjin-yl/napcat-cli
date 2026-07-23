"""Wake-command builder — shared by daemon, orchestrator, and CLI.

Renders a shell command template, substituting placeholders with shlex-quoted
values so the rendered string is safe to pass to ``subprocess.run(..., shell=True)``.

Supported placeholders (all shlex-quoted):
  $REASON / ${REASON} / {reason}   wake reason (e.g. ``AT_ME``, ``NEW_MESSAGE``)
  {prompt}                         the prompt text to send to the agent
  {session}                        the target session name/id

Empty template -> ``''``. Empty values render as ``''``.
"""
from __future__ import annotations

import shlex


def _q(value: str) -> str:
    """shlex.quote a value; empty -> ''."""
    return shlex.quote(value) if value else "''"


def render_wake_command(
    template: str,
    *,
    reason: str = "",
    prompt: str = "",
    prompt_file: str = "",
    session: str = "",
) -> str:
    """Render a wake command template with shlex-quoted placeholders."""
    if not template:
        return ""
    q_reason = _q(reason)
    q_prompt = _q(prompt)
    q_prompt_file = _q(prompt_file)
    q_session = _q(session)
    return (
        template.replace("$REASON", q_reason)
        .replace("${REASON}", q_reason)
        .replace("{reason}", q_reason)
        .replace("{prompt}", q_prompt)
        .replace("{prompt_file}", q_prompt_file)
        .replace("{session}", q_session)
    )


def build_wake_command(wake_command: str, reason: str) -> str:
    """Back-compat wrapper: render with only $REASON/${REASON}/{reason}.

    Kept so existing callers (and tests) that only pass a reason keep working;
    new code should call :func:`render_wake_command` with prompt/session.
    """
    return render_wake_command(wake_command, reason=reason)
