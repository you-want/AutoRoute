#!/usr/bin/env python3
"""Queue a model change on the currently running Codex thread.

Codex's queue command targets an existing thread, so the thread history stays
attached to the next turn. This is preferable to trying to write keystrokes to
an arbitrary terminal (which can target the wrong session).
"""

from __future__ import annotations

import os
import subprocess
from typing import Sequence


def current_thread_id() -> str | None:
    return os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID")


def switch_command(
    model: str,
    effort: str | None,
    message: str,
    *,
    thread_id: str | None = None,
    codex_bin: str | None = None,
) -> list[str] | None:
    thread = thread_id or current_thread_id()
    if not thread:
        return None
    command = [
        codex_bin or os.environ.get("AUTOROUTE_CODEX_BIN", "codex"),
        "queue",
        "--thread",
        thread,
        "--message",
        message,
        "-m",
        model,
    ]
    if effort:
        command.extend(["-c", f'model_reasoning_effort="{effort}"'])
    return command


def queue_model_switch(
    model: str,
    effort: str | None,
    message: str,
    *,
    thread_id: str | None = None,
    codex_bin: str | None = None,
) -> tuple[bool, list[str] | None, str | None]:
    """Queue a model switch and continuation on the current Codex thread."""
    command = switch_command(model, effort, message, thread_id=thread_id, codex_bin=codex_bin)
    if command is None:
        return False, None, "CODEX_THREAD_ID is unavailable"
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, command, str(exc)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip() or f"exit code {completed.returncode}"
        return False, command, detail
    return True, command, None


__all__: Sequence[str] = ("current_thread_id", "switch_command", "queue_model_switch")
